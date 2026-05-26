"""Phase 16 follow-up (2026-05-26): レビュー指摘への対応テスト.

検証する 4 問題:
P1. honsen + gami_warning 同一 combo → decision_state=GAMI_WARNING
P1. final_best + watch_only 同一 combo → decision_state=WATCH_ONLY
P1. source_rules merge (union) / value_label 慎重側 / gami_risk max
P2. MarketBias: HeadBias と AxisBias で coverage 判定が分かれる
P2. BUCKET_DUPLICATE が bucket_memberships ベース

追加テスト:
- merge_value_label helper
- 静岡6R 相当 (watch_only に 2-7-1 / final_best empty)
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode
from app.decision_engine import (
    CandidateLifecycle,
    DECISION_STATE_BUYABLE, DECISION_STATE_GAMI_WARNING,
    DECISION_STATE_TENTATIVE, DECISION_STATE_WATCH_ONLY,
    DISPLAY_BUCKET_ANA, DISPLAY_BUCKET_GAMI_WARNING,
    DISPLAY_BUCKET_HONSEN, DISPLAY_BUCKET_HONSEN_MIOKURI,
    DISPLAY_BUCKET_OSAE, DISPLAY_BUCKET_WATCH_ONLY,
    build_decision_engine_data, build_warnings_from_lifecycles,
    merge_value_label,
)
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInfo, RaceInput, Rider,
)
from app.output_plan import OutputPlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bet(
    combo: str,
    *,
    odds=None,
    value_label=None,
    gami_risk=0.0,
    source_rules=None,
    category="本線",
) -> BetRecommendation:
    return BetRecommendation(
        category=category,
        bet_type="3連単",
        combination=combo,
        reason="t",
        gami_risk=gami_risk,
        market_odds=odds,
        value_label=value_label,
        source_rules=tuple(source_rules or ()),
    )


def _minimal_input(*, odds_combos=None) -> RaceInput:
    """市場人気 odds を組み入れた最小 RaceInput."""
    odds_combos = odds_combos or []
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
            OddsEntry(bet_type="3連単", combination=c, odds=o)
            for c, o in odds_combos
        ],
    )


def _minimal_prediction() -> Prediction:
    return Prediction(
        race_id="20260526-test-1",
        venue="test", race_no=1, is_girls=False, marks={},
        summary="t", weather_text="t", lines_text="t", venue_trend_text="t",
        honsen=[], osae=[], ana=[], ooana=[],
        final_conclusion="",
        gami_memo="",
        reflection_points=[],
    )


# ---------------------------------------------------------------------------
# 1. merge_value_label helper
# ---------------------------------------------------------------------------


class TestMergeValueLabel:
    def test_returns_more_conservative(self):
        # 見送り寄り > 妙味あり
        assert merge_value_label("妙味あり", "見送り寄り") == "見送り寄り"
        assert merge_value_label("見送り寄り", "妙味あり") == "見送り寄り"

    def test_returns_non_none_side(self):
        assert merge_value_label(None, "本線向き") == "本線向き"
        assert merge_value_label("ガミ注意", None) == "ガミ注意"

    def test_both_none(self):
        assert merge_value_label(None, None) is None

    def test_unknown_label_falls_back_to_zero(self):
        # 未知ラベルは 0 扱い、既知ラベルが優先
        assert merge_value_label("unknown", "見送り寄り") == "見送り寄り"


# ---------------------------------------------------------------------------
# 2. P1: honsen + gami_warning 同一 combo → state=GAMI_WARNING
# ---------------------------------------------------------------------------


class TestHonsenAndGamiWarningCollision:
    def test_state_becomes_gami_warning(self):
        """同 combo が honsen と gami_warning の両方にある場合、
        decision_state=GAMI_WARNING に倒れる (display_bucket は honsen でも
        OK だが、state は慎重側)."""
        # honsen 側: 本線向き
        honsen_bet = _bet(
            "1-2-3", odds=4.5, value_label="本線向き",
            source_rules=["market_head"], category="本線",
        )
        # gami_warning 側: 同じ combo に gami_warning タグ
        # (BetRecommendation の category は Literal なので「本線」のまま)
        gami_bet = _bet(
            "1-2-3", odds=4.5, value_label="ガミ注意", gami_risk=0.9,
            source_rules=["gami_warning", "low_odds"],
        )
        plan = OutputPlan(
            honsen=[honsen_bet],
            gami_warning=[gami_bet],
            final_best=[honsen_bet],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        # 1 lifecycle に merge されている
        assert len(lifecycles) == 1
        lc = lifecycles[0]
        # state は慎重側
        assert lc.decision_state == DECISION_STATE_GAMI_WARNING
        # source_rules は union
        assert "market_head" in lc.source_rules
        assert "gami_warning" in lc.source_rules
        assert "low_odds" in lc.source_rules
        # bucket_memberships は両方
        assert "honsen" in lc.bucket_memberships
        assert "gami_warning" in lc.bucket_memberships
        # purchase coverage には入らない (GAMI_WARNING は購入対象外)
        assert lc.include_in_purchase_coverage is False


# ---------------------------------------------------------------------------
# 3. P1: final_best + watch_only 同一 combo → state=WATCH_ONLY
# ---------------------------------------------------------------------------


class TestFinalBestAndWatchOnlyCollision:
    def test_state_becomes_watch_only(self):
        """final_best にも入っているが watch_only にも居る combo は、
        慎重側 (WATCH_ONLY) に倒れる."""
        bet_final = _bet(
            "1-2-3", odds=10.0, value_label="本線向き",
            source_rules=["market_head"],
        )
        bet_watch = _bet(
            "1-2-3", odds=10.0, value_label="見送り寄り",
            source_rules=["watch_only"],
        )
        plan = OutputPlan(
            final_best=[bet_final],
            honsen=[bet_final],
            watch_only=[bet_watch],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert len(lifecycles) == 1
        lc = lifecycles[0]
        # state は WATCH_ONLY (見送り寄り label or watch_only bucket)
        assert lc.decision_state == DECISION_STATE_WATCH_ONLY
        assert lc.include_in_purchase_coverage is False


# ---------------------------------------------------------------------------
# 4. P1: source_rules merge (union)
# ---------------------------------------------------------------------------


class TestSourceRulesMerge:
    def test_union_across_buckets(self):
        """同 combo が複数 bucket にあるとき source_rules は union."""
        b1 = _bet(
            "5-1-3", odds=12.0,
            source_rules=["line_direct", "market_head"],
        )
        b2 = _bet(
            "5-1-3", odds=12.0,
            source_rules=["market_pair", "individual"],
        )
        plan = OutputPlan(
            honsen=[b1], osae=[b2],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert len(lifecycles) == 1
        rules = set(lifecycles[0].source_rules)
        assert rules == {
            "line_direct", "market_head", "market_pair", "individual",
        }

    def test_gami_risk_max(self):
        """gami_risk は max."""
        b1 = _bet("1-2-3", odds=10.0, gami_risk=0.3)
        b2 = _bet("1-2-3", odds=10.0, gami_risk=0.7)
        plan = OutputPlan(honsen=[b1], ana=[b2])
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert lifecycles[0].gami_risk == pytest.approx(0.7)

    def test_market_odds_any_non_null(self):
        """odds は最初に見つかった non-null を保持."""
        b1 = _bet("1-2-3", odds=None)
        b2 = _bet("1-2-3", odds=15.0)
        plan = OutputPlan(honsen=[b1], ana=[b2])
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert lifecycles[0].market_odds == 15.0


# ---------------------------------------------------------------------------
# 5. P2: bucket_memberships duplicate → BUCKET_DUPLICATE warning
# ---------------------------------------------------------------------------


class TestBucketMembershipsDuplicate:
    def test_honsen_and_ana_triggers_warning(self):
        """同 combo が honsen と ana の両方にあると BUCKET_DUPLICATE が出る."""
        bet = _bet("1-2-3", odds=10.0)
        plan = OutputPlan(honsen=[bet], ana=[bet])
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        # bucket_memberships に honsen と ana 両方が記録される
        assert lifecycles[0].bucket_memberships == frozenset({"honsen", "ana"})
        # WarningEngine が BUCKET_DUPLICATE を生成
        warnings = build_warnings_from_lifecycles(lifecycles)
        codes = [w.code for w in warnings]
        assert "BUCKET_DUPLICATE" in codes

    def test_gami_and_watch_only_intentional_no_warning(self):
        """gami_warning と watch_only の同時所属は意図的 (Phase 13)。
        BUCKET_DUPLICATE 警告を出さない."""
        bet_g = _bet(
            "1-2-3", odds=3.0, gami_risk=0.9,
            source_rules=["gami_warning"],
        )
        bet_w = _bet(
            "1-2-3", odds=3.0,
            source_rules=["watch_only"],
        )
        plan = OutputPlan(gami_warning=[bet_g], watch_only=[bet_w])
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert lifecycles[0].bucket_memberships == frozenset(
            {"gami_warning", "watch_only"}
        )
        warnings = build_warnings_from_lifecycles(lifecycles)
        codes = [w.code for w in warnings]
        assert "BUCKET_DUPLICATE" not in codes

    def test_single_bucket_no_warning(self):
        bet = _bet("1-2-3", odds=10.0)
        plan = OutputPlan(honsen=[bet])
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), _minimal_input(),
        )
        warnings = build_warnings_from_lifecycles(lifecycles)
        codes = [w.code for w in warnings]
        assert "BUCKET_DUPLICATE" not in codes


# ---------------------------------------------------------------------------
# 6. P2: MarketBias coverage bias_type 別判定
# ---------------------------------------------------------------------------


class TestMarketBiasCoverageBiasType:
    def test_axis_bias_only_matches_axis(self):
        """AxisBias=2-5 のとき、2-7-1 は coverage=False、2-5-1 は True."""
        # AxisBias を作るには、上位 5 件のうち 3 件以上が同じ (head, second)
        # で揃う必要がある。3 連単 odds に 2-5-* を 3 件入れる。
        odds_combos = [
            ("2-5-1", 5.0),
            ("2-5-3", 6.0),
            ("2-5-7", 7.0),
            ("3-1-2", 8.0),
            ("4-2-1", 9.0),
        ]
        ri = _minimal_input(odds_combos=odds_combos)

        # bet1: 2-7-1 (axis に一致しない、頭だけ一致)
        bet_no_match = _bet("2-7-1", odds=10.0)
        # bet2: 2-5-7 (axis に一致)
        bet_match = _bet("2-5-7", odds=12.0)
        plan = OutputPlan(honsen=[bet_no_match, bet_match])

        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), ri,
        )
        by_combo = {lc.combination: lc for lc in lifecycles}
        # AxisBias なので 2-7-1 は coverage=False
        assert by_combo["2-7-1"].include_in_market_bias_coverage is False
        assert by_combo["2-7-1"].market_bias_match_type is None
        # 2-5-7 は coverage=True
        assert by_combo["2-5-7"].include_in_market_bias_coverage is True
        assert by_combo["2-5-7"].market_bias_match_type == "axis"

    def test_head_bias_only_matches_head(self):
        """HeadBias=2 (axis なし) のとき、頭 2 は coverage=True."""
        # 上位 5 件のうち頭 2 が 3 件以上、ただし axis (head, second) は
        # 揃わないようにする
        odds_combos = [
            ("2-1-3", 5.0),
            ("2-3-4", 6.0),
            ("2-7-5", 7.0),
            ("3-1-2", 8.0),
            ("4-1-2", 9.0),
        ]
        ri = _minimal_input(odds_combos=odds_combos)

        bet_head = _bet("2-6-7", odds=15.0)
        bet_other = _bet("5-1-2", odds=20.0)
        plan = OutputPlan(honsen=[bet_head, bet_other])

        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), ri,
        )
        by_combo = {lc.combination: lc for lc in lifecycles}
        # 頭 2 は coverage=True (HeadBias)
        assert by_combo["2-6-7"].include_in_market_bias_coverage is True
        assert by_combo["2-6-7"].market_bias_match_type == "head"
        # 頭 5 は coverage=False
        assert by_combo["5-1-2"].include_in_market_bias_coverage is False

    def test_no_bias_no_coverage(self):
        """market bias 無し (odds なし) のとき、coverage は全部 False."""
        ri = _minimal_input(odds_combos=[])
        bet = _bet("1-2-3", odds=10.0)
        plan = OutputPlan(honsen=[bet])
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), ri,
        )
        assert lifecycles[0].include_in_market_bias_coverage is False
        assert lifecycles[0].market_bias_match_type is None


# ---------------------------------------------------------------------------
# 7. 静岡6R 相当: watch_only に 2-7-1 (6.1倍) / final_best empty
# ---------------------------------------------------------------------------


class TestShizuokaR6Scenario:
    def test_2_7_1_in_honsen_miokuri_only(self):
        """2-7-1 が honsen_miokuri に居て、final_best が空のとき:
        - display coverage=True
        - purchase coverage=False
        - market_bias coverage=True (頭 2 が市場集中)
        - decision_state=WATCH_ONLY
        """
        # 市場上位: 頭 2 集中 (HeadBias)
        odds_combos = [
            ("2-3-1", 5.0),
            ("2-1-3", 6.0),
            ("2-7-1", 6.1),
            ("3-1-2", 12.0),
            ("4-2-1", 15.0),
        ]
        ri = _minimal_input(odds_combos=odds_combos)
        bet_271 = _bet(
            "2-7-1", odds=6.1, value_label="見送り寄り",
            source_rules=["market_head", "market_pair"],
        )
        plan = OutputPlan(
            honsen_miokuri=[bet_271],
            purchase_mode=PurchaseMode.SKIP,
        )
        lifecycles, metrics, _ = build_decision_engine_data(
            plan, _minimal_prediction(), ri,
        )
        assert len(lifecycles) == 1
        lc = lifecycles[0]
        assert lc.combination == "2-7-1"
        assert lc.display_bucket == DISPLAY_BUCKET_HONSEN_MIOKURI
        assert lc.decision_state == DECISION_STATE_WATCH_ONLY
        assert lc.include_in_display_coverage is True
        assert lc.include_in_purchase_coverage is False
        # 頭 2 が市場集中 → market_bias coverage=True
        # ただしテスト odds 構成が axis (2-3 など) を含んでいるので、
        # axis_count >= 3 なら axis 判定。本シナリオは axis なし
        # (head 一致のみ) なので head 判定にしたい。
        # 上の構成: 2-3-1, 2-1-3, 2-7-1, 3-1-2, 4-2-1 → axis は 2-3 が 1件、
        # 2-1 が 1件、2-7 が 1件 で axis_count < 3 → HeadBias のみ
        assert lc.include_in_market_bias_coverage is True
        assert lc.market_bias_match_type == "head"

        # coverage: display 1/1, purchase 0/0, market_bias 1/1
        assert metrics.display.total == 1
        assert metrics.display.with_odds == 1
        assert metrics.purchase.total == 0
        # 候補側 0/0 + 市場人気 5 件 → 矛盾には見えない (purchase total=0)
        assert metrics.market_popular.total == 5
        assert metrics.has_zero_purchase_with_market() is False

    def test_market_bias_watch_only_warning(self):
        """同シナリオで build_warnings_from_lifecycles を呼ぶと
        MARKET_BIAS_WATCH_ONLY が出る (purchase に無く display にはある)."""
        odds_combos = [
            ("2-3-1", 5.0),
            ("2-1-3", 6.0),
            ("2-7-1", 6.1),
            ("3-1-2", 12.0),
            ("4-2-1", 15.0),
        ]
        ri = _minimal_input(odds_combos=odds_combos)
        bet_271 = _bet(
            "2-7-1", odds=6.1, value_label="見送り寄り",
            source_rules=["market_head"],
        )
        plan = OutputPlan(
            honsen_miokuri=[bet_271],
            purchase_mode=PurchaseMode.SKIP,
        )
        lifecycles, _, _ = build_decision_engine_data(
            plan, _minimal_prediction(), ri,
        )
        warnings = build_warnings_from_lifecycles(lifecycles)
        codes = [w.code for w in warnings]
        assert "MARKET_BIAS_WATCH_ONLY" in codes
