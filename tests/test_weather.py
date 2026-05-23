"""天候プロバイダの単体テスト。

実ネットワーク通信は一切行わない。HttpClient は session を MagicMock に
差し替え、JSONレスポンスをそのまま返させる。
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.fetchers import FileCache, HttpClient, RateLimiter
from app.models import Weather
from app.weather import (
    OpenMeteoWeatherProvider,
    WeatherFetchError,
    build_open_meteo_url,
    build_weather_provider,
    degree_to_compass,
    resolve_lat_lon,
    supported_venues,
    weather_code_to_condition,
)
from app.weather.open_meteo import parse_open_meteo_response
from app.weather.parsers import pick_hourly_index, build_wind_note


# ---------------------------------------------------------------------------
# venues
# ---------------------------------------------------------------------------


def test_resolve_lat_lon_known():
    lat, lon = resolve_lat_lon("大垣")
    assert isinstance(lat, float) and isinstance(lon, float)
    assert 30 < lat < 45 and 125 < lon < 145


def test_resolve_lat_lon_unknown_raises():
    with pytest.raises(WeatherFetchError) as excinfo:
        resolve_lat_lon("不明")
    assert "未対応" in str(excinfo.value)


def test_resolve_lat_lon_empty_raises():
    with pytest.raises(WeatherFetchError):
        resolve_lat_lon("")


def test_supported_venues_includes_phase_targets():
    vs = supported_venues()
    for v in ("大垣", "大宮", "松山", "奈良", "青森", "名古屋", "高知", "京王閣", "久留米"):
        assert v in vs


def test_supported_venues_includes_added_majors():
    """フェーズで追加された主要場が含まれていること。"""
    vs = supported_venues()
    for v in ("平塚", "立川", "川崎", "取手", "別府", "小倉", "函館", "千葉", "前橋"):
        assert v in vs
    # 座標の妥当性（日本の緯度経度範囲内）
    for v in ("平塚", "立川", "別府"):
        lat, lon = [lp for name, lp in [(name, resolve_lat_lon(name)) for name in [v]]][0]
        assert 30 < lat < 46 and 125 < lon < 146


# ---------------------------------------------------------------------------
# weather_code_to_condition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        (0, "晴れ"),
        (1, "晴れ"),
        (2, "晴れ"),
        (3, "曇り"),
        (45, "霧"),
        (48, "霧"),
        (51, "小雨"),
        (55, "小雨"),
        (61, "雨"),
        (63, "雨"),
        (65, "雨"),
        (80, "雨"),
        (82, "強雨"),
        (71, "雪"),
        (75, "雪"),
        (95, "雷雨"),
        (96, "雷雨"),
        (None, "不明"),
        (-1, "不明"),
        (999, "不明"),
    ],
)
def test_weather_code_to_condition_mapping(code, expected):
    assert weather_code_to_condition(code) == expected


def test_weather_code_invalid_input():
    assert weather_code_to_condition("abc") == "不明"


# ---------------------------------------------------------------------------
# degree_to_compass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "deg,expected",
    [
        (0, "北"),
        (22, "北"),
        (23, "北東"),
        (45, "北東"),
        (90, "東"),
        (135, "南東"),
        (180, "南"),
        (225, "南西"),
        (270, "西"),
        (315, "北西"),
        (360, "北"),
        (350, "北"),
        (None, None),
    ],
)
def test_degree_to_compass(deg, expected):
    assert degree_to_compass(deg) == expected


def test_degree_to_compass_invalid():
    assert degree_to_compass("abc") is None


# ---------------------------------------------------------------------------
# build_wind_note
# ---------------------------------------------------------------------------


def test_build_wind_note_full():
    assert build_wind_note("南西", 2.0) == "南西2.0m/s"


def test_build_wind_note_direction_only():
    assert build_wind_note("北", 0) == "北"


def test_build_wind_note_speed_only():
    assert build_wind_note(None, 3.5) == "3.5m/s"


def test_build_wind_note_none():
    assert build_wind_note(None, 0) is None
    assert build_wind_note(None, None) is None


# ---------------------------------------------------------------------------
# pick_hourly_index
# ---------------------------------------------------------------------------


_TIMES_24H = [f"2026-05-22T{h:02d}:00" for h in range(24)]


def test_pick_hourly_index_closest_to_start_time():
    # 17:36 → 18:00 が最も近い（idx=18）
    assert pick_hourly_index(_TIMES_24H, date="2026-05-22", start_time="17:36") == 18


def test_pick_hourly_index_exact_match():
    assert pick_hourly_index(_TIMES_24H, date="2026-05-22", start_time="10:00") == 10


def test_pick_hourly_index_default_to_noon():
    # start_time 未指定 → 12:00
    assert pick_hourly_index(_TIMES_24H, date="2026-05-22") == 12


def test_pick_hourly_index_empty_raises():
    with pytest.raises(ValueError):
        pick_hourly_index([], date="2026-05-22")


def test_pick_hourly_index_invalid_start_time():
    with pytest.raises(ValueError):
        pick_hourly_index(_TIMES_24H, date="2026-05-22", start_time="abc")


# ---------------------------------------------------------------------------
# build_open_meteo_url
# ---------------------------------------------------------------------------


def test_build_open_meteo_url_includes_required_params():
    url = build_open_meteo_url(34.685, 135.805, "2026-05-22")
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")
    assert "latitude=34.6850" in url
    assert "longitude=135.8050" in url
    assert "wind_speed_unit=ms" in url
    assert "timezone=Asia" in url
    assert "weather_code" in url
    assert "precipitation" in url
    assert "wind_speed_10m" in url
    assert "wind_direction_10m" in url
    assert "temperature_2m" in url


def test_build_open_meteo_url_invalid_date():
    with pytest.raises(WeatherFetchError):
        build_open_meteo_url(34.685, 135.805, "2026/05/22")


# ---------------------------------------------------------------------------
# parse_open_meteo_response
# ---------------------------------------------------------------------------


def _make_payload(*, hourly_overrides: dict | None = None) -> dict:
    """24 時間分の最低限のレスポンスを構築する。"""
    times = _TIMES_24H
    hourly = {
        "time": times,
        # 17:00=雨 18:00=晴れ にしてインデックス選択を確認
        "weather_code": [0] * 17 + [61, 0] + [0] * 5,
        "precipitation": [0.0] * 17 + [1.5, 0.0] + [0.0] * 5,
        "wind_speed_10m": [1.0] * 24,
        "wind_direction_10m": [225.0] * 24,
        "temperature_2m": [15.0] * 24,
    }
    if hourly_overrides:
        hourly.update(hourly_overrides)
    return {"hourly": hourly}


def test_parse_open_meteo_response_picks_nearest_hourly():
    """start_time=17:36 → idx=18 (18:00) を採用、晴れになる想定だが
    実際は precip/wind 等を取って Weather を作る。
    """
    w = parse_open_meteo_response(_make_payload(), date="2026-05-22", start_time="17:36")
    # idx=18 の値が拾われる: precip=0.0, weather_code=0(晴れ)
    assert w.condition == "晴れ"
    assert w.rain_mm_per_hour == 0.0
    # 17:00 を選ぶならは "雨" になるはずなので、しっかり 18 を選んでいることを確認
    assert w.condition != "雨"


def test_parse_open_meteo_response_at_17_00_is_rain():
    w = parse_open_meteo_response(_make_payload(), date="2026-05-22", start_time="17:00")
    assert w.condition == "雨"
    assert w.rain_mm_per_hour == 1.5
    assert w.wind_speed_mps == 1.0
    assert w.wind_direction == "南西"
    assert w.wind_note == "南西1.0m/s"
    assert w.temperature_c == 15.0


def test_parse_open_meteo_response_default_noon():
    w = parse_open_meteo_response(_make_payload(), date="2026-05-22")
    # idx=12 → weather_code=0 (晴れ)
    assert w.condition == "晴れ"


def test_parse_open_meteo_response_negative_precip_clamped():
    payload = _make_payload(
        hourly_overrides={"precipitation": [-1.0] * 24}
    )
    w = parse_open_meteo_response(payload, date="2026-05-22", start_time="12:00")
    assert w.rain_mm_per_hour == 0.0


def test_parse_open_meteo_response_missing_hourly():
    with pytest.raises(WeatherFetchError) as excinfo:
        parse_open_meteo_response({}, date="2026-05-22")
    assert "hourly" in str(excinfo.value)


def test_parse_open_meteo_response_empty_times():
    with pytest.raises(WeatherFetchError) as excinfo:
        parse_open_meteo_response({"hourly": {"time": []}}, date="2026-05-22")
    assert "hourly.time" in str(excinfo.value)


def test_parse_open_meteo_response_non_dict():
    with pytest.raises(WeatherFetchError):
        parse_open_meteo_response("not a dict", date="2026-05-22")  # type: ignore[arg-type]


def test_parse_open_meteo_response_to_weather_model():
    w = parse_open_meteo_response(_make_payload(), date="2026-05-22", start_time="12:00")
    assert isinstance(w, Weather)
    # Weather を再シリアライズして RaceInput からも検証可能
    raw = json.loads(w.model_dump_json())
    Weather.model_validate(raw)


# ---------------------------------------------------------------------------
# OpenMeteoWeatherProvider.fetch_weather (HTTPモック)
# ---------------------------------------------------------------------------


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def _make_http_client(tmp_path: Path, session: MagicMock) -> HttpClient:
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    return HttpClient(cache=cache, rate_limiter=rl, session=session)


def test_open_meteo_provider_returns_weather(tmp_path: Path):
    session = MagicMock()
    session.get.return_value = _make_response(
        200, json.dumps(_make_payload(), ensure_ascii=False)
    )
    client = _make_http_client(tmp_path, session)
    provider = OpenMeteoWeatherProvider(http_client=client)
    w = provider.fetch_weather(venue="松山", date="2026-05-22", start_time="17:00")
    assert isinstance(w, Weather)
    assert w.condition == "雨"
    assert session.get.call_count == 1
    called = session.get.call_args.args[0]
    # URL: open-meteo の forecast を叩いている
    assert "api.open-meteo.com" in called
    assert "wind_speed_unit=ms" in called
    # User-Agent
    headers = session.get.call_args.kwargs.get("headers", {})
    assert "User-Agent" in headers


def test_open_meteo_provider_without_http_client_raises():
    p = OpenMeteoWeatherProvider(http_client=None)
    with pytest.raises(WeatherFetchError) as excinfo:
        p.fetch_weather(venue="松山", date="2026-05-22")
    assert "HttpClient" in str(excinfo.value)


def test_open_meteo_provider_unknown_venue(tmp_path: Path):
    session = MagicMock()
    session.get.return_value = _make_response(200, "{}")
    client = _make_http_client(tmp_path, session)
    p = OpenMeteoWeatherProvider(http_client=client)
    with pytest.raises(WeatherFetchError) as excinfo:
        p.fetch_weather(venue="不明", date="2026-05-22")
    assert "未対応" in str(excinfo.value)
    # 通信は発生しない
    assert session.get.call_count == 0


def test_open_meteo_provider_http_failure(tmp_path: Path):
    session = MagicMock()
    session.get.return_value = _make_response(503, "")
    client = _make_http_client(tmp_path, session)
    p = OpenMeteoWeatherProvider(http_client=client)
    with pytest.raises(WeatherFetchError) as excinfo:
        p.fetch_weather(venue="松山", date="2026-05-22")
    assert "Open-Meteo" in str(excinfo.value) or "通信" in str(excinfo.value)


def test_open_meteo_provider_invalid_json(tmp_path: Path):
    session = MagicMock()
    session.get.return_value = _make_response(200, "<<not json>>")
    client = _make_http_client(tmp_path, session)
    p = OpenMeteoWeatherProvider(http_client=client)
    with pytest.raises(WeatherFetchError) as excinfo:
        p.fetch_weather(venue="松山", date="2026-05-22")
    assert "JSON" in str(excinfo.value)


def test_open_meteo_provider_does_not_leak_raw_response(tmp_path: Path):
    """戻り値は Weather のみで、APIレスポンスのキー（hourly等）は出ない。"""
    session = MagicMock()
    session.get.return_value = _make_response(
        200, json.dumps(_make_payload(), ensure_ascii=False)
    )
    client = _make_http_client(tmp_path, session)
    p = OpenMeteoWeatherProvider(http_client=client)
    w = p.fetch_weather(venue="松山", date="2026-05-22", start_time="17:00")
    serialized = json.dumps(w.model_dump(), ensure_ascii=False, default=str)
    for key in ("hourly", "weather_code", "wind_speed_10m"):
        assert key not in serialized


def test_build_weather_provider_open_meteo():
    p = build_weather_provider("open-meteo")
    assert isinstance(p, OpenMeteoWeatherProvider)
    assert p.source_name == "open-meteo"


def test_build_weather_provider_unknown():
    with pytest.raises(WeatherFetchError):
        build_weather_provider("foo")
