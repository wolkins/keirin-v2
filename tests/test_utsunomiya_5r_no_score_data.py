"""宇都宮5R 相当: 数値不足モード（全選手 score=0、コメントだけ）のテスト。

並び: 5-1-3 / 2-4-6 / 7単騎
コメント:
  5: "追" / 1: "番手" / 3: "両"
  2: "逃" / 4: "番手" / 6: "追"
  7: "両"
3連単オッズ: 4-1-3 (8.2倍) / 1-4-3 (11.5倍) / 4-1-5 (18.0倍) / ...
3連複オッズ: 1=3=4 (3.1倍) / 1=4=5 (6.8倍)

仕様要件:
- 数値不足モード検出
- 5（line先頭・追い込み）が line_leader として過大評価されない
- 2（line先頭・逃）が主導権ライン候補に
- honsen に 4-1-3 / 1-4-3 (3連単人気上位) が含まれる
- honsen の先頭が 5-1-3 ではない
- summary に「数値未取得」警告が出る
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.llm_client import build_default_client
from app.models import RaceInput
from app.prompt_builder import build_full_prompt
from app.scoring import (
    _infer_role_tag,
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
    Path(__file__).resolve().parent / "fixtures" / "utsunomiya_5r_no_score_data.json"
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
    # 数値不足モードでは boost_multiplier を上げる
    apply_market_signals(
        scores, ri.odds,
        boost_multiplier=3.0 if detect_score_data_insufficient(ri) else 1.0,
    )
    return scores


# ---------------------------------------------------------------------------
# 数値不足モード検出
# ---------------------------------------------------------------------------


def test_detect_score_data_insufficient():
    """全選手の score/B/決まり手が 0 で True を返す。"""
    ri = _load()
    assert detect_score_data_insufficient(ri) is True


def test_detect_score_data_insufficient_false_with_normal_data(tmp_path):
    """通常データ（score >0 や B>0 がある）では False。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["riders"][0]["score"] = 75.0  # 1人でも score > 0 があれば False
    ri = RaceInput.model_validate(raw)
    assert detect_score_data_insufficient(ri) is False


# ---------------------------------------------------------------------------
# 脚質コメント推定ヘルパ
# ---------------------------------------------------------------------------


def test_infer_role_tag_leader():
    """逃 / 先行 / 自力 → leader."""
    from types import SimpleNamespace
    r = SimpleNamespace(comment="逃", style_tags=["自力"])
    assert _infer_role_tag(r) == "leader"
    r2 = SimpleNamespace(comment="先行", style_tags=[])
    assert _infer_role_tag(r2) == "leader"


def test_infer_role_tag_jizai():
    from types import SimpleNamespace
    r = SimpleNamespace(comment="両", style_tags=["自在"])
    assert _infer_role_tag(r) == "jizai"


def test_infer_role_tag_oikomi():
    from types import SimpleNamespace
    r = SimpleNamespace(comment="追", style_tags=["追込"])
    assert _infer_role_tag(r) == "oikomi"
    r2 = SimpleNamespace(comment="", style_tags=["番手", "差し"])
    assert _infer_role_tag(r2) == "oikomi"


def test_infer_role_tag_unknown():
    from types import SimpleNamespace
    r = SimpleNamespace(comment="", style_tags=[])
    assert _infer_role_tag(r) == "unknown"


# ---------------------------------------------------------------------------
# 数値不足モード時の line_leader 抑制
# ---------------------------------------------------------------------------


def test_oikomi_at_line_head_is_not_main_leader():
    """5（line 先頭・追い込み）がスコア上位にならない。"""
    ri = _load()
    scores = _full_scores(ri)
    top1 = max(scores, key=lambda s: s.total())
    assert top1.car_no != 5, (
        f"追い込み型(5)が top1 になっている: {top1.car_no} (期待: 2 か 4 など逃げ/自力タグ)"
    )


def test_leader_at_line_head_gets_main_priority():
    """2（line先頭・逃）が top1 で本命ライン扱い。"""
    ri = _load()
    scores = _full_scores(ri)
    top1 = max(scores, key=lambda s: s.total())
    # 2 が top1 になっている（逃げタグ + market_signals）
    assert top1.car_no == 2, (
        f"逃げ型(2)が top1 でない: 実際 top1={top1.car_no}"
    )


# ---------------------------------------------------------------------------
# 3連単人気上位を本線に強制追加
# ---------------------------------------------------------------------------


def test_honsen_contains_4_1_3_or_1_4_3():
    """3連単人気1位 4-1-3 または 2位 1-4-3 が本線に含まれる。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    has_4_1_3 = "4-1-3" in honsen_combos
    has_1_4_3 = "1-4-3" in honsen_combos
    assert has_4_1_3 and has_1_4_3, (
        f"3連単人気上位が本線に無い:\n  本線: {honsen_combos}"
    )


def test_honsen_first_is_not_5_1_3():
    """5-1-3（追い込み line 先頭）が honsen[0] にならない。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert bets["本線"], "本線が空"
    first = bets["本線"][0].combination
    assert first != "5-1-3", (
        f"本線最上位が 5-1-3（追込ライン先頭）になっている。"
        f"全本線: {[b.combination for b in bets['本線']]}"
    )


def test_osae_includes_trio_top_derivations():
    """3連複1位 (1=3=4) 由来の3連単派生が押さえに含まれる。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_combos = [b.combination for b in bets["押さえ"]]
    # 1-3-4 のような順列が押さえにある (3連複 1=3=4 由来)
    from itertools import permutations
    trio_perms = ["-".join(str(c) for c in p) for p in permutations([1, 3, 4])]
    found = [c for c in trio_perms if c in osae_combos]
    assert len(found) >= 1, (
        f"3連複1位(1=3=4)派生が押さえに無い:\n{osae_combos}"
    )


# ---------------------------------------------------------------------------
# 警告メッセージ
# ---------------------------------------------------------------------------


def test_summary_contains_data_insufficient_warning():
    """summary に「数値未取得」警告が含まれる。"""
    ri = _load()
    scores = _full_scores(ri)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    assert (
        "競走得点" in pred.summary
        or "未取得" in pred.summary
        or "脚質コメント" in pred.summary
    ), f"警告メッセージが summary に無い:\n{pred.summary}"


def test_normal_data_no_warning(tmp_path):
    """通常データでは警告が出ない。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # 全選手に score を入れる
    for r in raw["riders"]:
        r["score"] = 80.0
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    apply_market_signals(scores, ri.odds)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    assert "未取得のため" not in pred.summary
