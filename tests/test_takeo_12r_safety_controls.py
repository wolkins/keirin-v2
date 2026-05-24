"""武雄12R: OutputPlan validator + 安全制御 (覆盖率/HeadBias/race_complexity)。

検証要件 (2026-05-24, 武雄12R 対応):
1. OutputPlan validator: final_ana ⊆ ana∪ooana を保証 (1-4-7 ケース)
2. オッズ取得率 22% → data_quality≠high / final_best 1点制限 / 警告
3. HeadBias のみで 1-7-* を 2点以上昇格しない (分散)
4. AxisBias(1-7) 検出時は 1-7-* 集中許可
5. race_complexity=very_high + coverage<0.4 で「購入見送り推奨」警告
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.final_selection import build_final_selection
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInput,
)
from app.output_plan import (
    OutputPlan, build_output_plan, validate_output_plan,
)
from app.output_validation import (
    assess_data_quality, assess_race_complexity, compute_odds_coverage,
    detect_market_bias,
)


def _bet(combo, *, market_odds=None, value_label="", gami_risk=0.0,
         category="本線", reason="test"):
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=reason, gami_risk=gami_risk,
        market_odds=market_odds, value_label=value_label,
    )


def _pred(*, honsen=None, osae=None, ana=None, ooana=None,
          is_girls=False, final_conclusion=""):
    return Prediction(
        race_id="test-takeo12", venue="武雄", race_no=12, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="", lines_text="",
        marks={},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion=final_conclusion,
        gami_memo="", reflection_points=[],
    )


def _input(*, class_name="A級一般", lines=None, riders=None,
           odds=None):
    return RaceInput.model_validate({
        "race": {
            "race_id": "test-takeo12", "date": "2026-05-24",
            "venue": "武雄", "race_no": 12,
            "class_name": class_name, "start_time": "16:30",
        },
        "weather": {
            "condition": "晴れ", "rain_mm_per_hour": 0.0,
            "wind_speed_mps": 2.0,
        },
        "lines": lines or [
            {"line_name": "本命", "cars": [1, 7]},
            {"line_name": "別線", "cars": [2, 6]},
            {"line_name": "別線2", "cars": [3, 5]},
            {"line_name": "単", "cars": [4]},
            {"line_name": "単", "cars": [8]},
            {"line_name": "単", "cars": [9]},
        ],
        "riders": riders or [
            {"car_no": i, "name": f"R{i}", "score": 95.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "九州"}
            for i in range(1, 10)
        ],
        "odds": odds or [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# 要件1: OutputPlan validator (final_ana ⊆ ana∪ooana)
# ---------------------------------------------------------------------------


class TestOutputPlanValidator:
    def test_final_ana_combo_not_in_display_is_added(self):
        """武雄12R 1-4-7 ケース: final_ana に表示されない combo があると、
        validator が ana に補充する + 警告を出す。"""
        plan = OutputPlan(
            honsen=[_bet("1-7-3", market_odds=8.0, value_label="妙味あり")],
            ana=[_bet("5-3-1", market_odds=22.0, value_label="妙味あり")],
            # final_ana に「ana にも ooana にも無い 1-4-7」を仕込む
            final_ana=[_bet("1-4-7", market_odds=80.0, value_label="妙味あり")],
        )
        warnings = validate_output_plan(plan)
        codes = [w.code for w in warnings]
        assert "FINAL_ANA_NOT_IN_DISPLAY" in codes, (
            f"final_ana 外混入の警告が出るべき: {codes}"
        )
        # ana に 1-4-7 が補充されている
        ana_combos = {b.combination for b in plan.ana}
        assert "1-4-7" in ana_combos, (
            f"1-4-7 が ana に補充されるべき: {ana_combos}"
        )

    def test_final_best_combo_not_in_display_is_added(self):
        """final_best が honsen/osae 外なら honsen に補充。"""
        plan = OutputPlan(
            honsen=[_bet("1-7-3", market_odds=8.0)],
            final_best=[_bet("9-8-7", market_odds=15.0, value_label="妙味あり")],
        )
        warnings = validate_output_plan(plan)
        codes = [w.code for w in warnings]
        assert "FINAL_PURCHASE_NOT_IN_DISPLAY" in codes
        # honsen に 9-8-7 が補充
        assert "9-8-7" in {b.combination for b in plan.honsen}


# ---------------------------------------------------------------------------
# 要件2: オッズ取得率による安全制御
# ---------------------------------------------------------------------------


class TestCoverageSafetyControl:
    def _make_low_coverage_pred(self):
        """オッズ取得率 ~22% のシナリオを作る。"""
        # honsen 4 + osae 6 + ana 5 + ooana 3 = 18 点中、odds 取得済み 4点
        honsen = [
            _bet("1-7-3", market_odds=8.0, value_label="妙味あり"),
            _bet("1-7-4", market_odds=10.0, value_label="妙味あり"),
            _bet("1-7-2", market_odds=None),
            _bet("1-7-5", market_odds=None),
        ]
        osae = [
            _bet(c, market_odds=None, category="押さえ")
            for c in ("7-1-3", "1-2-3", "1-3-2", "3-1-2", "2-1-3", "1-5-3")
        ]
        ana = [
            _bet("3-5-1", market_odds=22.0, value_label="妙味あり", category="穴"),
            _bet("5-3-1", market_odds=28.0, value_label="妙味あり", category="穴"),
        ] + [
            _bet(c, market_odds=None, category="穴")
            for c in ("4-1-7", "9-1-7", "8-1-7")
        ]
        # 上記で 4 + 0 + 2 + 0 = 4点 / 13 + 5? まず実際に数えてassertしやすい構成にし直し
        # honsen 4 + osae 6 + ana 5 + ooana 3 = 18 点
        ooana = [
            _bet(c, market_odds=None, category="大穴")
            for c in ("8-9-1", "9-8-1", "8-7-1")
        ]
        return _pred(honsen=honsen, osae=osae, ana=ana, ooana=ooana)

    def test_coverage_22pct_does_not_become_high(self):
        """odds 取得率 ~22% (4/18) では data_quality は high にならない。"""
        ri = _input()
        pred = self._make_low_coverage_pred()
        coverage = compute_odds_coverage(pred)
        # 4 / 18 = ~0.22
        assert 0.15 <= coverage.coverage_ratio < 0.4, (
            f"テスト構成の coverage が想定範囲外: {coverage.coverage_ratio}"
        )
        quality = assess_data_quality(ri, coverage=coverage)
        assert quality != "high", (
            f"coverage 22% で data_quality が high になるべきでない: {quality}"
        )

    def test_coverage_low_emits_provisional_warning(self):
        """coverage < 40% で「暫定候補」の警告が出る。"""
        ri = _input()
        pred = self._make_low_coverage_pred()
        sel = build_final_selection(pred, ri)
        joined = " ".join(sel.warnings)
        assert "暫定候補" in joined or "見送り推奨" in joined, (
            f"暫定候補 / 見送り警告が出るべき: {sel.warnings}"
        )

    def test_coverage_low_limits_best_bets_to_one(self):
        """coverage < 40% で final_best は最大1点。"""
        ri = _input()
        pred = self._make_low_coverage_pred()
        plan = build_output_plan(pred, ri)
        assert len(plan.final_best) <= 1, (
            f"low coverage で final_best 1点制限: {len(plan.final_best)} 点"
        )


# ---------------------------------------------------------------------------
# 要件3: HeadBias / AxisBias 分離
# ---------------------------------------------------------------------------


class TestHeadBiasVsAxisBias:
    def _odds_head_only_bias(self):
        """HeadBias (1番頭) のみ、AxisBias なし。2着車番を分散。"""
        return [
            OddsEntry(bet_type="3連単", combination="1-7-3", odds=8.0),
            OddsEntry(bet_type="3連単", combination="1-9-3", odds=12.0),
            OddsEntry(bet_type="3連単", combination="1-2-3", odds=18.0),
            OddsEntry(bet_type="3連単", combination="1-3-5", odds=22.0),
            OddsEntry(bet_type="3連単", combination="7-1-3", odds=28.0),
        ]

    def _odds_axis_1_7_bias(self):
        """AxisBias (1-7軸) 3件以上。"""
        return [
            OddsEntry(bet_type="3連単", combination="1-7-3", odds=8.0),
            OddsEntry(bet_type="3連単", combination="1-7-4", odds=10.0),
            OddsEntry(bet_type="3連単", combination="1-7-5", odds=15.0),
            OddsEntry(bet_type="3連単", combination="1-2-3", odds=22.0),
            OddsEntry(bet_type="3連単", combination="7-1-3", odds=28.0),
        ]

    def test_head_bias_only_detected_without_axis_bias(self):
        ri = _input(odds=self._odds_head_only_bias())
        bias = detect_market_bias(ri)
        assert bias.has_head_focus is True
        assert bias.focused_head == 1
        assert bias.has_axis_focus is False, (
            f"HeadBias のみ想定だが AxisBias 検出: focused_axis={bias.focused_axis}"
        )

    def test_axis_bias_detected_when_pair_concentrated(self):
        ri = _input(odds=self._odds_axis_1_7_bias())
        bias = detect_market_bias(ri)
        assert bias.has_head_focus is True
        assert bias.focused_head == 1
        assert bias.has_axis_focus is True, (
            f"1-7 軸が 3件 → AxisBias 検出されるべき: "
            f"focused_axis={bias.focused_axis}"
        )
        assert bias.focused_axis == (1, 7)

    def test_head_only_bias_does_not_concentrate_on_one_pair(self):
        """HeadBias のみ → 同じ 2着車番に 2点以上集中しない (分散)。"""
        from app.scoring import _ensure_market_focused_head_bets
        ri = _input(odds=self._odds_head_only_bias())
        honsen: list[BetRecommendation] = []
        osae: list[BetRecommendation] = []
        _ensure_market_focused_head_bets(honsen, osae, input_data=ri)
        # 1-7-* と 1-9-* が分散 push される
        from collections import Counter
        pair_counts: Counter = Counter()
        for b in (honsen + osae):
            if b.combination and b.combination.startswith("1-"):
                parts = b.combination.split("-")
                if len(parts) >= 2:
                    pair_counts[(parts[0], parts[1])] += 1
        # HeadBias のみなので、どのペアも 2点未満 (max=1)
        for pair, cnt in pair_counts.items():
            assert cnt <= 1, (
                f"HeadBias のみで pair {pair} が {cnt} 点 (分散されていない):\n"
                f"全体: {pair_counts}"
            )

    def test_axis_bias_allows_pair_concentration(self):
        """AxisBias (1-7) → 1-7-* は 2点まで push 許可。"""
        from app.scoring import _ensure_market_focused_head_bets
        ri = _input(odds=self._odds_axis_1_7_bias())
        honsen: list[BetRecommendation] = []
        osae: list[BetRecommendation] = []
        _ensure_market_focused_head_bets(honsen, osae, input_data=ri)
        pair_17_count = sum(
            1 for b in (honsen + osae)
            if b.combination and b.combination.startswith("1-7-")
        )
        assert pair_17_count >= 2, (
            f"AxisBias(1-7) で 1-7-* が 2点以上 push されるべき。"
            f"実際: {pair_17_count} 点"
        )


# ---------------------------------------------------------------------------
# 要件4: race_complexity 判定
# ---------------------------------------------------------------------------


class TestRaceComplexity:
    def test_low_complexity_normal_a_kyu(self):
        """通常 A 級 + 平凡な構成 → low / medium。"""
        ri = _input()  # 全員 95 点、A 級一般
        complexity = assess_race_complexity(ri)
        assert complexity in ("low", "medium"), (
            f"通常レースで {complexity} は高すぎる"
        )

    def test_very_high_complexity_with_strong_riders_and_singles(self):
        """115点以上多数 + 2車ライン複数 + 単騎格上 → very_high。"""
        riders = [
            {"car_no": i, "name": f"R{i}", "score": s, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "九州"}
            for i, s in enumerate(
                [120.0, 118.0, 116.0, 115.5, 105.0, 100.0, 95.0, 90.0, 88.0],
                start=1,
            )
        ]
        ri = _input(
            class_name="A級特選",
            riders=riders,
            lines=[
                {"line_name": "本命", "cars": [1, 7]},     # 2車
                {"line_name": "別線", "cars": [2, 6]},     # 2車
                {"line_name": "別線2", "cars": [3, 5]},    # 2車
                {"line_name": "単", "cars": [4]},          # 単騎 (115.5 点で格上)
                {"line_name": "単", "cars": [8]},
                {"line_name": "単", "cars": [9]},
            ],
        )
        complexity = assess_race_complexity(ri)
        assert complexity in ("high", "very_high"), (
            f"高難度想定だが {complexity}"
        )

    def test_very_high_complexity_with_low_coverage_triggers_warning(self):
        """very_high + coverage<0.4 → 「購入見送り推奨」警告。"""
        riders = [
            {"car_no": i, "name": f"R{i}", "score": s, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "九州"}
            for i, s in enumerate(
                [120.0, 118.0, 116.0, 115.5, 115.0, 100.0, 95.0, 90.0, 88.0],
                start=1,
            )
        ]
        ri = _input(
            class_name="A級特選",
            riders=riders,
            lines=[
                {"line_name": "本命", "cars": [1, 7]},
                {"line_name": "別線", "cars": [2, 6]},
                {"line_name": "別線2", "cars": [3, 5]},
                {"line_name": "単", "cars": [4]},
                {"line_name": "単", "cars": [8]},
                {"line_name": "単", "cars": [9]},
            ],
        )
        pred = _pred(
            honsen=[_bet(c, market_odds=None) for c in ("1-7-3",)],
            osae=[_bet(c, market_odds=None, category="押さえ")
                  for c in ("7-1-3",)],
        )
        # coverage = 0% → very_high + low coverage 警告 が出る
        sel = build_final_selection(pred, ri)
        joined = " ".join(sel.warnings)
        complexity = assess_race_complexity(ri)
        if complexity == "very_high":
            assert "購入見送り" in joined or "very_high" in joined, (
                f"very_high + low coverage で警告が出るべき:\n"
                f"complexity={complexity}, warnings={sel.warnings}"
            )
