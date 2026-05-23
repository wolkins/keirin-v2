"""ファイルベースのキャッシュ。

過剰アクセスを避けるため、URL + クエリパラメータをキーに JSON で保存する。
TTL を過ぎたエントリは miss 扱い。`--no-cache` でCLIから無効化可能。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional


DEFAULT_CACHE_DIR = Path(".cache/keirin")
DEFAULT_TTL_SECONDS = 180  # 3分。サイトに優しい既定値


def _stable_params(params: Optional[dict[str, Any]]) -> str:
    if not params:
        return ""
    items = sorted((str(k), str(v)) for k, v in params.items())
    return json.dumps(items, ensure_ascii=False)


def make_cache_key(method: str, url: str, params: Optional[dict[str, Any]] = None) -> str:
    """method+URL+ソート済みparams を sha256 でキー化する。"""
    raw = f"{method.upper()}|{url}|{_stable_params(params)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FileCache:
    """ファイルベース TTL キャッシュ。

    保存形式:
        {"fetched_at": <epoch>, "ttl": <int>, "url": "...", "method": "GET",
         "params": {...}, "body": "...", "headers": {...}}
    """

    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        enabled: bool = True,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled

    def _path_for(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str, *, now: Optional[float] = None) -> Optional[dict[str, Any]]:
        """キャッシュ取得。無効・期限切れ・ファイル無し → None。"""
        if not self.enabled:
            return None
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        fetched_at = float(data.get("fetched_at", 0))
        ttl = int(data.get("ttl", self.ttl_seconds))
        current = now if now is not None else time.time()
        if current - fetched_at > ttl:
            return None
        return data

    def set(
        self,
        key: str,
        *,
        url: str,
        method: str,
        params: Optional[dict[str, Any]],
        body: str,
        headers: Optional[dict[str, str]] = None,
        ttl: Optional[int] = None,
        now: Optional[float] = None,
    ) -> None:
        if not self.enabled:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": now if now is not None else time.time(),
            "ttl": ttl if ttl is not None else self.ttl_seconds,
            "method": method,
            "url": url,
            "params": params or {},
            "body": body,
            "headers": headers or {},
        }
        self._path_for(key).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def clear(self) -> None:
        if not self.cache_dir.exists():
            return
        for f in self.cache_dir.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass

    def invalidate(self, key: str) -> bool:
        """指定キーのキャッシュエントリを削除する。

        Returns:
            削除した場合 True、存在しなかった場合 False
        """
        path = self._path_for(key)
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False
