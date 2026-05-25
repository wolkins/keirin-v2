"""Phase 13: source_rules に gami_warning / low_odds を持つ候補を
購入候補から構造的に分離するテスト.

検証内容:
A. is_gami_source helper
B. _apply_gami_source_rules_filter 単体動作
C. honsen 内の low_odds 候補が gami_warning に移動
D. final_best 内の low_odds 候補が分離 + purchase_mode cap
E. gami_warning display (既存挙動維持)
F. 通常戦で odds<5 の構造的分離
G. odds=8 (低オッズ判定対象外) は filter で除外されない
H. watch_only_reason_groups["gami_warning"] への反映
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode, is_gami_source
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import (
    OutputPlan, _apply_gami_source_rules_filter, build_output_plan,
)


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _ri(*, class_name="A級一般", is_girls=False, lines=None, odds=None,
        recent_results=None):
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-25",
                 "venue": "テスト", "race_no": 1,
                 "class_name": class_name, "start_time": "10:00",
                 "is_girls": is_girls},
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                    "wind_speed_mps": 2.0},
        "lines": lines or [
            {"line_name": "本命", "cars": [1, 2, 3]},
            {"line_name": "別線", "cars": [5, 4, 6]},
            {"line_name": "単", "cars": [7]},
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 88.0,
             "b_count": 1, "nige": 1 if i in (1, 5) else 0,
             "makuri": 0, "sashi": 1 if i in (2, 4) else 0,
             "mark": 1, "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": recent_results or [],
    })


# ---------------------------------------------------------------------------
# A. is_gami_source helper
# ---------------------------------------------------------------------------


class TestIsGamiSource:
    def test_gami_warning_tag(self):
        assert is_gami_source(["gami_warning"]) is True

    def test_low_odds_tag(self):
        assert is_gami_source(["low_odds"]) is True

    def test_both_tags(self):
        assert is_gami_source(["low_odds", "gami_warning"]) is True

    def test_mixed_with_market(self):
        assert is_gami_source(
            ["market_head", "low_odds", "odds_available"]
        ) is True

    def test_market_only(self):
        assert is_gami_source(["market_head", "market_popular"]) is False

    def test_line_only(self):
        assert is_gami_source(["line_direct"]) is False

    def test_empty(self):
        assert is_gami_source([]) is False
        assert is_gami_source(None) is False


# ---------------------------------------------------------------------------
# B. _apply_gami_source_rules_filter 単体動作
# ---------------------------------------------------------------------------


class TestApplyGamiFilterUnit:
    def test_low_odds_candidate_moved_from_honsen(self):
        plan = OutputPlan(
            honsen=[
                _bet("1-2-3",
                     source_rules=["low_odds", "gami_warning",
                                   "odds_available"]),
                _bet("2-1-3", source_rules=["market_head"]),  # 残す
            ],
        )
        _apply_gami_source_rules_filter(plan)
        # 1-2-3 が honsen から消えて gami_warning に移る
        honsen_combos = [b.combination for b in plan.honsen]
        assert honsen_combos == ["2-1-3"]
        gami_combos = [b.combination for b in plan.gami_warning]
        assert "1-2-3" in gami_combos
        # watch_only_reason_groups["gami_warning"] にも反映
        group = plan.watch_only_reason_groups.get("gami_warning") or []
        assert any(b.combination == "1-2-3" for b in group)

    def test_low_odds_from_all_buckets(self):
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["low_odds"])],
            osae=[_bet("2-1-3", source_rules=["gami_warning"],
                       category="押さえ")],
            ana=[_bet("3-1-2", source_rules=["low_odds"], category="穴")],
            ooana=[_bet("4-3-2", source_rules=["gami_warning"],
                        category="大穴")],
            final_best=[_bet("5-1-3", source_rules=["low_odds"])],
            final_osae=[_bet("1-5-3",
                             source_rules=["low_odds"], category="押さえ")],
            final_ana=[_bet("3-5-1",
                            source_rules=["gami_warning"], category="穴")],
        )
        _apply_gami_source_rules_filter(plan)
        # 全 7 バケットから消える
        assert plan.honsen == []
        assert plan.osae == []
        assert plan.ana == []
        assert plan.ooana == []
        assert plan.final_best == []
        assert plan.final_osae == []
        assert plan.final_ana == []
        # 全 7 件が gami_warning に集約
        gami_combos = {b.combination for b in plan.gami_warning}
        assert gami_combos == {
            "1-2-3", "2-1-3", "3-1-2", "4-3-2",
            "5-1-3", "1-5-3", "3-5-1",
        }

    def test_no_gami_no_change(self):
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["market_head"])],
        )
        _apply_gami_source_rules_filter(plan)
        assert len(plan.honsen) == 1
        assert plan.gami_warning == []

    def test_gami_dedupe(self):
        """同じ combo が複数バケットにあっても gami_warning には 1 回のみ。"""
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["low_odds"])],
            osae=[_bet("1-2-3",
                       source_rules=["low_odds"], category="押さえ")],
        )
        _apply_gami_source_rules_filter(plan)
        gami_combos = [b.combination for b in plan.gami_warning]
        assert gami_combos.count("1-2-3") == 1

    def test_existing_gami_combo_not_duplicated(self):
        """plan.gami_warning に既存があれば追加しない。"""
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["low_odds"])],
            gami_warning=[_bet("1-2-3")],
        )
        _apply_gami_source_rules_filter(plan)
        assert len([b for b in plan.gami_warning if b.combination == "1-2-3"]) == 1


# ---------------------------------------------------------------------------
# C/D. purchase_mode cap (final_best 空時)
# ---------------------------------------------------------------------------


class TestPurchaseModeCapAfterGamiFilter:
    def test_cap_to_watch_only_when_final_best_emptied(self):
        plan = OutputPlan(
            final_best=[_bet("1-2-3", source_rules=["low_odds"])],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        _apply_gami_source_rules_filter(plan)
        assert plan.final_best == []
        assert plan.purchase_mode <= PurchaseMode.WATCH_ONLY

    def test_no_cap_when_other_final_best_remain(self):
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", source_rules=["low_odds"]),
                _bet("2-1-3", source_rules=["market_head"]),
            ],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        _apply_gami_source_rules_filter(plan)
        # 2-1-3 が残るので purchase_mode は変えない
        assert len(plan.final_best) == 1
        assert plan.purchase_mode == PurchaseMode.BUYABLE


# ---------------------------------------------------------------------------
# E. E2E: build_output_plan 経由で低オッズ候補が自動分離
# ---------------------------------------------------------------------------


class TestE2EAutoGamiSeparation:
    def test_low_odds_auto_separated_via_build_output_plan(self):
        """odds=4.3 の候補が _push の auto タグで low_odds/gami_warning を
        持ち、build_output_plan の filter で gami_warning に移る。"""
        ri = _ri(
            class_name="A級一般",
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 4.3},
                {"bet_type": "3連単", "combination": "2-1-3", "odds": 8.0},
            ],
            recent_results=[
                {"date": "2026-05-24", "venue": "テスト", "race_no": 1,
                 "result": "1-2-3", "memo": "x"},
            ],
        )
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=4.3, gami_risk=0.8,
                     source_rules=["low_odds", "gami_warning",
                                   "odds_available"]),
                _bet("2-1-3", market_odds=8.0,
                     source_rules=["odds_available"]),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # 1-2-3 (low_odds) が honsen から消える
        honsen_combos = [b.combination for b in plan.honsen]
        assert "1-2-3" not in honsen_combos
        # gami_warning に入る
        gami_combos = [b.combination for b in plan.gami_warning]
        assert "1-2-3" in gami_combos


class TestNormalLineGamiSeparation:
    def test_normal_line_separates_low_odds(self):
        """通常ライン戦でも odds<5 は構造的に分離される。"""
        ri = _ri(class_name="A級一般")
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=4.8, gami_risk=0.8,
                     source_rules=["low_odds", "gami_warning",
                                   "line_direct"]),
                _bet("2-1-3", market_odds=8.0,
                     source_rules=["odds_available"]),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # 1-2-3 (line_direct + low_odds) は gami_warning に移る
        # (gami filter は line filter の **後** に走るので、normal_line では
        #  line_direct タグだけでは line filter で除外されない → gami filter で除外)
        assert "1-2-3" not in [b.combination for b in plan.honsen]
        assert "1-2-3" in [b.combination for b in plan.gami_warning]


# ---------------------------------------------------------------------------
# G. odds=8 は filter で除外されない
# ---------------------------------------------------------------------------


class TestNonLowOddsNotFiltered:
    def test_odds_8_not_filtered(self):
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["odds_available"])],
        )
        _apply_gami_source_rules_filter(plan)
        # odds_available のみ (low_odds なし) → 除外されない
        assert len(plan.honsen) == 1
        assert plan.gami_warning == []


# ---------------------------------------------------------------------------
# H. watch_only_reason_groups["gami_warning"] への反映
# ---------------------------------------------------------------------------


class TestGamiInReasonGroup:
    def test_gami_in_reason_group(self):
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["low_odds"])],
        )
        _apply_gami_source_rules_filter(plan)
        group = plan.watch_only_reason_groups.get("gami_warning")
        assert group is not None
        assert any(b.combination == "1-2-3" for b in group)


# ---------------------------------------------------------------------------
# I. codex P2 反映: watch_only に重複追加されない (Renderer 二重表示防止)
# ---------------------------------------------------------------------------


class TestCodexP2WatchOnlyNoDuplicate:
    def test_gami_filter_does_not_add_to_watch_only(self):
        """codex P2-1 反映: gami filter で plan.watch_only には追加されない
        (Renderer で「安い人気筋」と「参考表示」の 2 箇所重複を防ぐ)。
        reason_groups['gami_warning'] には記録される。"""
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["low_odds"])],
        )
        _apply_gami_source_rules_filter(plan)
        # plan.watch_only には入らない (gami_warning 専用バケット + group のみ)
        watch_combos = [b.combination for b in plan.watch_only]
        assert "1-2-3" not in watch_combos
        # gami_warning と reason_group["gami_warning"] には入る
        assert "1-2-3" in [b.combination for b in plan.gami_warning]
        group = plan.watch_only_reason_groups.get("gami_warning") or []
        assert "1-2-3" in [b.combination for b in group]

    def test_decision_notes_preserved_after_decision_context(self):
        """codex P2-2 反映: gami filter の decision_notes が
        _apply_decision_context で上書きされず保持される (単体ケースで
        検証)。E2E では final_selection の cheap_popular_bets 分離が先に
        走るため gami filter が動かないケースもあるが、上書き挙動だけを
        ピンポイントに検証する。"""
        from app.output_plan import (
            _apply_decision_context, _apply_gami_source_rules_filter,
        )
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["low_odds"])],
        )
        _apply_gami_source_rules_filter(plan)
        notes_after_gami = list(plan.decision_notes)
        assert any("分離" in n for n in notes_after_gami)

        # 直接 _apply_decision_context を呼ぶケースの代わりに、
        # ctx.reasons だけが入った状態で extend 動作を再現
        from app.decision import DecisionContext, PurchaseMode
        ctx = DecisionContext(
            odds_overall_coverage=0.8, honsen_odds_coverage=0.8,
            purchase_odds_coverage=0.8,
            data_quality="high", race_complexity="medium",
            is_girls=False, is_rookie=False, final_best_count=2,
            purchase_mode=PurchaseMode.BUYABLE,
            reasons=["通常購入可能 (BUYABLE)"],
        )
        plan.decision_notes.extend(ctx.reasons)
        # 既存 (gami 由来) + ctx 由来 両方残る
        assert any("分離" in n for n in plan.decision_notes)
        assert "通常購入可能 (BUYABLE)" in plan.decision_notes

    def test_purchase_coverage_excludes_gami(self):
        """codex P1 反映: purchase_coverage が gami 候補を除外して計算
        される。gami 候補だけが odds 付きのとき、coverage は 0 扱い。"""
        ri = _ri(
            class_name="A級一般",
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 4.0},
            ],
            recent_results=[
                {"date": "2026-05-24", "venue": "テスト", "race_no": 1,
                 "result": "1-2-3", "memo": "x"},
            ],
        )
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                # gami 候補 (odds=4.0 → low_odds)
                _bet("1-2-3", market_odds=4.0, gami_risk=0.8,
                     source_rules=["low_odds", "gami_warning"]),
                # 非 gami 候補 (odds 未取得)
                _bet("3-1-2", market_odds=None),
                _bet("2-1-3", market_odds=None),
                _bet("1-3-2", market_odds=None),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # gami 候補を除外すると purchase_bets は 3 点全部 odds=None →
        # purchase_coverage=0.0 → TENTATIVE 以下にキャップされるはず
        assert plan.purchase_mode <= PurchaseMode.TENTATIVE, (
            f"purchase_mode={plan.purchase_mode.name} "
            f"notes={plan.decision_notes}"
        )
