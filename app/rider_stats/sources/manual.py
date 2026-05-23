"""手入力 / fixture JSON からの取得ソース。

ローカル JSON ファイルから RiderStatsBundle を読み込む。テスト用 + 手入力運用。

JSON 形式は RiderStatsBundle と互換、または簡易形式 (riders のみ):

  {"riders": [
    {"car_no": 1, "name": "X", "score": 100.0, "b_count": 3,
     "nige": 5, "makuri": 2, "sashi": 1, "mark": 0, "quality": "actual"},
    ...
  ]}
"""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..models import RiderStat, RiderStatsBundle, compute_quality_summary


class ManualSource:
    name = "manual"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def fetch(
        self,
        *,
        venue: str,
        date: Date,
        race_no: int,
        session_no: int = 1,
    ) -> RiderStatsBundle:
        warnings: list[str] = []
        if not self.path.exists():
            warnings.append(f"manual ファイルが見つかりません: {self.path}")
            return self._missing_bundle(
                venue, date, race_no, session_no, warnings,
            )
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            warnings.append(f"manual JSON パース失敗: {type(e).__name__}: {e}")
            return self._missing_bundle(
                venue, date, race_no, session_no, warnings,
            )

        # フォーマット判定: RiderStatsBundle 完全形 or 簡易形式
        if "riders" not in raw:
            warnings.append("manual JSON に 'riders' キーがありません")
            return self._missing_bundle(
                venue, date, race_no, session_no, warnings,
            )

        riders: list[RiderStat] = []
        for r in raw.get("riders") or []:
            if not isinstance(r, dict):
                continue
            try:
                quality = r.get("quality")
                if quality not in ("actual", "estimated", "missing"):
                    # quality 未指定なら、数値が入っていれば actual
                    if (
                        (r.get("score") or 0) > 0
                        or (r.get("b_count") or 0) > 0
                        or (r.get("nige") or 0) > 0
                    ):
                        quality = "actual"
                    else:
                        quality = "missing"
                riders.append(RiderStat(
                    car_no=int(r["car_no"]),
                    name=r.get("name"),
                    score=float(r.get("score") or 0.0),
                    b_count=int(r.get("b_count") or 0),
                    nige=int(r.get("nige") or 0),
                    makuri=int(r.get("makuri") or 0),
                    sashi=int(r.get("sashi") or 0),
                    mark=int(r.get("mark") or 0),
                    quality=quality,
                    source_label=self.name,
                    notes=r.get("notes"),
                ))
            except (KeyError, ValueError, TypeError) as e:
                warnings.append(f"rider 行パース失敗: {e}")
                continue

        return RiderStatsBundle(
            source=self.name,
            venue=venue,
            date=date,
            race_no=race_no,
            session_no=session_no,
            riders=riders,
            quality_summary=compute_quality_summary(riders),
            fetched_at=datetime.now(),
            warnings=warnings,
        )

    def _missing_bundle(
        self, venue, date, race_no, session_no, warnings,
    ) -> RiderStatsBundle:
        riders = [
            RiderStat(car_no=i, quality="missing", source_label=self.name)
            for i in range(1, 10)
        ]
        return RiderStatsBundle(
            source=self.name,
            venue=venue,
            date=date,
            race_no=race_no,
            session_no=session_no,
            riders=riders,
            quality_summary=compute_quality_summary(riders),
            fetched_at=datetime.now(),
            warnings=warnings,
        )
