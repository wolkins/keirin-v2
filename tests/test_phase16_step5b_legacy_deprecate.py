"""Phase 16 Step 5B (2026-05-26): 旧 warning / 旧 coverage の deprecate.

検証する 4 シナリオ:
A. 静岡6R 相当 — MARKET_BIAS_WATCH_ONLY が出て、旧 MARKET_BIAS_NOT_COVERED
   が出ない / 旧「オッズ取得済み: 0/8」が出ない / 新 layout が出る
B. purchase 候補に bias 頭がある — 旧 / V2 NOT_COVERED が共に出ない
C. coverage_metrics missing (v2 populate 失敗) — DECISION_ENGINE_NOT_POPULATED
   警告 + 旧 OddsCoverage fallback
D. v1 legacy 経路 — 旧 render_odds_coverage_section が動く (削除されていない)
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInfo, RaceInput, Rider,
)
from app.output_plan import OutputPlan


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


def _race_input(odds_combos=None) -> RaceInput:
    odds_combos = odds_combos or []
    return RaceInput(
        race=RaceInfo(
            race_id="20260526-test-6",
            date="2026-05-26",
            venue="test",
            race_no=6,
            class_name="A級一般",
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


def _prediction() -> Prediction:
    return Prediction(
        race_id="20260526-test-6",
        venue="test", race_no=6, is_girls=False, marks={},
        summary="t", weather_text="t", lines_text="t", venue_trend_text="t",
        honsen=[], osae=[], ana=[], ooana=[],
        final_conclusion="",
        gami_memo="",
        reflection_points=[],
    )


def _render(plan: OutputPlan, ri: RaceInput) -> str:
    from app.markdown_renderer import render_output_plan
    return render_output_plan(plan, _prediction(), ri)


# 静岡6R odds set (HeadBias 2 番頭、AxisBias なし)
_SHIZUOKA_ODDS = [
    ("2-3-1", 5.5), ("2-1-3", 6.0), ("2-7-1", 6.1),
    ("3-1-2", 12.0), ("4-2-1", 15.0),
]


# ---------------------------------------------------------------------------
# A. 静岡6R 相当
# ---------------------------------------------------------------------------


class TestScenarioA_Shizuoka6R:
    def _build(self):
        ri = _race_input(_SHIZUOKA_ODDS)
        bet_271 = _bet(
            "2-7-1", odds=6.1, value_label="見送り寄り",
            source_rules=["market_head", "market_pair"],
        )
        plan = OutputPlan(
            honsen_miokuri=[bet_271],
            purchase_mode=PurchaseMode.SKIP,
        )
        # 模擬: final_selection が legacy MARKET_BIAS_NOT_COVERED を出す
        # 状況を作る (warning を事前に入れる)
        from app.output_plan import OutputPlanWarning
        plan.warnings.append(OutputPlanWarning(
            code="MARKET_BIAS_NOT_COVERED",
            severity="warning",
            message=(
                "市場偏り(2番頭集中) に合うオッズ取得済み買い目が"
                "final_selection に無いため、購入前に再確認してください。"
            ),
        ))
        from app.output_plan import _populate_decision_engine_data
        _populate_decision_engine_data(plan, _prediction(), ri)
        return plan, ri

    def test_v2_market_bias_watch_only_emitted(self):
        plan, _ = self._build()
        codes = [w.code for w in plan.warnings]
        assert "MARKET_BIAS_WATCH_ONLY" in codes

    def test_legacy_market_bias_not_covered_suppressed(self):
        """旧 MARKET_BIAS_NOT_COVERED は v2 populate 成功時に除外される."""
        plan, _ = self._build()
        codes = [w.code for w in plan.warnings]
        assert "MARKET_BIAS_NOT_COVERED" not in codes

    def test_new_coverage_layout_in_md(self):
        plan, ri = self._build()
        md = _render(plan, ri)
        # 新 layout
        assert "### 候補買い目オッズ取得率" in md
        assert "表示候補オッズ: 1/1点" in md
        assert "購入候補オッズ: 購入候補なし" in md
        # 旧表示 (混在しない)
        assert "オッズ取得済み: 0/" not in md


# ---------------------------------------------------------------------------
# B. purchase 候補に bias 頭がある
# ---------------------------------------------------------------------------


class TestScenarioB_BiasInPurchase:
    def test_no_market_bias_warning(self):
        """purchase 候補に bias 頭の candidate がある場合、
        MARKET_BIAS_NOT_COVERED も MARKET_BIAS_NOT_COVERED_V2 も
        MARKET_BIAS_WATCH_ONLY も出ない (PURCHASE_COVERED は info のみ)."""
        ri = _race_input(_SHIZUOKA_ODDS)
        # bias 頭 (2) の candidate を final_best に入れる
        bet = _bet(
            "2-3-1", odds=10.0, value_label="本線向き",
            source_rules=["market_head"],
        )
        plan = OutputPlan(
            honsen=[bet], final_best=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        # legacy warning は事前に入れない (final_selection が出さない想定)
        from app.output_plan import _populate_decision_engine_data
        _populate_decision_engine_data(plan, _prediction(), ri)
        codes = [w.code for w in plan.warnings]
        assert "MARKET_BIAS_NOT_COVERED" not in codes
        assert "MARKET_BIAS_NOT_COVERED_V2" not in codes
        assert "MARKET_BIAS_WATCH_ONLY" not in codes

    def test_purchase_in_coverage_metrics(self):
        ri = _race_input(_SHIZUOKA_ODDS)
        bet = _bet(
            "2-3-1", odds=10.0, value_label="本線向き",
            source_rules=["market_head"],
        )
        plan = OutputPlan(
            honsen=[bet], final_best=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        from app.output_plan import _populate_decision_engine_data
        _populate_decision_engine_data(plan, _prediction(), ri)
        # 2-3-1 は purchase coverage に含まれる
        assert plan.coverage_metrics.purchase.total == 1
        assert plan.coverage_metrics.purchase.with_odds == 1
        # market_bias coverage にも含まれる
        assert plan.coverage_metrics.market_bias.total == 1


# ---------------------------------------------------------------------------
# C. coverage_metrics missing (populate 失敗)
# ---------------------------------------------------------------------------


class TestScenarioC_CoverageMetricsMissing:
    def test_missing_metrics_emits_warning_and_falls_back(self):
        """plan.coverage_metrics=None で render すると、
        DECISION_ENGINE_NOT_POPULATED 警告 + 旧 OddsCoverage 表示で
        fallback する."""
        ri = _race_input([("1-2-3", 10.0)])
        bet = _bet("1-2-3", odds=10.0)
        plan = OutputPlan(
            honsen=[bet], final_best=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        # 意図的に coverage_metrics を populate しない
        plan.coverage_metrics = None
        md = _render(plan, ri)
        codes = [w.code for w in plan.warnings]
        # 警告コードが追加される
        assert "DECISION_ENGINE_NOT_POPULATED" in codes
        # safe fallback: 旧 layout が出る (新ではなく)
        # 旧の「取得済み:」 (新は「表示候補オッズ:」) を確認
        assert "- 取得済み:" in md
        # 新 layout の項目は出ない
        assert "表示候補オッズ:" not in md


# ---------------------------------------------------------------------------
# D. v1 legacy 経路: 旧 render_odds_coverage_section が動く
# ---------------------------------------------------------------------------


class TestScenarioD_V1Legacy:
    def test_legacy_function_still_works(self):
        """旧 render_odds_coverage_section は v1 legacy 経路用に残っており、
        OddsCoverage 入力で旧フォーマットを返す."""
        from app.output_validation import (
            OddsCoverage, render_odds_coverage_section,
        )
        cov = OddsCoverage(
            total=10, with_odds=5,
            honsen_total=3, honsen_with_odds=2,
            honsen_real_total=3, honsen_real_with_odds=2,
        )
        text = render_odds_coverage_section(cov)
        # 旧フォーマットが返る
        assert "### 候補買い目オッズ取得率" in text
        assert "- 取得済み: 5/10点" in text
        assert "- 本線オッズ取得済み: 2/3点" in text
        # 新 layout の項目は出ない (v1 legacy なので)
        assert "表示候補オッズ:" not in text
        assert "購入候補オッズ:" not in text

    def test_v1_render_prediction_uses_legacy_coverage(self):
        """v1 経路 (cli.py:render_prediction) でも旧 OddsCoverage 表示が
        出る (v2 必須化の影響を受けない)。"""
        # v1 経路は cli.render_prediction(prediction, input_data=...)
        # で呼ばれる。Renderer 内部で render_odds_coverage_section を直接
        # 呼んでいることを統合的に確認。
        from app.cli import render_prediction
        import json
        from pathlib import Path
        fixture = (
            Path(__file__).parent / "fixtures" / "shizuoka_7r_5_3_1.json"
        )
        ri = RaceInput(**json.loads(fixture.read_text(encoding="utf-8")))
        # 最小 prediction で v1 renderer を直接呼ぶ
        # (build_output_plan を経由しないので coverage_metrics は不要)
        pred = _prediction()
        # race_id を fixture と合わせる
        pred.race_id = ri.race.race_id
        md = render_prediction(pred, input_data=ri)
        # v1 では旧フォーマットが出る
        assert "### 候補買い目オッズ取得率" in md
        # 旧表示の項目 (本線オッズ取得済み: X/Y点) または取得済み: X/Y
        assert "- 取得済み:" in md or "本線オッズ取得済み" in md
