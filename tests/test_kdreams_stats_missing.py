"""Kドリームス parser の stats_missing フラグ + 数値不足モード連携テスト。

実 Kドリームスの /racecard/ ページには競走得点・決まり手が無いことが
判明したため、parser は stats_missing=True を立てるべき。
スコアリング側はそのフラグを見て数値不足モードに入る。
"""

from __future__ import annotations

import pytest

from app.fetchers.parsers.kdreams_race_card import (
    extract_stats_from_text,
    parse_race_card_html,
)
from app.models import RaceInput, Rider
from app.scoring import detect_score_data_insufficient


# ---------------------------------------------------------------------------
# extract_stats_from_text: 複数表記対応
# ---------------------------------------------------------------------------


def test_extract_stats_with_full_labels():
    """『競走得点：109.78 B 3 逃14 捲5 差2 マ0』フル表記"""
    text = "競走得点：109.78 B 3 逃14 捲5 差2 マ0"
    out = extract_stats_from_text(text)
    assert out["score"] == pytest.approx(109.78)
    assert out["b_count"] == 3
    assert out["nige"] == 14
    assert out["makuri"] == 5
    assert out["sashi"] == 2
    assert out["mark"] == 0


def test_extract_stats_with_short_labels():
    """『得点 100.54 逃0 捲0 差5 マ2』短縮表記"""
    text = "得点 100.54 逃0 捲0 差5 マ2"
    out = extract_stats_from_text(text)
    assert out["score"] == pytest.approx(100.54)
    assert out["nige"] == 0
    assert out["makuri"] == 0
    assert out["sashi"] == 5
    assert out["mark"] == 2
    # B 不在 → None
    assert out["b_count"] is None


def test_extract_stats_long_form_kimarite():
    """『逃げ14 捲り5 差し2 マーク0』完全表記"""
    text = "得点：85.71 逃げ14 捲り5 差し2 マーク0"
    out = extract_stats_from_text(text)
    assert out["score"] == pytest.approx(85.71)
    assert out["nige"] == 14
    assert out["makuri"] == 5
    assert out["sashi"] == 2
    assert out["mark"] == 0


def test_extract_stats_no_label_decimal_leading():
    """『100.54 ...』先頭スコアのみ"""
    text = "100.54 何か他の情報"
    out = extract_stats_from_text(text)
    assert out["score"] == pytest.approx(100.54)
    assert out["b_count"] is None
    assert out["nige"] is None


def test_extract_stats_empty():
    out = extract_stats_from_text("")
    assert all(v is None for v in out.values())
    out = extract_stats_from_text("関係ない文字列")
    assert all(v is None for v in out.values())


# ---------------------------------------------------------------------------
# Rider.stats_missing がモデルで正しく扱われる
# ---------------------------------------------------------------------------


def test_rider_stats_missing_default_false():
    """既定値は False（後方互換）。"""
    r = Rider(car_no=1, name="X")
    assert r.stats_missing is False


def test_rider_stats_missing_true():
    r = Rider(car_no=1, name="X", stats_missing=True)
    assert r.stats_missing is True


# ---------------------------------------------------------------------------
# detect_score_data_insufficient が stats_missing も考慮
# ---------------------------------------------------------------------------


def _make_input(riders: list[dict]) -> RaceInput:
    return RaceInput.model_validate({
        "race": {
            "race_id": "20260523-t-1", "date": "2026-05-23",
            "venue": "t", "race_no": 1, "class_name": "A",
        },
        "riders": riders,
        "lines": [],
    })


def test_detect_score_data_insufficient_via_stats_missing_majority():
    """選手の過半数が stats_missing=True なら数値不足モード。"""
    # 4/7 が missing → True
    riders = [
        {"car_no": i, "name": f"R{i}", "score": 75.0, "stats_missing": (i <= 4)}
        for i in range(1, 8)
    ]
    ri = _make_input(riders)
    assert detect_score_data_insufficient(ri) is True


def test_detect_score_data_insufficient_minority_missing_is_false():
    """missing が少数なら False（全員に有効データがあるはず）。"""
    riders = [
        {"car_no": i, "name": f"R{i}", "score": 75.0, "stats_missing": (i == 1)}
        for i in range(1, 8)
    ]
    ri = _make_input(riders)
    assert detect_score_data_insufficient(ri) is False


def test_detect_score_data_insufficient_all_zero_no_flag_is_true():
    """フラグなしでも全数値が 0 なら True（後方互換）。"""
    riders = [
        {"car_no": i, "name": f"R{i}"}  # 全て既定 (score=0, ...)
        for i in range(1, 8)
    ]
    ri = _make_input(riders)
    assert detect_score_data_insufficient(ri) is True


# ---------------------------------------------------------------------------
# 実 Kドリームス HTML パース時の stats_missing
# ---------------------------------------------------------------------------


def test_build_rider_real_sets_stats_missing():
    """_build_rider_real は実 Kドリームス /racecard/ ページ用。
    スコア・決まり手が無いので stats_missing=True を立てる。
    """
    from app.fetchers.parsers.kdreams_race_card import _build_rider_real
    row = {
        "car_no": "5",
        "name": "山下一輝",
        "pref": "東京",
        "rank": "S級1班",
        "style": "追",
    }
    rider = _build_rider_real(row)
    assert rider is not None
    assert rider["car_no"] == 5
    assert rider["name"] == "山下一輝"
    # 数値は 0、stats_missing=True
    assert rider["score"] == 0.0
    assert rider["b_count"] == 0
    assert rider["stats_missing"] is True


def test_build_rider_fixture_sets_stats_missing_when_no_data():
    """fixture-style パーサも、score/B/決まり手 すべて空なら stats_missing=True"""
    from app.fetchers.parsers.kdreams_race_card import _build_rider
    row = {
        "car-no": "3",
        "name": "Y3",
        "comment": "番手",
        "recent": "好調",
    }
    rider = _build_rider(row)
    assert rider is not None
    assert rider["stats_missing"] is True


def test_build_rider_fixture_no_stats_missing_when_score_present():
    """fixture-style: score を持つ rider は stats_missing=False"""
    from app.fetchers.parsers.kdreams_race_card import _build_rider
    row = {
        "car-no": "3",
        "name": "Y3",
        "score": "85.71",
        "b-count": "3",
        "nige": "5",
    }
    rider = _build_rider(row)
    assert rider is not None
    assert rider["score"] == pytest.approx(85.71)
    assert rider["b_count"] == 3
    assert rider["nige"] == 5
    assert rider["stats_missing"] is False


# ---------------------------------------------------------------------------
# /racedetail/ URL ビルダ
# ---------------------------------------------------------------------------


def test_build_race_detail_url():
    """build_race_detail_url が /racedetail/ パスを返す。"""
    from app.fetchers.kdreams import build_race_detail_url
    from datetime import date
    url = build_race_detail_url("武雄", date(2026, 5, 23), 5, session_no=3)
    assert "/takeo/racedetail/" in url
    assert url.endswith("/")
