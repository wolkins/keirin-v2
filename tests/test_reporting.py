"""成績レポートのテスト。

外部通信なし。すべて SQLite ローカルで完結する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.models import (
    BetRecommendation,
    Prediction,
    Reflection,
    RiderScore,
)
from app.reporting import (
    WIND_BUCKETS,
    _wind_bucket,
    build_performance_report,
    classify_hit,
    render_report_text,
    total_bet_count,
)
from app.storage import Storage


# ---------------------------------------------------------------------------
# データ作成ヘルパ
# ---------------------------------------------------------------------------


def _bet(category: str, combo: str, *, gami_risk: float = 0.0) -> BetRecommendation:
    return BetRecommendation(
        category=category,  # type: ignore[arg-type]
        bet_type="3連単",
        combination=combo,
        reason="test",
        gami_risk=gami_risk,
    )


def _make_prediction(
    *,
    race_id: str,
    venue: str,
    race_no: int,
    is_girls: bool = False,
    honsen: list[str] = ("5-1-3",),
    osae: list[str] = ("1-5-3",),
    ana: list[str] = ("6-5-1",),
    ooana: list[str] = ("7-5-1",),
    high_gami: bool = False,
) -> Prediction:
    return Prediction(
        race_id=race_id,
        venue=venue,
        race_no=race_no,
        is_girls=is_girls,
        summary="test",
        venue_trend_text="",
        weather_text="",
        lines_text="",
        marks={},
        honsen=[_bet("本線", c, gami_risk=0.8 if high_gami else 0.0) for c in honsen],
        osae=[_bet("押さえ", c) for c in osae],
        ana=[_bet("穴", c) for c in ana],
        ooana=[_bet("大穴", c) for c in ooana],
        final_conclusion="",
        gami_memo="",
        reflection_points=[],
        rider_scores=[],
    )


def _make_reflection(
    *,
    race_id: str,
    venue: str,
    race_no: int,
    is_girls: bool = False,
    weather: str = "曇り",
    wind: float = 5.0,
    rain: float = 0.0,
    actual: str = "5-2-3",
    categories: list[str] = ("別線番手を軽視",),
) -> Reflection:
    return Reflection(
        race_id=race_id,
        venue=venue,
        race_no=race_no,
        is_girls=is_girls,
        weather_condition=weather,
        wind_speed_mps=wind,
        rain_mm_per_hour=rain,
        actual_result=actual,
        categories=list(categories),
    )


# ---------------------------------------------------------------------------
# ヘルパ単体
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wind,expected",
    [
        (0.0, "0-2m/s"),
        (1.9, "0-2m/s"),
        (2.0, "2-4m/s"),
        (3.9, "2-4m/s"),
        (4.0, "4-6m/s"),
        (5.9, "4-6m/s"),
        (6.0, "6m/s以上"),
        (10.0, "6m/s以上"),
        (None, "不明"),
        (-1.0, "不明"),
    ],
)
def test_wind_bucket_boundaries(wind, expected):
    assert _wind_bucket(wind) == expected


def test_wind_buckets_constant_order():
    assert WIND_BUCKETS == ("0-2m/s", "2-4m/s", "4-6m/s", "6m/s以上", "不明")


def test_classify_hit_main():
    p = _make_prediction(race_id="20260522-test-1", venue="X", race_no=1)
    assert classify_hit(p, "5-1-3") == "main_hit"


def test_classify_hit_backup():
    p = _make_prediction(race_id="20260522-test-1", venue="X", race_no=1)
    assert classify_hit(p, "1-5-3") == "backup_hit"


def test_classify_hit_longshot():
    p = _make_prediction(race_id="20260522-test-1", venue="X", race_no=1)
    assert classify_hit(p, "6-5-1") == "longshot_hit"


def test_classify_hit_big_longshot():
    p = _make_prediction(race_id="20260522-test-1", venue="X", race_no=1)
    assert classify_hit(p, "7-5-1") == "big_longshot_hit"


def test_classify_hit_miss():
    p = _make_prediction(race_id="20260522-test-1", venue="X", race_no=1)
    assert classify_hit(p, "9-8-7") == "miss"


def test_classify_hit_invalid_result():
    p = _make_prediction(race_id="20260522-test-1", venue="X", race_no=1)
    assert classify_hit(p, "not-a-result") == "miss"


def test_total_bet_count():
    p = _make_prediction(
        race_id="20260522-test-1",
        venue="X",
        race_no=1,
        honsen=("5-1-3", "5-1-6"),
        osae=("1-5-3",),
        ana=("6-5-1",),
        ooana=("7-5-1", "2-6-1"),
    )
    assert total_bet_count(p) == 6


# ---------------------------------------------------------------------------
# build_performance_report
# ---------------------------------------------------------------------------


def test_report_empty_db(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    report = build_performance_report(s)
    assert report["summary"]["total"] == 0
    assert report["summary"]["with_result"] == 0
    assert report["by_venue"] == {}
    assert "improvement_notes" in report


def test_report_basic_counts(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    # 3件投入: 1件main_hit, 1件miss(結果あり), 1件結果なし
    p1 = _make_prediction(race_id="20260522-ogaki-1", venue="大垣", race_no=1)
    p2 = _make_prediction(race_id="20260522-ogaki-2", venue="大垣", race_no=2)
    p3 = _make_prediction(race_id="20260522-ogaki-3", venue="大垣", race_no=3)
    for p in (p1, p2, p3):
        s.save_prediction(p)
    s.save_result("20260522-ogaki-1", "5-1-3")  # main_hit
    s.save_result("20260522-ogaki-2", "9-8-7")  # miss

    report = build_performance_report(s)
    assert report["summary"]["total"] == 3
    assert report["summary"]["with_result"] == 2
    assert report["summary"]["main_hit"] == 1
    assert report["summary"]["miss"] == 1
    assert report["summary"]["hit_rate"] == 0.5


def test_report_hit_classification_includes_all_buckets(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    cases = [
        ("20260522-X-1", "5-1-3", "main_hit"),
        ("20260522-X-2", "1-5-3", "backup_hit"),
        ("20260522-X-3", "6-5-1", "longshot_hit"),
        ("20260522-X-4", "7-5-1", "big_longshot_hit"),
        ("20260522-X-5", "9-8-7", "miss"),
    ]
    for race_id, _, _ in cases:
        s.save_prediction(_make_prediction(race_id=race_id, venue="X", race_no=int(race_id[-1])))
    for race_id, result, _ in cases:
        s.save_result(race_id, result)

    report = build_performance_report(s)
    summary = report["summary"]
    assert summary["main_hit"] == 1
    assert summary["backup_hit"] == 1
    assert summary["longshot_hit"] == 1
    assert summary["big_longshot_hit"] == 1
    assert summary["miss"] == 1
    assert summary["listed_but_not_main"] == 3  # backup+longshot+big
    assert summary["with_result"] == 5
    assert summary["hit_rate"] == 0.8


def test_report_venue_filter(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    s.save_prediction(_make_prediction(race_id="20260522-大垣-1", venue="大垣", race_no=1))
    s.save_prediction(_make_prediction(race_id="20260522-松山-1", venue="松山", race_no=1))
    s.save_result("20260522-大垣-1", "5-1-3")
    s.save_result("20260522-松山-1", "9-8-7")

    r_ogaki = build_performance_report(s, venue="大垣")
    assert r_ogaki["summary"]["total"] == 1
    assert r_ogaki["summary"]["main_hit"] == 1

    r_matsu = build_performance_report(s, venue="松山")
    assert r_matsu["summary"]["total"] == 1
    assert r_matsu["summary"]["miss"] == 1


def test_report_date_range_filter(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    s.save_prediction(_make_prediction(race_id="20260501-X-1", venue="X", race_no=1))
    s.save_prediction(_make_prediction(race_id="20260515-X-1", venue="X", race_no=1))
    s.save_prediction(_make_prediction(race_id="20260531-X-1", venue="X", race_no=1))

    r = build_performance_report(s, from_date="2026-05-10", to_date="2026-05-20")
    assert r["summary"]["total"] == 1


def test_report_weather_filter(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    s.save_prediction(_make_prediction(race_id="20260522-X-1", venue="X", race_no=1))
    s.save_prediction(_make_prediction(race_id="20260522-X-2", venue="X", race_no=2))
    s.save_reflection(_make_reflection(race_id="20260522-X-1", venue="X", race_no=1, weather="雨"))
    s.save_reflection(_make_reflection(race_id="20260522-X-2", venue="X", race_no=2, weather="晴れ"))

    r_rain = build_performance_report(s, weather_condition="雨")
    assert r_rain["summary"]["total"] == 1
    r_sunny = build_performance_report(s, weather_condition="晴れ")
    assert r_sunny["summary"]["total"] == 1


def test_report_wind_buckets(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    cases = [
        ("20260522-X-1", 1.0, "0-2m/s"),
        ("20260522-X-2", 3.0, "2-4m/s"),
        ("20260522-X-3", 5.0, "4-6m/s"),
        ("20260522-X-4", 8.0, "6m/s以上"),
    ]
    for race_id, _, _ in cases:
        s.save_prediction(_make_prediction(race_id=race_id, venue="X", race_no=int(race_id[-1])))
    for race_id, wind, _ in cases:
        s.save_reflection(
            _make_reflection(race_id=race_id, venue="X", race_no=int(race_id[-1]), wind=wind)
        )

    r = build_performance_report(s)
    for race_id, _, bucket in cases:
        assert r["by_wind_bucket"][bucket]["total"] >= 1


def test_report_race_class_split(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    s.save_prediction(_make_prediction(race_id="20260522-X-1", venue="X", race_no=1, is_girls=True))
    s.save_prediction(_make_prediction(race_id="20260522-X-2", venue="X", race_no=2, is_girls=False))
    s.save_result("20260522-X-1", "5-1-3")
    s.save_result("20260522-X-2", "9-8-7")

    r = build_performance_report(s)
    assert r["by_race_class"]["girls"]["total"] == 1
    assert r["by_race_class"]["regular"]["total"] == 1
    assert r["by_race_class"]["girls"]["main_hit"] == 1
    assert r["by_race_class"]["regular"]["miss"] == 1


def test_report_top_categories(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    s.save_prediction(_make_prediction(race_id="20260522-X-1", venue="X", race_no=1))
    s.save_prediction(_make_prediction(race_id="20260522-X-2", venue="X", race_no=2))
    s.save_reflection(_make_reflection(
        race_id="20260522-X-1", venue="X", race_no=1,
        categories=["別線番手を軽視", "3番手の伸びを軽視"],
    ))
    s.save_reflection(_make_reflection(
        race_id="20260522-X-2", venue="X", race_no=2,
        categories=["別線番手を軽視"],
    ))

    r = build_performance_report(s)
    top = dict(r["top_reflection_categories"])
    assert top["別線番手を軽視"] == 2
    assert top["3番手の伸びを軽視"] == 1


def test_report_improvement_note_rain(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    for i in range(3):
        rid = f"2026052{i}-X-1"
        s.save_prediction(_make_prediction(race_id=rid, venue="X", race_no=1))
        s.save_reflection(
            _make_reflection(
                race_id=rid, venue="X", race_no=1,
                weather="雨", rain=2.0,
                categories=["別線番手を軽視"],
            )
        )
    r = build_performance_report(s)
    notes = " / ".join(r["improvement_notes"])
    assert "雨天時" in notes and "別線番手" in notes


def test_report_improvement_note_strong_wind(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    for i in range(3):
        rid = f"2026052{i}-X-1"
        s.save_prediction(_make_prediction(race_id=rid, venue="X", race_no=1))
        s.save_reflection(
            _make_reflection(
                race_id=rid, venue="X", race_no=1,
                wind=6.0, weather="曇り",
                categories=["3番手の伸びを軽視"],
            )
        )
    r = build_performance_report(s)
    notes = " / ".join(r["improvement_notes"])
    assert "強風" in notes or "風速4m/s" in notes


def test_report_improvement_note_girls(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    for i in range(3):
        rid = f"2026052{i}-X-1"
        s.save_prediction(_make_prediction(race_id=rid, venue="X", race_no=1, is_girls=True))
        s.save_reflection(
            _make_reflection(
                race_id=rid, venue="X", race_no=1, is_girls=True,
                categories=["ガールズの位置取り評価不足"],
            )
        )
    r = build_performance_report(s)
    notes = " / ".join(r["improvement_notes"])
    assert "ガールズ" in notes and "位置取り" in notes


def test_report_improvement_default_message(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    r = build_performance_report(s)
    assert r["improvement_notes"]
    assert any("検出されません" in n for n in r["improvement_notes"])


def test_report_avg_bet_count_and_gami(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    s.save_prediction(_make_prediction(
        race_id="20260522-X-1", venue="X", race_no=1,
        honsen=("5-1-3", "5-1-6"),
        high_gami=True,
    ))
    r = build_performance_report(s)
    # 本線2 + 押さえ1 + 穴1 + 大穴1 = 5点
    assert r["summary"]["avg_bet_count"] == 5.0
    assert r["summary"]["high_gami_count"] == 2  # 本線2件とも high_gami=0.8


def test_render_report_text_contains_sections(tmp_path: Path):
    s = Storage(tmp_path / "t.db")
    s.save_prediction(_make_prediction(race_id="20260522-X-1", venue="X", race_no=1))
    r = build_performance_report(s)
    text = render_report_text(r)
    for header in (
        "成績レポート",
        "成績サマリー",
        "場別成績",
        "天候別成績",
        "風速別成績",
        "レース種別成績",
        "反省カテゴリ上位",
        "改善メモ",
    ):
        assert header in text


# ---------------------------------------------------------------------------
# CLI reports
# ---------------------------------------------------------------------------


def test_cli_reports_text_empty(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(cli, ["--db", str(db), "reports"])
    assert result.exit_code == 0, result.output
    assert "成績レポート" in result.output
    assert "予想数: 0" in result.output


def test_cli_reports_json_format(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    # 1件投入
    s = Storage(db)
    s.save_prediction(_make_prediction(race_id="20260522-X-1", venue="X", race_no=1))
    s.save_result("20260522-X-1", "5-1-3")
    result = runner.invoke(
        cli, ["--db", str(db), "reports", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(result.output)
    assert "summary" in raw
    assert raw["summary"]["total"] == 1
    assert raw["summary"]["main_hit"] == 1


def test_cli_reports_invalid_format(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli, ["--db", str(db), "reports", "--format", "html"]
    )
    assert result.exit_code != 0
    assert "未対応" in result.output


def test_cli_reports_invalid_date(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli, ["--db", str(db), "reports", "--from-date", "2026/05/01"]
    )
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in result.output


def test_cli_reports_venue_filter(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    s = Storage(db)
    s.save_prediction(_make_prediction(race_id="20260522-大垣-1", venue="大垣", race_no=1))
    s.save_prediction(_make_prediction(race_id="20260522-松山-1", venue="松山", race_no=1))
    result = runner.invoke(
        cli, ["--db", str(db), "reports", "--venue", "大垣", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(result.output)
    assert raw["summary"]["total"] == 1


def test_cli_reports_limit_reflections_negative(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli, ["--db", str(db), "reports", "--limit-reflections", "-1"]
    )
    assert result.exit_code != 0
    assert "0以上" in result.output or "limit-reflections" in result.output
