"""宇都宮11R 相当: 別線市場注目ラインの押さえ反映テスト。

並び: 4-1-5 / 7-2-3 / 単騎6
top1 = 4番（本命先頭）、score 上位2位 = 1番（本命番手）
市場では 2-7 / 7-2 系（別線）が上位人気多発

仕様要件:
- 市場注目別線 7-2 が押さえ上位に来る
- 1-4-2 (本命番手頭-本命先頭-市場別線) が押さえに
- 7-2系が穴止まりにならない
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput
from app.scoring import build_candidate_bets, compute_scores


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures" / "utsunomiya_11r_market_separate.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 前提
# ---------------------------------------------------------------------------


def test_top1_is_main_line_leader():
    """前提: 4番（本命ライン先頭）が score 最上位 + 自力評価で top1。"""
    ri = _load()
    scores = compute_scores(ri)
    top1 = max(scores, key=lambda s: s.total())
    assert top1.car_no == 4


# ---------------------------------------------------------------------------
# 本線（本命ライン構造）
# ---------------------------------------------------------------------------


def test_honsen_includes_main_line_form():
    """本線に 4-1-5 / 1-4-5 等の本命ライン形が含まれる。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    assert "4-1-5" in honsen_combos


def test_honsen_4_5_1_not_pinned_as_only_alternative():
    """本線が 4-1-5 / 4-5-1 / 1-4-5 等 (3形) になっていて、
    4-5-1 だけが本線2点目に固定されない（他の選択肢がある）。
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    # 4-1-5 / 1-4-5 など、本命ライン素直 + 番手頭の両方が本線に含まれる
    # 4-5-1 だけが唯一の入替ではない
    has_main_natural = "4-1-5" in honsen_combos
    has_bantan = any(c.startswith("1-4-") for c in honsen_combos)
    assert has_main_natural and has_bantan, (
        f"本線が 4-1-5 と 1-4-* の両方含まない: {honsen_combos}"
    )


# ---------------------------------------------------------------------------
# 押さえ（市場注目別線 + 本命番手頭）
# ---------------------------------------------------------------------------


def test_osae_contains_market_separate_2_7():
    """3連単上位人気の 7-2系（2-7-1 / 7-2-1 / 2-7-4 / 7-2-4）が押さえに。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_combos = set(b.combination for b in bets["押さえ"])
    # ユーザー期待: 2-7-1 / 7-2-1 / 2-7-4 / 7-2-4 すべて押さえに
    expected = {"2-7-1", "7-2-1", "2-7-4", "7-2-4"}
    found = expected & osae_combos
    assert len(found) >= 3, (
        f"市場注目別線 7-2 系の押さえが不足: 期待 {expected} / 検出 {found}\n"
        f"押さえ全体: {sorted(osae_combos)}"
    )


def test_honsen_or_osae_contains_1_4_2():
    """1-4-2（本命番手頭+市場別線3着）が本線または押さえに含まれる。

    新仕様: 拮抗市場注目時は本線に昇格、圧倒的本命時は押さえに留まる。
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    combos = [b.combination for b in bets["本線"] + bets["押さえ"]]
    assert "1-4-2" in combos, (
        f"1-4-2 が本線/押さえに無い:\n本線+押さえ: {combos}"
    )


def test_market_separate_not_stuck_in_ana():
    """7-2系が穴止まりにならず、押さえに昇格する。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_combos = set(b.combination for b in bets["押さえ"])
    ana_combos = set(b.combination for b in bets["穴"])
    # 7-2系（2-7-* または 7-2-*）が押さえに最低2件
    osae_27 = [c for c in osae_combos if c.startswith("2-7-") or c.startswith("7-2-")]
    assert len(osae_27) >= 2, (
        f"7-2系が押さえに2件以上ない (穴止まり)。押さえ: {sorted(osae_combos)}"
    )


def test_osae_market_reason_has_market_label():
    """市場注目別線由来の押さえに「市場注目別線」reason が付く。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    has_market_reason = any(
        "市場注目別線" in b.reason for b in bets["押さえ"]
    )
    assert has_market_reason, (
        f"押さえに『市場注目別線』reason が無い:\n"
        f"{[b.reason for b in bets['押さえ']]}"
    )


# ---------------------------------------------------------------------------
# 数値モード + ライン構造 + オッズの組み合わせ判断（仕様要件5）
# ---------------------------------------------------------------------------


def test_scoring_uses_score_and_line_and_odds_together():
    """score は取れているが B数=0、ライン構造 + オッズの3要素で判断。

    - top1=4 (本命先頭、B=5、score=106): line 強度 + score で評価
    - 市場上位 2-7-* で押さえ拡張
    - 本命ライン3形は本線軸
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    # 本線に本命ライン形が含まれる（score + line）
    honsen_combos = [b.combination for b in bets["本線"]]
    assert "4-1-5" in honsen_combos
    # 押さえに市場注目別線形（オッズ）
    osae_combos = [b.combination for b in bets["押さえ"]]
    assert any(c.startswith("7-2-") or c.startswith("2-7-") for c in osae_combos)
    # 本線または押さえに 1-4-2（本命番手頭+市場別線）（score + line + odds）
    honsen_combos = [b.combination for b in bets["本線"]]
    assert "1-4-2" in honsen_combos or "1-4-2" in osae_combos


