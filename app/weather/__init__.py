"""天候プロバイダパッケージ。

公開API:
- WeatherProvider / WeatherFetchError
- OpenMeteoWeatherProvider
- build_weather_provider(source, http_client)
- resolve_lat_lon / supported_venues / weather_code_to_condition / degree_to_compass

予想ロジックや fetchers パッケージとは独立しており、
生APIレスポンスは外に漏らさず Weather モデルのみ返す。
"""

from __future__ import annotations

from typing import Optional

from ..fetchers.http import HttpClient
from .base import WeatherFetchError, WeatherProvider
from .open_meteo import OpenMeteoWeatherProvider, build_open_meteo_url
from .parsers import (
    build_wind_note,
    degree_to_compass,
    pick_hourly_index,
    weather_code_to_condition,
)
from .venues import resolve_lat_lon, supported_venues


SUPPORTED_WEATHER_SOURCES = ("manual", "open-meteo")


def build_weather_provider(
    source: str,
    *,
    http_client: Optional[HttpClient] = None,
) -> WeatherProvider:
    """source 名から WeatherProvider を構築する。

    'manual' は WeatherProvider を返さない（呼び出し側で None として扱う）。
    """
    s = (source or "").strip().lower()
    if s == "open-meteo":
        return OpenMeteoWeatherProvider(http_client=http_client)
    raise WeatherFetchError(
        f"未対応の weather-source: '{source}'。"
        f"サポート対象: open-meteo"
    )


__all__ = [
    "WeatherProvider",
    "WeatherFetchError",
    "OpenMeteoWeatherProvider",
    "SUPPORTED_WEATHER_SOURCES",
    "build_weather_provider",
    "build_open_meteo_url",
    "resolve_lat_lon",
    "supported_venues",
    "weather_code_to_condition",
    "degree_to_compass",
    "pick_hourly_index",
    "build_wind_note",
]
