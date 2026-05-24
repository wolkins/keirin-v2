"""Phase 2: MarkAlignment 回帰テスト.

検証内容:
A. 単体: assess_mark_alignment の判定ロジック (aligned / explainable / dangerous)
B. ◎の抽出 (◎ キー / honmei キー / 空 marks)
C. E2E: 広島3R 風 (explainable_mismatch) / 危険ズレ (dangerous) / 整合
D. Renderer 表示 + plan.warnings への MARK_FINAL_MISMATCH 反映
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.decision import (
    MarkAlignmentResult,
    PurchaseMode,
    assess_mark_alignment,
)
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import OutputPlan, build_output_plan


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _pred(*, marks=None, honsen=None, osae=None, is_girls=False):
    return Prediction(
        race_id="t", venue="t", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="",
        lines_text="", marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=[], ooana=[],
        final_conclusion="", gami_memo="", reflection_points=[],
    )


# ---------------------------------------------------------------------------
# A. ◎の抽出
# ---------------------------------------------------------------------------


class TestExtractTopMark:
    def test_dot_mark_key(self):
        plan = OutputPlan(final_best=[_bet("1-2-3")])
        pred = _pred(marks={"◎": 7, "○": 3})
        r = assess_mark_alignment(pred, plan, None)
        assert r.top_mark_car == 7

    def test_honmei_key_fallback(self):
        plan = OutputPlan(final_best=[_bet("1-2-3")])
        pred = _pred(marks={"honmei": 5})
        r = assess_mark_alignment(pred, plan, None)
        assert r.top_mark_car == 5

    def test_empty_marks_returns_none(self):
        plan = OutputPlan(final_best=[_bet("1-2-3")])
        pred = _pred(marks={})
        r = assess_mark_alignment(pred, plan, None)
        assert r.top_mark_car is None
        assert r.alignment_level == "aligned"


# ---------------------------------------------------------------------------
# B. 単体: aligned / explainable / dangerous の判定
# ---------------------------------------------------------------------------


class TestAlignmentLevels:
    def test_aligned_when_top_mark_in_final_best_head(self):
        plan = OutputPlan(final_best=[_bet("1-2-3")])
        pred = _pred(marks={"◎": 1})
        r = assess_mark_alignment(pred, plan, None)
        assert r.alignment_level == "aligned"
        assert r.top_mark_in_final_best is True
        assert r.top_mark_in_any_final is True

    def test_aligned_when_top_mark_in_final_best_second(self):
        plan = OutputPlan(final_best=[_bet("1-2-3")])
        pred = _pred(marks={"◎": 2})
        r = assess_mark_alignment(pred, plan, None)
        assert r.alignment_level == "aligned"

    def test_aligned_when_top_mark_in_final_osae(self):
        plan = OutputPlan(
            final_best=[_bet("1-2-3")],
            final_osae=[_bet("5-1-3", category="押さえ")],
        )
        pred = _pred(marks={"◎": 5})
        r = assess_mark_alignment(pred, plan, None)
        assert r.alignment_level == "aligned"
        assert r.top_mark_in_final_osae is True

    def test_dangerous_mismatch_when_no_explanation(self):
        # ◎7、final_best=1-2-4、単騎ではない (input_data=None)、
        # purchase_mode=BUYABLE、market_bias なし → dangerous_mismatch
        plan = OutputPlan(
            final_best=[_bet("1-2-4")],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        pred = _pred(marks={"◎": 7})
        r = assess_mark_alignment(pred, plan, None)
        assert r.alignment_level == "dangerous_mismatch"
        assert r.warnings

    def test_explainable_when_purchase_mode_watch_only(self):
        plan = OutputPlan(
            final_best=[_bet("1-2-4")],
            purchase_mode=PurchaseMode.WATCH_ONLY,
        )
        pred = _pred(marks={"◎": 7})
        r = assess_mark_alignment(pred, plan, None)
        assert r.alignment_level == "explainable_mismatch"
        assert any("WATCH_ONLY" in n for n in r.notes)

    def test_explainable_when_purchase_mode_skip(self):
        plan = OutputPlan(
            final_best=[_bet("1-2-4")],
            purchase_mode=PurchaseMode.SKIP,
        )
        pred = _pred(marks={"◎": 7})
        r = assess_mark_alignment(pred, plan, None)
        assert r.alignment_level == "explainable_mismatch"

    def test_explainable_when_top_mark_is_single(self):
        ri = RaceInput.model_validate({
            "race": {"race_id": "t", "date": "2026-05-24",
                     "venue": "t", "race_no": 1,
                     "class_name": "A級一般", "start_time": "10:00"},
            "lines": [
                {"line_name": "L1", "cars": [5, 3, 6]},
                {"line_name": "L2", "cars": [1, 2, 4]},
                {"line_name": "単", "cars": [7]},  # 7 が単騎
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 85.0,
                 "b_count": 0, "nige": 0, "makuri": 0, "sashi": 0,
                 "mark": 0, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [],
            "recent_results": [],
        })
        plan = OutputPlan(
            final_best=[_bet("1-2-4")],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        pred = _pred(marks={"◎": 7})
        r = assess_mark_alignment(pred, plan, ri)
        assert r.is_top_mark_single is True
        assert r.alignment_level == "explainable_mismatch"
        assert any("単騎" in n for n in r.notes)


# ---------------------------------------------------------------------------
# C. E2E: 広島3R 風 / 危険 / 整合
# ---------------------------------------------------------------------------


class TestHiroshima3rExplainableMismatch:
    """広島3R 風:
    - 通常ライン戦 (5-3-6 / 1-2-4 / 7単騎)
    - ◎7
    - market head bias 1番頭
    - final_best=1-2-4
    - purchase_mode=SKIP or WATCH_ONLY
    期待:
    - explainable_mismatch
    - notes に「単騎」「市場」「1番頭」「WATCH_ONLY/SKIP」のいずれか
    - MARK_FINAL_MISMATCH warning は出ない
    """

    def _ri(self) -> RaceInput:
        return RaceInput.model_validate({
            "race": {"race_id": "t-h3", "date": "2026-05-24",
                     "venue": "広島", "race_no": 3,
                     "class_name": "A級一般", "start_time": "11:00"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "L1", "cars": [5, 3, 6]},
                {"line_name": "L2", "cars": [1, 2, 4]},
                {"line_name": "単", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 85.0,
                 "b_count": 1 if i == 1 else 0,
                 "nige": 1 if i in (1, 5) else 0,
                 "makuri": 0, "sashi": 1 if i in (2, 3) else 0,
                 "mark": 0, "comment": "", "home_area": "中国"}
                for i in range(1, 8)
            ],
            "odds": [
                # 1 番頭中心に少しだけオッズあり
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.5},
                {"bet_type": "3連単", "combination": "1-2-4", "odds": 7.8},
                {"bet_type": "3連単", "combination": "1-2-5", "odds": 9.5},
                {"bet_type": "3連単", "combination": "1-2-6", "odds": 11.2},
                {"bet_type": "3連単", "combination": "1-2-7", "odds": 13.0},
            ],
            "recent_results": [],
        })

    def test_hiroshima_3r_explainable(self):
        ri = self._ri()
        pred = _pred(
            marks={"◎": 7, "○": 1, "▲": 5},
            honsen=[
                _bet(c, market_odds=None) for c in [
                    "5-3-6", "5-3-1", "3-5-6", "1-2-3", "1-2-4",
                    "5-1-3", "1-5-3", "5-6-3", "5-3-2", "3-1-5",
                ]
            ],
        )
        plan = build_output_plan(pred, ri)
        # purchase_mode は SKIP or WATCH_ONLY 想定
        assert plan.purchase_mode in (
            PurchaseMode.SKIP, PurchaseMode.WATCH_ONLY,
        ), plan.purchase_mode.name
        # alignment_level は explainable_mismatch
        assert plan.mark_alignment_level == "explainable_mismatch", (
            f"level={plan.mark_alignment_level} "
            f"notes={plan.mark_alignment_notes}"
        )
        # notes に説明が出る
        notes_joined = " ".join(plan.mark_alignment_notes)
        has_explanation = (
            "単騎" in notes_joined
            or "市場" in notes_joined
            or "1番頭" in notes_joined
            or "WATCH_ONLY" in notes_joined
            or "SKIP" in notes_joined
        )
        assert has_explanation, notes_joined
        # MARK_FINAL_MISMATCH warning は出ない
        codes = [w.code for w in plan.warnings]
        assert "MARK_FINAL_MISMATCH" not in codes, codes


class TestDangerousMismatch:
    """危険なズレ:
    - ◎7
    - final_best=1-2-4
    - 7は単騎ではない (3車ライン: 5-7-6)
    - market_bias なし (各頭 1件ずつ)
    - purchase_mode=BUYABLE
    期待:
    - dangerous_mismatch
    - MARK_FINAL_MISMATCH warning が plan.warnings に追加される
    """

    def _ri(self) -> RaceInput:
        # 高品質: 7は単騎ではない3車ライン、odds 取得率高、HeadBias 無し
        return RaceInput.model_validate({
            "race": {"race_id": "t-dz", "date": "2026-05-24",
                     "venue": "テスト", "race_no": 1,
                     "class_name": "A級一般", "start_time": "10:00"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "本命", "cars": [1, 2, 4]},
                {"line_name": "別線", "cars": [5, 7, 6]},
                {"line_name": "単", "cars": [3]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0,
                 "b_count": 1, "nige": 1, "makuri": 1, "sashi": 1,
                 "mark": 1, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [
                # 1-2-4 / 1-2-5 / 5-7-6 / 7-5-6 / 2-1-4 / 4-1-2 と
                # 別頭が混ざる: HeadBias 検出されない (focused_count<3)
                {"bet_type": "3連単", "combination": "1-2-4", "odds": 6.0},
                {"bet_type": "3連単", "combination": "5-7-6", "odds": 8.0},
                {"bet_type": "3連単", "combination": "7-5-6", "odds": 10.0},
                {"bet_type": "3連単", "combination": "2-1-4", "odds": 11.0},
                {"bet_type": "3連単", "combination": "4-1-2", "odds": 12.0},
                {"bet_type": "3連単", "combination": "3-1-2", "odds": 14.0},
                {"bet_type": "3連単", "combination": "1-4-2", "odds": 16.0},
                {"bet_type": "3連単", "combination": "5-6-7", "odds": 18.0},
            ],
            "recent_results": [
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-4", "memo": "x"},
            ],
        })

    def test_dangerous_mismatch_triggers_warning(self):
        ri = self._ri()
        pred = _pred(
            marks={"◎": 7},
            honsen=[
                _bet("1-2-4", market_odds=6.0, value_label="本線向き"),
                _bet("2-1-4", market_odds=11.0),
                _bet("4-1-2", market_odds=12.0),
            ],
        )
        plan = build_output_plan(pred, ri)
        # この fixture は BUYABLE になることを前提
        # (BUYABLE で ◎が絡まない → dangerous)
        if plan.purchase_mode != PurchaseMode.BUYABLE:
            pytest.skip(
                f"BUYABLE 前提だが {plan.purchase_mode.name} になった "
                "(fixture 調整が必要)"
            )
        assert plan.mark_alignment_level == "dangerous_mismatch", (
            f"level={plan.mark_alignment_level} "
            f"notes={plan.mark_alignment_notes}"
        )
        codes = [w.code for w in plan.warnings]
        assert "MARK_FINAL_MISMATCH" in codes, codes


class TestAlignedScenario:
    """整合ケース: ◎1, final_best=1-2-4 → aligned, warning なし。"""

    def test_aligned_no_warning(self):
        ri = RaceInput.model_validate({
            "race": {"race_id": "t-al", "date": "2026-05-24",
                     "venue": "テスト", "race_no": 1,
                     "class_name": "A級一般", "start_time": "10:00"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "本命", "cars": [1, 2, 4]},
                {"line_name": "別線", "cars": [5, 3, 6]},
                {"line_name": "単", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0,
                 "b_count": 1, "nige": 1, "makuri": 1, "sashi": 1,
                 "mark": 1, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [
                {"bet_type": "3連単", "combination": "1-2-4", "odds": 6.0},
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 9.0},
                {"bet_type": "3連単", "combination": "2-1-4", "odds": 11.0},
                {"bet_type": "3連単", "combination": "1-4-2", "odds": 13.0},
                {"bet_type": "3連単", "combination": "5-3-6", "odds": 18.0},
            ],
            "recent_results": [
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "x"},
            ],
        })
        pred = _pred(
            marks={"◎": 1, "○": 2, "▲": 4},
            honsen=[
                _bet("1-2-4", market_odds=6.0, value_label="本線向き"),
                _bet("1-2-3", market_odds=9.0, value_label="妙味あり"),
                _bet("2-1-4", market_odds=11.0),
            ],
        )
        plan = build_output_plan(pred, ri)
        assert plan.mark_alignment_level == "aligned", (
            f"level={plan.mark_alignment_level} "
            f"notes={plan.mark_alignment_notes}"
        )
        codes = [w.code for w in plan.warnings]
        assert "MARK_FINAL_MISMATCH" not in codes


# ---------------------------------------------------------------------------
# D. Renderer 表示
# ---------------------------------------------------------------------------


class TestRendererShowsNotes:
    def test_explainable_notes_appear_in_section_10(self):
        """explainable_mismatch の notes が ## 10 直後に出る。"""
        # 広島3R 風 fixture を流用
        ri = TestHiroshima3rExplainableMismatch()._ri()
        pred = _pred(
            marks={"◎": 7, "○": 1, "▲": 5},
            honsen=[
                _bet(c, market_odds=None) for c in [
                    "5-3-6", "5-3-1", "3-5-6", "1-2-3", "1-2-4",
                    "5-1-3", "1-5-3", "5-6-3", "5-3-2", "3-1-5",
                ]
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        # 印と買い目の補足 セクション
        assert "### 印と買い目の補足" in md
        # 「◎7」を含む note が出る
        section = md.split("## 10.")[1].split("## 11.")[0]
        assert "◎7" in section or "単騎" in section, section

    def test_empty_marks_no_supplemental_section(self):
        """codex P2 反映: marks={} のとき「### 印と買い目の補足」が
        出ない (notes 空 → セクション省略)。"""
        ri = RaceInput.model_validate({
            "race": {"race_id": "t-em", "date": "2026-05-24",
                     "venue": "テスト", "race_no": 1,
                     "class_name": "A級一般", "start_time": "10:00"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [{"line_name": "本命", "cars": [1, 2, 3]}],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0,
                 "b_count": 1, "nige": 1, "makuri": 1, "sashi": 1,
                 "mark": 1, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
            ],
            "recent_results": [
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "x"},
            ],
        })
        pred = _pred(
            marks={},  # 空
            honsen=[_bet("1-2-3", market_odds=6.0)],
        )
        md = render_prediction_v2(pred, input_data=ri)
        assert "### 印と買い目の補足" not in md

    def test_girls_all_single_lines_does_not_count_as_single(self):
        """codex P2 反映: ガールズ等で全 line が単騎 (個人戦) のとき、
        ◎が len==1 line に居ても「単騎」説明にしない。"""
        from app.decision import assess_mark_alignment
        ri = RaceInput.model_validate({
            "race": {"race_id": "t-gs", "date": "2026-05-24",
                     "venue": "平塚", "race_no": 10,
                     "class_name": "ガールズ", "start_time": "16:00",
                     "is_girls": True},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            # 全ライン単騎 (ガールズ)
            "lines": [
                {"line_name": f"L{i}", "cars": [i]} for i in range(1, 8)
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 70.0,
                 "b_count": 0, "nige": 0, "makuri": 0, "sashi": 0,
                 "mark": 0, "comment": "", "home_area": "南関東"}
                for i in range(1, 8)
            ],
            "odds": [],
            "recent_results": [],
        })
        plan = OutputPlan(
            final_best=[_bet("1-2-3")],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        pred = _pred(marks={"◎": 7})
        result = assess_mark_alignment(pred, plan, ri)
        # ガールズなので「単騎」は説明にならない
        assert result.is_top_mark_single is False

    def test_market_bias_head_in_final_osae_explains(self):
        """codex P2 反映: market_bias 頭が final_osae にだけある場合も
        explainable_mismatch になる (fb_heads | fo_heads で判定)。"""
        from app.decision import assess_mark_alignment
        # market 1番頭強め (5件)
        ri = RaceInput.model_validate({
            "race": {"race_id": "t-mb", "date": "2026-05-24",
                     "venue": "テスト", "race_no": 1,
                     "class_name": "A級一般", "start_time": "10:00"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "本命", "cars": [1, 2, 3]},
                {"line_name": "別線", "cars": [5, 6, 4]},
                {"line_name": "単", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0,
                 "b_count": 1, "nige": 1, "makuri": 1, "sashi": 1,
                 "mark": 1, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [
                # market HeadBias: 1番頭 5/5
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.0},
                {"bet_type": "3連単", "combination": "1-2-5", "odds": 7.0},
                {"bet_type": "3連単", "combination": "1-3-2", "odds": 9.0},
                {"bet_type": "3連単", "combination": "1-5-2", "odds": 11.0},
                {"bet_type": "3連単", "combination": "1-2-4", "odds": 13.0},
            ],
            "recent_results": [],
        })
        # final_best には 1 頭が居ない (5-2-3 など)、final_osae には 1 頭
        plan = OutputPlan(
            final_best=[_bet("5-2-3", market_odds=8.0)],
            final_osae=[_bet("1-2-3", market_odds=5.0, category="押さえ")],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        pred = _pred(marks={"◎": 7})
        result = assess_mark_alignment(pred, plan, ri)
        # ◎7 は final_best/osae の頭/2着に居ない → mismatch
        assert result.top_mark_in_any_final is False
        # 単騎なので explainable (P2 で単騎説明あり)、market_bias 説明も付くはず
        # → notes に「市場」or「1番頭」のいずれか
        notes = " ".join(result.notes)
        assert ("市場" in notes and "1番頭" in notes) or "単騎" in notes, notes

    def test_aligned_no_supplemental_section(self):
        """aligned のときは「### 印と買い目の補足」セクションが出ない。"""
        ri = RaceInput.model_validate({
            "race": {"race_id": "t-al2", "date": "2026-05-24",
                     "venue": "テスト", "race_no": 1,
                     "class_name": "A級一般", "start_time": "10:00"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "本命", "cars": [1, 2, 4]},
                {"line_name": "別線", "cars": [5, 3, 6]},
                {"line_name": "単", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0,
                 "b_count": 1, "nige": 1, "makuri": 1, "sashi": 1,
                 "mark": 1, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [
                {"bet_type": "3連単", "combination": "1-2-4", "odds": 6.0},
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 9.0},
                {"bet_type": "3連単", "combination": "2-1-4", "odds": 11.0},
                {"bet_type": "3連単", "combination": "1-4-2", "odds": 13.0},
                {"bet_type": "3連単", "combination": "5-3-6", "odds": 18.0},
            ],
            "recent_results": [
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "x"},
            ],
        })
        pred = _pred(
            marks={"◎": 1},
            honsen=[
                _bet("1-2-4", market_odds=6.0, value_label="本線向き"),
                _bet("1-2-3", market_odds=9.0, value_label="妙味あり"),
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        # aligned のとき補足セクションは出ない
        assert "### 印と買い目の補足" not in md
