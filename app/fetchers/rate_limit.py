"""ドメイン別のレート制限。

サイト規約とサーバ負荷を尊重するため、同一ドメインへの連続アクセス間に
最低wait秒を入れる。テストでは `_sleep` を差し替えることで時間経過なしに
動作確認できる。
"""

from __future__ import annotations

import time
from typing import Callable, Optional
from urllib.parse import urlparse


class RateLimiter:
    """ドメイン別の最低wait制限。

    使い方:
        limiter = RateLimiter(min_interval_seconds=1.0)
        limiter.await_if_needed("https://example.com/foo")
        # ↑ 直近1秒以内に同じドメインへ叩いていれば sleep する

    テストフック:
        - sleep_fn: 差し替え可能なsleep関数（既定: time.sleep）
        - now_fn:   差し替え可能な現在時刻関数（既定: time.monotonic）
    """

    def __init__(
        self,
        min_interval_seconds: float = 1.0,
        *,
        sleep_fn: Optional[Callable[[float], None]] = None,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._last_at: dict[str, float] = {}
        self._sleep = sleep_fn or time.sleep
        self._now = now_fn or time.monotonic

    def _domain_of(self, url: str) -> str:
        parsed = urlparse(url)
        return (parsed.netloc or url).lower()

    def await_if_needed(self, url: str) -> float:
        """必要ならsleepし、実際に待った秒数を返す。"""
        if self.min_interval_seconds <= 0:
            return 0.0
        domain = self._domain_of(url)
        now = self._now()
        last = self._last_at.get(domain)
        waited = 0.0
        if last is not None:
            elapsed = now - last
            if elapsed < self.min_interval_seconds:
                waited = self.min_interval_seconds - elapsed
                self._sleep(waited)
        # last_at は sleep 後の時刻
        self._last_at[domain] = self._now()
        return waited
