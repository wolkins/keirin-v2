"""data_quality 5項目分解 (#377)。

検証:
- assess_data_quality_breakdown が score/odds/kimarite/recent/weather の
  5項目を bool で返す
- overall フィールドは既存 assess_data_quality と整合
- to_markdown_lines は 5行返す (○ / × 付き)
- render_prediction_v2 出力に内訳が含まれる
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_validation import (
    assess_data_quality,
    assess_data_quality_breakdown,
)


def _ri(
    *,
    riders_missing=0,
    odds=True,
    kimarite=True,
    recent=True,
    weather=True,
) -> RaceInput:
    riders = [
        {
            "car_no": i, "name": f"R{i}",
            "score": 85.0 if i > riders_missing else 0.0,
            "b_count": 1, "nige": 1 if kimarite else 0,
            "makuri": 0, "sashi": 0, "mark": 0,
            "comment": "", "home_area": "中部",
            "stats_missing": i <= riders_missing,
        }
        for i in range(1, 8)
    ]
    payload = {
        "race": {"race_id": "t", "date": "2026-05-24",
                 "venue": "静岡", "race_no": 4,
                 "class_name": "A級一般", "start_time": "11:30"},
        "lines": [{"line_name": f"L{i}", "cars": [i]} for i in range(1, 8)],
        "riders": riders,
        "odds": ([{"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0}]
                 if odds else []),
        "recent_results": ([{"date": "2026-05-23", "venue": "静岡",
                             "race_no": 1, "result": "1-2-3", "memo": "x"}]
                           if recent else []),
    }
    if weather:
        payload["weather"] = {
            "condition": "晴れ", "rain_mm_per_hour": 0.0,
            "wind_speed_mps": 2.0,
        }
    return RaceInput.model_validate(payload)


class TestBreakdownFields:
    def test_all_present(self):
        b = assess_data_quality_breakdown(_ri())
        assert b.score is True
        assert b.odds is True
        assert b.kimarite is True
        assert b.recent is True
        assert b.weather is True

    def test_score_missing(self):
        # 7名中5名 stats_missing → score_ratio=2/7<0.8
        b = assess_data_quality_breakdown(_ri(riders_missing=5))
        assert b.score is False

    def test_odds_missing(self):
        b = assess_data_quality_breakdown(_ri(odds=False))
        assert b.odds is False

    def test_kimarite_missing(self):
        b = assess_data_quality_breakdown(_ri(kimarite=False))
        assert b.kimarite is False

    def test_recent_missing(self):
        b = assess_data_quality_breakdown(_ri(recent=False))
        assert b.recent is False

    def test_weather_missing(self):
        b = assess_data_quality_breakdown(_ri(weather=False))
        assert b.weather is False


class TestBreakdownOverall:
    def test_overall_matches_assess(self):
        ri = _ri()
        b = assess_data_quality_breakdown(ri)
        assert b.overall == assess_data_quality(ri)

    def test_all_riders_missing_yields_very_low(self):
        """全 rider が stats_missing → score=False, overall=very_low。"""
        ri = _ri(riders_missing=7, odds=False)
        b = assess_data_quality_breakdown(ri)
        assert b.overall == "very_low"
        assert b.score is False


class TestMarkdownLines:
    def test_returns_5_lines(self):
        b = assess_data_quality_breakdown(_ri())
        ms = b.to_markdown_lines()
        assert len(ms) == 5

    def test_marks_indicate_status(self):
        b = assess_data_quality_breakdown(_ri(odds=False))
        ms = b.to_markdown_lines()
        joined = "\n".join(ms)
        assert "× オッズ" in joined
        assert "○ 競走得点" in joined


class TestRenderIntegration:
    def test_breakdown_appears_in_markdown(self):
        ri = _ri()
        pred = Prediction(
            race_id="t", venue="静岡", race_no=4, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.0, market_odds=8.0,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        md = render_prediction_v2(pred, input_data=ri)
        assert "### データ品質:" in md
        assert "競走得点 (80%+揃い)" in md
        assert "天候情報 (風速/雨量/天気)" in md
