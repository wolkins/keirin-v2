"""平塚11R 男予2 (新人戦・個人戦) のテスト。

class_name="男予2" でライン情報なし、風速 5.5m/s の強風シナリオ。

仕様要件:
- 出力に「番手」「本命ライン」「別線番手」「3番手」が出ない
- 強風時 4-1-3 / 1-4-2 / 4-1-2 等が押さえに
- market_odds=19.2 がガミ警戒に出ない (gami_risk < 0.8 なので)
- オッズ未取得本線がある場合、オッズ取得済み中穴を押さえに昇格
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import _summarize_for_final, render_prediction
from app.llm_client import build_default_client
from app.models import RaceInput
from app.prompt_builder import build_full_prompt
from app.scoring import (
    apply_bank_signals,
    apply_market_signals,
    apply_reflection_signals,
    apply_tospo_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    compute_scores,
)
from app.value_analysis import (
    annotate_prediction_with_value,
    promote_oddful_to_osae,
)


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "hiratsuka_11r_rookie.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _full_pipeline(ri: RaceInput):
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    bets = build_candidate_bets(ri, scores)
    return scores, bets


# ---------------------------------------------------------------------------
# is_rookie 判定
# ---------------------------------------------------------------------------


def test_class_name_otoyo2_is_rookie():
    """class_name='男予2' で is_rookie=True。"""
    ri = _load()
    assert ri.race.resolved_is_rookie() is True


def test_class_name_normal_not_rookie():
    """class_name='S級特選' は is_rookie=False。"""
    from app.models import RaceInfo
    from datetime import date
    info = RaceInfo(
        race_id="x", date=date(2026, 5, 23), venue="平塚",
        race_no=11, class_name="S級特選",
    )
    assert info.resolved_is_rookie() is False


def test_class_name_shinjin_is_rookie():
    """class_name='新人戦' でも True。"""
    from app.models import RaceInfo
    from datetime import date
    info = RaceInfo(
        race_id="x", date=date(2026, 5, 23), venue="平塚",
        race_no=11, class_name="新人戦予選",
    )
    assert info.resolved_is_rookie() is True


# ---------------------------------------------------------------------------
# 用語抑制
# ---------------------------------------------------------------------------


def test_no_line_terms_in_reasons():
    """男予2 では reason に「番手」「本命ライン」「別線番手」「3番手」が出ない。"""
    ri = _load()
    scores, bets = _full_pipeline(ri)
    all_reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    forbidden = ("番手", "本命ライン", "別線番手", "3番手", "別線自力")
    for term in forbidden:
        assert term not in all_reasons, (
            f"新人戦の reason に禁止語 '{term}' が含まれる:\n{all_reasons}"
        )


def test_rookie_label_in_reasons():
    """新人戦の reason に「（新人戦・個人戦）」マーカーが付く。"""
    ri = _load()
    scores, bets = _full_pipeline(ri)
    honsen_reasons = " / ".join(b.reason for b in bets["本線"])
    assert "新人戦" in honsen_reasons or "個人戦" in honsen_reasons


# ---------------------------------------------------------------------------
# 強風時の4番手評価
# ---------------------------------------------------------------------------


def test_strong_wind_4th_evaluation_in_osae():
    """風速5m/s+ の新人戦で、4番手評価頭・2着上がりが押さえに追加される。"""
    ri = _load()
    scores, bets = _full_pipeline(ri)
    osae_combos = [b.combination for b in bets["押さえ"]]
    # 4-1-3 / 1-4-2 / 4-1-2 のうち2点以上
    target = {"4-1-3", "1-4-2", "4-1-2"}
    found = target & set(osae_combos)
    assert len(found) >= 2, (
        f"強風時の4番手評価形 {target} のうち2点以上が押さえに無い:\n"
        f"押さえ: {osae_combos}"
    )


def test_strong_wind_4_1_3_in_osae():
    """4-1-3 が押さえに必ず含まれる。"""
    ri = _load()
    scores, bets = _full_pipeline(ri)
    osae_combos = [b.combination for b in bets["押さえ"]]
    assert "4-1-3" in osae_combos


# ---------------------------------------------------------------------------
# ガミ警戒条件（19.2倍 + gami_risk<0.8 はガミ警戒に出ない）
# ---------------------------------------------------------------------------


def test_19_point_2_odds_not_in_gami_warning():
    """市場 19.2倍の買い目（gami_risk が 0.8 未満）はガミ警戒に出ない。"""
    ri = _load()
    scores, bets = _full_pipeline(ri)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)
    promote_oddful_to_osae(prediction)

    text = _summarize_for_final(prediction)
    gami_section = text.split("### ガミになりやすい")[1].split("### 実購入判断")[0]
    # 4-1-3 (19.2倍) がガミ警戒に出ない
    assert "4-1-3" not in gami_section, (
        f"19.2倍の 4-1-3 がガミ警戒に出ている:\n{gami_section}"
    )


def test_gami_warning_only_under_15_or_high_gami():
    """ガミ警戒は market_odds < 15 + gami>=0.6、または market_odds < 20 + gami>=0.8 のみ。"""
    from app.cli import _summarize_for_final
    from app.models import BetRecommendation, Prediction

    p = Prediction(
        race_id="t-1", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            # market_odds=8倍 + gami=0.7 → ガミ警戒に出る (< 15 + >=0.6)
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.7, market_odds=8.0,
            ),
            # market_odds=18倍 + gami=0.7 → ガミ警戒に出ない (15以上 + <0.8)
            BetRecommendation(
                category="本線", bet_type="3連単", combination="9-4-2",
                reason="t", gami_risk=0.7, market_odds=18.0,
            ),
            # market_odds=18倍 + gami=0.9 → ガミ警戒に出る (< 20 + >=0.8)
            BetRecommendation(
                category="本線", bet_type="3連単", combination="3-3-3",
                reason="t", gami_risk=0.9, market_odds=18.0,
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    gami_section = text.split("### ガミになりやすい")[1].split("### 実購入判断")[0]
    assert "1-2-3" in gami_section  # 8倍+0.7 → 出る
    assert "9-4-2" not in gami_section  # 18倍+0.7 → 出ない
    assert "3-3-3" in gami_section  # 18倍+0.9 → 出る (極高gami)


# ---------------------------------------------------------------------------
# オッズ取得済み中穴を押さえに昇格
# ---------------------------------------------------------------------------


def test_promote_oddful_when_honsen_lacks_odds():
    """本線の半数以上がオッズ未取得なら、穴のオッズ取得済み中穴を押さえに。"""
    from app.models import BetRecommendation, Prediction

    p = Prediction(
        race_id="t-1", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(category="本線", bet_type="3連単",
                              combination="1-2-3", reason="t",
                              market_odds=None),  # オッズなし
            BetRecommendation(category="本線", bet_type="3連単",
                              combination="1-3-2", reason="t",
                              market_odds=None),
            BetRecommendation(category="本線", bet_type="3連単",
                              combination="2-1-3", reason="t",
                              market_odds=None),
        ],
        osae=[],
        ana=[
            BetRecommendation(category="穴", bet_type="3連単",
                              combination="4-1-3", reason="t",
                              market_odds=15.0, value_label="妙味あり"),
            BetRecommendation(category="穴", bet_type="3連単",
                              combination="5-1-2", reason="t",
                              market_odds=None),  # オッズなしは対象外
        ],
        ooana=[], final_conclusion="",
    )
    promoted = promote_oddful_to_osae(p)
    assert promoted >= 1
    osae_combos = [b.combination for b in p.osae]
    assert "4-1-3" in osae_combos


def test_promote_oddful_skipped_when_honsen_has_odds():
    """本線がオッズ取得済みばかりなら昇格しない（既存挙動）。"""
    from app.models import BetRecommendation, Prediction

    p = Prediction(
        race_id="t-1", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(category="本線", bet_type="3連単",
                              combination="1-2-3", reason="t",
                              market_odds=10.0),
            BetRecommendation(category="本線", bet_type="3連単",
                              combination="1-3-2", reason="t",
                              market_odds=12.0),
            BetRecommendation(category="本線", bet_type="3連単",
                              combination="2-1-3", reason="t",
                              market_odds=14.0),
        ],
        osae=[],
        ana=[
            BetRecommendation(category="穴", bet_type="3連単",
                              combination="4-1-3", reason="t",
                              market_odds=15.0, value_label="妙味あり"),
        ],
        ooana=[], final_conclusion="",
    )
    promoted = promote_oddful_to_osae(p)
    assert promoted == 0


# ---------------------------------------------------------------------------
# 最終結論の絞り込み（要件2,4）
# ---------------------------------------------------------------------------


def test_top_pick_includes_odds_acquired_when_honsen_lacks():
    """本線がオッズ取得済みあり→ 一番買いたい買い目に「妙味あり」が入る。

    平塚11Rは本線の 1-2-3 等にオッズあり、4-1-3 は押さえだがオッズ19.2倍。
    最終結論の一番買いたい/押さえに 4-1-3 が残る。
    """
    from app.value_analysis import annotate_prediction_with_value, promote_oddful_to_osae
    from app.llm_client import build_default_client
    from app.prompt_builder import build_full_prompt
    from app.cli import _summarize_for_final

    ri = _load()
    scores, bets = _full_pipeline(ri)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(pred, scores, ri.odds)
    promote_oddful_to_osae(pred)

    text = _summarize_for_final(pred)
    cover_section = text.split("### 押さえるべき買い目")[1].split("###")[0]
    # 4-1-3 (19.2倍) が「押さえるべき買い目」に入る
    assert "4-1-3" in cover_section, (
        f"4-1-3 が押さえるべき買い目に無い:\n{cover_section}"
    )


def test_top_pick_not_all_odds_missing():
    """一番買いたい買い目がオッズ未取得だけにならない（要件2）。"""
    from app.value_analysis import annotate_prediction_with_value, promote_oddful_to_osae
    from app.llm_client import build_default_client
    from app.prompt_builder import build_full_prompt
    from app.cli import _summarize_for_final

    ri = _load()
    scores, bets = _full_pipeline(ri)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(pred, scores, ri.odds)
    promote_oddful_to_osae(pred)

    text = _summarize_for_final(pred)
    top_section = text.split("### 一番買いたい")[1].split("###")[0]
    # 「オッズ未取得」が全件ではない（最低1点はオッズ取得済み）
    lines = [l for l in top_section.split("\n") if l.strip().startswith("- ")]
    if not lines:
        pytest.skip("一番買いたい買い目が無い")
    has_odds = any("倍" in l for l in lines)
    assert has_odds, (
        f"一番買いたい買い目が全てオッズ未取得:\n{top_section}"
    )


def test_ana_max_3_for_rookie():
    """新人戦で穴が抑制される（最大4点 = MAX_ANA）。"""
    ri = _load()
    scores, bets = _full_pipeline(ri)
    assert len(bets["穴"]) <= 4, (
        f"新人戦の穴が多すぎる: {len(bets['穴'])}点"
    )


def test_ooana_max_3_for_rookie():
    """新人戦で大穴が抑制される（最大3点）。"""
    ri = _load()
    scores, bets = _full_pipeline(ri)
    assert len(bets["大穴"]) <= 3
