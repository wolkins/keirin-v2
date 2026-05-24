"""静岡4R: 2番頭+2-5軸 AxisBias の派生候補生成 + 低オッズ分離 (#375).

検証要件:
1. detect_market_bias で AxisBias=(2,5), focused_axis_count>=3 が検出される
2. cheapest_focused_odds<5 で is_focused_head_cheap が True
3. build_candidate_bets で 2-5-? の派生が honsen に複数入る
4. 低オッズ分離: 5-2-? (head/2着 入れ替え) が押さえに最低1点入る
5. AxisBias 無し + HeadBias のみ時の分散ロジックは別シナリオに影響しない
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput
from app.output_validation import detect_market_bias
from app.scoring import (
    apply_market_signals,
    build_candidate_bets,
    compute_scores,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "shizuoka_4r_axis_bias.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


def _bets(ri: RaceInput):
    scores = compute_scores(ri)
    apply_market_signals(scores, ri.odds)
    return build_candidate_bets(ri, scores)


class TestAxisBiasDetection:
    def test_detect_axis_bias_2_5(self):
        """3連単上位5件すべて 2-5-? → AxisBias=(2,5)。"""
        ri = _load()
        bias = detect_market_bias(ri)
        assert bias.has_axis_focus is True
        assert bias.focused_axis == (2, 5), (
            f"AxisBias=(2,5) を期待: actual={bias.focused_axis}"
        )
        assert bias.focused_axis_count >= 3

    def test_head_bias_also_detected(self):
        """同じ fixture で 2 番頭が複数あるため HeadBias=2 も検出される。"""
        ri = _load()
        bias = detect_market_bias(ri)
        assert bias.has_head_focus is True
        assert bias.focused_head == 2

    def test_is_focused_head_cheap(self):
        """cheapest_focused_odds=4.5<5.0 → is_focused_head_cheap=True。"""
        ri = _load()
        bias = detect_market_bias(ri)
        assert bias.is_focused_head_cheap is True, (
            f"4.5倍があるのに is_focused_head_cheap=False: bias={bias}"
        )


class TestAxisBiasDerivedCandidates:
    """`_ensure_market_focused_head_bets` は派生候補を osae に積む設計
    (本線3点制約維持のため)。広島8R テストと同様、本線+押さえ合算で検証。"""

    def test_axis_combo_in_honsen_or_osae(self):
        ri = _load()
        bets = _bets(ri)
        all_combos = (
            [b.combination for b in bets["本線"]]
            + [b.combination for b in bets["押さえ"]]
        )
        axis_combos = [
            c for c in all_combos
            if c and c.split("-")[:2] == ["2", "5"]
        ]
        assert len(axis_combos) >= 2, (
            f"2-5-? が本線+押さえに2点未満: {axis_combos} / 全={all_combos}"
        )


class TestLowOddsFlip:
    """要件4: cheapest_focused_odds<5 → 5-2-? が押さえに分離される。"""

    def test_flip_combo_in_osae(self):
        ri = _load()
        bets = _bets(ri)
        osae_combos = [b.combination for b in bets["押さえ"]]
        # 5-2-? (頭/2着入れ替え) のズレ目が押さえに最低1点
        flip_combos = [
            c for c in osae_combos
            if c and c.split("-")[:2] == ["5", "2"]
        ]
        assert len(flip_combos) >= 1, (
            f"5-2-? の入れ替えズレ目が押さえに無い: 押さえ={osae_combos}"
        )


# ---------------------------------------------------------------------------
# #376: 4車以上ラインの4番手流れ込み
# ---------------------------------------------------------------------------


LONG_LINE_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "shizuoka_4r_long_line.json"
)


def _load_long_line() -> RaceInput:
    return RaceInput.model_validate(
        json.loads(LONG_LINE_FIXTURE.read_text(encoding="utf-8"))
    )


class TestLongLineFourthFlow:
    """4車以上ライン (例: 1-2-3-4) の 4 番手が押さえに流れ込みで入る。"""

    def test_fourth_finish_in_osae(self):
        ri = _load_long_line()
        bets = _bets(ri)
        osae_combos = [b.combination for b in bets["押さえ"]]
        # 1-2-4 (本命先頭-番手-4番手)
        assert "1-2-4" in osae_combos, (
            f"4車ライン 1-2-4 流れ込みが押さえに無い: {osae_combos}"
        )

    def test_fourth_second_in_osae(self):
        ri = _load_long_line()
        bets = _bets(ri)
        osae_combos = [b.combination for b in bets["押さえ"]]
        # 1-4-2 (本命先頭-4番手-番手): 4番手2着上がり
        assert "1-4-2" in osae_combos, (
            f"4車ライン 1-4-2 (4番手2着上がり) が押さえに無い: {osae_combos}"
        )

    def test_girls_does_not_apply_long_line_fourth(self):
        """ガールズはラインを使わないため、4車「ライン」処理は適用されない。"""
        data = json.loads(LONG_LINE_FIXTURE.read_text(encoding="utf-8"))
        data["race"]["is_girls"] = True
        ri = RaceInput.model_validate(data)
        bets = _bets(ri)
        osae_combos = [b.combination for b in bets["押さえ"]]
        # ガールズでは「4車ライン4番手の流れ込み」が出ない
        # (スコア由来で 1-2-4 が偶然出る可能性はあるが、ライン由来の
        # reason が付かないため reason ベースで確認)
        reasons = [b.reason for b in bets["押さえ"]]
        assert not any("4車ライン4番手" in r for r in reasons), (
            f"ガールズに 4車ライン由来 reason が混入: {reasons}"
        )
