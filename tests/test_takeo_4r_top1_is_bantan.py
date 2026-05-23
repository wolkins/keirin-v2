"""武雄4R 相当: スコア最上位が line_second (本命番手) のケース。

並び: 7-1 / 2-9 / 5-3 / 4-8 / 単騎6
top1 = 1（柏野・本命番手）
本命ライン = 7-1（2車・third 不在）
別線高スコア: 2-9 (9 も高得点番手)

仕様要件:
- 本線または押さえに 7-1-* / 1-7-* が入る
- 押さえに 9-2-* または 2-9-* が入る
- 7-1-3 が honsen[0] に固定されすぎない
- 最終結論に 9-2系または2-9系が最低1点含まれる
- 印は本命ライン構造を反映: ◎1(top1) → ◯7(本命先頭) → ▲9(別線最強)
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
    build_marks,
    compute_scores,
)
from app.value_analysis import annotate_prediction_with_value


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "takeo_4r_top1_is_bantan.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _full_scores(ri: RaceInput):
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    return scores


# ---------------------------------------------------------------------------
# 前提確認
# ---------------------------------------------------------------------------


def test_top1_is_car_1_bantan():
    """前提: 1番（line_second）がスコア最上位。"""
    ri = _load()
    scores = _full_scores(ri)
    top1 = max(scores, key=lambda s: s.total())
    assert top1.car_no == 1


# ---------------------------------------------------------------------------
# 本線・押さえへの反映（仕様要件1-5）
# ---------------------------------------------------------------------------


def test_honsen_or_osae_contains_7_1_or_1_7():
    """honsen または osae に 7-1-* または 1-7-* が入る（仕様要件1）。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    osae_combos = [b.combination for b in bets["押さえ"]]
    has_7_1 = any(c.startswith("7-1-") for c in honsen_combos + osae_combos)
    has_1_7 = any(c.startswith("1-7-") for c in honsen_combos + osae_combos)
    assert has_7_1 or has_1_7, (
        f"7-1-* / 1-7-* が無い:\n本線: {honsen_combos}\n押さえ: {osae_combos}"
    )


def test_osae_contains_2_9_or_9_2():
    """別線高スコアライン 2-9 由来の 2-9-* または 9-2-* が押さえに入る（仕様要件2）。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_combos = [b.combination for b in bets["押さえ"]]
    has_2_9 = any(c.startswith("2-9-") for c in osae_combos)
    has_9_2 = any(c.startswith("9-2-") for c in osae_combos)
    assert has_2_9 and has_9_2, (
        f"押さえに 2-9-* と 9-2-* の両方が無い:\n{osae_combos}"
    )


def test_honsen_first_is_not_arbitrary_short_line_padding():
    """本命ライン2車（third不在）の場合、本線最上位の reason が
    『本命ライン3番手を3着固定』ではなく『別線スコア上位』を使う（仕様要件3）。
    """
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert bets["本線"], "本線が空"
    first = bets["本線"][0]
    # 3番手固定ではなく「別線スコア上位」由来の reason
    assert "別線スコア上位" in first.reason or "ライン外" in first.reason, (
        f"本線最上位の reason が本命ライン3番手固定になっている: "
        f"{first.combination} / {first.reason}"
    )


def test_honsen_uses_high_score_bessen_as_3rd():
    """本線最上位の3着は別線スコア上位（9）であって、低スコアの別線車（3など）でない。
    （仕様要件3: 7-1-3 のような形を最上位に置きすぎない）
    """
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert bets["本線"], "本線が空"
    first = bets["本線"][0]
    # 7-1-3 のような3着低スコアではなく、7-1-9 のような3着高スコア
    parts = first.combination.split("-")
    if first.combination.startswith("7-1-") or first.combination.startswith("1-7-"):
        third_car = int(parts[2])
        # 3着は別線上位スコア（9 のような高得点車）
        assert third_car == 9, (
            f"本線最上位の3着が低スコア別線車になっている: "
            f"{first.combination}（3着={third_car}、期待は 9）"
        )


def test_osae_contains_user_expected_combos():
    """ユーザー指摘の期待形 9-2-1 / 2-9-1 / 9-2-7 / 2-9-7 がすべて押さえに含まれる。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_combos = set(b.combination for b in bets["押さえ"])
    for expected in ("9-2-1", "2-9-1", "9-2-7", "2-9-7"):
        assert expected in osae_combos, (
            f"押さえに {expected} が無い:\n{sorted(osae_combos)}"
        )


# ---------------------------------------------------------------------------
# 印の整合性（仕様要件4 派生）
# ---------------------------------------------------------------------------


def test_marks_reflect_main_line_structure_when_top1_is_bantan():
    """top1 が本命番手のとき、◯ は本命ライン先頭（line_leader）に。"""
    ri = _load()
    scores = _full_scores(ri)
    marks = build_marks(scores, ri)
    # ◎ = 1 (top1)
    assert marks.get("◎") == 1
    # ◯ = 7 (本命ライン先頭, ll_car)
    assert marks.get("◯") == 7, (
        f"top1 が本命番手のとき ◯ は本命先頭(7)。実際: ◯={marks.get('◯')}"
    )
    # ▲ = 9 (別線最強)
    assert marks.get("▲") == 9


# ---------------------------------------------------------------------------
# 最終結論への反映（仕様要件6）
# ---------------------------------------------------------------------------


def test_final_conclusion_includes_2_9_line():
    """最終結論 Markdown のどこかに 9-2系 または 2-9系の買い目が含まれる。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)
    md = render_prediction(prediction)
    has_2_9 = "2-9-" in md
    has_9_2 = "9-2-" in md
    assert has_2_9 or has_9_2, (
        f"最終結論に 2-9系 / 9-2系 が無い:\n{md[:3000]}"
    )


def test_summarize_includes_main_line_top_picks():
    """『一番買いたい買い目』に 7-1-* または 1-7-* が含まれる。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)
    text = _summarize_for_final(prediction)
    top_pick_section = text.split("### 押さえるべき")[0]
    has_main_line = ("7-1-" in top_pick_section) or ("1-7-" in top_pick_section)
    assert has_main_line, (
        f"一番買いたい買い目に本命ライン由来が無い:\n{top_pick_section}"
    )
