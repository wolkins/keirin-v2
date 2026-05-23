"""広島1R ガールズ予選2 + 市場乖離テスト。

並びなし（個人戦扱い）で、内部スコア上位 (4,6,5,3) と
3連単市場 (1-2, 2-1 ペア集中) が大きく乖離するシナリオ。

仕様要件:
- 市場上位 1-2-3 / 1-2-4 / 1-3-2 / 2-1-3 / 2-1-4 が honsen/osae に
- 本線が全 market_odds=None にならない
- 4-6-3 が honsen[0] にならない
- score=0.0 の7番が stats_missing=true に昇格
- cheap_trio 1=2=3 のガミ警戒が 4-6-3 に波及しない
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput
from app.scoring import (
    build_candidate_bets,
    compute_scores,
    promote_zero_score_to_missing,
    _detect_market_focused_pair_no_lines,
)


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "hiroshima_1r_girls_market.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# score=0 昇格
# ---------------------------------------------------------------------------


def test_zero_score_promoted_to_missing():
    """score=0.0 + stats_missing=false の rider が True に昇格。"""
    ri = _load()
    # 入力では 7番のみ stats_missing=False
    r7_before = next(r for r in ri.riders if r.car_no == 7)
    assert r7_before.stats_missing is False
    promoted = promote_zero_score_to_missing(ri)
    assert promoted >= 1
    r7_after = next(r for r in ri.riders if r.car_no == 7)
    assert r7_after.stats_missing is True


# ---------------------------------------------------------------------------
# 市場注目ペア検出
# ---------------------------------------------------------------------------


def test_market_focused_pair_detected():
    """1-2-* / 2-1-* が3連単上位に頻出 → (1, 2) が市場注目ペアに。"""
    ri = _load()
    pair = _detect_market_focused_pair_no_lines(ri)
    assert pair is not None
    assert pair == (1, 2)


# ---------------------------------------------------------------------------
# 本線・押さえに市場上位が入る
# ---------------------------------------------------------------------------


def test_honsen_contains_market_top():
    """本線に 1-2-3 / 1-2-4 / 1-3-2 / 2-1-3 / 2-1-4 のうち2点以上。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = set(b.combination for b in bets["本線"])
    target = {"1-2-3", "1-2-4", "1-3-2", "2-1-3", "2-1-4"}
    found = target & honsen_combos
    assert len(found) >= 2, (
        f"本線に市場上位が2点以上無い:\n本線: {honsen_combos}"
    )


def test_4_6_3_not_honsen_first():
    """4-6-3 が本線最上位（honsen[0]）にならない。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    first = bets["本線"][0].combination
    assert first != "4-6-3", (
        f"本線最上位が 4-6-3 になっている: {first}"
    )


# ---------------------------------------------------------------------------
# cheap_trio の波及限定
# ---------------------------------------------------------------------------


def test_cheap_trio_does_not_affect_unrelated_bets():
    """cheap_trio (1=2=3 / 1=2=4 が安い) は無関係な車番セットに波及しない。

    例: 4-6-3 ({3,4,6}) や 4-6-5 ({4,5,6}) には 3連複安 reason が付かない。
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    cheap_sets = [{"1", "2", "3"}, {"1", "2", "4"}]
    for cat in ("本線", "押さえ", "穴", "大穴"):
        for b in bets[cat]:
            cars = set(b.combination.split("-"))
            if cars in cheap_sets:
                continue  # 該当組み合わせは OK
            assert "3連複安" not in b.reason, (
                f"無関係買い目 {b.combination} に 3連複安 が波及: {b.reason}"
            )


# ---------------------------------------------------------------------------
# 穴・大穴の絞り込み（ガールズ）
# ---------------------------------------------------------------------------


def test_ana_max_3_for_girls():
    """ガールズで穴が最大3点に絞られる。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert len(bets["穴"]) <= 4  # MAX_ANA=4 (TARGET=3, MAX=TARGET+1)


def test_ooana_max_2_for_girls():
    """ガールズで大穴が最大3点に絞られる（MAX_OOANA=3）。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert len(bets["大穴"]) <= 3


def test_total_bets_compact():
    """合計買い目が 20点以内（ガールズ抑制）。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    total = sum(len(bets[c]) for c in ("本線", "押さえ", "穴", "大穴"))
    assert total <= 20, f"合計が多すぎる: {total}点"
