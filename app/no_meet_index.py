"""「開催なし」を記録するインデックスファイル。

prepare-json 一括モードで「広島の 2026-05-22 はそもそも開催が無い」と
判定された場合、その情報を永続化して同じ場+日付の再リクエストで
無駄な通信を発生させないようにする。

設計:
- 場所: `.cache/keirin/no_meet_index.json`
- キー: "{venue}|{YYYYMMDD}|{session_no}"
- 値: ISO形式の記録時刻
- TTL: 既定 12時間（開催情報は更新されうるが、同日中は変わらない想定）
- `--refresh-cache` または `--no-cache` のときは無視

開催が「ある」ことが確認されたら自動的にエントリを削除する設計にもできるが、
複雑になるため、TTL 経過 + --refresh-cache での手動上書きで運用する。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


_INDEX_FILENAME = "no_meet_index.json"
DEFAULT_NO_MEET_TTL_SECONDS = 12 * 3600  # 12時間


def _key(venue: str, date_str: str, session_no: int) -> str:
    return f"{venue}|{date_str}|{int(session_no)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


class NoMeetIndex:
    """場+日付+sessionの「開催なし」をTTL付きで記録するファイルベースのインデックス。"""

    def __init__(
        self,
        cache_dir: Path,
        *,
        ttl_seconds: int = DEFAULT_NO_MEET_TTL_SECONDS,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.path = self.cache_dir / _INDEX_FILENAME
        self.ttl_seconds = int(ttl_seconds)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_known_no_meet(
        self, venue: str, date_str: str, session_no: int = 1,
        *, now: Optional[datetime] = None,
    ) -> bool:
        """TTL内に「開催なし」が記録されていれば True。"""
        if not venue or not date_str:
            return False
        data = self._load()
        key = _key(venue, date_str, session_no)
        ts = data.get(key)
        if ts is None:
            return False
        recorded = _parse_iso(ts)
        if recorded is None:
            return False
        now_dt = now or datetime.now(timezone.utc)
        return (now_dt - recorded) < timedelta(seconds=self.ttl_seconds)

    def record_no_meet(
        self, venue: str, date_str: str, session_no: int = 1,
    ) -> None:
        """開催なしを記録する。"""
        if not venue or not date_str:
            return
        data = self._load()
        data[_key(venue, date_str, session_no)] = _now_iso()
        self._save(data)

    def clear(self, venue: Optional[str] = None, date_str: Optional[str] = None) -> None:
        """指定の場/日付（または全体）の記録を削除する。"""
        if venue is None and date_str is None:
            if self.path.exists():
                try:
                    self.path.unlink()
                except OSError:
                    pass
            return
        data = self._load()
        keep = {}
        for k, v in data.items():
            try:
                v_venue, v_date, _ = k.split("|")
            except ValueError:
                continue
            if venue is not None and v_venue != venue:
                keep[k] = v
                continue
            if date_str is not None and v_date != date_str:
                keep[k] = v
                continue
        self._save(keep)
