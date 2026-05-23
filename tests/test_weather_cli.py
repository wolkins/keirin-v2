"""CLI fetch-weather と prepare-json --weather-source のテスト。

実ネットワーク通信は一切しない。HttpClient は monkeypatch で差し替え。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.fetchers import HttpClient
from app.models import RaceInput


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RACE_CARD_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


# 24時間分の hourly レスポンス（17:00 が雨、それ以外は晴れ）
def _open_meteo_payload() -> dict:
    times = [f"2026-05-22T{h:02d}:00" for h in range(24)]
    return {
        "hourly": {
            "time": times,
            "weather_code": [0] * 17 + [61, 0] + [0] * 5,
            "precipitation": [0.0] * 17 + [1.5, 0.0] + [0.0] * 5,
            "wind_speed_10m": [2.5] * 24,
            "wind_direction_10m": [225.0] * 24,
            "temperature_2m": [12.0] * 24,
        }
    }


def _route_session(*, weather_payload: dict | None = None, fail_weather: bool = False) -> MagicMock:
    """racecard / open-meteo を出し分けるセッションを作る。"""
    session = MagicMock()
    payload_str = (
        json.dumps(weather_payload or _open_meteo_payload(), ensure_ascii=False)
    )

    def _get(url: str, **kwargs):
        if "open-meteo" in url:
            if fail_weather:
                return _make_response(503, "")
            return _make_response(200, payload_str)
        if "racecard" in url:
            return _make_response(200, RACE_CARD_HTML)
        return _make_response(404, "")

    session.get.side_effect = _get
    return session


def _patch_session(monkeypatch, **kwargs) -> MagicMock:
    session = _route_session(**kwargs)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    return session


# ---------------------------------------------------------------------------
# fetch-weather CLI
# ---------------------------------------------------------------------------


def test_cli_fetch_weather_open_meteo(tmp_path: Path, monkeypatch):
    session = _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "w.json"
    result = runner.invoke(
        cli,
        [
            "fetch-weather",
            "--provider", "open-meteo",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--start-time", "17:36",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["source"] == "open-meteo"
    assert raw["venue"] == "松山"
    assert raw["date"] == "2026-05-22"
    assert raw["start_time"] == "17:36"
    # 17:36 → 18:00 が選ばれるので 晴れ
    assert raw["weather"]["condition"] == "晴れ"
    assert raw["weather"]["wind_direction"] == "南西"
    assert raw["weather"]["wind_speed_mps"] == 2.5
    assert raw["weather"]["temperature_c"] == 12.0
    # 通信は1回
    assert session.get.call_count == 1


def test_cli_fetch_weather_at_17_00_rainy(tmp_path: Path, monkeypatch):
    _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "w.json"
    result = runner.invoke(
        cli,
        [
            "fetch-weather",
            "--provider", "open-meteo",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--start-time", "17:00",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["weather"]["condition"] == "雨"
    assert raw["weather"]["rain_mm_per_hour"] == 1.5


def test_cli_fetch_weather_unknown_venue(tmp_path: Path, monkeypatch):
    _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "w.json"
    result = runner.invoke(
        cli,
        [
            "fetch-weather",
            "--venue", "存在しない場",
            "--date", "2026-05-22",
            "--out", str(out),
            "--no-cache",
        ],
    )
    assert result.exit_code != 0
    assert "未対応" in result.output


def test_cli_fetch_weather_unsupported_provider(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "w.json"
    result = runner.invoke(
        cli,
        [
            "fetch-weather",
            "--provider", "yahoo-weather",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "未対応" in result.output


def test_cli_fetch_weather_invalid_date(tmp_path: Path, monkeypatch):
    _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "w.json"
    result = runner.invoke(
        cli,
        [
            "fetch-weather",
            "--venue", "松山",
            "--date", "2026/05/22",
            "--out", str(out),
            "--no-cache",
        ],
    )
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in result.output


def test_cli_fetch_weather_no_html_leak(tmp_path: Path, monkeypatch):
    """生APIレスポンス（hourly等）は出力JSONに含まれないこと。"""
    _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "w.json"
    result = runner.invoke(
        cli,
        [
            "fetch-weather",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--start-time", "12:00",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0
    body = out.read_text(encoding="utf-8")
    for key in ("hourly", "weather_code", "wind_speed_10m", "wind_direction_10m"):
        assert key not in body


# ---------------------------------------------------------------------------
# prepare-json --weather-source open-meteo
# ---------------------------------------------------------------------------


def test_cli_prepare_json_open_meteo(tmp_path: Path, monkeypatch):
    session = _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--no-odds",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--weather-source", "open-meteo",
            "--start-time", "17:00",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.weather is not None
    # 17:00 → 雨
    assert ri.weather.condition == "雨"
    assert ri.weather.wind_direction == "南西"
    assert ri.weather.wind_speed_mps == 2.5
    # 通信回数: race_card 1 + racedetail(補完試行) 1 + open-meteo 1 = 3
    # racedetail 補完試行は失敗してもカウントされる
    assert session.get.call_count >= 2


def test_cli_prepare_json_manual_weather_overrides_api(tmp_path: Path, monkeypatch):
    """手入力 weather が API 結果より優先されることを確認。"""
    _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--no-odds",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--weather-source", "open-meteo",
            "--start-time", "17:00",
            # API は 雨/南西 を返すが、手入力で上書き
            "--weather", "晴れ",
            "--wind-direction", "北",
            "--wind-speed", "5.0",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["weather"]["condition"] == "晴れ"
    assert raw["weather"]["wind_direction"] == "北"
    assert raw["weather"]["wind_speed_mps"] == 5.0


def test_cli_prepare_json_open_meteo_failure_keeps_card(tmp_path: Path, monkeypatch):
    """天候API 503 → 警告のみで race_card は使える。"""
    _patch_session(monkeypatch, fail_weather=True)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--no-odds",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--weather-source", "open-meteo",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output
    assert "天候API" in text
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    # 出走表は使えている。weather は None でもOK（手入力なし、API失敗）
    assert len(ri.riders) == 7


def test_cli_prepare_json_open_meteo_failure_with_manual_fallback(tmp_path: Path, monkeypatch):
    """API失敗 + 手入力 → 手入力のweatherが残る。"""
    _patch_session(monkeypatch, fail_weather=True)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--no-odds",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--weather-source", "open-meteo",
            "--weather", "曇り",
            "--wind-speed", "3.0",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["weather"]["condition"] == "曇り"
    assert raw["weather"]["wind_speed_mps"] == 3.0


def test_cli_prepare_json_weather_source_manual_explicit(tmp_path: Path, monkeypatch):
    """--weather-source manual を明示すると Open-Meteo API は叩かない。"""
    session = _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--no-odds",
            "--weather-source", "manual",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--weather", "曇り",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    # open-meteo 系URLは叩かれていない
    urls = [c.args[0] for c in session.get.call_args_list]
    assert not any("open-meteo" in u for u in urls)


def test_cli_prepare_json_invalid_weather_source(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "manual",
            "--fallback-input", str(SAMPLE),
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--weather-source", "bogus",
            "--no-results",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "未対応" in result.output


def test_cli_prepare_json_then_predict_with_open_meteo(tmp_path: Path, monkeypatch):
    """API取得後の RaceInput を predict に渡して動くこと。"""
    _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "p.json"
    r1 = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--no-odds",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--weather-source", "open-meteo",
            "--start-time", "17:00",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert r1.exit_code == 0, r1.output
    db = tmp_path / "t.db"
    r2 = runner.invoke(
        cli,
        [
            "--db", str(db),
            "predict",
            "--input", str(out),
            "--no-save",
            "--no-reflections",
            "--provider", "mock",
        ],
    )
    assert r2.exit_code == 0, r2.output
    assert "予想結果" in r2.output
