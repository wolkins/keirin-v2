"""Rider 統計情報（競走得点・B数・決まり手）の取得結果モデル。

**0 と未取得を厳密に区別する**ためのデータ品質タグ付きモデル:

- ``quality="actual"``: 実数値が取れた（信頼度高）
- ``quality="estimated"``: 戦法ラベル等から推定（信頼度中）
- ``quality="missing"``: 取得失敗（0埋めではなく明示的に missing）

これは検証用の独立モデル。既存の `Rider` モデル（`app/models.py`）への
マージは別途行う（取得失敗時に予想全体を止めない安全性のため）。
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


Quality = Literal["actual", "estimated", "missing"]


class RiderStat(BaseModel):
    """1選手分の統計情報 + 品質タグ。"""

    model_config = ConfigDict(extra="forbid")

    car_no: int = Field(..., ge=1, le=9)
    name: Optional[str] = Field(None, max_length=50)
    score: float = Field(0.0, description="競走得点。missing なら 0.0")
    b_count: int = Field(0, ge=0, description="バック数")
    nige: int = Field(0, ge=0, description="逃げ回数")
    makuri: int = Field(0, ge=0, description="捲り回数")
    sashi: int = Field(0, ge=0, description="差し回数")
    mark: int = Field(0, ge=0, description="マーク回数")
    quality: Quality = Field(
        "missing",
        description="actual=実数取得 / estimated=戦法等からの推定 / missing=取得失敗",
    )
    source_label: Optional[str] = Field(
        None,
        description="どこから取得したかの識別子 (例: 'yenjoy_static', 'yenjoy_dynamic')",
    )
    notes: Optional[str] = Field(
        None,
        description="参考情報（戦法ラベル、上がりタイム等）",
    )


class RiderStatsQualitySummary(BaseModel):
    """quality_summary 構造。"""

    model_config = ConfigDict(extra="forbid")

    actual_count: int = 0
    estimated_count: int = 0
    missing_count: int = 0
    total: int = 0


class RiderStatsBundle(BaseModel):
    """RiderStat の取得結果バンドル。

    検証 CLI (`fetch-rider-stats`) の出力形式。
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., description="取得元 (yenjoy_static / yenjoy_dynamic / manual)")
    venue: str
    date: Date
    race_no: int = Field(..., ge=1, le=12)
    session_no: int = Field(1, ge=1, le=10)
    riders: list[RiderStat] = Field(default_factory=list)
    quality_summary: RiderStatsQualitySummary = Field(
        default_factory=RiderStatsQualitySummary,
    )
    fetched_at: Optional[datetime] = None
    warnings: list[str] = Field(default_factory=list)


def compute_quality_summary(riders: list[RiderStat]) -> RiderStatsQualitySummary:
    """riders から quality_summary を集計する。"""
    a = sum(1 for r in riders if r.quality == "actual")
    e = sum(1 for r in riders if r.quality == "estimated")
    m = sum(1 for r in riders if r.quality == "missing")
    return RiderStatsQualitySummary(
        actual_count=a, estimated_count=e, missing_count=m, total=len(riders),
    )
