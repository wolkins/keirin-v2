"""Open-Meteo 天候APIプロバイダ（試験実装、APIキー不要想定）。

仕様:
- forecast エンドポイントの hourly を取得し、対象時刻に最も近い値を採用
- wind_speed_unit=ms で m/s 取得
- HttpClient を経由（User-Agent / cache / rate_limit を必ず通す）
- レスポンスJSONは内部処理に閉じ込め、戻り値は `Weather` モデルのみ
- 通信失敗・非2xx・JSON不正は WeatherFetchError（日本語）に変換

注意:
- 自動投票・購入処理は実装しない
- POST は使わない（GETのみ）
- 競輪場の緯度経度は近似値（`venues.py`）
"""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

from ..fetchers.base import FetchError
from ..fetchers.http import HttpClient
from ..models import Weather
from .base import WeatherFetchError, WeatherProvider
from .parsers import (
    build_wind_note,
    degree_to_compass,
    pick_hourly_index,
    weather_code_to_condition,
)
from .venues import resolve_lat_lon


_OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
_HOURLY_FIELDS = (
    "weather_code",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "temperature_2m",
)


def _coerce_date(date: Date | str) -> Date:
    if isinstance(date, Date):
        return date
    try:
        return datetime.strptime(str(date), "%Y-%m-%d").date()
    except ValueError as e:
        raise WeatherFetchError(
            f"日付は YYYY-MM-DD 形式で指定してください: '{date}'"
        ) from e


def build_open_meteo_url(
    latitude: float,
    longitude: float,
    date: Date | str,
    *,
    timezone: str = "Asia/Tokyo",
) -> str:
    """Open-Meteo の hourly forecast URL を構築する。"""
    d = _coerce_date(date)
    params = {
        "latitude": f"{float(latitude):.4f}",
        "longitude": f"{float(longitude):.4f}",
        "hourly": ",".join(_HOURLY_FIELDS),
        "start_date": d.strftime("%Y-%m-%d"),
        "end_date": d.strftime("%Y-%m-%d"),
        "timezone": timezone,
        "wind_speed_unit": "ms",
    }
    return f"{_OPEN_METEO_ENDPOINT}?{urlencode(params)}"


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_open_meteo_response(
    payload: dict[str, Any],
    *,
    date: Date | str,
    start_time: Optional[str] = None,
) -> Weather:
    """Open-Meteo のレスポンス dict を Weather に変換する。

    Raises:
        WeatherFetchError: hourly 配列が無い等の構造不正
    """
    if not isinstance(payload, dict):
        raise WeatherFetchError("Open-Meteo レスポンスが dict ではありません")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise WeatherFetchError(
            "Open-Meteo レスポンスに hourly が含まれていません。サイト構造変更の可能性があります。"
        )
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        raise WeatherFetchError("Open-Meteo の hourly.time が空または不正です。")

    try:
        idx = pick_hourly_index(times, date=date, start_time=start_time)
    except ValueError as e:
        raise WeatherFetchError(str(e)) from e

    def _at(key: str) -> Any:
        arr = hourly.get(key)
        if not isinstance(arr, list) or idx >= len(arr):
            return None
        return arr[idx]

    code = _at("weather_code")
    rain = _safe_float(_at("precipitation")) or 0.0
    if rain < 0:
        rain = 0.0
    wind_speed = _safe_float(_at("wind_speed_10m")) or 0.0
    if wind_speed < 0:
        wind_speed = 0.0
    wind_deg = _safe_float(_at("wind_direction_10m"))
    temp = _safe_float(_at("temperature_2m"))

    direction = degree_to_compass(wind_deg)
    condition = weather_code_to_condition(code)
    note = build_wind_note(direction, wind_speed)

    return Weather(
        condition=condition,
        rain_mm_per_hour=rain,
        wind_direction=direction,
        wind_speed_mps=wind_speed,
        wind_note=note,
        temperature_c=temp,
    )


class OpenMeteoWeatherProvider(WeatherProvider):
    """Open-Meteo 用の WeatherProvider 実装。"""

    source_name = "open-meteo"

    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http_client = http_client

    def fetch_weather(
        self,
        *,
        venue: str,
        date: Date | str,
        start_time: Optional[str] = None,
    ) -> Weather:
        if self.http_client is None:
            raise WeatherFetchError(
                "HttpClient が未設定です。Open-Meteo 取得には HttpClient を渡してください。"
            )
        lat, lon = resolve_lat_lon(venue)
        d = _coerce_date(date)
        url = build_open_meteo_url(lat, lon, d)
        try:
            body = self.http_client.get(url)
        except FetchError as e:
            raise WeatherFetchError(f"Open-Meteo への通信に失敗しました: {e}") from e
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise WeatherFetchError(
                f"Open-Meteo のレスポンスがJSONとして解釈できません: {e}"
            ) from e
        return parse_open_meteo_response(payload, date=d, start_time=start_time)
