"""Phase 4: RaceTypePolicy の回帰テスト.

検証内容:
A. resolve_race_type_policy: 4 種別の判定
B. 各 policy のフィールド値
C. build_output_plan で plan.race_type が設定される
D. DecisionContext で policy が反映される (force_watch_only_when_low_quality
   / low_coverage_threshold / low_quality_max_purchase_mode)
E. Renderer の「### レース種別: ...」セクション表示
F. 既存シナリオ (広島3R/静岡4R/平塚4R/6R/7R/10R) との整合性
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.decision import (
    PurchaseMode,
    RaceTypePolicy,
    resolve_race_type_policy,
)
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import build_output_plan


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _pred(*, honsen=None, osae=None, is_girls=False, marks=None):
    return Prediction(
        race_id="t", venue="t", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="",
        lines_text="", marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=[], ooana=[],
        final_conclusion="", gami_memo="", reflection_points=[],
    )


def _ri(*, class_name="A級一般", is_girls=False, is_rookie_class=None,
        riders=None, odds=None, lines=None, recent_results=None):
    payload = {
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
        "riders": riders or [
            {"car_no": i, "name": f"R{i}", "score": 90.0,
             "b_count": 1, "nige": 1, "makuri": 1, "sashi": 1,
             "mark": 1, "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": odds if odds is not None else [],
        "recent_results": recent_results or [],
    }
    return RaceInput.model_validate(payload)


# ---------------------------------------------------------------------------
# A. resolve_race_type_policy: 4 種別の判定
# ---------------------------------------------------------------------------


class TestResolveRaceTypePolicy:
    def test_normal_line_default(self):
        ri = _ri(class_name="A級一般")
        p = resolve_race_type_policy(ri)
        assert p.race_type == "normal_line"
        assert p.allow_line_logic is True
        assert p.allow_line_terms is True
        assert p.force_watch_only_when_low_quality is False

    def test_girls(self):
        ri = _ri(class_name="ガールズ", is_girls=True)
        p = resolve_race_type_policy(ri)
        assert p.race_type == "girls"
        assert p.allow_line_logic is False
        assert p.allow_line_terms is False
        assert p.market_weight >= 0.7
        assert p.force_watch_only_when_low_quality is True

    def test_rookie(self):
        # 「新人」を含むクラス名で is_rookie 判定
        ri = _ri(class_name="A級新人")
        p = resolve_race_type_policy(ri)
        assert p.race_type == "rookie"
        assert p.allow_line_logic is False
        assert p.allow_line_terms is False
        assert p.force_watch_only_when_low_quality is True

    def test_girls_rookie(self):
        ri = _ri(class_name="ガールズ新人決勝", is_girls=True)
        p = resolve_race_type_policy(ri)
        assert p.race_type == "girls_rookie"
        # girls + rookie の厳しい方
        assert p.max_final_best == 2
        # codex P1 反映: low_coverage_threshold は girls と同じ 0.25
        # (= coverage<0.25 で WATCH_ONLY。0.20 は実質 normal_line と同じ
        # 強さになるため girls より弱くなってしまう)
        assert p.low_coverage_threshold == 0.25
        assert p.force_watch_only_when_low_quality is True

    def test_none_input_data_returns_normal_line(self):
        p = resolve_race_type_policy(None)
        assert p.race_type == "normal_line"


# ---------------------------------------------------------------------------
# B. Policy フィールド値の妥当性
# ---------------------------------------------------------------------------


class TestPolicyValues:
    def test_girls_rookie_stricter_than_girls(self):
        """ガールズ新人は girls より厳しい設定。"""
        from app.decision.race_type_policy import (
            _GIRLS_POLICY, _GIRLS_ROOKIE_POLICY,
        )
        assert (
            _GIRLS_ROOKIE_POLICY.max_final_best
            <= _GIRLS_POLICY.max_final_best
        )
        assert (
            _GIRLS_ROOKIE_POLICY.low_coverage_threshold
            <= _GIRLS_POLICY.low_coverage_threshold
        )

    def test_low_quality_cap_for_girls_is_watch_only(self):
        from app.decision.race_type_policy import _GIRLS_POLICY
        assert (
            _GIRLS_POLICY.low_quality_max_purchase_mode
            == PurchaseMode.WATCH_ONLY
        )

    def test_low_quality_cap_for_normal_line_is_tentative(self):
        from app.decision.race_type_policy import _NORMAL_LINE_POLICY
        assert (
            _NORMAL_LINE_POLICY.low_quality_max_purchase_mode
            == PurchaseMode.TENTATIVE
        )


# ---------------------------------------------------------------------------
# C. build_output_plan で race_type が設定される
# ---------------------------------------------------------------------------


class TestBuildOutputPlanSetsRaceType:
    def test_normal_line_race_type(self):
        ri = _ri(class_name="A級一般", odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
        ], recent_results=[
            {"date": "2026-05-23", "venue": "テスト",
             "race_no": 1, "result": "1-2-3", "memo": "x"},
        ])
        pred = _pred(honsen=[_bet("1-2-3", market_odds=6.0)])
        plan = build_output_plan(pred, ri)
        assert plan.race_type == "normal_line"
        assert plan.race_type_policy_notes

    def test_girls_race_type(self):
        ri = _ri(class_name="ガールズ", is_girls=True, odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
        ])
        pred = _pred(
            is_girls=True,
            honsen=[_bet("1-2-3", market_odds=6.0)],
        )
        plan = build_output_plan(pred, ri)
        assert plan.race_type == "girls"

    def test_girls_rookie_race_type(self):
        ri = _ri(class_name="ガールズ新人決勝", is_girls=True, odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
        ])
        pred = _pred(
            is_girls=True,
            honsen=[_bet("1-2-3", market_odds=6.0)],
        )
        plan = build_output_plan(pred, ri)
        assert plan.race_type == "girls_rookie"


# ---------------------------------------------------------------------------
# D. DecisionContext で policy が反映される
# ---------------------------------------------------------------------------


class TestDecisionContextReflectsPolicy:
    def test_girls_low_quality_forces_watch_only(self):
        """ガールズ + data_quality=low → force_watch_only で WATCH_ONLY 以下。"""
        # data_quality=low を発動: stats_missing 5名 (7名中)
        riders = [
            {"car_no": 1, "name": "R1", "score": 70.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "南関東"},
            {"car_no": 2, "name": "R2", "score": 70.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "南関東"},
        ] + [
            {"car_no": i, "name": f"R{i}", "score": 0.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "stats_missing": True, "home_area": "南関東"}
            for i in range(3, 8)
        ]
        ri = _ri(
            class_name="ガールズ", is_girls=True,
            riders=riders,
            odds=[{"bet_type": "3連単", "combination": "1-2-3",
                   "odds": 6.0}],
        )
        pred = _pred(
            is_girls=True,
            honsen=[_bet("1-2-3", market_odds=6.0)],
        )
        plan = build_output_plan(pred, ri)
        # race_type=girls で data_quality=low → WATCH_ONLY 以下
        assert plan.purchase_mode <= PurchaseMode.WATCH_ONLY
        # reason に race_type=girls が含まれる
        joined = " ".join(plan.decision_notes)
        assert "girls" in joined

    def test_girls_rookie_coverage_below_25_caps_to_watch_only(self):
        """ガールズ新人 + coverage<0.20 → SKIP (race_type_policy の閾値、
        force_watch_only も併せて適用されるため、結果は SKIP or WATCH_ONLY)。
        """
        ri = _ri(
            class_name="ガールズ新人決勝", is_girls=True,
            odds=[
                # 全 3連単候補に対して取得済みが少なすぎる構成
                # → coverage 低 → SKIP 寄り
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
            ],
            recent_results=[
                {"date": "2026-05-23", "venue": "テスト", "race_no": 1,
                 "result": "1-2-3", "memo": "x"},
            ],
        )
        # honsen 多めで coverage を下げる
        pred = _pred(
            is_girls=True,
            honsen=[_bet(c, market_odds=None) for c in [
                "1-2-3", "2-1-3", "3-1-2", "1-3-2", "2-3-1",
                "3-2-1", "1-2-4", "1-2-5", "2-1-5", "3-1-4",
            ]],
        )
        plan = build_output_plan(pred, ri)
        # purchase_mode は SKIP or WATCH_ONLY (BUYABLE/TENTATIVE にならない)
        assert plan.purchase_mode <= PurchaseMode.WATCH_ONLY


# ---------------------------------------------------------------------------
# E. Renderer 表示
# ---------------------------------------------------------------------------


class TestCodexP1Regressions:
    """codex P1 回帰テスト (Phase 4 後続レビュー反映)。"""

    def test_PHASE4_P1_girls_rookie_coverage_22pct_not_buyable(self):
        """codex P1: girls_rookie で全体 coverage 22% でも BUYABLE になる
        バグの回帰。girls_rookie.low_coverage_threshold=0.25 (= girls と同じ)
        にしたので、22% < 25% → WATCH_ONLY 以下になる。
        """
        ri = _ri(
            class_name="ガールズ新人決勝", is_girls=True,
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0},
                {"bet_type": "3連単", "combination": "2-1-3", "odds": 12.0},
            ],
            recent_results=[
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "x"},
            ],
        )
        # honsen 2点 + ana 7点 = 9点。odds 取得済み 2点 → coverage 22%
        ana_bets = [
            BetRecommendation(
                category="穴", bet_type="3連単", combination=c,
                reason="t", gami_risk=0.0, market_odds=None,
            )
            for c in (
                "3-1-2", "3-2-1", "1-3-2", "2-3-1",
                "1-2-4", "2-1-4", "4-1-2",
            )
        ]
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=8.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
            ],
            osae=[], ana=ana_bets, ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # race_type=girls_rookie で coverage 22% → 種別閾値 25% 未満
        # → WATCH_ONLY 以下になる
        assert plan.race_type == "girls_rookie"
        assert plan.purchase_mode <= PurchaseMode.WATCH_ONLY, (
            f"purchase_mode={plan.purchase_mode.name} "
            f"reasons={plan.decision_notes}"
        )

    def test_PHASE4_P1_is_girls_synced_from_input_data(self):
        """codex P1: prediction.is_girls=False でも input_data.race が
        ガールズなら、race_type=girls で sanitize も実行される
        (本命ライン等が出ない)。"""
        ri = _ri(class_name="ガールズ", is_girls=True, odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0},
        ])
        pred = Prediction(
            race_id="t", venue="t", race_no=1,
            is_girls=False,  # わざと False (input_data はガールズ)
            summary="本命ライン優勢、番手差し決まりやすい",
            venue_trend_text="本命ラインの番手差し",
            weather_text="",
            lines_text="",
            marks={},
            honsen=[_bet("1-2-3", market_odds=8.0, reason="本命番手")],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        md = render_prediction_v2(pred, input_data=ri)
        # race_type=girls が表示される
        assert "### レース種別: girls" in md
        # 本文に「本命ライン」「番手差し」が残らない (sanitize 実行)
        # 警告セクション以前で確認
        body = md
        for sep in ("### 出力整合性チェック", "### OutputPlan 警告"):
            if sep in body:
                body = body[:body.rfind(sep)]
        assert "本命ライン" not in body, body[-1500:]
        assert "番手差し" not in body, body[-1500:]


class TestMaxFinalBestLimit:
    """Phase 4 後続: policy.max_final_best で final_best が制限される。"""

    def test_girls_rookie_caps_final_best_to_2(self):
        """girls_rookie で final_best が 3 点 → 2 点に制限。
        超過分は WATCH_ONLY 以下なので watch_only に prepend。"""
        from app.output_plan import (
            OutputPlan, _apply_max_final_best_limit, _apply_race_type_policy,
        )

        ri = _ri(class_name="ガールズ新人決勝", is_girls=True)
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", market_odds=8.0),
                _bet("2-1-3", market_odds=12.0),
                _bet("3-1-2", market_odds=15.0),
            ],
            purchase_mode=PurchaseMode.WATCH_ONLY,
        )
        _apply_race_type_policy(plan, ri)
        _apply_max_final_best_limit(plan)
        # girls_rookie は max_final_best=2 → 2 点に切り詰め
        assert len(plan.final_best) == 2
        assert [b.combination for b in plan.final_best] == ["1-2-3", "2-1-3"]
        # 超過分 (3-1-2) は WATCH_ONLY なので watch_only に
        assert "3-1-2" in [b.combination for b in plan.watch_only]
        # decision_notes に「最大2点に制限」
        notes = " ".join(plan.decision_notes)
        assert "最大 2 点" in notes or "制限" in notes

    def test_buyable_overflow_goes_to_final_osae(self):
        """BUYABLE のとき、超過分は watch_only ではなく final_osae に格下げ。"""
        from app.output_plan import (
            OutputPlan, _apply_max_final_best_limit, _apply_race_type_policy,
        )

        ri = _ri(class_name="ガールズ新人決勝", is_girls=True)
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", market_odds=8.0),
                _bet("2-1-3", market_odds=12.0),
                _bet("3-1-2", market_odds=15.0),
            ],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        _apply_race_type_policy(plan, ri)
        _apply_max_final_best_limit(plan)
        assert len(plan.final_best) == 2
        # 超過分は final_osae に
        assert "3-1-2" in [b.combination for b in plan.final_osae]
        # watch_only には移動しない (BUYABLE は格下げ先が osae)
        assert "3-1-2" not in [b.combination for b in plan.watch_only]

    def test_normal_line_keeps_3_points(self):
        """normal_line は max_final_best=3 → 3 点は制限しない。"""
        from app.output_plan import (
            OutputPlan, _apply_max_final_best_limit, _apply_race_type_policy,
        )

        ri = _ri(class_name="A級一般")
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", market_odds=8.0),
                _bet("2-1-3", market_odds=12.0),
                _bet("3-1-2", market_odds=15.0),
            ],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        _apply_race_type_policy(plan, ri)
        _apply_max_final_best_limit(plan)
        # 3 点維持
        assert len(plan.final_best) == 3
        # 制限 note は出ない
        notes = " ".join(plan.decision_notes)
        assert "最大 3 点に制限" not in notes

    def test_normal_line_caps_4_points_to_3(self):
        """normal_line で final_best=4 点 → 3 点に制限。"""
        from app.output_plan import (
            OutputPlan, _apply_max_final_best_limit, _apply_race_type_policy,
        )

        ri = _ri(class_name="A級一般")
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", market_odds=8.0),
                _bet("2-1-3", market_odds=12.0),
                _bet("3-1-2", market_odds=15.0),
                _bet("1-3-2", market_odds=18.0),
            ],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        _apply_race_type_policy(plan, ri)
        _apply_max_final_best_limit(plan)
        assert len(plan.final_best) == 3
        assert "1-3-2" in [b.combination for b in plan.final_osae]

    def test_no_limit_when_no_race_type_policy(self):
        """plan._race_type_policy が無い (input_data=None で
        build_output_plan を経由しない) ケースでは制限しない。"""
        from app.output_plan import (
            OutputPlan, _apply_max_final_best_limit,
        )

        plan = OutputPlan(
            final_best=[
                _bet("1-2-3"), _bet("2-1-3"), _bet("3-1-2"),
                _bet("1-3-2"), _bet("2-3-1"),
            ],
        )
        # _race_type_policy 未設定
        _apply_max_final_best_limit(plan)
        # 何も変わらない
        assert len(plan.final_best) == 5

    def test_e2e_girls_rookie_build_output_plan_caps_final_best(self):
        """E2E: build_output_plan で girls_rookie の final_best が 2 点に。"""
        ri = _ri(
            class_name="ガールズ新人決勝", is_girls=True,
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0},
                {"bet_type": "3連単", "combination": "2-1-3", "odds": 12.0},
                {"bet_type": "3連単", "combination": "3-1-2", "odds": 15.0},
            ],
            recent_results=[
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "x"},
            ],
        )
        pred = _pred(
            is_girls=True,
            honsen=[
                _bet("1-2-3", market_odds=8.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
                _bet("3-1-2", market_odds=15.0, value_label="妙味あり"),
            ],
        )
        plan = build_output_plan(pred, ri)
        # girls_rookie の policy が適用される
        assert plan.race_type == "girls_rookie"
        # max_final_best=2 で制限
        assert len(plan.final_best) <= 2, (
            f"final_best が 2 点を超えた: "
            f"{[b.combination for b in plan.final_best]} "
            f"reasons={plan.decision_notes}"
        )


class TestRendererShowsRaceTypeSection:
    def test_normal_line_section_appears(self):
        ri = _ri(class_name="A級一般", odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
        ], recent_results=[
            {"date": "2026-05-23", "venue": "テスト",
             "race_no": 1, "result": "1-2-3", "memo": "x"},
        ])
        pred = _pred(honsen=[_bet("1-2-3", market_odds=6.0)])
        md = render_prediction_v2(pred, input_data=ri)
        assert "### レース種別: normal_line" in md

    def test_girls_section_appears(self):
        ri = _ri(class_name="ガールズ", is_girls=True, odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
        ])
        pred = _pred(
            is_girls=True,
            honsen=[_bet("1-2-3", market_odds=6.0)],
        )
        md = render_prediction_v2(pred, input_data=ri)
        assert "### レース種別: girls" in md

    def test_girls_rookie_section_appears(self):
        ri = _ri(class_name="ガールズ新人決勝", is_girls=True, odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
        ])
        pred = _pred(
            is_girls=True,
            honsen=[_bet("1-2-3", market_odds=6.0)],
        )
        md = render_prediction_v2(pred, input_data=ri)
        assert "### レース種別: girls_rookie" in md
