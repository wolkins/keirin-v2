"""手入力JSONからの読み込み Fetcher。

外部取得が失敗したときのフォールバック先としても使う。
ネットワーク通信は行わない。
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path
from typing import Any, Optional

from ..models import RaceInput
from .base import Fetcher, FetchError, RaceCardData


class ManualFetcher(Fetcher):
    """手入力JSONローダ。

    入力ファイルは RaceInput と同じスキーマでなければならない。
    fetch_race_card は全体を返し、他のメソッドは関連箇所だけを切り出す。
    """

    source_name = "manual"

    def __init__(self, input_path: str | Path) -> None:
        self.input_path = Path(input_path)

    def _load(self) -> dict[str, Any]:
        if not self.input_path.exists():
            raise FetchError(
                f"手入力JSONが見つかりません: {self.input_path}"
            )
        try:
            raw = json.loads(self.input_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise FetchError(
                f"手入力JSONのJSONパースに失敗しました: {self.input_path} ({e})"
            ) from e
        # 構造化バリデーション（生HTMLを上位に渡さないため、ここで弾く）
        try:
            RaceInput.model_validate(raw)
        except Exception as e:
            raise FetchError(
                f"手入力JSONが RaceInput スキーマに合致しません: {e}"
            ) from e
        return raw

    def fetch_race_card(
        self,
        *,
        venue: Optional[str] = None,
        race_no: Optional[int] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> RaceCardData:
        data = self._load()
        # フィルタは現状ベストエフォート（一致しなくても返す）
        race = data.get("race", {})
        if venue and race.get("venue") and race["venue"] != venue:
            # 一致しない場合でも返す前にメモ。FetchError にはしない。
            data.setdefault("user_note", "")
            data["user_note"] = (
                (data["user_note"] or "")
                + f" [manualフェッチ: 指定 venue={venue} と入力 {race.get('venue')} が不一致]"
            ).strip()
        if race_no and race.get("race_no") and race["race_no"] != race_no:
            data.setdefault("user_note", "")
            data["user_note"] = (
                (data["user_note"] or "")
                + f" [manualフェッチ: 指定 race_no={race_no} と入力 {race.get('race_no')} が不一致]"
            ).strip()
        return data

    def fetch_odds(
        self,
        *,
        venue: Optional[str] = None,
        race_no: Optional[int] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        data = self._load()
        return list(data.get("odds") or [])

    def fetch_results(
        self,
        *,
        venue: Optional[str] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        data = self._load()
        return list(data.get("recent_results") or [])

    def fetch_venue_trend(
        self,
        *,
        venue: Optional[str] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> Optional[dict[str, Any]]:
        data = self._load()
        return data.get("venue_trend")
