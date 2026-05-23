"""外部データ取得パッケージ。

公開API:
- Fetcher / FetchError / NotImplementedSource
- HttpClient / FileCache / RateLimiter
- ManualFetcher / KDreamsFetcher / OddsParkFetcher
- build_fetcher(source) — ソース名から Fetcher を取得

このパッケージは予想処理と独立しており、生HTMLは Fetcher 内に閉じ込められる。
"""

from __future__ import annotations

from typing import Optional

from .base import Fetcher, FetchError, NotImplementedSource, RaceCardData
from .cache import FileCache
from .http import HttpClient
from .kdreams import KDreamsFetcher
from .manual import ManualFetcher
from .oddspark import OddsParkFetcher
from .rate_limit import RateLimiter
from .tospo import TospoFetcher


SUPPORTED_SOURCES = ("manual", "kdreams", "oddspark", "tospo")


def build_fetcher(
    source: str,
    *,
    http_client: Optional[HttpClient] = None,
    manual_input_path: Optional[str] = None,
) -> Fetcher:
    """ソース名から Fetcher を構築する。

    - manual: 手入力JSONローダ。manual_input_path が必要。
    - kdreams / oddspark: HttpClient を受け取るが、本フェーズでは
      fetch_* メソッド呼び出し時に NotImplementedSource を投げる。
    """
    s = (source or "").strip().lower()
    if s == "manual":
        if not manual_input_path:
            raise FetchError(
                "ManualFetcher を使うには --input または manual_input_path が必要です"
            )
        return ManualFetcher(input_path=manual_input_path)
    if s == "kdreams":
        return KDreamsFetcher(http_client=http_client)
    if s == "oddspark":
        return OddsParkFetcher(http_client=http_client)
    if s == "tospo":
        return TospoFetcher(http_client=http_client)
    raise FetchError(
        f"未知のソース: '{source}'。サポート対象: {', '.join(SUPPORTED_SOURCES)}"
    )


__all__ = [
    "Fetcher",
    "FetchError",
    "NotImplementedSource",
    "RaceCardData",
    "HttpClient",
    "FileCache",
    "RateLimiter",
    "ManualFetcher",
    "KDreamsFetcher",
    "OddsParkFetcher",
    "TospoFetcher",
    "SUPPORTED_SOURCES",
    "build_fetcher",
]
