"""Phase 16 Step 5C (2026-05-26): DecisionEngine 異常時の fallback 安全性.

レビュー指摘 (Step 5B follow-up):
> coverage_metrics=None fallback時に旧warningが復活する可能性
> ユーザー向けには「この出力は旧診断表示にフォールバックしている」
> 「coverage/warningの整合性はv2通常時より弱い」を明示すべき

検証する 3 シナリオ:
A. coverage_metrics missing — 強化された警告 + purchase_mode cap +
   旧 MARKET_BIAS_NOT_COVERED の文言弱化
B. v1 legacy — fallback safety の影響を受けない
C. normal v2 — fallback safety が発動しない
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInfo, RaceInput, Rider,
)
from app.output_plan import OutputPlan, OutputPlanWarning


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


# ---------------------------------------------------------------------------
# A. coverage_metrics missing (v2 fallback)
# ---------------------------------------------------------------------------


class TestScenarioA_FallbackSafety:
    def _build(self):
        ri = _race_input([("1-2-3", 10.0)])
        bet = _bet("1-2-3", odds=10.0, value_label="本線向き")
        plan = OutputPlan(
            honsen=[bet], final_best=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        # 意図的に coverage_metrics を populate しない
        plan.coverage_metrics = None
        return plan, ri

    def test_warning_message_is_strong(self):
        """fallback 警告に「旧診断表示」「通常 v2 より弱い」「再確認」
        相当の文言が含まれる."""
        plan, ri = self._build()
        _render(plan, ri)
        target = next(
            (w for w in plan.warnings
             if w.code == "DECISION_ENGINE_NOT_POPULATED"),
            None,
        )
        assert target is not None
        msg = target.message
        assert "旧診断表示" in msg
        assert "通常 v2 より弱い" in msg
        assert "再確認" in msg

    def test_buyable_mode_is_capped_to_tentative(self):
        """BUYABLE は TENTATIVE に cap される."""
        plan, ri = self._build()
        _render(plan, ri)
        assert plan.purchase_mode == PurchaseMode.TENTATIVE

    def test_tentative_mode_stays_tentative(self):
        """TENTATIVE は cap 対象外でそのまま (既に慎重側)."""
        ri = _race_input([("1-2-3", 10.0)])
        bet = _bet("1-2-3", odds=10.0)
        plan = OutputPlan(
            honsen=[bet], final_best=[bet],
            purchase_mode=PurchaseMode.TENTATIVE,
        )
        plan.coverage_metrics = None
        _render(plan, ri)
        assert plan.purchase_mode == PurchaseMode.TENTATIVE

    def test_skip_mode_stays_skip(self):
        """SKIP も cap 対象外."""
        ri = _race_input([("1-2-3", 10.0)])
        plan = OutputPlan(
            purchase_mode=PurchaseMode.SKIP,
        )
        plan.coverage_metrics = None
        _render(plan, ri)
        assert plan.purchase_mode == PurchaseMode.SKIP

    def test_legacy_market_bias_warning_is_prefixed(self):
        """fallback 時に旧 MARKET_BIAS_NOT_COVERED が残っていれば
        「旧診断fallback中」prefix と「参考扱い」suffix が付く."""
        ri = _race_input([("1-2-3", 10.0)])
        plan = OutputPlan(
            purchase_mode=PurchaseMode.BUYABLE,
        )
        plan.coverage_metrics = None
        plan.warnings.append(OutputPlanWarning(
            code="MARKET_BIAS_NOT_COVERED",
            severity="warning",
            message="市場偏り(2番頭集中) に合うオッズ取得済み買い目がない",
        ))
        _render(plan, ri)
        target = next(
            (w for w in plan.warnings
             if w.code == "MARKET_BIAS_NOT_COVERED"),
            None,
        )
        assert target is not None
        assert target.message.startswith("[旧診断fallback中]")
        assert "参考扱い" in target.message

    def test_strong_purchase_phrases_not_in_body(self):
        """cap 後は本文に「一番買いたい / 購入対象」が出ない (TENTATIVE
        の文言になる)."""
        plan, ri = self._build()
        md = _render(plan, ri)
        # 「### 候補買い目オッズ取得率」より前の本文部分を抽出
        body = md.split("### 候補買い目オッズ取得率", 1)[0]
        # BUYABLE 専用の強い表現が出ない
        assert "一番買いたい" not in body
        assert "購入対象" not in body
        assert "実購入候補" not in body

    def test_idempotent_no_duplicate_warning(self):
        """fallback safety を 2 回呼んでも DECISION_ENGINE_NOT_POPULATED
        が 1 件だけ."""
        plan, ri = self._build()
        from app.markdown_renderer import (
            _apply_decision_engine_fallback_safety,
        )
        _apply_decision_engine_fallback_safety(plan)
        _apply_decision_engine_fallback_safety(plan)
        count = sum(
            1 for w in plan.warnings
            if w.code == "DECISION_ENGINE_NOT_POPULATED"
        )
        assert count == 1


# ---------------------------------------------------------------------------
# B. v1 legacy 経路は影響を受けない
# ---------------------------------------------------------------------------


class TestScenarioB_V1LegacyUnaffected:
    def test_v1_renderer_no_decision_engine_warning(self):
        """v1 (cli.py:render_prediction) では DECISION_ENGINE_NOT_POPULATED
        が出ない (Step 5C safety は v2 専用)."""
        from app.cli import render_prediction
        import json
        from pathlib import Path
        fixture = (
            Path(__file__).parent / "fixtures" / "shizuoka_7r_5_3_1.json"
        )
        ri = RaceInput(**json.loads(fixture.read_text(encoding="utf-8")))
        pred = _prediction()
        pred.race_id = ri.race.race_id
        md = render_prediction(pred, input_data=ri)
        assert "DECISION_ENGINE_NOT_POPULATED" not in md


# ---------------------------------------------------------------------------
# C. normal v2: fallback safety が発動しない
# ---------------------------------------------------------------------------


class TestScenarioC_NormalV2:
    def test_normal_v2_no_fallback_warning(self):
        """coverage_metrics が populate されていれば fallback warning は
        出ない / BUYABLE もそのまま維持."""
        ri = _race_input([("1-2-3", 10.0)])
        bet = _bet("1-2-3", odds=10.0, value_label="本線向き")
        plan = OutputPlan(
            honsen=[bet], final_best=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        # 正常な populate を実行
        from app.output_plan import _populate_decision_engine_data
        _populate_decision_engine_data(plan, _prediction(), ri)
        assert plan.coverage_metrics is not None
        _render(plan, ri)
        # fallback warning は出ない
        codes = [w.code for w in plan.warnings]
        assert "DECISION_ENGINE_NOT_POPULATED" not in codes
        # purchase_mode は cap されない
        assert plan.purchase_mode == PurchaseMode.BUYABLE
