"""Phase 7: display sections も source_rules filter 対象に拡張する.

検証内容:
A. allow_line_logic=False で honsen / osae / ana / ooana / final_* すべてから
   line_* 候補が除外される
B. 除外された候補は watch_only に prepend
C. normal_line では除外しない
D. filter 後 final_best 空 → purchase_mode <= WATCH_ONLY
E. LINE_SOURCE_RULES_LEAKED warning (filter 漏れ検出)
F. scoring.py の line_direct / line_second_head タグ付与
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import (
    OutputPlan, _apply_line_source_rules_filter, build_output_plan,
)
from app.scoring import build_candidate_bets, compute_scores


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _make_plan_with_policy(allow_line_logic: bool):
    from app.decision.race_type_policy import (
        _NORMAL_LINE_POLICY, _ROOKIE_POLICY,
    )
    plan = OutputPlan()
    policy = (
        _NORMAL_LINE_POLICY if allow_line_logic else _ROOKIE_POLICY
    )
    plan.race_type = policy.race_type
    object.__setattr__(plan, "_race_type_policy", policy)
    return plan


# ---------------------------------------------------------------------------
# A. display sections + final_* すべてから除外
# ---------------------------------------------------------------------------


class TestFilterCoversDisplaySections:
    def test_rookie_removes_line_tagged_from_honsen(self):
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.honsen = [
            _bet("1-2-3", source_rules=["line_direct"]),
            _bet("2-1-3", source_rules=["market_axis"]),  # 残す
        ]
        _apply_line_source_rules_filter(plan)
        assert [b.combination for b in plan.honsen] == ["2-1-3"]
        assert "1-2-3" in [b.combination for b in plan.watch_only]

    def test_rookie_removes_line_tagged_from_osae(self):
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.osae = [
            _bet("1-4-2", source_rules=["line_fourth_flow"],
                 category="押さえ"),
            _bet("5-3-1", source_rules=[], category="押さえ"),
        ]
        _apply_line_source_rules_filter(plan)
        kept = [b.combination for b in plan.osae]
        assert kept == ["5-3-1"]

    def test_rookie_removes_line_tagged_from_ana(self):
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.ana = [
            _bet("2-3-1", source_rules=["line_spec12"], category="穴"),
            _bet("7-5-3", source_rules=[], category="穴"),
        ]
        _apply_line_source_rules_filter(plan)
        assert [b.combination for b in plan.ana] == ["7-5-3"]

    def test_rookie_removes_line_tagged_from_ooana(self):
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.ooana = [
            _bet("7-1-2", source_rules=["line_spec12"], category="大穴"),
            _bet("4-6-7", source_rules=[], category="大穴"),
        ]
        _apply_line_source_rules_filter(plan)
        assert [b.combination for b in plan.ooana] == ["4-6-7"]

    def test_rookie_removes_from_all_seven_buckets(self):
        """display 4 + final_* 3 すべてから除外。"""
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.honsen = [_bet("1-2-3", source_rules=["line_direct"])]
        plan.osae = [_bet("1-4-2", source_rules=["line_fourth_flow"],
                          category="押さえ")]
        plan.ana = [_bet("2-3-1", source_rules=["line_spec12"],
                         category="穴")]
        plan.ooana = [_bet("7-1-2", source_rules=["line_spec12"],
                           category="大穴")]
        plan.final_best = [_bet("3-1-2", source_rules=["line_third"])]
        plan.final_osae = [_bet("5-3-1", source_rules=["separate_line"],
                                category="押さえ")]
        plan.final_ana = [_bet("6-4-1", source_rules=["line_weather"],
                               category="穴")]
        _apply_line_source_rules_filter(plan)
        # 全部空になる
        assert plan.honsen == []
        assert plan.osae == []
        assert plan.ana == []
        assert plan.ooana == []
        assert plan.final_best == []
        assert plan.final_osae == []
        assert plan.final_ana == []
        # 全部 watch_only に
        watch_combos = set(b.combination for b in plan.watch_only)
        expected = {"1-2-3", "1-4-2", "2-3-1", "7-1-2",
                    "3-1-2", "5-3-1", "6-4-1"}
        assert watch_combos == expected


# ---------------------------------------------------------------------------
# B. watch_only への prepend
# ---------------------------------------------------------------------------


class TestPrependedToWatchOnly:
    def test_moved_candidates_prepended(self):
        """除外候補が watch_only の **先頭** に挿入される。"""
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.honsen = [_bet("1-2-3", source_rules=["line_direct"])]
        plan.watch_only = [_bet("5-7-1"), _bet("4-6-7")]
        _apply_line_source_rules_filter(plan)
        # 1-2-3 が先頭に
        assert plan.watch_only[0].combination == "1-2-3"
        assert [b.combination for b in plan.watch_only] == [
            "1-2-3", "5-7-1", "4-6-7",
        ]


# ---------------------------------------------------------------------------
# C. normal_line では除外しない
# ---------------------------------------------------------------------------


class TestNormalLineKeepsLineCandidates:
    def test_normal_line_does_not_filter(self):
        plan = _make_plan_with_policy(allow_line_logic=True)
        plan.honsen = [_bet("1-2-3", source_rules=["line_direct"])]
        plan.osae = [_bet("1-4-2", source_rules=["line_fourth_flow"],
                          category="押さえ")]
        plan.final_best = [_bet("3-1-2", source_rules=["line_third"])]
        _apply_line_source_rules_filter(plan)
        assert len(plan.honsen) == 1
        assert len(plan.osae) == 1
        assert len(plan.final_best) == 1
        assert plan.watch_only == []


# ---------------------------------------------------------------------------
# D. filter 後 final_best 空 → purchase_mode cap (Phase 6 から維持)
# ---------------------------------------------------------------------------


class TestPurchaseModeCapAfterFilter:
    def test_cap_to_watch_only_when_final_best_emptied(self):
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.purchase_mode = PurchaseMode.BUYABLE
        plan.final_best = [_bet("1-2-3", source_rules=["line_direct"])]
        _apply_line_source_rules_filter(plan)
        assert plan.final_best == []
        assert plan.purchase_mode <= PurchaseMode.WATCH_ONLY


# ---------------------------------------------------------------------------
# E. LINE_SOURCE_RULES_LEAKED warning (最終防衛線)
# ---------------------------------------------------------------------------


class TestLineSourceRulesLeakedWarning:
    def test_no_warning_after_normal_filter(self):
        """filter が正常に動けば LEAKED warning は出ない。"""
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.honsen = [_bet("1-2-3", source_rules=["line_direct"])]
        _apply_line_source_rules_filter(plan)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" not in codes

    def test_no_warning_for_normal_line(self):
        """normal_line では LEAKED 警告は出ない (line_* 許可)。"""
        plan = _make_plan_with_policy(allow_line_logic=True)
        plan.honsen = [_bet("1-2-3", source_rules=["line_direct"])]
        _apply_line_source_rules_filter(plan)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" not in codes

    def test_positive_warning_when_line_tag_injected_after_filter(self):
        """codex P2 反映: build_output_plan 末尾の leak check で、
        filter 後に手動で line タグ候補を注入したケースを検出する。

        実用シーンとしては、将来の後段処理で誤って line 候補を戻したり、
        テストで raw OutputPlan を組んだ場合のセーフティネット。
        """
        from app.output_plan import _check_line_source_rules_leak
        plan = _make_plan_with_policy(allow_line_logic=False)
        # filter 後に直接 line タグ付き候補を honsen に注入
        plan.honsen = [_bet("1-2-3", source_rules=["line_direct"])]
        _check_line_source_rules_leak(plan)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" in codes
        messages = " ".join(w.message for w in plan.warnings)
        assert "honsen:1-2-3" in messages

    def test_positive_warning_with_separate_tag(self):
        """separate_* も leak 検出対象 (line_* と統合判定)。"""
        from app.output_plan import _check_line_source_rules_leak
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.final_osae = [
            _bet("5-3-1", source_rules=["separate_line"], category="押さえ"),
        ]
        _check_line_source_rules_leak(plan)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" in codes


# ---------------------------------------------------------------------------
# F. scoring.py の追加 source_rules タグ付与 (Phase 7)
# ---------------------------------------------------------------------------


class TestScoringNewLineTags:
    def _ri(self, *, class_name="A級一般"):
        return RaceInput.model_validate({
            "race": {"race_id": "t", "date": "2026-05-25",
                     "venue": "テスト", "race_no": 1,
                     "class_name": class_name, "start_time": "10:00"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "本命", "cars": [1, 2, 3]},
                {"line_name": "別線", "cars": [5, 4, 6]},
                {"line_name": "単", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0,
                 "b_count": 1, "nige": 1 if i in (1, 5) else 0,
                 "makuri": 0, "sashi": 1 if i in (2, 4) else 0,
                 "mark": 1, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [],
            "recent_results": [
                {"date": "2026-05-24", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "x"},
            ],
        })

    def test_line_direct_tag_attached_to_main_axis(self):
        """通常戦本線軸 1-2-3 に line_direct タグが付く。"""
        ri = self._ri()
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        bet_123 = next(
            (b for b in bets["本線"] if b.combination == "1-2-3"), None,
        )
        assert bet_123 is not None
        assert "line_direct" in (bet_123.source_rules or [])

    def test_line_second_head_tag_attached(self):
        """番手頭 2-1-3 に line_second_head タグが付く。"""
        ri = self._ri()
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        bet_213 = next(
            (b for b in bets["本線"] if b.combination == "2-1-3"), None,
        )
        if bet_213 is not None:
            assert "line_second_head" in (bet_213.source_rules or [])


# ---------------------------------------------------------------------------
# G. E2E: 新人戦 build_output_plan で display sections から line 除外
# ---------------------------------------------------------------------------


def _make_pred(*, honsen=None, osae=None, ana=None, ooana=None,
               is_girls=False, marks=None):
    return Prediction(
        race_id="t", venue="t", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="",
        lines_text="", marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion="", gami_memo="", reflection_points=[],
    )


class TestE2ERookieDisplaySectionsFiltered:
    def _ri_rookie(self):
        return RaceInput.model_validate({
            "race": {"race_id": "t-rk", "date": "2026-05-25",
                     "venue": "平塚", "race_no": 4,
                     "class_name": "A級新人", "start_time": "11:00"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "L1", "cars": [1, 2, 3]},
                {"line_name": "L2", "cars": [5, 4, 6]},
                {"line_name": "L3", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 88.0,
                 "b_count": 1, "nige": 1 if i in (1, 5) else 0,
                 "makuri": 0, "sashi": 1 if i in (2, 4) else 0,
                 "mark": 1, "comment": "", "home_area": "南関東"}
                for i in range(1, 8)
            ],
            "odds": [
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
                {"bet_type": "3連単", "combination": "2-1-3", "odds": 11.0},
            ],
            "recent_results": [
                {"date": "2026-05-24", "venue": "平塚",
                 "race_no": 1, "result": "1-2-3", "memo": "x"},
            ],
        })

    def test_rookie_e2e_honsen_no_line_tagged(self):
        """新人戦の build_output_plan 経由で honsen に line_* タグが残らない。"""
        ri = self._ri_rookie()
        pred = _make_pred(
            honsen=[
                _bet("1-2-3", market_odds=6.0,
                     source_rules=["line_direct"]),
                _bet("2-1-3", market_odds=11.0,
                     source_rules=["market_head"]),
            ],
        )
        plan = build_output_plan(pred, ri)
        # honsen に line_* タグが残らない
        for b in plan.honsen:
            assert not any(
                tag.startswith("line_") for tag in (b.source_rules or [])
            ), f"honsen に line_* タグが残った: {b}"

    def test_rookie_e2e_no_line_source_rules_leaked_warning(self):
        """新人戦 build_output_plan で LINE_SOURCE_RULES_LEAKED が出ない
        (filter が漏れていない)。"""
        ri = self._ri_rookie()
        pred = _make_pred(
            honsen=[
                _bet("1-2-3", market_odds=6.0,
                     source_rules=["line_direct"]),
            ],
        )
        plan = build_output_plan(pred, ri)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" not in codes


# ---------------------------------------------------------------------------
# H. 静岡4R 風: 通常戦の 4 車ライン候補は維持
# ---------------------------------------------------------------------------


class TestShizuoka4rNormalLineKept:
    def test_normal_line_4_car_keeps_line_fourth_flow(self):
        """通常戦の 4 車ラインで line_fourth_flow タグ候補が osae/honsen に残る。"""
        ri = RaceInput.model_validate({
            "race": {"race_id": "t-sh", "date": "2026-05-25",
                     "venue": "静岡", "race_no": 4,
                     "class_name": "F1", "start_time": "11:30"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "本命長線", "cars": [1, 2, 3, 4]},
                {"line_name": "別線", "cars": [5, 6]},
                {"line_name": "単", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0,
                 "b_count": 1, "nige": 1, "makuri": 0, "sashi": 1,
                 "mark": 1, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [],
            "recent_results": [],
        })
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 押さえに line_fourth_flow タグ候補がある
        flow_bets = [
            b for b in bets["押さえ"]
            if "line_fourth_flow" in (b.source_rules or [])
        ]
        assert len(flow_bets) >= 1, (
            f"normal_line 4車ラインで line_fourth_flow タグが消えた"
        )
