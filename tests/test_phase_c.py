"""フェーズC: ガールズ専用候補 + 最終結論4区分 のテスト。

仕様10章「ガールズ競輪ルール」+ 16章「最終的には常に出す」をカバー。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import _summarize_for_final, cli, render_prediction
from app.models import (
    BetRecommendation,
    Prediction,
    RaceInput,
    RiderScore,
)
from app.scoring import build_candidate_bets, compute_scores


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


# ---------------------------------------------------------------------------
# ガールズ専用候補
# ---------------------------------------------------------------------------


def _girls_input() -> RaceInput:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"]["class_name"] = "ガールズ"
    raw["race"]["is_girls"] = True
    raw["lines"] = []
    return RaceInput.model_validate(raw)


def test_girls_adds_specific_forms():
    """ガールズで本命-対抗-中穴系の reason が含まれる。

    新仕様: ガールズ本線が3点に絞られ、市場注目ペアが優先される。
    そのため reason の全てが必須ではなく、ガールズ専用 reason が
    1つ以上含まれることをチェック。
    """
    ri = _girls_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = []
    for cat in ("本線", "押さえ", "穴", "大穴"):
        for b in bets[cat]:
            reasons.append(b.reason)
    joined = " / ".join(reasons)
    assert "ガールズ" in joined
    # ガールズ専用 reason のいずれかが出る（本線3点制限で全部は出なくなった）
    girls_reasons = [
        "本命頭-中穴2着-対抗3着",
        "対抗頭-本命-3位",
        "本命-対抗-中穴",
        "本命-3位-対抗",
    ]
    matched = [r for r in girls_reasons if r in joined]
    assert len(matched) >= 2, (
        f"ガールズ専用 reason が2つ以上見つからない: {matched}"
    )


def test_girls_does_not_use_line_logic():
    """ガールズで「別線番手」「強風補正」などライン系の reason が出ない。"""
    ri = _girls_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    # 強風補正/雨補正/直近トレンドはガールズで適用されない
    assert "強風補正" not in reasons
    assert "雨補正" not in reasons


def test_girls_includes_5th_place_head_in_ooana():
    """5位車の頭が大穴候補に入る。"""
    ri = _girls_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    ooana_reasons = " / ".join(b.reason for b in bets["大穴"])
    assert "5位頭の大波乱" in ooana_reasons


def test_non_girls_does_not_get_girls_reasons():
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    assert "ガールズ:" not in reasons


# ---------------------------------------------------------------------------
# 最終結論 4区分
# ---------------------------------------------------------------------------


def _bet(category: str, combo: str, *, gami: float = 0.0, value_label: str = None,
         market_odds: float = None) -> BetRecommendation:
    return BetRecommendation(
        category=category,  # type: ignore[arg-type]
        bet_type="3連単",
        combination=combo,
        reason="test",
        gami_risk=gami,
        value_label=value_label,
        market_odds=market_odds,
    )


def _make_pred(**buckets) -> Prediction:
    return Prediction(
        race_id="20260522-X-1", venue="X", race_no=1, is_girls=False,
        marks={},
        honsen=buckets.get("honsen", []),
        osae=buckets.get("osae", []),
        ana=buckets.get("ana", []),
        ooana=buckets.get("ooana", []),
        final_conclusion="",
    )


def test_summarize_includes_top_picks():
    # market_odds 付き → 「一番買いたい買い目」セクション
    # market_odds 無し → 「オッズ確認後に判断する本線候補」セクション
    pred = _make_pred(
        honsen=[
            _bet("本線", "1-2-3", market_odds=12.0),
            _bet("本線", "1-3-2", market_odds=18.0),
            _bet("本線", "1-2-4", market_odds=22.0),
        ],
    )
    text = _summarize_for_final(pred)
    assert "### 一番買いたい買い目" in text
    # 本線の上位2点が含まれる
    assert "1-2-3" in text
    assert "1-3-2" in text
    # 3点目は「一番買いたい」には含まれない
    top_section = text.split("### 押さえるべき")[0]
    assert "1-2-4" not in top_section


def test_summarize_includes_cover_picks():
    pred = _make_pred(
        osae=[_bet("押さえ", "2-1-3"), _bet("押さえ", "1-4-2")],
    )
    text = _summarize_for_final(pred)
    assert "### 押さえるべき買い目" in text
    assert "2-1-3" in text
    assert "1-4-2" in text


def test_summarize_extracts_small_longshot():
    """少額穴は最大2点まで抽出（新仕様: 実購入候補として絞る）。"""
    pred = _make_pred(
        ana=[
            _bet("穴", "6-1-5", value_label="妙味あり", market_odds=25.0),
            _bet("穴", "3-1-2", value_label="オッズ未取得・要確認"),
            _bet("穴", "4-1-3", value_label="穴として少額", market_odds=80.0),
        ],
        ooana=[_bet("大穴", "7-1-2", value_label="妙味あり", market_odds=110.0)],
    )
    text = _summarize_for_final(pred)
    longshot_section = text.split("### ガミ")[0].split("### 少額で足す穴")[1]
    # 妙味あり/穴として少額 から最大2点（順番上、穴が優先）
    assert "6-1-5" in longshot_section  # 妙味あり（最初）
    assert "4-1-3" in longshot_section  # 穴として少額（2番目）
    # オッズ未取得・要確認は除外される
    assert "3-1-2" not in longshot_section
    # 最大2点なので 7-1-2 (大穴の3番目) は入らない
    assert "7-1-2" not in longshot_section


def test_summarize_gami_warning_collects_high_risk():
    """ガミ警戒セクション: market_odds 取得済み + gami_risk>=0.6 のみ列挙。

    新仕様で market_odds=None は除外される（実オッズが不明な買い目を
    "ガミになりやすい" と決めつけない）。
    """
    pred = _make_pred(
        honsen=[_bet("本線", "1-2-3", gami=0.8, value_label="堅いが安い", market_odds=2.5)],
        osae=[_bet("押さえ", "2-1-3", gami=0.3, market_odds=10.0)],  # 閾値未満
        ana=[_bet("穴", "6-1-2", gami=0.7, market_odds=5.0)],  # 高リスク+オッズあり
        ooana=[_bet("大穴", "9-1-2", gami=0.7, market_odds=None)],  # オッズ未取得
    )
    text = _summarize_for_final(pred)
    gami_section = text.split("### ガミになりやすい")[1].split("### 実購入判断")[0]
    assert "1-2-3" in gami_section
    assert "6-1-2" in gami_section
    assert "2-1-3" not in gami_section  # 閾値未満
    assert "9-1-2" not in gami_section  # オッズ未取得は除外


def test_summarize_handles_empty_buckets():
    """すべて空でも例外を出さず、フォールバック文言を出力。"""
    pred = _make_pred()
    text = _summarize_for_final(pred)
    assert "該当なし" in text
    assert "妙味のある穴は検出されませんでした" in text
    assert "ガミリスク高の買い目は検出されませんでした" in text


def test_render_prediction_includes_4_sections(tmp_path: Path):
    """predict 全体で最終結論4区分が表示される (v1 legacy renderer の出力フォーマット検証)。

    2026-05-24 v2 デフォルト化以降は v2 がメインだが、本テストは v1 legacy
    の旧 4 区分セクション名を担保するため明示的に --renderer v1 で実行する。
    v2 の整合性は test_output_plan_integrity.py / test_renderer_selector.py
    で別途担保される。
    """
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        ["--db", str(db), "predict", "--input", str(SAMPLE),
         "--no-save", "--no-reflections", "--provider", "mock",
         "--renderer", "v1"],
    )
    assert result.exit_code == 0, result.output
    for heading in (
        "### 一番買いたい買い目",
        "### 押さえるべき買い目",
        "### 少額で足す穴",
        "### ガミになりやすい買い目",
    ):
        assert heading in result.output
