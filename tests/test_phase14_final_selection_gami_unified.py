"""Phase 14: final_selection.py のガミ判定を source_rules ベースに統合.

検証内容:
A. final_selection で market_odds<5 → source_rules に gami_warning/low_odds
   タグが補完付与され cheap_popular_bets に分離
B. gami_risk 条件: odds<15 + gami>=0.6 → source_rules に gami_warning 追加
C. source_rules に既に gami_warning → final_best に入らない
D. non-gami (odds=18.0, gami=0.2) → final_best 候補として残り得る
E. output_plan 連携: 最終 plan.gami_warning に入る
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode, is_gami_source
from app.final_selection import (
    _ensure_gami_source_rules, _is_cheap_popular, _qualifies_best,
    build_final_selection,
)
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import build_output_plan


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _ri(*, class_name="A級一般", is_girls=False, odds=None, recent=None):
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-25",
                 "venue": "テスト", "race_no": 1,
                 "class_name": class_name, "start_time": "10:00",
                 "is_girls": is_girls},
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                    "wind_speed_mps": 2.0},
        "lines": [
            {"line_name": "本命", "cars": [1, 2, 3]},
            {"line_name": "別線", "cars": [5, 4, 6]},
            {"line_name": "単", "cars": [7]},
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 88.0, "b_count": 1,
             "nige": 1 if i in (1, 5) else 0, "makuri": 0,
             "sashi": 1 if i in (2, 4) else 0, "mark": 1,
             "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": recent or [
            {"date": "2026-05-24", "venue": "テスト", "race_no": 1,
             "result": "1-2-3", "memo": "x"},
        ],
    })


# ---------------------------------------------------------------------------
# A. _ensure_gami_source_rules 単体
# ---------------------------------------------------------------------------


class TestEnsureGamiSourceRules:
    def test_low_odds_adds_low_odds_and_gami_warning(self):
        b = _bet("1-2-3", market_odds=4.3, gami_risk=0.8)
        _ensure_gami_source_rules(b)
        assert "low_odds" in b.source_rules
        assert "gami_warning" in b.source_rules
        assert "odds_available" in b.source_rules

    def test_mid_odds_high_gami_risk_adds_gami_warning(self):
        b = _bet("1-2-3", market_odds=12.0, gami_risk=0.7)
        _ensure_gami_source_rules(b)
        assert "gami_warning" in b.source_rules
        # low_odds は付かない (odds>=5)
        assert "low_odds" not in b.source_rules

    def test_higher_odds_very_high_gami_risk_adds_gami_warning(self):
        b = _bet("1-2-3", market_odds=18.0, gami_risk=0.85)
        _ensure_gami_source_rules(b)
        assert "gami_warning" in b.source_rules
        assert "low_odds" not in b.source_rules

    def test_safe_odds_no_tag_added(self):
        b = _bet("1-2-3", market_odds=18.0, gami_risk=0.2)
        _ensure_gami_source_rules(b)
        assert "gami_warning" not in b.source_rules
        assert "low_odds" not in b.source_rules

    def test_odds_none_no_tag(self):
        b = _bet("1-2-3", market_odds=None)
        _ensure_gami_source_rules(b)
        assert "low_odds" not in b.source_rules
        assert "gami_warning" not in b.source_rules

    def test_dedupe_existing_tags(self):
        b = _bet("1-2-3", market_odds=4.3,
                 source_rules=["low_odds", "gami_warning"])
        _ensure_gami_source_rules(b)
        # 既存 + odds_available (重複なし)
        assert b.source_rules.count("low_odds") == 1
        assert b.source_rules.count("gami_warning") == 1


# ---------------------------------------------------------------------------
# B/C. _qualifies_best + _is_cheap_popular で source_rules ベース判定
# ---------------------------------------------------------------------------


class TestQualifiesBestSourceRules:
    def test_gami_warning_tag_disqualifies(self):
        b = _bet("1-2-3", market_odds=8.0,
                 source_rules=["gami_warning"])
        assert _qualifies_best(b) is False

    def test_low_odds_tag_disqualifies(self):
        b = _bet("1-2-3", market_odds=8.0,
                 source_rules=["low_odds"])
        assert _qualifies_best(b) is False
        # _is_cheap_popular も True (Phase 14 で source_rules を見るように)
        assert _is_cheap_popular(b) is True

    def test_non_gami_qualifies(self):
        b = _bet("1-2-3", market_odds=18.0, gami_risk=0.2,
                 source_rules=["market_head"])
        assert _qualifies_best(b) is True

    def test_market_odds_low_disqualifies(self):
        b = _bet("1-2-3", market_odds=4.3, gami_risk=0.8)
        assert _qualifies_best(b) is False
        assert _is_cheap_popular(b) is True


# ---------------------------------------------------------------------------
# D. build_final_selection E2E
# ---------------------------------------------------------------------------


class TestBuildFinalSelectionGami:
    def test_low_odds_goes_to_cheap_popular_with_tags(self):
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 4.3},
            {"bet_type": "3連単", "combination": "2-1-3", "odds": 18.0},
        ])
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=4.3, gami_risk=0.8),
                _bet("2-1-3", market_odds=18.0,
                     value_label="妙味あり"),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        sel = build_final_selection(pred, ri)
        # 1-2-3 は cheap_popular_bets に
        cheap_combos = [b.combination for b in sel.cheap_popular_bets]
        assert "1-2-3" in cheap_combos
        # cheap pool 内の 1-2-3 に gami_warning タグが付く
        b_123 = next(
            (b for b in sel.cheap_popular_bets if b.combination == "1-2-3"),
            None,
        )
        assert b_123 is not None
        assert "gami_warning" in b_123.source_rules
        assert "low_odds" in b_123.source_rules
        # 1-2-3 は best_bets には入らない
        best_combos = [b.combination for b in sel.best_bets]
        assert "1-2-3" not in best_combos


# ---------------------------------------------------------------------------
# E. OutputPlan 連携
# ---------------------------------------------------------------------------


class TestOutputPlanIntegration:
    def test_gami_candidate_ends_up_in_gami_warning(self):
        """final_selection で cheap_popular_bets に分離された候補が、
        OutputPlan の gami_warning bucket に最終的に入る。"""
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 4.3},
        ])
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=4.3, gami_risk=0.8),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        gami_combos = [b.combination for b in plan.gami_warning]
        assert "1-2-3" in gami_combos
        # final_best にも honsen にも入らない
        assert "1-2-3" not in [b.combination for b in plan.final_best]
        assert "1-2-3" not in [b.combination for b in plan.honsen]

    def test_codex_p1_mid_odds_high_gami_not_in_cheap_pool(self):
        """codex P1 反映: odds=12.0 + gami_risk=0.7 は cheap_popular_bets に
        移動しない (source_rules には gami_warning タグが付くが、cheap_pool
        への移動は market_odds<5 のみ)。"""
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 12.0},
        ])
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=12.0, gami_risk=0.7,
                     value_label="妙味あり"),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        sel = build_final_selection(pred, ri)
        # 1-2-3 は cheap_popular_bets に **入らない** (odds=12.0 >= 5)
        cheap_combos = [b.combination for b in sel.cheap_popular_bets]
        assert "1-2-3" not in cheap_combos

    def test_codex_p2_does_not_mutate_original_prediction(self):
        """codex P2 反映: build_final_selection が元の Prediction の
        source_rules を破壊的に書き換えない (deep copy で保護)。"""
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 4.3},
        ])
        original_bet = _bet("1-2-3", market_odds=4.3, gami_risk=0.8,
                            value_label="本線向き")
        # 元の source_rules は空
        assert original_bet.source_rules == []
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[original_bet],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        build_final_selection(pred, ri)
        # 元の bet の source_rules は依然として空 (deep copy で保護)
        assert original_bet.source_rules == []

    def test_phase14_note_text_updated(self):
        """Phase 14 P3 反映: note 文言が
        '購入候補から gami_warning + watch_only_reason_groups[...] に分離'
        になっている (実装と整合)。"""
        from app.output_plan import (
            OutputPlan, _apply_gami_source_rules_filter,
        )
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["low_odds"])],
        )
        _apply_gami_source_rules_filter(plan)
        # decision_notes に新しい文言が入る
        joined = " ".join(plan.decision_notes)
        assert "watch_only_reason_groups" in joined
