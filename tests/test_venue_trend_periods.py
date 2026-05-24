"""場の傾向 長期/当日 分離 (#378).

検証:
- VenueTrend に long_term / today (Optional[str]) フィールドが追加され、
  既存 note + favors と共存できる
- markdown_renderer の ## 2 で long_term / today があれば併記表示
- どちらも None なら従来通り (note ベース表示のみ)
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.models import BetRecommendation, Prediction, RaceInput, VenueTrend


def _ri(*, long_term=None, today=None) -> RaceInput:
    payload = {
        "race": {"race_id": "t", "date": "2026-05-24",
                 "venue": "静岡", "race_no": 4,
                 "class_name": "A級一般", "start_time": "11:30"},
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                    "wind_speed_mps": 2.0},
        "lines": [{"line_name": f"L{i}", "cars": [i]} for i in range(1, 8)],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 85.0, "b_count": 0,
             "nige": 1 if i == 1 else 0, "makuri": 0,
             "sashi": 1 if i == 2 else 0, "mark": 0,
             "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": [{"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0}],
        "recent_results": [],
    }
    trend: dict = {"note": "番手差し優勢", "favors": ["番手"]}
    if long_term is not None:
        trend["long_term"] = long_term
    if today is not None:
        trend["today"] = today
    payload["venue_trend"] = trend
    return RaceInput.model_validate(payload)


def _pred() -> Prediction:
    return Prediction(
        race_id="t", venue="静岡", race_no=4, is_girls=False,
        summary="", venue_trend_text="番手差し優勢",
        weather_text="", lines_text="", marks={},
        honsen=[BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.0, market_odds=8.0,
        )],
        osae=[], ana=[], ooana=[],
        final_conclusion="", gami_memo="", reflection_points=[],
    )


class TestVenueTrendFields:
    def test_long_term_and_today_are_optional(self):
        """long_term / today を指定せず note + favors のみで生成できる
        (後方互換)。"""
        vt = VenueTrend(note="番手差し優勢", favors=["番手"])
        assert vt.long_term is None
        assert vt.today is None

    def test_long_term_and_today_can_be_set(self):
        vt = VenueTrend(
            note="番手差し優勢",
            favors=["番手"],
            long_term="直行先行が有利な開催が続く",
            today="今日は風弱く前残り",
        )
        assert vt.long_term == "直行先行が有利な開催が続く"
        assert vt.today == "今日は風弱く前残り"


class TestRenderIntegration:
    def test_long_term_appears_in_section_2(self):
        ri = _ri(long_term="開催を通じて番手が連発")
        md = render_prediction_v2(_pred(), input_data=ri)
        section_2 = md.split("## 2.")[1].split("## 3.")[0]
        assert "**長期傾向**: 開催を通じて番手が連発" in section_2
        # today が無いと **当日傾向** は出ない
        assert "**当日傾向**:" not in section_2

    def test_today_appears_in_section_2(self):
        ri = _ri(today="本日4R番手2着連発")
        md = render_prediction_v2(_pred(), input_data=ri)
        section_2 = md.split("## 2.")[1].split("## 3.")[0]
        assert "**当日傾向**: 本日4R番手2着連発" in section_2
        assert "**長期傾向**:" not in section_2

    def test_both_appear(self):
        ri = _ri(long_term="長期は番手有利",
                 today="当日は3番手が決め手")
        md = render_prediction_v2(_pred(), input_data=ri)
        section_2 = md.split("## 2.")[1].split("## 3.")[0]
        assert "**長期傾向**: 長期は番手有利" in section_2
        assert "**当日傾向**: 当日は3番手が決め手" in section_2

    def test_neither_keeps_legacy_display(self):
        """long_term / today が無いと従来通り note のみ表示。"""
        ri = _ri()  # 両方 None
        md = render_prediction_v2(_pred(), input_data=ri)
        section_2 = md.split("## 2.")[1].split("## 3.")[0]
        assert "**長期傾向**:" not in section_2
        assert "**当日傾向**:" not in section_2
