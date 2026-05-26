"""Phase 16 (2026-05-26): decision_engine の回帰テスト.

検証する 5 種類のブレ:
1. 候補状態のブレ — visible/display_bucket/decision_state の一貫性
2. coverage 母集団のブレ — display/purchase/market_bias coverage の分離
3. warning 判定のブレ — lifecycle ベースで MARKET_BIAS 3 段階化
4. race_type/policy のブレ — (Phase 15 までで対応済み)
5. 文言のブレ — GAMI_VS_HONSEN_MISMATCH 検出

対象 ID 事例:
- 静岡6R 2-7-1: 表示にあり / 購入には無い / 市場偏りに合う参考候補
- 平塚10R 3-4-7: ガミメモで(本線)だが lifecycle で watch_only
- 平塚4R: 同 combo が複数 bucket に出ない (BUCKET_DUPLICATE)
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode
from app.decision_engine import (
    CandidateLifecycle, Counts, CoverageMetrics,
    DECISION_STATE_BUYABLE, DECISION_STATE_GAMI_WARNING,
    DECISION_STATE_TENTATIVE, DECISION_STATE_WATCH_ONLY,
    DECISION_STATE_SKIP,
    DISPLAY_BUCKET_ANA, DISPLAY_BUCKET_DROPPED,
    DISPLAY_BUCKET_GAMI_WARNING, DISPLAY_BUCKET_HONSEN,
    DISPLAY_BUCKET_HONSEN_MIOKURI, DISPLAY_BUCKET_OSAE,
    DISPLAY_BUCKET_WATCH_ONLY,
    DiagCategory, Diagnostics,
    build_decision_engine_data, build_warnings_from_lifecycles,
)
from app.models import BetRecommendation, OddsEntry, RaceInfo, RaceInput, Rider
from app.output_plan import OutputPlan, OutputPlanWarning


# ---------------------------------------------------------------------------
# A. CandidateLifecycle dataclass
# ---------------------------------------------------------------------------


class TestCandidateLifecycleDataclass:
    def test_construction_and_flags(self):
        lc = CandidateLifecycle(
            combination="2-7-1",
            visible=True,
            display_bucket=DISPLAY_BUCKET_HONSEN_MIOKURI,
            decision_state=DECISION_STATE_WATCH_ONLY,
            market_odds=6.1,
            include_in_display_coverage=True,
            include_in_purchase_coverage=False,
            include_in_market_bias_coverage=True,
            source_rules=("market_head", "market_pair"),
        )
        assert lc.combination == "2-7-1"
        assert lc.has_odds is True
        assert lc.include_in_display_coverage
        assert not lc.include_in_purchase_coverage
        assert lc.include_in_market_bias_coverage

    def test_invalid_display_bucket_raises(self):
        with pytest.raises(ValueError):
            CandidateLifecycle(
                combination="1-2-3",
                visible=True,
                display_bucket="invalid_bucket",
                decision_state=DECISION_STATE_WATCH_ONLY,
            )

    def test_invalid_decision_state_raises(self):
        with pytest.raises(ValueError):
            CandidateLifecycle(
                combination="1-2-3",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN,
                decision_state="invalid_state",
            )

    def test_has_odds_false_when_none(self):
        lc = CandidateLifecycle(
            combination="1-2-3",
            visible=True,
            display_bucket=DISPLAY_BUCKET_HONSEN,
            decision_state=DECISION_STATE_WATCH_ONLY,
            market_odds=None,
        )
        assert lc.has_odds is False


# ---------------------------------------------------------------------------
# B. CoverageMetrics from_lifecycles
# ---------------------------------------------------------------------------


class TestCoverageMetrics:
    def test_separates_display_and_purchase(self):
        """静岡6R シナリオ: 表示 1 件 / 購入 0 件 / 市場人気 8 件 のとき、
        display=1/1, purchase=0/0, market_popular=8 が出る。"""
        lifecycles = [
            # 2-7-1: 表示候補で見送り寄り (display=True / purchase=False /
            # market_bias=True)
            CandidateLifecycle(
                combination="2-7-1",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN_MIOKURI,
                decision_state=DECISION_STATE_WATCH_ONLY,
                market_odds=6.1,
                include_in_display_coverage=True,
                include_in_purchase_coverage=False,
                include_in_market_bias_coverage=True,
            ),
        ]
        m = CoverageMetrics.from_lifecycles(
            lifecycles,
            market_popular_total=8,
            market_popular_by_bet_type={"3連単": 8},
        )
        assert m.display.total == 1
        assert m.display.with_odds == 1
        assert m.purchase.total == 0
        assert m.purchase.with_odds == 0
        assert m.market_bias.total == 1
        assert m.market_popular.total == 8

    def test_has_zero_purchase_with_market(self):
        """購入候補 0/N かつ市場人気 > 0 のとき True (旧 0/8 矛盾問題)."""
        lifecycles = [
            CandidateLifecycle(
                combination="2-7-1",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN_MIOKURI,
                decision_state=DECISION_STATE_WATCH_ONLY,
                market_odds=6.1,
                include_in_display_coverage=True,
                include_in_purchase_coverage=True,  # 購入対象だが odds 無い
                include_in_market_bias_coverage=False,
            ),
        ]
        # purchase total=1 with_odds=1 → has_zero_purchase_with_market=False
        m = CoverageMetrics.from_lifecycles(lifecycles, market_popular_total=8)
        assert m.has_zero_purchase_with_market() is False

        # purchase total=1 with_odds=0 + market_popular > 0 → True
        lifecycles[0].market_odds = None
        m = CoverageMetrics.from_lifecycles(lifecycles, market_popular_total=8)
        assert m.has_zero_purchase_with_market() is True

    def test_has_low_purchase_coverage(self):
        lifecycles = [
            CandidateLifecycle(
                combination=f"{i}-2-3",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN,
                decision_state=DECISION_STATE_BUYABLE,
                market_odds=10.0 if i == 1 else None,
                include_in_display_coverage=True,
                include_in_purchase_coverage=True,
                include_in_market_bias_coverage=False,
            )
            for i in range(1, 4)
        ]
        m = CoverageMetrics.from_lifecycles(lifecycles)
        # purchase 1/3 = 33% < 40% threshold
        assert m.has_low_purchase_coverage() is True
        assert m.has_low_purchase_coverage(threshold=0.30) is False


# ---------------------------------------------------------------------------
# C. Diagnostics
# ---------------------------------------------------------------------------


class TestDiagnostics:
    def test_add_and_get(self):
        d = Diagnostics()
        d.add(DiagCategory.WARNING, "msg1", code="W1", severity="warning")
        d.add(DiagCategory.MARK_ALIGNMENT, "note1")
        entries = d.get(DiagCategory.WARNING)
        assert len(entries) == 1
        assert entries[0].code == "W1"
        assert entries[0].severity == "warning"
        notes = d.get(DiagCategory.MARK_ALIGNMENT)
        assert len(notes) == 1
        assert notes[0].severity == "info"

    def test_warnings_only(self):
        d = Diagnostics()
        d.add(DiagCategory.WARNING, "w1", severity="warning")
        d.add(DiagCategory.WARNING, "w2", severity="error")
        d.add(DiagCategory.MARK_ALIGNMENT, "note", severity="info")
        warnings = d.warnings_only()
        assert len(warnings) == 2

    def test_is_empty(self):
        d = Diagnostics()
        assert d.is_empty()
        d.add(DiagCategory.WARNING, "x")
        assert not d.is_empty()


# ---------------------------------------------------------------------------
# D. build_decision_engine_data: OutputPlan → lifecycle スナップショット
# ---------------------------------------------------------------------------


def _minimal_input(odds_count: int = 5) -> RaceInput:
    return RaceInput(
        race=RaceInfo(
            race_id="20260526-test-1",
            date="2026-05-26",
            venue="test",
            race_no=1,
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
            OddsEntry(bet_type="3連単", combination=f"{i+1}-{(i+1)%7+1}-{(i+2)%7+1}", odds=5.0 + i)
            for i in range(odds_count)
        ],
    )


def _minimal_prediction():
    from app.models import Prediction
    return Prediction(
        race_id="20260526-test-1",
        venue="test", race_no=1, is_girls=False, marks={},
        summary="t", weather_text="t", lines_text="t", venue_trend_text="t",
        honsen=[], osae=[], ana=[], ooana=[],
        final_conclusion="",
        gami_memo="",
        reflection_points=[],
    )


class TestBuildDecisionEngineData:
    def test_empty_plan_yields_empty_lifecycle(self):
        plan = OutputPlan()
        prediction = _minimal_prediction()
        input_data = _minimal_input()
        lifecycles, metrics, diag = build_decision_engine_data(
            plan, prediction, input_data,
        )
        assert lifecycles == []
        assert metrics.display.total == 0
        assert metrics.market_popular.total == 5
        assert diag.is_empty()

    def test_honsen_with_odds_becomes_display_and_purchase_coverage(self):
        """honsen + final_best に居て odds 取得済み → display/purchase 両方
        coverage=True、decision_state=BUYABLE."""
        bet = BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.0, market_odds=10.0,
        )
        plan = OutputPlan(
            honsen=[bet], final_best=[bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        lifecycles, metrics, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert len(lifecycles) == 1
        lc = lifecycles[0]
        assert lc.combination == "1-2-3"
        assert lc.display_bucket == DISPLAY_BUCKET_HONSEN
        assert lc.decision_state == DECISION_STATE_BUYABLE
        assert lc.include_in_display_coverage
        assert lc.include_in_purchase_coverage
        assert lc.is_final_best

    def test_honsen_miokuri_becomes_watch_only_state(self):
        """静岡6R シナリオ: 2-7-1 が honsen_miokuri に居て odds=6.1 のとき、
        display=True / purchase=False / state=watch_only になる."""
        bet = BetRecommendation(
            category="本線", bet_type="3連単", combination="2-7-1",
            reason="t", gami_risk=0.0, market_odds=6.1,
            value_label="見送り寄り",
        )
        plan = OutputPlan(
            honsen_miokuri=[bet],
            purchase_mode=PurchaseMode.SKIP,
        )
        lifecycles, metrics, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert len(lifecycles) == 1
        lc = lifecycles[0]
        assert lc.display_bucket == DISPLAY_BUCKET_HONSEN_MIOKURI
        assert lc.decision_state == DECISION_STATE_WATCH_ONLY
        assert lc.include_in_display_coverage is True
        assert lc.include_in_purchase_coverage is False
        # 0/0 + market>0 では矛盾は出ない (purchase total が 0)
        assert metrics.purchase.total == 0
        assert metrics.display.total == 1
        assert metrics.display.with_odds == 1

    def test_gami_warning_state(self):
        """gami_warning bucket → state=GAMI_WARNING."""
        bet = BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.9, market_odds=3.0,
        )
        plan = OutputPlan(gami_warning=[bet])
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert lifecycles[0].decision_state == DECISION_STATE_GAMI_WARNING

    def test_skip_mode_state(self):
        """purchase_mode=SKIP のとき、final_best にある candidate は SKIP state."""
        bet = BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.0, market_odds=10.0,
        )
        plan = OutputPlan(
            honsen=[bet], final_best=[bet],
            purchase_mode=PurchaseMode.SKIP,
        )
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert lifecycles[0].decision_state == DECISION_STATE_SKIP


# ---------------------------------------------------------------------------
# E. WarningEngine
# ---------------------------------------------------------------------------


class TestWarningEngine:
    def test_market_bias_purchase_covered_no_warning(self):
        """市場偏り頭が購入候補にある → 警告なし (info も出さない)."""
        lifecycles = [
            CandidateLifecycle(
                combination="2-3-1",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN,
                decision_state=DECISION_STATE_BUYABLE,
                market_odds=8.0,
                include_in_display_coverage=True,
                include_in_purchase_coverage=True,
                include_in_market_bias_coverage=True,
            ),
        ]
        warnings = build_warnings_from_lifecycles(lifecycles)
        codes = [w.code for w in warnings]
        assert "MARKET_BIAS_WATCH_ONLY" not in codes
        assert "MARKET_BIAS_NOT_COVERED_V2" not in codes

    def test_market_bias_watch_only(self):
        """市場偏り頭が表示にあるが購入候補に無い → MARKET_BIAS_WATCH_ONLY
        (info)."""
        lifecycles = [
            CandidateLifecycle(
                combination="2-7-1",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN_MIOKURI,
                decision_state=DECISION_STATE_WATCH_ONLY,
                market_odds=6.1,
                include_in_display_coverage=True,
                include_in_purchase_coverage=False,
                include_in_market_bias_coverage=True,
            ),
        ]
        warnings = build_warnings_from_lifecycles(lifecycles)
        codes = [w.code for w in warnings]
        assert "MARKET_BIAS_WATCH_ONLY" in codes
        # severity=info (warning ではない)
        for w in warnings:
            if w.code == "MARKET_BIAS_WATCH_ONLY":
                assert w.severity == "info"

    def test_market_bias_not_covered_v2(self):
        """市場偏り頭が表示にも無い (= 全部 dropped) → NOT_COVERED_V2."""
        lifecycles = [
            CandidateLifecycle(
                combination="2-7-1",
                visible=False,
                display_bucket=DISPLAY_BUCKET_DROPPED,
                decision_state=DECISION_STATE_WATCH_ONLY,
                market_odds=6.1,
                include_in_display_coverage=False,
                include_in_purchase_coverage=False,
                include_in_market_bias_coverage=True,
            ),
        ]
        warnings = build_warnings_from_lifecycles(lifecycles)
        codes = [w.code for w in warnings]
        assert "MARKET_BIAS_NOT_COVERED_V2" in codes

    def test_no_market_bias_no_warning(self):
        """include_in_market_bias_coverage=True が 1 件も無いなら判定対象外."""
        lifecycles = [
            CandidateLifecycle(
                combination="1-2-3",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN,
                decision_state=DECISION_STATE_BUYABLE,
                market_odds=10.0,
                include_in_display_coverage=True,
                include_in_purchase_coverage=True,
                include_in_market_bias_coverage=False,
            ),
        ]
        warnings = build_warnings_from_lifecycles(lifecycles)
        codes = [w.code for w in warnings]
        assert "MARKET_BIAS_WATCH_ONLY" not in codes
        assert "MARKET_BIAS_NOT_COVERED_V2" not in codes

    def test_gami_vs_honsen_mismatch(self):
        """gami_memo の (本線) ラベルと lifecycle.decision_state が不一致な
        ら GAMI_VS_HONSEN_MISMATCH 警告."""
        lifecycles = [
            CandidateLifecycle(
                combination="3-4-7",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN_MIOKURI,
                decision_state=DECISION_STATE_WATCH_ONLY,
                market_odds=3.5,
                include_in_display_coverage=True,
            ),
        ]
        gami_memo = "- 3-4-7(本線): オッズ安め、ガミ警戒"
        warnings = build_warnings_from_lifecycles(
            lifecycles, gami_memo=gami_memo,
        )
        codes = [w.code for w in warnings]
        assert "GAMI_VS_HONSEN_MISMATCH" in codes

    def test_gami_vs_honsen_no_mismatch_when_state_matches(self):
        """state=BUYABLE で「(本線)」と書かれていれば mismatch ではない."""
        lifecycles = [
            CandidateLifecycle(
                combination="3-4-7",
                visible=True,
                display_bucket=DISPLAY_BUCKET_HONSEN,
                decision_state=DECISION_STATE_BUYABLE,
                market_odds=10.0,
                include_in_display_coverage=True,
                include_in_purchase_coverage=True,
            ),
        ]
        gami_memo = "- 3-4-7(本線): オッズ安め"
        warnings = build_warnings_from_lifecycles(
            lifecycles, gami_memo=gami_memo,
        )
        codes = [w.code for w in warnings]
        assert "GAMI_VS_HONSEN_MISMATCH" not in codes


# ---------------------------------------------------------------------------
# F. OutputPlan integration: build_output_plan が lifecycle/coverage/diag を
#    populate する
# ---------------------------------------------------------------------------


class TestOutputPlanPopulate:
    def test_build_output_plan_populates_lifecycle(self):
        """build_output_plan が plan.lifecycles を populate する."""
        import json
        from pathlib import Path
        from app.scoring import (
            apply_market_signals, build_candidate_bets, compute_scores,
        )
        from app.llm_client import MockLLMClient
        from app.value_analysis import (
            annotate_prediction_with_value, promote_oddful_to_honsen,
            promote_oddful_to_osae,
        )
        from app.output_plan import build_output_plan

        fixture = Path(__file__).parent / "fixtures" / "shizuoka_7r_5_3_1.json"
        ri = RaceInput(**json.loads(fixture.read_text(encoding="utf-8")))
        scores = compute_scores(ri)
        apply_market_signals(scores, ri.odds)
        bets = build_candidate_bets(ri, scores)
        client = MockLLMClient()
        pred = client.generate_prediction(ri, scores, bets, "")
        annotate_prediction_with_value(pred, scores, ri.odds)
        promote_oddful_to_osae(pred)
        promote_oddful_to_honsen(pred)
        plan = build_output_plan(pred, ri)

        # lifecycles が populate されている
        assert len(plan.lifecycles) > 0
        # coverage_metrics も populate
        assert plan.coverage_metrics is not None
        # diagnostics も populate
        assert plan.diagnostics is not None

        # market_popular = input_data.odds 件数
        assert plan.coverage_metrics.market_popular.total == len(ri.odds)

    def test_no_bucket_duplicate_in_normal_pipeline(self):
        """通常パイプラインで BUCKET_DUPLICATE が出ないことを確認."""
        import json
        from pathlib import Path
        from app.scoring import (
            apply_market_signals, build_candidate_bets, compute_scores,
        )
        from app.llm_client import MockLLMClient
        from app.value_analysis import (
            annotate_prediction_with_value, promote_oddful_to_honsen,
            promote_oddful_to_osae,
        )
        from app.output_plan import build_output_plan

        fixture = Path(__file__).parent / "fixtures" / "shizuoka_7r_5_3_1.json"
        ri = RaceInput(**json.loads(fixture.read_text(encoding="utf-8")))
        scores = compute_scores(ri)
        apply_market_signals(scores, ri.odds)
        bets = build_candidate_bets(ri, scores)
        client = MockLLMClient()
        pred = client.generate_prediction(ri, scores, bets, "")
        annotate_prediction_with_value(pred, scores, ri.odds)
        promote_oddful_to_osae(pred)
        promote_oddful_to_honsen(pred)
        plan = build_output_plan(pred, ri)

        codes = [w.code for w in plan.warnings]
        assert "BUCKET_DUPLICATE" not in codes


# ---------------------------------------------------------------------------
# G. Renderer 新 layout: Step 5A で旧 OddsCoverage と互換ではなく、
#    candidate state 別の独立した表示に切り替わる
# ---------------------------------------------------------------------------


class TestRendererStep5ALayout:
    def test_new_layout_has_candidate_categories(self):
        """新 layout は「表示候補オッズ / 購入候補オッズ / 本線表示候補
        オッズ / (参考候補オッズ) / (ガミ注意候補オッズ)」のような
        candidate state 別の項目を持つ."""
        from app.output_validation import render_coverage_metrics_section
        metrics = CoverageMetrics()
        metrics.display = Counts(total=16, with_odds=5)
        metrics.purchase = Counts(total=4, with_odds=4)
        metrics.honsen_real = Counts(total=3, with_odds=3)
        new_text = render_coverage_metrics_section(metrics)
        assert "### 候補買い目オッズ取得率" in new_text
        assert "表示候補オッズ: 5/16点" in new_text
        assert "購入候補オッズ: 4/4点" in new_text
        assert "本線表示候補オッズ: 3/3点" in new_text

    def test_purchase_zero_shows_no_candidate_message(self):
        """purchase.total=0 のとき「購入候補なし」と明示."""
        from app.output_validation import render_coverage_metrics_section
        metrics = CoverageMetrics()
        metrics.display = Counts(total=1, with_odds=1)
        metrics.purchase = Counts(total=0, with_odds=0)
        text = render_coverage_metrics_section(metrics)
        assert "購入候補オッズ: 購入候補なし" in text
        # 数値 0/0 のような誤解を生む表現は出さない
        assert "0/0" not in text
        # 警告も出す
        assert "購入候補なし" in text
