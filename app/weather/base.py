"""天候プロバイダの抽象基底。

予想ロジックと天候取得処理を疎結合に保つため、Fetcher パッケージとは
独立した抽象を用意する（依存先は `HttpClient` のみ）。

遵守事項:
- 自動投票・購入処理は一切実装しない
- 生APIレスポンスを上位（LLM/予想エンジン）に渡さない
- 失敗時は WeatherFetchError（日本語）を投げる
- HTTP は GET のみ
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date as Date
from typing import Optional

from ..models import Weather


class WeatherFetchError(ValueError):
    """天候取得まわりのエラー。メッセージは日本語で。"""


class WeatherProvider(ABC):
    """天候プロバイダの抽象基底。"""

    #: プロバイダ識別子（例: 'open-meteo'）
    source_name: str = "abstract"

    @abstractmethod
    def fetch_weather(
        self,
        *,
        venue: str,
        date: Date | str,
        start_time: Optional[str] = None,
    ) -> Weather:
        """指定場所・日時の天候を Weather モデルとして返す。

        Args:
            venue: 場名
            date: YYYY-MM-DD 文字列または date オブジェクト
            start_time: HH:MM。指定があればその時刻に最も近い hourly を選ぶ

        Returns:
            Weather モデル（condition / rain_mm_per_hour / wind_* / temperature_c）

        Raises:
            WeatherFetchError: 通信失敗、未対応場名、レスポンス不正など
        """
