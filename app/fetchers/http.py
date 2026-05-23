"""HTTPクライアント。

- User-Agent と timeout を必ず設定する
- キャッシュとレート制限を統合する
- 通信失敗・非2xx・タイムアウトは FetchError（日本語）に変換する
- テストでは session を差し替えて実ネットワーク通信を行わない
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from .base import FetchError
from .cache import FileCache, make_cache_key
from .rate_limit import RateLimiter


DEFAULT_USER_AGENT = (
    "keirin-predictor/0.1 (+predict-support-only; no-autobet; "
    "contact: local-dev)"
)
DEFAULT_TIMEOUT_SECONDS = 10.0


class HttpResponseLike(Protocol):
    """テスト用に最低限のレスポンスIF。"""

    status_code: int
    text: str
    headers: dict[str, str]


class HttpClient:
    """User-Agent / timeout / cache / rate_limit を統合した GET クライアント。

    本MVPでは GET のみ対応。POSTは（自動投票・ログイン誤用防止のため）
    意図的に実装しない。
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        cache: Optional[FileCache] = None,
        rate_limiter: Optional[RateLimiter] = None,
        session: Any = None,
        force_refresh: bool = False,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.cache = cache or FileCache(enabled=True)
        self.rate_limiter = rate_limiter or RateLimiter(min_interval_seconds=1.0)
        self._session = session  # None なら遅延 import で requests.Session を生成
        # True にすると、すべての get() がキャッシュを無視して再取得し、新結果で上書き
        self.force_refresh = force_refresh

    def _get_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            import requests  # type: ignore
        except ImportError as e:
            raise FetchError(
                "requests パッケージが見つかりません (`pip install requests`)"
            ) from e
        self._session = requests.Session()
        return self._session

    def get(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        use_cache: bool = True,
        ttl: Optional[int] = None,
        refresh: bool = False,
        validate_body: Optional[Any] = None,
    ) -> str:
        """GETしてレスポンス本文(text)を返す。

        - キャッシュヒット時は通信しない
        - refresh=True なら既存キャッシュを削除してから取得
        - レート制限により直近アクセスから一定時間 sleep
        - 非2xx は FetchError（キャッシュ書き込みなし）
        - ネットワーク例外は FetchError
        - validate_body は body を受けて bool を返す callable。False を返した場合は
          キャッシュに書き込まない（取得結果は呼び出し側に返す）
          → エラーページ（SYSTEM_ERROR等）の汚染を避けるための仕組み
        """
        key = make_cache_key("GET", url, params)
        # HttpClient 初期化時の force_refresh も尊重
        effective_refresh = refresh or self.force_refresh
        if effective_refresh:
            try:
                self.cache.invalidate(key)
            except AttributeError:
                pass
        if use_cache and not effective_refresh:
            hit = self.cache.get(key)
            if hit is not None:
                return str(hit.get("body", ""))

        self.rate_limiter.await_if_needed(url)
        session = self._get_session()
        headers = {"User-Agent": self.user_agent, "Accept": "text/html,application/json"}
        try:
            response = session.get(
                url, params=params, headers=headers, timeout=self.timeout
            )
        except Exception as e:
            raise FetchError(
                f"通信エラー: {url} への接続に失敗しました ({type(e).__name__}: {e})"
            ) from e

        status = int(getattr(response, "status_code", 0))
        if status < 200 or status >= 300:
            raise FetchError(
                f"HTTPエラー: {url} がステータス {status} を返しました"
            )
        # サーバが Content-Type に charset を返さないと requests は ISO-8859-1 を仮定して
        # 日本語が文字化けする（yen-joy 等で発生）。HTML の場合は apparent_encoding
        # (chardet 推定) に切り替えてから text を取得する。
        try:
            content_type = (response.headers or {}).get("Content-Type", "")
        except Exception:
            content_type = ""
        try:
            if (
                getattr(response, "encoding", None) == "ISO-8859-1"
                and "charset" not in content_type.lower()
            ):
                guess = getattr(response, "apparent_encoding", None)
                response.encoding = guess or "utf-8"
        except Exception:
            pass
        body = getattr(response, "text", "") or ""
        try:
            headers_out = dict(getattr(response, "headers", {}) or {})
        except Exception:
            headers_out = {}

        # validate_body によるエラーページ判定（キャッシュ汚染防止）
        cacheable = True
        if validate_body is not None:
            try:
                cacheable = bool(validate_body(body))
            except Exception:
                # validator 自体がエラーを出した場合は安全側でキャッシュしない
                cacheable = False

        if use_cache and cacheable:
            self.cache.set(
                key,
                url=url,
                method="GET",
                params=params,
                body=body,
                headers=headers_out,
                ttl=ttl,
            )
        return body
