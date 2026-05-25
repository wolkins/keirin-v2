"""Phase 14 後続2: 静岡4R で報告された矛盾 (race_type=normal_line なのに
LINE_SOURCE_RULES_LEAKED が出る) の修正回帰テスト + 関連修正.

検証内容:
A. normal_line + line_* 候補 → LINE_SOURCE_RULES_LEAKED 出さない
B. rookie + line_* 候補 → watch_only に移動 + LEAKED 出さない
C. forced leak (allow_line_logic=False で意図的に line_* を残す) → LEAKED 出る
D. race_no 整合性チェック
E. ガミ回避メモに gami_warning 内容反映
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.decision import PurchaseMode
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import (
    OutputPlan, _check_line_source_rules_leak,
    _check_race_no_consistency, build_output_plan,
)


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _ri(*, class_name="A級一般", is_girls=False, race_no=4):
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-25",
                 "venue": "静岡", "race_no": race_no,
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
            {"car_no": i, "name": f"R{i}", "score": 88.0,
             "b_count": 1, "nige": 1 if i in (1, 5) else 0,
             "makuri": 0, "sashi": 1 if i in (2, 4) else 0,
             "mark": 1, "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": [
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
        ],
        "recent_results": [
            {"date": "2026-05-24", "venue": "静岡", "race_no": 1,
             "result": "1-2-3", "memo": "x"},
        ],
    })


def _make_plan_with_policy(*, allow_line_logic: bool):
    from app.decision.race_type_policy import (
        _NORMAL_LINE_POLICY, _ROOKIE_POLICY,
    )
    plan = OutputPlan()
    policy = _NORMAL_LINE_POLICY if allow_line_logic else _ROOKIE_POLICY
    plan.race_type = policy.race_type
    object.__setattr__(plan, "_race_type_policy", policy)
    return plan


# ---------------------------------------------------------------------------
# A. normal_line + line_* 候補 → LEAKED 出さない (バグ修正)
# ---------------------------------------------------------------------------


class TestNormalLineNoLeakWarning:
    def test_normal_line_with_line_tags_no_leak_warning(self):
        """通常ライン戦で line_direct タグ付き候補が honsen に残っても
        LINE_SOURCE_RULES_LEAKED は出ない。"""
        plan = _make_plan_with_policy(allow_line_logic=True)
        plan.honsen = [
            _bet("1-2-3", source_rules=["line_direct"]),
            _bet("2-1-3", source_rules=["line_second_head"]),
        ]
        plan.osae = [
            _bet("1-2-4", source_rules=["line_fourth_flow"],
                 category="押さえ"),
        ]
        _check_line_source_rules_leak(plan)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" not in codes

    def test_normal_line_e2e_no_leak(self):
        """E2E: 静岡風 4R 通常戦の build_output_plan で LEAKED が出ない。"""
        ri = _ri(class_name="A級一般", race_no=4)
        pred = Prediction(
            race_id="20260525-shizuoka-4", venue="静岡", race_no=4,
            is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=6.0,
                     source_rules=["line_direct"]),
                _bet("2-1-3", source_rules=["line_second_head"]),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # race_type=normal_line 確認
        assert plan.race_type == "normal_line"
        # LINE_SOURCE_RULES_LEAKED が出ない
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" not in codes
        # line_direct タグ付き候補は honsen に残る
        honsen_combos = [b.combination for b in plan.honsen]
        assert "1-2-3" in honsen_combos


# ---------------------------------------------------------------------------
# B. rookie/girls + line_* → watch_only に移動 + LEAKED 出さない
# ---------------------------------------------------------------------------


class TestRookieLineMovedToWatchOnly:
    def test_rookie_line_tag_filtered_no_leak(self):
        """rookie で line_direct タグが filter で除外される → LEAKED 出さない
        (line_filter が走った後の状態で leak check)。"""
        ri = _ri(class_name="A級新人", race_no=4)
        pred = Prediction(
            race_id="t", venue="静岡", race_no=4, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=6.0,
                     source_rules=["line_direct"]),
                _bet("2-1-3", market_odds=10.0,
                     source_rules=["market_head"]),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # 1-2-3 (line_direct) は watch_only_reason_groups に移動
        line_group = plan.watch_only_reason_groups.get("line_source_filtered")
        assert line_group and any(
            b.combination == "1-2-3" for b in line_group
        )
        # LEAKED 警告は出ない (filter が成功している)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" not in codes


# ---------------------------------------------------------------------------
# C. forced leak positive test (filter 後に意図的に line_* を戻す)
# ---------------------------------------------------------------------------


class TestForcedLeakPositive:
    def test_forced_line_tag_after_filter_triggers_leak(self):
        """allow_line_logic=False で line_filter 後に line_* タグを手動で
        honsen に注入 → LEAKED 警告が出る。"""
        plan = _make_plan_with_policy(allow_line_logic=False)
        # filter 後に line タグ candidate を手動注入
        plan.honsen = [
            _bet("1-2-3", source_rules=["line_direct"]),
        ]
        _check_line_source_rules_leak(plan)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" in codes

    def test_no_policy_set_no_leak_check(self):
        """plan._race_type_policy が未設定なら leak check は skip。"""
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["line_direct"])],
        )
        # _race_type_policy 未設定
        _check_line_source_rules_leak(plan)
        codes = [w.code for w in plan.warnings]
        assert "LINE_SOURCE_RULES_LEAKED" not in codes


# ---------------------------------------------------------------------------
# D. race_no 整合性チェック
# ---------------------------------------------------------------------------


class TestRaceNoConsistency:
    def test_matching_race_no_no_warning(self):
        """race_no が一致 → warning 出ない。"""
        ri = _ri(race_no=4)
        pred = Prediction(
            race_id="t", venue="静岡", race_no=4, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[], osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = OutputPlan()
        _check_race_no_consistency(plan, pred, ri)
        codes = [w.code for w in plan.warnings]
        assert "RACE_NO_OUTPUT_MISMATCH" not in codes

    def test_mismatched_race_no_warning(self):
        """race_no が不一致 → RACE_NO_MISMATCH 警告。"""
        ri = _ri(race_no=5)
        pred = Prediction(
            race_id="t", venue="静岡", race_no=4, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[], osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = OutputPlan()
        _check_race_no_consistency(plan, pred, ri)
        codes = [w.code for w in plan.warnings]
        assert "RACE_NO_OUTPUT_MISMATCH" in codes
        message = next(
            w.message for w in plan.warnings
            if w.code == "RACE_NO_OUTPUT_MISMATCH"
        )
        assert "4" in message and "5" in message


# ---------------------------------------------------------------------------
# E. ガミ回避メモに gami_warning 内容反映
# ---------------------------------------------------------------------------


class TestGamiMemoReflection:
    def test_low_odds_gami_appears_in_section_11(self):
        """plan.gami_warning に低オッズ候補があれば ## 11. ガミ回避メモに
        「N-N-N(N.N倍)は売れすぎ」が出る。"""
        ri = _ri(race_no=4)
        # 2-5-7 を 1.4 倍で gami_warning に
        pred = Prediction(
            race_id="t", venue="静岡", race_no=4, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("2-5-7", market_odds=1.4, gami_risk=0.9,
                     source_rules=["low_odds", "gami_warning"]),
                _bet("1-2-3", market_odds=6.0, value_label="妙味あり"),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        md = render_prediction_v2(pred, input_data=ri)
        # ## 11 セクションに低オッズ表記が出る
        section_11 = md.split("## 11.")[1].split("## 12.")[0]
        assert "2-5-7" in section_11
        assert "1.4倍" in section_11
        assert "売れすぎ" in section_11

    def test_no_low_odds_no_section(self):
        """gami_warning が空、または高オッズしかなければ追記なし。"""
        ri = _ri(race_no=4)
        pred = Prediction(
            race_id="t", venue="静岡", race_no=4, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[_bet("1-2-3", market_odds=8.0)],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="(該当なし)",
            reflection_points=[],
        )
        md = render_prediction_v2(pred, input_data=ri)
        section_11 = md.split("## 11.")[1].split("## 12.")[0]
        assert "売れすぎ" not in section_11
