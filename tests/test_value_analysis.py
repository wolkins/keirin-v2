"""オッズ妙味分析のテスト。

外部通信なし。HTTP・LLM は呼ばない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.models import (
    BetRecommendation,
    OddsEntry,
    Prediction,
    RaceInput,
    Reflection,
    RiderScore,
)
from app.prompt_builder import build_full_prompt, build_value_analysis_section
from app.reporting import build_performance_report
from app.scoring import build_candidate_bets, compute_scores
from app.storage import Storage
from app.value_analysis import (
    VALUE_LABEL_SCORES,
    analyze_value,
    annotate_prediction_with_value,
    build_market_rank_map,
    compute_predicted_strength,
)


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _bet(category: str, combo: str, *, gami_risk: float = 0.0) -> BetRecommendation:
    return BetRecommendation(
        category=category,  # type: ignore[arg-type]
        bet_type="3連単",
        combination=combo,
        reason="test",
        gami_risk=gami_risk,
    )


def _rider_score(car_no: int, *, win: float, second: float, third: float) -> RiderScore:
    return RiderScore(
        car_no=car_no, name=f"R{car_no}",
        win_score=win, second_score=second, third_score=third,
    )


def _odds(combo: str, value: float, bet_type: str = "3連単") -> OddsEntry:
    return OddsEntry(bet_type=bet_type, combination=combo, odds=value)


def _make_prediction(
    honsen: list[BetRecommendation] = (),
    osae: list[BetRecommendation] = (),
    ana: list[BetRecommendation] = (),
    ooana: list[BetRecommendation] = (),
) -> Prediction:
    return Prediction(
        race_id="20260522-X-1", venue="X", race_no=1, is_girls=False,
        marks={}, honsen=list(honsen), osae=list(osae), ana=list(ana), ooana=list(ooana),
    )


# ---------------------------------------------------------------------------
# compute_predicted_strength
# ---------------------------------------------------------------------------


def test_strength_trifecta_high():
    """5-1-3: 5番のwin、1番のsecond、3番のthird で 構成。"""
    scores = [
        _rider_score(1, win=2.0, second=5.0, third=1.0),
        _rider_score(3, win=1.0, second=1.0, third=4.0),
        _rider_score(5, win=8.0, second=2.0, third=0.0),
    ]
    b = _bet("本線", "5-1-3")
    s = compute_predicted_strength(b, scores)
    # 8.0 + 5.0*0.8 + 4.0*0.6 = 8.0 + 4.0 + 2.4 = 14.4
    assert s == pytest.approx(14.4)


def test_strength_missing_car_returns_none():
    scores = [_rider_score(1, win=1.0, second=1.0, third=1.0)]
    b = _bet("本線", "5-1-3")
    assert compute_predicted_strength(b, scores) is None


def test_strength_exacta():
    scores = [
        _rider_score(1, win=1.0, second=5.0, third=0.0),
        _rider_score(5, win=8.0, second=2.0, third=0.0),
    ]
    b = BetRecommendation(category="本線", bet_type="2車単", combination="5-1", reason="t")
    s = compute_predicted_strength(b, scores)
    # 8.0 + 5.0*0.8 = 12.0
    assert s == pytest.approx(12.0)


def test_strength_trio():
    scores = [
        _rider_score(1, win=2.0, second=4.0, third=0.0),
        _rider_score(3, win=1.0, second=3.0, third=0.0),
        _rider_score(5, win=8.0, second=2.0, third=0.0),
    ]
    b = BetRecommendation(category="本線", bet_type="3連複", combination="1=3=5", reason="t")
    s = compute_predicted_strength(b, scores)
    # max(2,1,8)=8.0 + mean(4,3,2)*0.7 = 8.0 + 3.0*0.7 = 10.1
    assert s == pytest.approx(10.1)


# ---------------------------------------------------------------------------
# build_market_rank_map
# ---------------------------------------------------------------------------


def test_market_rank_map_orders_by_odds():
    odds = [
        _odds("5-1-3", 8.5),
        _odds("5-1-6", 12.4),
        _odds("1-5-3", 15.0),
    ]
    m = build_market_rank_map(odds)
    assert m[("3連単", "5-1-3")] == (8.5, 1)
    assert m[("3連単", "5-1-6")] == (12.4, 2)
    assert m[("3連単", "1-5-3")] == (15.0, 3)


def test_market_rank_map_separates_bet_types():
    odds = [
        _odds("5-1-3", 8.5, "3連単"),
        _odds("1=3=5", 4.0, "3連複"),
    ]
    m = build_market_rank_map(odds)
    assert m[("3連単", "5-1-3")] == (8.5, 1)
    assert m[("3連複", "1=3=5")] == (4.0, 1)


def test_market_rank_map_accepts_dict_entries():
    m = build_market_rank_map([
        {"bet_type": "3連単", "combination": "5-1-3", "odds": 8.5}
    ])
    assert m[("3連単", "5-1-3")] == (8.5, 1)


# ---------------------------------------------------------------------------
# annotate_prediction_with_value: ラベル判定マトリクス
# ---------------------------------------------------------------------------


def _build_scenario(*, target_odds: float):
    """1本線+2ダミーで scenario を構築し、target combination のラベルを返す。"""
    # 3つの bet を作って strength_tier の percentile が安定するようにする
    scores = [
        _rider_score(1, win=3.0, second=3.0, third=3.0),
        _rider_score(2, win=2.0, second=2.0, third=2.0),
        _rider_score(3, win=1.0, second=1.0, third=1.0),
        _rider_score(4, win=0.5, second=0.5, third=0.5),
        _rider_score(5, win=10.0, second=10.0, third=10.0),
    ]
    high = _bet("本線", "5-1-2")  # 高 strength
    mid = _bet("本線", "1-2-3")   # 中
    low = _bet("本線", "3-4-2")   # 低
    pred = _make_prediction(honsen=[high, mid, low])
    odds_list = [_odds("5-1-2", target_odds)]
    annotate_prediction_with_value(pred, scores, odds_list)
    return pred.honsen[0]


def test_label_high_cheap():
    """高strength × 安オッズ(<5) → 堅いが安い。"""
    b = _build_scenario(target_odds=3.0)
    assert b.value_label == "堅いが安い"
    assert b.value_score == VALUE_LABEL_SCORES["堅いが安い"]
    assert b.gami_risk >= 0.6


def test_label_high_mid():
    """高strength × 中オッズ(5-15) → 本線向き。"""
    b = _build_scenario(target_odds=10.0)
    assert b.value_label == "本線向き"


def test_label_high_chuana():
    """高strength × 中穴(15-50) → 妙味あり。"""
    b = _build_scenario(target_odds=25.0)
    assert b.value_label == "妙味あり"


def test_label_high_ooana():
    """高strength × 大穴(50+) → 妙味あり。"""
    b = _build_scenario(target_odds=80.0)
    assert b.value_label == "妙味あり"


def _build_low_scenario(*, target_odds: float):
    """target combination が低strength になるシナリオ。"""
    scores = [
        _rider_score(1, win=10.0, second=10.0, third=10.0),
        _rider_score(2, win=8.0, second=8.0, third=8.0),
        _rider_score(3, win=6.0, second=6.0, third=6.0),
        _rider_score(4, win=0.1, second=0.1, third=0.1),
        _rider_score(5, win=0.5, second=0.5, third=0.5),
    ]
    high = _bet("本線", "1-2-3")
    mid = _bet("本線", "2-3-1")
    low = _bet("本線", "4-5-3")  # 低
    pred = _make_prediction(honsen=[high, mid, low])
    odds_list = [_odds("4-5-3", target_odds)]
    annotate_prediction_with_value(pred, scores, odds_list)
    # low が target
    return pred.honsen[2]


def test_label_low_cheap_miokuri():
    b = _build_low_scenario(target_odds=3.0)
    assert b.value_label == "見送り寄り"
    assert b.gami_risk >= 0.7


def test_label_low_mid_miokuri():
    b = _build_low_scenario(target_odds=10.0)
    assert b.value_label == "見送り寄り"


def test_label_low_chuana_ana_shougaku():
    b = _build_low_scenario(target_odds=25.0)
    assert b.value_label == "穴として少額"


def test_label_low_ooana_ana_shougaku():
    b = _build_low_scenario(target_odds=80.0)
    assert b.value_label == "穴として少額"


def test_label_no_odds_unknown():
    """オッズ未取得 → 「オッズ未取得・要確認」。"""
    scores = [_rider_score(c, win=1.0, second=1.0, third=1.0) for c in range(1, 6)]
    b = _bet("穴", "3-7-1")
    pred = _make_prediction(ana=[b])
    annotate_prediction_with_value(pred, scores, odds=[])
    assert pred.ana[0].value_label == "オッズ未取得・要確認"
    assert pred.ana[0].value_score == 0.0
    assert pred.ana[0].market_odds is None
    assert pred.ana[0].market_rank is None


# ---------------------------------------------------------------------------
# analyze_value（非破壊版）
# ---------------------------------------------------------------------------


def test_analyze_value_returns_list_non_destructive(sample_input):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    pred = _make_prediction(
        honsen=list(bets["本線"]),
        osae=list(bets["押さえ"]),
        ana=list(bets["穴"]),
        ooana=list(bets["大穴"]),
    )
    # 元の bet オブジェクトは label 未設定
    before_label = pred.honsen[0].value_label
    items = analyze_value(sample_input, pred, scores)
    assert items
    assert all(it.combination for it in items)
    # analyze_value は破壊的でない
    assert pred.honsen[0].value_label == before_label


# ---------------------------------------------------------------------------
# prompt_builder
# ---------------------------------------------------------------------------


def test_prompt_includes_value_analysis_section(sample_input):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    prompt = build_full_prompt(sample_input, scores, bets, value_analysis=True)
    assert "オッズ妙味分析" in prompt
    assert "妙味あり" in prompt or "本線向き" in prompt or "オッズ未取得" in prompt


def test_prompt_excludes_value_analysis_when_off(sample_input):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    prompt = build_full_prompt(sample_input, scores, bets, value_analysis=False)
    assert "オッズ妙味分析" not in prompt


def test_build_value_analysis_section_directly(sample_input):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    sec = build_value_analysis_section(sample_input, scores, bets)
    assert "オッズ妙味分析" in sec
    assert "強度" in sec
    # 厚く買う本線と少額穴を分けるよう指示
    assert "厚く買う本線" in sec or "少額" in sec


# ---------------------------------------------------------------------------
# CLI predict --value-analysis / --no-value-analysis
# ---------------------------------------------------------------------------


def test_cli_predict_value_analysis_on_shows_labels(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        [
            "--db", str(db),
            "predict",
            "--input", str(SAMPLE),
            "--no-save",
            "--no-reflections",
            "--value-analysis",
            "--provider", "mock",
        ],
    )
    assert result.exit_code == 0, result.output
    # 既知のオッズが付いた買い目があるためラベル表示が出る
    assert ("妙味あり" in result.output) or ("本線向き" in result.output) or ("堅いが安い" in result.output)


def test_cli_predict_no_value_analysis_omits_labels(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        [
            "--db", str(db),
            "predict",
            "--input", str(SAMPLE),
            "--no-save",
            "--no-reflections",
            "--no-value-analysis",
            "--provider", "mock",
        ],
    )
    assert result.exit_code == 0, result.output
    # 妙味ラベルは出ない
    for lbl in ("妙味あり", "本線向き", "堅いが安い", "穴として少額", "見送り寄り"):
        assert lbl not in result.output


def test_cli_predict_no_value_analysis_prompt_out(tmp_path: Path):
    """--no-value-analysis なら prompt にも妙味セクションが入らない。"""
    runner = CliRunner()
    db = tmp_path / "t.db"
    p_out = tmp_path / "p.txt"
    result = runner.invoke(
        cli,
        [
            "--db", str(db),
            "predict",
            "--input", str(SAMPLE),
            "--no-save",
            "--no-reflections",
            "--no-value-analysis",
            "--provider", "openai",  # 実LLMフォーマットでプロンプトを書き出させる
            "--prompt-out", str(p_out),
        ],
    )
    assert result.exit_code == 0
    body = p_out.read_text(encoding="utf-8")
    assert "オッズ妙味分析" not in body


# ---------------------------------------------------------------------------
# reports に value_label_summary
# ---------------------------------------------------------------------------


def test_reports_includes_value_label_summary(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    # 3件投入: 各ラベルの buy を持つ
    pred = Prediction(
        race_id="20260522-X-1", venue="X", race_no=1, is_girls=False,
        marks={},
        honsen=[
            BetRecommendation(category="本線", combination="5-1-3", reason="t",
                              value_label="本線向き", market_odds=8.5,
                              value_score=0.3, gami_risk=0.0),
            BetRecommendation(category="本線", combination="5-1-6", reason="t",
                              value_label="堅いが安い", market_odds=3.0,
                              value_score=-0.3, gami_risk=0.6),
        ],
        osae=[],
        ana=[
            BetRecommendation(category="穴", combination="6-5-1", reason="t",
                              value_label="妙味あり", market_odds=25.0,
                              value_score=0.7, gami_risk=0.0),
        ],
        ooana=[],
    )
    s.save_prediction(pred)
    s.save_result("20260522-X-1", "5-1-3")  # 本線向きが的中

    r = build_performance_report(s)
    vls = r["value_label_summary"]
    assert "本線向き" in vls
    assert vls["本線向き"]["total"] == 1
    assert vls["本線向き"]["hit"] == 1
    assert vls["本線向き"]["hit_rate"] == 1.0
    assert "堅いが安い" in vls
    assert vls["堅いが安い"]["total"] == 1
    assert vls["堅いが安い"]["hit"] == 0
    assert vls["妙味あり"]["total"] == 1
    assert vls["妙味あり"]["hit"] == 0
    # high_gami: 堅いが安い(5-1-6, gami_risk=0.6) は外したので 0
    assert r["high_gami_hit_count"] == 0


def test_reports_high_gami_hit_count(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    pred = Prediction(
        race_id="20260522-X-1", venue="X", race_no=1, is_girls=False,
        marks={},
        honsen=[
            BetRecommendation(category="本線", combination="5-1-3", reason="t",
                              value_label="堅いが安い", market_odds=3.0,
                              gami_risk=0.8),
        ],
        osae=[], ana=[], ooana=[],
    )
    s.save_prediction(pred)
    s.save_result("20260522-X-1", "5-1-3")
    r = build_performance_report(s)
    assert r["high_gami_hit_count"] == 1


def test_reports_text_renders_value_label_section(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    pred = Prediction(
        race_id="20260522-X-1", venue="X", race_no=1, is_girls=False,
        marks={},
        honsen=[
            BetRecommendation(category="本線", combination="5-1-3", reason="t",
                              value_label="本線向き", market_odds=8.5,
                              gami_risk=0.0),
        ],
        osae=[], ana=[], ooana=[],
    )
    s.save_prediction(pred)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--db", str(tmp_path / "t.db"), "reports"]
    )
    assert result.exit_code == 0, result.output
    assert "妙味ラベル別成績" in result.output
    assert "本線向き" in result.output


def test_reports_json_includes_value_label_summary(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--db", str(tmp_path / "t.db"), "reports", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(result.output)
    assert "value_label_summary" in raw
    assert "high_gami_hit_count" in raw
