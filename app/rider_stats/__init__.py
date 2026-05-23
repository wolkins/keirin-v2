"""Rider 統計情報取得パッケージ。

`fetch-rider-stats` CLI 経由で各ソースから検証取得し、
quality 区別付きの RiderStatsBundle を出力する。

設計方針:
- 実数値・推定値・未取得を **厳密に区別**（0 と未取得を混同しない）
- 取得失敗時は missing として明示、予想全体を止めない
- 既存の RaceInput/scoring パイプラインには **影響を与えない**（検証用独立モジュール）
"""

from .models import (
    Quality,
    RiderStat,
    RiderStatsBundle,
    RiderStatsQualitySummary,
    compute_quality_summary,
)
from .sources import (
    AVAILABLE_SOURCES,
    StatsSource,
    fetch_rider_stats,
)


__all__ = [
    "Quality",
    "RiderStat",
    "RiderStatsBundle",
    "RiderStatsQualitySummary",
    "compute_quality_summary",
    "AVAILABLE_SOURCES",
    "StatsSource",
    "fetch_rider_stats",
]
