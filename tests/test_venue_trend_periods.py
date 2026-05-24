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


# ---------------------------------------------------------------------------
# a122ae1 後続レビュー: venue_trend.long_term / today のサニタイズ
# ---------------------------------------------------------------------------


def _girls_ri(*, long_term=None, today=None) -> RaceInput:
    """ガールズ新人戦 fixture (venue_trend 含む)。"""
    payload = {
        "race": {"race_id": "t-g", "date": "2026-05-24",
                 "venue": "平塚", "race_no": 10,
                 "class_name": "ガールズ新人決勝", "start_time": "16:30",
                 "is_girls": True},
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                    "wind_speed_mps": 2.0},
        "lines": [{"line_name": f"L{i}", "cars": [i]} for i in range(1, 8)],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 70.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "南関東"}
            for i in range(1, 8)
        ],
        "odds": [],
        "recent_results": [],
    }
    trend: dict = {"note": "前残り傾向", "favors": []}
    if long_term is not None:
        trend["long_term"] = long_term
    if today is not None:
        trend["today"] = today
    payload["venue_trend"] = trend
    return RaceInput.model_validate(payload)


def _girls_pred(is_girls=True) -> Prediction:
    return Prediction(
        race_id="t-g", venue="平塚", race_no=10, is_girls=is_girls,
        summary="", venue_trend_text="前残り傾向",
        weather_text="", lines_text="", marks={},
        honsen=[BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.0, market_odds=8.0,
        )],
        osae=[], ana=[], ooana=[],
        final_conclusion="", gami_memo="", reflection_points=[],
    )


class TestVenueTrendSanitizeGirls:
    """ガールズ新人戦: venue_trend.long_term / today から
    ライン前提語が消える。"""

    def test_today_with_forbidden_terms_is_sanitized(self):
        ri = _girls_ri(today="本命ラインの番手差し、ライン3番手の伸びが目立つ")
        md = render_prediction_v2(_girls_pred(), input_data=ri)
        section_2 = md.split("## 2.")[1].split("## 3.")[0]
        # 禁止語が消えること
        assert "本命ライン" not in section_2, section_2
        assert "番手差し" not in section_2, section_2
        assert "ライン3番手" not in section_2, section_2
        # 「番手」単独も置換される (「番手差し」が先に消費されるが、それ以外の
        # 「番手」も追走に置換されることを担保)
        assert "番手" not in section_2, section_2
        # 期待される置換結果
        assert "上位評価" in section_2  # 本命ライン → 上位評価
        assert "差し" in section_2       # 番手差し → 差し
        assert "中位" in section_2       # ライン3番手 → 中位

    def test_long_term_sanitized_too(self):
        ri = _girls_ri(long_term="本命頭が安定、本命自力も4番手評価まで届く")
        md = render_prediction_v2(_girls_pred(), input_data=ri)
        section_2 = md.split("## 2.")[1].split("## 3.")[0]
        assert "本命頭" not in section_2
        assert "本命自力" not in section_2
        assert "4番手評価" not in section_2
        # 期待される置換
        assert "上位評価の頭" in section_2
        assert "上位評価選手" in section_2
        assert "4位評価" in section_2

    def test_line_keyword_replaced(self):
        """「ライン」単独も「位置取り」に置換される。"""
        ri = _girls_ri(today="ライン構成より個々の脚力")
        md = render_prediction_v2(_girls_pred(), input_data=ri)
        section_2 = md.split("## 2.")[1].split("## 3.")[0]
        assert "ライン構成" not in section_2
        assert "位置取り構成" in section_2


class TestVenueTrendSanitizeNormalKeepsTerms:
    """通常ライン戦では venue_trend に「番手差し」「本命ライン」が出ても
    置換されない (事実記述として正しい用語)。"""

    def test_normal_race_keeps_line_terms(self):
        payload = {
            "race": {"race_id": "t-n", "date": "2026-05-24",
                     "venue": "静岡", "race_no": 4,
                     "class_name": "A級一般", "start_time": "11:30"},
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [{"line_name": "本命", "cars": [1, 2, 3]}],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 85.0, "b_count": 0,
                 "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
                 "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [{"bet_type": "3連単", "combination": "1-2-3",
                      "odds": 8.0}],
            "recent_results": [],
            "venue_trend": {
                "note": "本命ラインの番手差し優勢",
                "favors": ["番手"],
                "today": "本命ラインの番手差し連発、ライン3番手も絡む",
            },
        }
        ri = RaceInput.model_validate(payload)
        pred = Prediction(
            race_id="t-n", venue="静岡", race_no=4, is_girls=False,
            summary="", venue_trend_text="本命ラインの番手差し優勢",
            weather_text="", lines_text="", marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.0, market_odds=8.0,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        md = render_prediction_v2(pred, input_data=ri)
        section_2 = md.split("## 2.")[1].split("## 3.")[0]
        # 通常戦では原文を維持
        assert "本命ライン" in section_2
        assert "番手差し" in section_2
        assert "ライン3番手" in section_2


class TestVenueTrendSanitizeUnit:
    """sanitize_venue_trend_text の単体テスト。"""

    def test_no_op_for_normal_race(self):
        from app.output_validation import sanitize_venue_trend_text
        text = "本命ラインの番手差し"
        assert sanitize_venue_trend_text(
            text, is_girls=False, is_rookie=False
        ) == text

    def test_girls_replaces_terms(self):
        from app.output_validation import sanitize_venue_trend_text
        out = sanitize_venue_trend_text(
            "本命ラインの番手差し、ライン3番手の伸び",
            is_girls=True, is_rookie=False,
        )
        assert "本命ライン" not in out
        assert "番手差し" not in out
        assert "ライン3番手" not in out

    def test_rookie_replaces_terms(self):
        from app.output_validation import sanitize_venue_trend_text
        out = sanitize_venue_trend_text(
            "別線番手の差し、4番手評価が浮上",
            is_girls=False, is_rookie=True,
        )
        assert "別線番手" not in out
        assert "4番手評価" not in out
        assert "追走型" in out
        assert "4位評価" in out

    def test_empty_text_returns_empty(self):
        from app.output_validation import sanitize_venue_trend_text
        assert sanitize_venue_trend_text("", is_girls=True, is_rookie=False) == ""

    def test_standalone_n_bantan_not_collapsed_to_n_tsuusou(self):
        """codex P2 反映: 単独 `番手` 置換が先に走って `4番手` → `4追走` に
        ならないことを担保。3番手 / 5番手 も同様。"""
        from app.output_validation import sanitize_venue_trend_text
        out = sanitize_venue_trend_text(
            "4番手の伸びと3番手の浮上、5番手評価が決まる",
            is_girls=True, is_rookie=False,
        )
        # 数字付き番手は適切に置換される
        assert "4位評価" in out
        assert "中位" in out  # 3番手
        assert "5位評価" in out  # 5番手評価
        # 誤って「N追走」になっていない
        assert "4追走" not in out
        assert "3追走" not in out
        assert "5追走" not in out