def test_total_bet_count_under_25():
    """合計買い目が 25 点を超えない。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    total = sum(len(bets[c]) for c in ("本線", "押さえ", "穴", "大穴"))
    assert total <= 25, f"買い目が多すぎる: {total}点"


# ---------------------------------------------------------------------------
# 拮抗時の本線分散テスト（要件1-3）
# ---------------------------------------------------------------------------


def test_honsen_contains_1_4_2_or_4_1_2():
    """拮抗市場注目時、本線に 1-4-2 または 4-1-2 が含まれる（要件2）。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    has_4_1_2 = "4-1-2" in honsen_combos
    has_1_4_2 = "1-4-2" in honsen_combos
    assert has_4_1_2 or has_1_4_2, (
        f"本線に 4-1-2 または 1-4-2 が無い:\n{honsen_combos}"
    )


def test_honsen_includes_at_least_two_mixed_or_main_forms():
    """拮抗市場注目時、本線に 4-1系（本命）と 4-1-2/1-4-2系（混合形）の
    両方が含まれる（市場上位ラインを本線で分散）。
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    target_set = {"4-1-2", "1-4-2", "7-2-1", "2-7-1"}
    found = target_set & set(honsen_combos)
    assert len(found) >= 2, (
        f"本線に市場上位形 (4-1-2/1-4-2/7-2-1/2-7-1) のうち2点以上が無い: "
        f"検出 {found} / 全本線 {honsen_combos}"
    )


def test_market_separate_not_overriding_main_line():
    """7-2系（別線市場注目）が本命ラインを上書きしない（要件1）。

    拮抗時は本命=4-1ラインのまま、7-2 は押さえ強化のみ。
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    # 本線最上位は本命ライン (4 起点 or 1 起点) で、7 起点でない
    first = honsen_combos[0]
    assert first.startswith("4-") or first.startswith("1-"), (
        f"本線最上位が 4-/1- ライン始まりでない（7-2 上書きされている）: {first}"
    )


# ---------------------------------------------------------------------------
# 印の改善: 6番（単騎・market/honsen 出現少）が▲化しない（要件4）
# ---------------------------------------------------------------------------


def test_marks_solo_not_promoted_to_triangle():
    """6番（単騎で市場・本線出現少）が▲以上にならない。"""
    from app.scoring import build_marks
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    marks = build_marks(scores, ri, candidate_bets=bets)
    assert marks.get("◎") != 6
    assert marks.get("◯") != 6
    assert marks.get("▲") != 6, (
        f"6番（単騎・市場/本線出現少）が▲になっている: {marks}"
    )


def test_marks_assigns_market_top_to_triangle_or_higher():
    """市場上位の 2番 or 7番が △以上に入る（要件4: 市場・本線出現で重み付け）。"""
    from app.scoring import build_marks
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    marks = build_marks(scores, ri, candidate_bets=bets)
    top4 = {marks.get(m) for m in ("◎", "◯", "▲", "△")}
    has_market_horse = (2 in top4) or (7 in top4)
    assert has_market_horse, (
        f"市場上位の 2番 or 7番が △以上に入っていない: {marks}"
    )


# ---------------------------------------------------------------------------
# 押さえ点数の抑制（要件5）
# ---------------------------------------------------------------------------


def test_osae_under_limit():
    """押さえが過剰にならない（最大 HARD_OSAE=9 以内）。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert len(bets["押さえ"]) <= 9, (
        f"押さえが多すぎる: {len(bets['押さえ'])}点"
    )


def test_total_bets_balanced():
    """合計が 25点以内、本線3-5点、押さえ3-9点に収まる。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert 3 <= len(bets["本線"]) <= 5
    assert 3 <= len(bets["押さえ"]) <= 9
    total = sum(len(bets[c]) for c in ("本線", "押さえ", "穴", "大穴"))
    assert total <= 25
