"""Phase 16 Step 6 (2026-05-26): CandidateLifecycle.transitions の populate.

検証 5 シナリオ (A〜E):
A. line_source_rules_filter — line_* タグの候補が watch_only に移動
B. gami_source_rules_filter — low_odds/gami_warning タグの候補が
   gami_warning に移動
C. market_bias_head_only_axis_limit — HeadBias-only で同一軸過多を抑制
D. max_final_best_limit — race_type policy で final_best 上限超過
E. fallback safety — coverage_metrics=None で BUYABLE → TENTATIVE cap
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode, resolve_race_type_policy
from app.decision_engine import Transition
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInfo, RaceInput, Rider,
)
from app.output_plan import OutputPlan, _populate_decision_engine_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bet(combo, *, odds=None, value_label=None, gami_risk=0.0,
         source_rules=None):
    return BetRecommendation(
        category="本線",
        bet_type="3連単",
        combination=combo,
        reason="t",
        gami_risk=gami_risk,
        market_odds=odds,
        value_label=value_label,
        source_rules=tuple(source_rules or ()),
    )


def _race_input(*, is_girls=False, odds_combos=None) -> RaceInput:
    odds_combos = odds_combos or []
    return RaceInput(
        race=RaceInfo(
            race_id="20260526-test-6",
            date="2026-05-26",
            venue="test",
            race_no=6,
            class_name=("ガールズ" if is_girls else "A級一般"),
            start_time="12:00",
        ),
        riders=[
            Rider(
                car_no=i, name=f"R{i}", score=80.0, b_count=0,
                nige=0, makuri=0, sashi=0, mark=0, comment="",
                home_area="中部",
            )
            for i in range(1, 8)
        ],
        lines=[],
        odds=[
            OddsEntry(bet_type="3連単", combination=c, odds=o)
            for c, o in odds_combos
        ],
    )


def _prediction(is_girls=False) -> Prediction:
    return Prediction(
        race_id="20260526-test-6",
        venue="test", race_no=6, is_girls=is_girls, marks={},
        summary="t", weather_text="t", lines_text="t", venue_trend_text="t",
        honsen=[], osae=[], ana=[], ooana=[],
        final_conclusion="",
        gami_memo="",
        reflection_points=[],
    )


# ---------------------------------------------------------------------------
# A. line_source_rules_filter
# ---------------------------------------------------------------------------


class TestScenarioA_LineSourceRulesFilter:
    def test_line_candidate_moves_to_watch_only(self):
        """allow_line_logic=False の race_type で line_* タグの候補が
        honsen → watch_only に移動し、transition が記録される."""
        ri = _race_input(is_girls=True)
        bet = _bet(
            "1-2-3", odds=10.0,
            source_rules=["line_direct"],
        )
        plan = OutputPlan(
            honsen=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        # race_type_policy をセット (girls なら allow_line_logic=False)
        policy = resolve_race_type_policy(ri)
        object.__setattr__(plan, "_race_type_policy", policy)
        plan.race_type = policy.race_type
        from app.output_plan import _apply_line_source_rules_filter
        _apply_line_source_rules_filter(plan)

        # transition が記録されている
        assert "1-2-3" in plan.candidate_transitions
        transitions = plan.candidate_transitions["1-2-3"]
        steps = [t.step for t in transitions]
        assert "line_source_rules_filter" in steps
        target = next(
            t for t in transitions if t.step == "line_source_rules_filter"
        )
        assert target.from_bucket == "honsen"
        assert target.to_bucket == "watch_only"
        assert "allow_line_logic=False" in (target.reason or "")
        assert "line_direct" in target.source_rules


# ---------------------------------------------------------------------------
# B. gami_source_rules_filter
# ---------------------------------------------------------------------------


class TestScenarioB_GamiSourceRulesFilter:
    def test_gami_candidate_moves_to_gami_warning(self):
        """source_rules に gami_warning/low_odds がある候補が
        honsen → gami_warning に移動し、transition が記録される."""
        bet = _bet(
            "1-2-3", odds=3.0, gami_risk=0.9,
            source_rules=["gami_warning", "low_odds"],
        )
        plan = OutputPlan(
            honsen=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        from app.output_plan import _apply_gami_source_rules_filter
        _apply_gami_source_rules_filter(plan)

        assert "1-2-3" in plan.candidate_transitions
        transitions = plan.candidate_transitions["1-2-3"]
        steps = [t.step for t in transitions]
        assert "gami_source_rules_filter" in steps
        target = next(
            t for t in transitions
            if t.step == "gami_source_rules_filter"
        )
        assert target.from_bucket == "honsen"
        assert target.to_bucket == "gami_warning"
        assert "gami_warning" in target.source_rules


# ---------------------------------------------------------------------------
# C. market_bias_head_only_axis_limit
# ---------------------------------------------------------------------------


class TestScenarioC_MarketBiasHeadOnlyAxisLimit:
    def test_axis_overflow_suppressed(self):
        """HeadBias-only で final_best に同一軸 2 点があるとき、
        2 点目以降が watch_only に移動し transition が記録される."""
        # HeadBias=2 のみ (Axis なし) の odds 構成
        ri = _race_input(odds_combos=[
            ("2-1-3", 5.0), ("2-3-4", 6.0), ("2-7-5", 7.0),
            ("3-1-2", 8.0), ("4-1-2", 9.0),
        ])
        # final_best に同一軸 1-4 系候補 2 点を持たせる... ではなく、
        # 2-1 軸 2 点で抑制を狙う。focused_head=2 で axis_count<3 想定
        bet1 = _bet("2-1-3", odds=10.0)
        bet2 = _bet("2-1-7", odds=12.0)  # 同じ (2,1) axis
        plan = OutputPlan(
            final_best=[bet1, bet2],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        # race_type_policy / market_bias_decision を実行
        from app.output_plan import (
            _apply_market_bias_decision, _apply_race_type_policy,
        )
        _apply_race_type_policy(plan, ri)
        _apply_market_bias_decision(plan, ri)

        # 2-1-7 が抑制されている (2 点目)
        assert "2-1-7" in plan.candidate_transitions
        transitions = plan.candidate_transitions["2-1-7"]
        steps = [t.step for t in transitions]
        assert "market_bias_head_only_axis_limit" in steps
        target = next(
            t for t in transitions
            if t.step == "market_bias_head_only_axis_limit"
        )
        assert target.from_bucket == "final_best"
        assert target.to_bucket == "watch_only"
        assert "HeadBias" in (target.reason or "")


# ---------------------------------------------------------------------------
# D. max_final_best_limit
# ---------------------------------------------------------------------------


class TestScenarioD_MaxFinalBestLimit:
    def test_overflow_candidates_recorded(self):
        """girls 等の race_type で final_best 上限超過分が transition に
        記録される."""
        ri = _race_input(is_girls=True)
        # girls policy の max_final_best を確認 (通常 3 程度)
        policy_pre = resolve_race_type_policy(ri)
        if policy_pre.max_final_best is None:
            pytest.skip("max_final_best=None では超過テストできない")
        # max + 1 点入れて確実に 1 点超過させる
        n_bets = policy_pre.max_final_best + 1
        bets = [_bet(f"{i}-{(i+1)%7+1}-{(i+2)%7+1}", odds=10.0 + i)
                for i in range(1, n_bets + 1)]
        plan = OutputPlan(
            final_best=bets,
            purchase_mode=PurchaseMode.BUYABLE,
        )
        policy = resolve_race_type_policy(ri)
        object.__setattr__(plan, "_race_type_policy", policy)
        plan.race_type = policy.race_type

        from app.output_plan import _apply_max_final_best_limit
        _apply_max_final_best_limit(plan)

        # 超過分 (最後の bet) に transition がある
        overflow_combo = bets[-1].combination
        assert overflow_combo in plan.candidate_transitions
        transitions = plan.candidate_transitions[overflow_combo]
        steps = [t.step for t in transitions]
        assert "max_final_best_limit" in steps
        target = next(
            t for t in transitions if t.step == "max_final_best_limit"
        )
        assert target.from_bucket == "final_best"
        # purchase_mode=BUYABLE なので final_osae に格下げ
        assert target.to_bucket == "final_osae"


# ---------------------------------------------------------------------------
# E. fallback safety (decision_engine_fallback_safety)
# ---------------------------------------------------------------------------


class TestScenarioE_FallbackSafety:
    def test_fallback_buyable_to_tentative_recorded(self):
        """coverage_metrics=None / BUYABLE のとき、
        purchase_mode が TENTATIVE に cap され transition が
        __plan__ に記録される."""
        plan = OutputPlan(
            purchase_mode=PurchaseMode.BUYABLE,
        )
        plan.coverage_metrics = None
        from app.markdown_renderer import (
            _apply_decision_engine_fallback_safety,
        )
        _apply_decision_engine_fallback_safety(plan)

        assert plan.purchase_mode == PurchaseMode.TENTATIVE
        assert "__plan__" in plan.candidate_transitions
        transitions = plan.candidate_transitions["__plan__"]
        target = next(
            (t for t in transitions
             if t.step == "decision_engine_fallback_safety"),
            None,
        )
        assert target is not None
        assert target.from_state == "BUYABLE"
        assert target.to_state == "TENTATIVE"

    def test_fallback_idempotent_no_double_transition(self):
        """fallback safety を 2 回呼んでも transition は 1 件のみ
        (purchase_mode が既に TENTATIVE なら追加しない)."""
        plan = OutputPlan(
            purchase_mode=PurchaseMode.BUYABLE,
        )
        plan.coverage_metrics = None
        from app.markdown_renderer import (
            _apply_decision_engine_fallback_safety,
        )
        _apply_decision_engine_fallback_safety(plan)
        _apply_decision_engine_fallback_safety(plan)

        transitions = plan.candidate_transitions.get("__plan__", [])
        fallback_count = sum(
            1 for t in transitions
            if t.step == "decision_engine_fallback_safety"
        )
        # 2 回目は purchase_mode が既に TENTATIVE なので transition 追加なし
        assert fallback_count == 1


# ---------------------------------------------------------------------------
# F. lifecycle.transitions に集約される
# ---------------------------------------------------------------------------


class TestLifecycleAggregation:
    def test_lifecycle_transitions_populated_for_moved_candidate(self):
        """build_decision_engine_data 経由で lifecycle.transitions に
        plan.candidate_transitions の内容が集約される."""
        bet = _bet(
            "1-2-3", odds=3.0, gami_risk=0.9,
            source_rules=["gami_warning", "low_odds"],
        )
        plan = OutputPlan(
            honsen=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        from app.output_plan import _apply_gami_source_rules_filter
        _apply_gami_source_rules_filter(plan)
        _populate_decision_engine_data(plan, _prediction(), _race_input())

        # lifecycle.transitions に gami transition がある
        lc = next(
            (lc for lc in plan.lifecycles if lc.combination == "1-2-3"),
            None,
        )
        assert lc is not None
        steps = [t.step for t in lc.transitions]
        assert "gami_source_rules_filter" in steps

    def test_plan_pseudocombination_not_in_lifecycle(self):
        """__plan__ 擬似 combination は lifecycle に集約されない
        (個別 candidate ではなく plan 全体の transition なので)."""
        plan = OutputPlan(
            purchase_mode=PurchaseMode.BUYABLE,
        )
        plan.coverage_metrics = None
        from app.markdown_renderer import (
            _apply_decision_engine_fallback_safety,
        )
        _apply_decision_engine_fallback_safety(plan)
        _populate_decision_engine_data(plan, _prediction(), _race_input())

        # __plan__ は lifecycle に出ない
        plan_combos = [lc.combination for lc in plan.lifecycles]
        assert "__plan__" not in plan_combos
        # plan.candidate_transitions には残っている (集約スキップしただけ)
        assert "__plan__" in plan.candidate_transitions
