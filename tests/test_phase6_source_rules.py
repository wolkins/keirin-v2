"""Phase 6: BetRecommendation.source_rules を実候補生成に付与し、
allow_line_logic=False で line_* 候補を構造的に除外する.

検証内容:
A. scoring.py が line 候補に source_rules=["line_X"] を付ける
   (4車ライン流れ込み / 仕様12)
B. OutputPlanValidator (_apply_line_source_rules_filter) が
   allow_line_logic=False で final_* から line_* タグ候補を除外
C. normal_line では line_* タグ候補が final_* に残る
D. 除外された候補は watch_only に移動
E. 文字列検出 (LINE_TERMS_LEAKED) は最終防衛線として残る
"""

from __future__ import annotations

import pytest

from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import (
    OutputPlan, _apply_line_source_rules_filter, build_output_plan,
)
from app.decision import PurchaseMode, resolve_race_type_policy
from app.scoring import build_candidate_bets, compute_scores


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _ri(*, class_name="A級一般", is_girls=False, lines=None, odds=None,
        recent_results=None):
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-24",
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
             "makuri": 1 if i == 7 else 0,
             "sashi": 1 if i in (2, 4) else 0, "mark": 1,
             "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": recent_results or [
            {"date": "2026-05-23", "venue": "テスト",
             "race_no": 1, "result": "1-2-3", "memo": "x"},
        ],
    })


# ---------------------------------------------------------------------------
# A. scoring.py が source_rules を付与する
# ---------------------------------------------------------------------------


