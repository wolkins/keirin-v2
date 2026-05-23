"""Rider 統計取得ソース。

各ソースは StatsSource プロトコルを実装:
    fetch(venue, date, race_no, session_no) -> RiderStatsBundle

取得失敗時も例外を投げず、missing 扱いの RiderStatsBundle を返す。
"""

from __future__ import annotations

from datetime import date as Date
from typing import Optional, Protocol

from ..models import RiderStatsBundle


class StatsSource(Protocol):
    """統計取得ソースのプロトコル。"""

    name: str

    def fetch(
        self,
        *,
        venue: str,
        date: Date,
        race_no: int,
        session_no: int = 1,
    ) -> RiderStatsBundle:
        ...


AVAILABLE_SOURCES = ("yenjoy", "yenjoy_dynamic", "manual")


def fetch_rider_stats(
    *,
    source: str,
    venue: str,
    date: Date,
    race_no: int,
    session_no: int = 1,
    http_client=None,
    manual_path: Optional[object] = None,
) -> RiderStatsBundle:
    """指定ソースから統計を取得する。

    ソース別:
      - "yenjoy": 静的取得（戦法ラベル + 4ヶ月得点）→ estimated 扱い
      - "yenjoy_dynamic": Playwright で動的取得（実験的、未安定）→ actual 扱い（成功時）
      - "manual": ローカル JSON ファイルから読み込み → actual 扱い
    """
    if source == "yenjoy":
        from .yenjoy_static import YenJoyStaticSource
        src = YenJoyStaticSource(http_client=http_client)
    elif source == "yenjoy_dynamic":
        from .yenjoy_dynamic import YenJoyDynamicSource
        src = YenJoyDynamicSource()
    elif source == "manual":
        from .manual import ManualSource
        if manual_path is None:
            raise ValueError("manual ソースには --manual-path が必要です")
        src = ManualSource(path=manual_path)
    else:
        raise ValueError(
            f"未対応のソース: '{source}'。"
            f"利用可能: {', '.join(AVAILABLE_SOURCES)}"
        )
    return src.fetch(
        venue=venue, date=date, race_no=race_no, session_no=session_no,
    )
