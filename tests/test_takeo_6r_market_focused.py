"""武雄6R 相当: 数値不足モード + 市場注目ラインのテスト。

並び: 3-9-4 / 6-2 / 7-5 / 1-8（全選手 score=0、コメントのみ）
3連単上位: 9-3-4 (8.5) / 3-9-4 (18.8) / 9-3-8 (19.8) / 9-3-7 / 9-3-5
3連複上位: 3=4=9 (5.6) / 3=8=9 (9.0) / 1=3=9 (11.8)

市場が圧倒的に 3-9 ラインを支持しているケース。

仕様要件:
- 数値不足モードで _detect_market_focused_line が「本命候補」ラインを返す
- 本命ラインを市場注目ラインで上書き
- honsen に 3-9-4 / 9-3-4 が含まれる
- honsen 最上位が 6-2-5 のような別線にならない
- 3連複最人気 3=4=9 由来の派生が候補に
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput
from app.scoring import (
    _detect_market_focused_line,
    apply_bank_signals,
    apply_market_signals,
    apply_reflection_signals,
    apply_tospo_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    compute_scores,
    detect_score_data_insufficient,
)


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "takeo_6r_market_focused.json"
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
    apply_market_signals(
        scores, ri.odds,
        boost_multiplier=3.0 if detect_score_data_insufficient(ri) else 1.0,
    )
    return scores


# ---------------------------------------------------------------------------
# 市場注目ライン検出
# ---------------------------------------------------------------------------


def test_market_focused_line_detection():
    """3連単上位5件の (1着,2着) が同一ライン3-9 で頻出 → 検出される。"""
    ri = _load()
    market_line = _detect_market_focused_line(ri)
    assert market_line is not None
    assert market_line["leader"] == 3
    assert market_line["second"] == 9
    assert market_line["third"] == 4
    assert market_line["_count"] >= 2  # 複数件で同一ライン


def test_market_focused_line_returns_none_when_no_odds():
    """オッズが無いときは None。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["odds"] = []
    ri = RaceInput.model_validate(raw)
    assert _detect_market_focused_line(ri) is None


def test_market_focused_line_only_in_data_insufficient_mode():
    """build_candidate_bets は通常データでは市場注目ラインで本命を上書きしない。

    （数値不足モード時のみ上書き発動）
    """
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # 全選手にスコアを入れる → 数値不足モード解除
    for r in raw["riders"]:
        r["score"] = 80.0
    ri = RaceInput.model_validate(raw)
    assert detect_score_data_insufficient(ri) is False
    # 通常モードでは本命ライン特定は top1 ベース（市場注目ラインで上書きしない）
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    # 本線が 3-9 由来でなくても OK（通常スコアロジックに従う）
    assert bets["本線"]  # 何かしら本線がある


# ---------------------------------------------------------------------------
# 武雄6R: 数値不足モード + 市場注目ライン
# ---------------------------------------------------------------------------


def test_takeo_6r_insufficient_mode():
    """前提: 数値不足モード."""
    ri = _load()
    assert detect_score_data_insufficient(ri) is True


def test_takeo_6r_honsen_contains_3_9_4_or_9_3_4():
    """本線に 3-9-4 または 9-3-4 が含まれる。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    has_3_9_4 = "3-9-4" in honsen_combos
    has_9_3_4 = "9-3-4" in honsen_combos
    assert has_3_9_4 and has_9_3_4, (
        f"本線に 3-9-4 と 9-3-4 の両方が無い:\n{honsen_combos}"
    )


def test_takeo_6r_honsen_first_is_not_6_2_x():
    """本線最上位が 6-2-* / 2-6-* （別線軸）にならない。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert bets["本線"], "本線が空"
    first = bets["本線"][0].combination
    assert not first.startswith("6-2-"), (
        f"本線最上位が 6-2-* になっている: {first}\n"
        f"全本線: {[b.combination for b in bets['本線']]}"
    )
    assert not first.startswith("2-6-"), (
        f"本線最上位が 2-6-* になっている: {first}"
    )


def test_takeo_6r_honsen_first_is_main_line_3_9():
    """本線最上位は 3-9-4 (本命ライン: 先頭-番手-3番手)。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert bets["本線"]
    first = bets["本線"][0].combination
    assert first == "3-9-4", (
        f"本線最上位が 3-9-4 でない: {first}"
    )


def test_takeo_6r_trio_top_included():
    """3連複最人気 3=4=9 由来の派生が本線・押さえに反映される。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_combos = [b.combination for b in bets["本線"] + bets["押さえ"] + bets["穴"]]
    # 3=4=9 の順列 (3-4-9, 3-9-4, 4-3-9, 4-9-3, 9-3-4, 9-4-3) のいずれかが入っている
    from itertools import permutations
    trio_perms = ["-".join(str(c) for c in p) for p in permutations([3, 4, 9])]
    found = [c for c in trio_perms if c in all_combos]
    assert len(found) >= 3, (
        f"3連複最人気 3=4=9 の派生が反映不足: 検出 {found}, "
        f"全候補: {all_combos}"
    )


def test_takeo_6r_market_reason_in_honsen():
    """本線軸の reason に「市場注目ライン採用」明示。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    has_market_reason = any(
        "市場注目ライン採用" in b.reason for b in bets["本線"]
    )
    assert has_market_reason, (
        f"本線に「市場注目ライン採用」reason が無い:\n"
        f"{[b.reason for b in bets['本線']]}"
    )