class TestScoringAttachesSourceRules:
    def test_4_car_line_flow_tagged_line_fourth_flow(self):
        """通常戦の 4 車ラインで、1-2-4 (4 番手流れ込み) に
        source_rules=['line_fourth_flow'] が付く。"""
        ri = _ri(
            class_name="F1",
            lines=[
                {"line_name": "本命長線", "cars": [1, 2, 3, 4]},
                {"line_name": "別線", "cars": [5, 6]},
                {"line_name": "単", "cars": [7]},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 押さえに 1-2-4 が存在し、source_rules に line_fourth_flow
        bet_124 = next(
            (b for b in bets["押さえ"] if b.combination == "1-2-4"), None,
        )
        assert bet_124 is not None
        assert "line_fourth_flow" in (bet_124.source_rules or []), (
            f"source_rules={bet_124.source_rules}"
        )

    def test_spec12_candidates_tagged_line_spec12(self):
        """通常戦の仕様12候補に source_rules=['line_spec12'] が付く。"""
        ri = _ri(class_name="A級一般")
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # ana セクションに仕様12候補があり、source_rules に line_spec12
        spec12_bets = [
            b for bucket in bets.values() for b in bucket
            if "line_spec12" in (b.source_rules or [])
        ]
        assert len(spec12_bets) >= 1, (
            f"仕様12 タグ付き候補が見つからない: "
            f"all_rules={[b.source_rules for bucket in bets.values() for b in bucket]}"
        )


# ---------------------------------------------------------------------------
# B/C. _apply_line_source_rules_filter
# ---------------------------------------------------------------------------


class TestApplyLineSourceRulesFilter:
    def _make_plan_with_policy(self, allow_line_logic: bool):
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

    def test_rookie_removes_line_tagged_from_final(self):
        """allow_line_logic=False (rookie) で source_rules に line_* タグが
        ある候補を final_best から除外し watch_only に移動する。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        plan.final_best = [
            _bet("1-2-3", source_rules=["line_third"]),
            _bet("2-1-3", source_rules=["market_axis"]),  # 残す
            _bet("3-1-2", source_rules=["line_spec12"]),
        ]
        _apply_line_source_rules_filter(plan)
        # line_* タグの 2 点が除外される
        kept = [b.combination for b in plan.final_best]
        assert kept == ["2-1-3"], kept
        # 除外された 2 点は watch_only に
        watch = [b.combination for b in plan.watch_only]
        assert "1-2-3" in watch
        assert "3-1-2" in watch

    def test_rookie_filters_all_three_buckets(self):
        """final_best / final_osae / final_ana すべてから line_* を除外。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        plan.final_best = [_bet("1-2-3", source_rules=["line_third"])]
        plan.final_osae = [
            _bet("2-1-3", source_rules=["line_fourth_flow"],
                 category="押さえ"),
        ]
        plan.final_ana = [
            _bet("3-1-2", source_rules=["line_spec12"], category="穴"),
        ]
        _apply_line_source_rules_filter(plan)
        assert plan.final_best == []
        assert plan.final_osae == []
        assert plan.final_ana == []
        # 全て watch_only に
        watch_combos = [b.combination for b in plan.watch_only]
        assert set(watch_combos) >= {"1-2-3", "2-1-3", "3-1-2"}

    def test_normal_line_keeps_line_tagged(self):
        """allow_line_logic=True (normal_line) では line_* タグ候補を
        除外しない。"""
        plan = self._make_plan_with_policy(allow_line_logic=True)
        plan.final_best = [
            _bet("1-2-3", source_rules=["line_third"]),
            _bet("2-1-3", source_rules=["market_axis"]),
        ]
        _apply_line_source_rules_filter(plan)
        # 何も変わらない
        kept = [b.combination for b in plan.final_best]
        assert kept == ["1-2-3", "2-1-3"]

    def test_no_policy_no_change(self):
        """plan._race_type_policy 未設定 (古い fixture) では何もしない。"""
        plan = OutputPlan(
            final_best=[_bet("1-2-3", source_rules=["line_third"])],
        )
        _apply_line_source_rules_filter(plan)
        assert len(plan.final_best) == 1

    def test_decision_notes_added(self):
        """除外時、decision_notes に「構造的除外」note が追加される。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        plan.final_best = [_bet("1-2-3", source_rules=["line_third"])]
        _apply_line_source_rules_filter(plan)
        notes = " ".join(plan.decision_notes)
        assert "構造的除外" in notes or "watch_only に移動" in notes

    def test_filter_caps_purchase_mode_to_watch_only_when_empty(self):
        """codex P2 反映: filter で final_best が空になったら
        purchase_mode を WATCH_ONLY 以下にキャップする。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        # BUYABLE で line タグ付き 1点のみ
        plan.purchase_mode = PurchaseMode.BUYABLE
        plan.final_best = [_bet("1-2-3", source_rules=["line_third"])]
        _apply_line_source_rules_filter(plan)
        # 除外で final_best が空 → WATCH_ONLY 以下にキャップ
        assert plan.final_best == []
        assert plan.purchase_mode <= PurchaseMode.WATCH_ONLY, (
            f"purchase_mode={plan.purchase_mode.name}"
        )
        # decision_notes に「見送り寄りに cap」note
        joined = " ".join(plan.decision_notes)
        assert "見送り寄り" in joined or "cap" in joined.lower()

    def test_filter_does_not_change_purchase_mode_if_final_best_not_empty(self):
        """除外しても final_best が残る場合は purchase_mode を変えない。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        plan.purchase_mode = PurchaseMode.BUYABLE
        plan.final_best = [
            _bet("1-2-3", source_rules=["line_third"]),
            _bet("2-1-3", source_rules=["market_axis"]),
        ]
        _apply_line_source_rules_filter(plan)
        # 2-1-3 が残る
        assert len(plan.final_best) == 1
        # BUYABLE のまま
        assert plan.purchase_mode == PurchaseMode.BUYABLE

    def test_no_source_rules_kept(self):
        """source_rules が空 ([]) の候補は除外しない。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        plan.final_best = [
            _bet("1-2-3", source_rules=[]),
            _bet("2-1-3"),  # source_rules default
        ]
        _apply_line_source_rules_filter(plan)
        assert len(plan.final_best) == 2

    def test_non_line_tags_kept(self):
        """line_* 以外のタグ (market_*, individual_* 等) は除外しない。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        plan.final_best = [
            _bet("1-2-3", source_rules=["market_axis"]),
            _bet("2-1-3", source_rules=["market_head"]),
            _bet("3-1-2", source_rules=["individual_top"]),
        ]
        _apply_line_source_rules_filter(plan)
        assert len(plan.final_best) == 3


# ---------------------------------------------------------------------------
# D. E2E: build_output_plan で構造的除外が走る
# ---------------------------------------------------------------------------


class TestE2EBuildOutputPlanFilters:
    def test_rookie_e2e_no_line_tagged_in_final(self):
        """新人戦の build_output_plan で final_* に line_* タグが残らない。"""
        ri = _ri(class_name="A級新人")
        # 新人戦なので allow_line_logic=False、scoring 側で大半は既に
        # 抑制されているはず
        # honsen に手動で line_* タグ付き候補を仕込んで、validator が
        # フィルタすることを確認
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=6.0,
                     source_rules=["line_third"]),
                _bet("2-1-3", market_odds=10.0,
                     source_rules=["market_axis"]),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # final_best から line_third タグの 1-2-3 が除外されている
        final_best_rules = [
            b.source_rules for b in plan.final_best
        ]
        has_line_in_final = any(
            any(t.startswith("line_") for t in (rules or []))
            for rules in final_best_rules
        )
        assert not has_line_in_final, (
            f"final_best に line_* タグが残った: {final_best_rules}"
        )
