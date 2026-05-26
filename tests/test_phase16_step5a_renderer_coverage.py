"""Phase 16 Step 5A (2026-05-26): Renderer の coverage 表示を lifecycle ベース
に切替えた効果を検証.

レビュー指摘:
> 静岡6R で「本文 6.1倍 / 末尾 0/8 (0%)」の矛盾
> SKIP なのに「購入対象」が残り PURCHASE_MODE_VIOLATION 誤発火
> MARKET_BIAS_NOT_COVERED が出るが、実際は watch_only にカバー候補がある
> data_quality の「オッズ」と purchase coverage の混同

検証する 4 シナリオ (A〜D):
A. 静岡6R 相当 (watch_only に 2-7-1 / final_best empty / SKIP)
B. gami_warning 候補 (2-5-7, 1.4倍)
C. purchase 候補あり (final_best に odds 付き / TENTATIVE)
D. data_quality のオッズラベル: 「オッズソース」と分離
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


def _render_md(plan: OutputPlan, input_data: RaceInput) -> str:
    from app.markdown_renderer import render_output_plan
    return render_output_plan(plan, _prediction(), input_data)


# ---------------------------------------------------------------------------
# A. 静岡6R 相当
# ---------------------------------------------------------------------------


class TestScenarioA_Shizuoka6R:
    """purchase_mode=SKIP / watch_only に 2-7-1 / final_best empty
    のとき、本文には表示候補オッズが 1件あり、購入候補オッズは「購入候補なし」
    と明示される。「購入対象」「実購入候補」「一番買いたい」が本文に出ない。"""

    def _build_plan(self):
        # 市場上位 5 件 (頭 2 集中、AxisBias 無し)
        odds_combos = [
            ("2-3-1", 5.5),
            ("2-1-3", 6.0),
            ("2-7-1", 6.1),
            ("3-1-2", 12.0),
            ("4-2-1", 15.0),
        ]
        ri = _race_input(odds_combos)
        bet_271 = _bet(
            "2-7-1", odds=6.1, value_label="見送り寄り",
            source_rules=["market_head", "market_pair"],
        )
        plan = OutputPlan(
            honsen_miokuri=[bet_271],
            purchase_mode=PurchaseMode.SKIP,
        )
        # Phase 16 populate を発動 (warnings 追加も走る)
        from app.output_plan import _populate_decision_engine_data
        _populate_decision_engine_data(plan, _prediction(), ri)
        return plan, ri

    def test_purchase_zero_shows_no_candidate(self):
        plan, ri = self._build_plan()
        md = _render_md(plan, ri)
        assert "### 候補買い目オッズ取得率" in md
        # 表示候補オッズ >= 1 (2-7-1 が odds 付きで表示にある)
        assert "表示候補オッズ: 1/1点" in md
        # 購入候補オッズ: 購入候補なし
        assert "購入候補オッズ: 購入候補なし" in md
        # 旧表示「オッズ取得済み: 0/8」だけは出さない
        assert "オッズ取得済み: 0/" not in md

    def test_no_forbidden_words_in_body(self):
        plan, ri = self._build_plan()
        md = _render_md(plan, ri)
        # 本文 (### 候補買い目オッズ取得率 より前) に禁止語が出ない
        body = md.split("### 候補買い目オッズ取得率", 1)[0]
        for word in ("購入対象", "実購入対象", "一番買いたい", "実購入候補"):
            assert word not in body, f"本文に禁止語「{word}」が残っている"

    def test_no_purchase_mode_violation(self):
        plan, ri = self._build_plan()
        md = _render_md(plan, ri)
        # PURCHASE_MODE_VIOLATION が出ない (Renderer 分岐が正しく動く)
        assert "[PURCHASE_MODE_VIOLATION]" not in md

    def test_market_bias_v2_emitted_not_legacy(self):
        plan, ri = self._build_plan()
        md = _render_md(plan, ri)
        # V2 が出ていれば legacy MARKET_BIAS_NOT_COVERED は出ない
        codes = [w.code for w in plan.warnings]
        assert (
            "MARKET_BIAS_WATCH_ONLY" in codes
            or "MARKET_BIAS_NOT_COVERED_V2" in codes
        )
        # legacy は plan.warnings から除外されているはず
        assert "MARKET_BIAS_NOT_COVERED" not in codes


# ---------------------------------------------------------------------------
# B. gami_warning 候補
# ---------------------------------------------------------------------------


class TestScenarioB_GamiWarning:
    """gami_warning に 2-5-7 (1.4倍) を入れる。ガミ注意候補オッズ で
    集計され、購入候補オッズには含まれない。"""

    def test_gami_only_appears_in_gami_section(self):
        odds_combos = [("2-5-7", 1.4)]
        ri = _race_input(odds_combos)
        bet = _bet(
            "2-5-7", odds=1.4, gami_risk=0.9, value_label="ガミ注意",
            source_rules=["gami_warning", "low_odds"],
        )
        plan = OutputPlan(
            gami_warning=[bet],
            purchase_mode=PurchaseMode.SKIP,
        )
        from app.output_plan import _populate_decision_engine_data
        _populate_decision_engine_data(plan, _prediction(), ri)

        md = _render_md(plan, ri)
        # ガミ注意候補オッズ: 1/1点 が出る
        assert "ガミ注意候補オッズ: 1/1点" in md
        # 購入候補オッズには含まれない
        assert "購入候補オッズ: 購入候補なし" in md


# ---------------------------------------------------------------------------
# C. purchase 候補あり (TENTATIVE)
# ---------------------------------------------------------------------------


class TestScenarioC_PurchaseCandidates:
    """final_best に odds 付き / purchase_mode=TENTATIVE のとき、
    購入候補オッズ と 表示候補オッズ 両方にカウントされる。"""

    def test_buyable_candidate_in_both_sections(self):
        bet = _bet("1-2-3", odds=12.0, value_label="本線向き",
                   source_rules=["market_head"])
        plan = OutputPlan(
            honsen=[bet],
            final_best=[bet],
            purchase_mode=PurchaseMode.TENTATIVE,
        )
        ri = _race_input([("1-2-3", 12.0)])
        from app.output_plan import _populate_decision_engine_data
        _populate_decision_engine_data(plan, _prediction(), ri)

        md = _render_md(plan, ri)
        # 購入候補オッズと表示候補オッズ両方に 1/1
        assert "表示候補オッズ: 1/1点" in md
        assert "購入候補オッズ: 1/1点" in md
        # 「購入候補なし」は出ない
        assert "購入候補オッズ: 購入候補なし" not in md


# ---------------------------------------------------------------------------
# D. data_quality のオッズラベル
# ---------------------------------------------------------------------------


class TestScenarioD_DataQualityOddsLabel:
    """data_quality 内訳の「オッズソース」と coverage の「購入候補オッズ」が
    混同しない表示."""

    def test_data_quality_uses_odds_source_label(self):
        from app.output_validation import DataQualityBreakdown
        bd = DataQualityBreakdown(
            score=True,
            odds=True,
            kimarite=True,
            recent=False,
            weather=True,
            overall="medium",
        )
        lines = bd.to_markdown_lines()
        joined = "\n".join(lines)
        assert "オッズソース" in joined
        # 旧ラベル「オッズ (1件以上)」は出ない
        assert "オッズ (1件以上)" not in joined

    def test_no_conflict_with_purchase_coverage_label(self):
        """coverage section の「購入候補オッズ」と data_quality の
        「オッズソース」が分かれて見える."""
        odds_combos = [("1-2-3", 10.0)]
        ri = _race_input(odds_combos)
        plan = OutputPlan(purchase_mode=PurchaseMode.SKIP)
        from app.output_plan import _populate_decision_engine_data
        _populate_decision_engine_data(plan, _prediction(), ri)
        md = _render_md(plan, ri)
        # data_quality 行と coverage 行が別セクションに出る
        assert "### データ品質" in md
        assert "オッズソース" in md  # data_quality 行
        assert "購入候補オッズ" in md  # coverage 行
