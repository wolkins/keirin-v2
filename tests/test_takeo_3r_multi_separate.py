"""武雄3R 相当: 別線ライン複数本対応 + ガミ警戒条件のテスト。

仕様要件:
- 直近結果に「本線先頭-番手-別線番手」「本命先頭-別線自力-別線番手」がある場合、
  別線ライン上位2本に対して必須形を push する
- 並び 1-7 / 2-9-4 / 3-5 / 6-8 で、ll=2 の場合:
    2-9-5 / 2-3-5 / 2-6-8 / 3-5-2 が候補に入る
- ガミ警戒に market_odds>=20 / market_odds=None の買い目を出さない
- cheap_trio が ana/ooana の gami_risk を一律 0.6 にしない
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput
from app.scoring import build_candidate_bets, compute_scores


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "takeo_3r_multi_separate.json"


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 武雄3R fixture: 別線ライン複数本のトレンド反映
# ---------------------------------------------------------------------------


def test_takeo_3r_includes_main_line_natural_bets():
    """本線が 2-9-4 / 2-4-9 / 9-2-4 になっている。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    assert "2-9-4" in honsen_combos
    assert "9-2-4" in honsen_combos


def test_takeo_3r_includes_2_9_5_in_osae():
    """直近結果トレンド → 2-9-5（本線先頭-番手-別線番手）が押さえに含まれる。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_combos = [b.combination for b in bets["押さえ"]]
    assert "2-9-5" in osae_combos, (
        f"押さえに 2-9-5 が無い:\n{osae_combos}"
    )


def test_takeo_3r_includes_2_3_5_in_osae():
    """直近結果トレンド → 2-3-5（本命先頭-別線自力-別線番手）が押さえに含まれる。

    別線1本目 (3-5) 由来。
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_combos = [b.combination for b in bets["押さえ"]]
    assert "2-3-5" in osae_combos, (
        f"押さえに 2-3-5 が無い（別線1本目 3-5 の連発が反映されていない）:\n"
        f"{osae_combos}"
    )


def test_takeo_3r_includes_2_6_8_in_osae():
    """直近結果トレンド → 2-6-8（本命先頭-別線自力-別線番手）が押さえに含まれる。

    別線2本目 (6-8) 由来。複数別線ライン対応を確認。
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_combos = [b.combination for b in bets["押さえ"]]
    assert "2-6-8" in osae_combos, (
        f"押さえに 2-6-8 が無い（別線2本目 6-8 が反映されていない）:\n"
        f"{osae_combos}"
    )


def test_takeo_3r_includes_3_5_2_in_ana():
    """直近結果トレンド → 3-5-2（別線自力頭-別線番手-本命の波乱形）が穴に含まれる。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    ana_combos = [b.combination for b in bets["穴"]]
    assert "3-5-2" in ana_combos, (
        f"穴に 3-5-2 が無い:\n{ana_combos}"
    )


def test_takeo_3r_includes_6_8_2_in_ana():
    """別線2本目 (6-8) の波乱形 6-8-2 も穴に含まれる。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    ana_combos = [b.combination for b in bets["穴"]]
    assert "6-8-2" in ana_combos, (
        f"穴に 6-8-2 が無い（別線2本目の波乱形が反映されていない）:\n{ana_combos}"
    )


def test_takeo_3r_total_bet_count_under_25():
    """合計買い目が 25点を超えないこと。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    total = sum(len(bets[c]) for c in ("本線", "押さえ", "穴", "大穴"))
    assert total <= 25, f"買い目合計が多すぎる: {total}点"


# ---------------------------------------------------------------------------
# 別線ライン解決ヘルパ
# ---------------------------------------------------------------------------


def test_resolve_separate_lines_returns_multiple():
    """_resolve_separate_lines が複数本の別線ラインを返す。"""
    from app.scoring import _resolve_separate_lines
    ri = _load()
    scores = compute_scores(ri)
    sep_lines = _resolve_separate_lines(ri, scores)
    # 別線ラインが3本（1-7, 3-5, 6-8）
    assert len(sep_lines) == 3
    # スコア降順 (3-5 leader 3 が最高 90.0 / 6-8 leader 6 が 85.0 / 1-7 leader 1 が 80.0)
    leaders = [sl.get("leader") for sl in sep_lines]
    assert leaders == [3, 6, 1], f"別線ラインの並び順が想定外: {leaders}"
