"""dict ↔ RaceNotes 相互変換ヘルパ。

既存の Tospo パーサ等は dict を返すので、それを Pydantic モデルに正規化する。
逆に Pydantic モデルを JSON ダンプ用に dict 化する関数も提供。

著作権配慮:
- raw_excerpt は max_length=50 で Pydantic が自動で弾く
- 全文は保存しない
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Optional

from app.models import RaceNotes, RiderNote


_SOURCES = {"tospo", "winticket", "netkeirin", "oddspark", "yenjoy", "manual_text", "generic"}


def _coerce_date(value: Any) -> Optional[Date]:
    if value is None:
        return None
    if isinstance(value, Date):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def dict_to_race_notes(payload: dict[str, Any]) -> RaceNotes:
    """dict（Tospo パーサ等の戻り値）を RaceNotes に変換する。

    未知の source は "generic" に正規化。
    """
    if not isinstance(payload, dict):
        raise ValueError(f"dict 形式を期待: {type(payload).__name__}")

    src = str(payload.get("source") or "generic").strip().lower()
    if src not in _SOURCES:
        src = "generic"

    rider_notes_raw = payload.get("rider_notes") or []
    if not isinstance(rider_notes_raw, list):
        raise ValueError("rider_notes は list 形式である必要があります")

    rider_notes: list[RiderNote] = []
    for n in rider_notes_raw:
        if not isinstance(n, dict):
            continue
        try:
            car_no = int(n.get("car_no"))
        except (TypeError, ValueError):
            continue
        if not 1 <= car_no <= 9:
            continue
        rider_notes.append(
            RiderNote(
                car_no=car_no,
                name=n.get("name") or None,
                comment_summary=str(n.get("comment_summary") or "")[:120],
                signals=list(n.get("signals") or []),
                confidence=n.get("confidence"),
                raw_excerpt=(
                    str(n.get("raw_excerpt"))[:50] if n.get("raw_excerpt") else None
                ),
            )
        )

    return RaceNotes(
        source=src,  # type: ignore[arg-type]
        venue=payload.get("venue"),
        date=_coerce_date(payload.get("date")),
        race_no=payload.get("race_no"),
        race_summary=(
            str(payload.get("race_summary"))[:300]
            if payload.get("race_summary")
            else None
        ),
        rider_notes=rider_notes,
        line_hint=(
            str(payload.get("line_hint"))[:200]
            if payload.get("line_hint") else None
        ),
        prediction_hint=(
            str(payload.get("prediction_hint"))[:300]
            if payload.get("prediction_hint") else None
        ),
    )


def race_notes_to_dict(notes: RaceNotes) -> dict[str, Any]:
    """RaceNotes を JSON 互換 dict に変換する（既存 enrichment.merge_race_notes 互換）。"""
    return notes.model_dump(mode="json", exclude_none=False)
