"""手入力JSONを生成するための補助モジュール。

- `parse_lines` : "3-7-2 / 1-5 / 4-6" のような並び文字列を `Line` のリストに変換
- `build_quick_input` : フラグ群からRaceInputを組み立てる（quick-jsonコマンド用）
- `build_placeholder_rider` : 出走表が無いとき用の最小限riderテンプレ
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date as Date
from datetime import datetime
from typing import Iterable, Optional

from .models import Line, RaceInfo, RaceInput, Rider, Weather


# ライン区切り（/, |, 全角）
_LINE_SEP_RE = re.compile(r"[/|｜・]")
# ライン内区切り（半角ハイフン / 空白 / カンマ / 読点）
# 各種ダッシュは事前に半角ハイフンへ置換するため、ここではASCIIだけで十分。
_CAR_SEP_RE = re.compile(r"[\-_\s,、]+")

# NFKC で吸収しきれないダッシュ変種をASCIIハイフンへ正規化する。
_DASH_VARIANTS = "‐‑‒–—―−"


# ---------------------------------------------------------------------------
# 並び文字列のパース
# ---------------------------------------------------------------------------


class LinesParseError(ValueError):
    """並び文字列のパース失敗。"""


def parse_lines(text: str) -> list[Line]:
    """並び文字列を Line のリストに変換する。

    例:
        "3-7-2 / 1-5 / 4-6" →
            [Line(ライン1, [3,7,2]), Line(ライン2, [1,5]), Line(単騎, [4,6]??)]

    実装上の注意:
        - 区切りは / | ｜ ・ を許容
        - ライン内の車番区切りは半角/全角ハイフン、空白、カンマ、読点を許容
        - 全角数字 (０〜９) は半角に正規化
        - 単騎ライン (要素1) は line_name="単騎" にする
        - 同じ車番が複数ラインに出現したらエラー
        - 1〜9 以外の数値はエラー
    """
    if not text or not text.strip():
        raise LinesParseError("並び文字列が空です。例: '5-1-3 / 2-6-4'")

    normalized = unicodedata.normalize("NFKC", text)
    for ch in _DASH_VARIANTS:
        normalized = normalized.replace(ch, "-")
    normalized = normalized.strip()
    segments = [s.strip() for s in _LINE_SEP_RE.split(normalized) if s.strip()]
    if not segments:
        raise LinesParseError("ライン区切りが見つかりません。例: '5-1-3 / 2-6-4'")

    lines: list[Line] = []
    seen: dict[int, str] = {}  # 車番 → どのライン名で出現したか
    line_counter = 0
    for raw_seg in segments:
        cars_text = [c for c in _CAR_SEP_RE.split(raw_seg) if c]
        if not cars_text:
            raise LinesParseError(f"ラインに車番が含まれていません: '{raw_seg}'")
        cars: list[int] = []
        for token in cars_text:
            if not token.isdigit():
                raise LinesParseError(
                    f"車番として解釈できない値があります: '{token}' (ライン '{raw_seg}')"
                )
            n = int(token)
            if not 1 <= n <= 9:
                raise LinesParseError(
                    f"車番は1〜9の範囲で指定してください: '{n}' (ライン '{raw_seg}')"
                )
            cars.append(n)
        # 同一ライン内の重複
        if len(set(cars)) != len(cars):
            raise LinesParseError(
                f"同じライン内で車番が重複しています: '{raw_seg}'"
            )
        # 他ラインとの重複
        for c in cars:
            if c in seen:
                raise LinesParseError(
                    f"車番 {c} が複数ラインに出現しています: '{seen[c]}' と '{raw_seg}'"
                )
        if len(cars) == 1:
            name = "単騎"
        else:
            line_counter += 1
            name = f"ライン{line_counter}"
        for c in cars:
            seen[c] = raw_seg
        lines.append(Line(line_name=name, cars=cars, description=raw_seg))
    return lines


# ---------------------------------------------------------------------------
# placeholder rider 生成
# ---------------------------------------------------------------------------


def build_placeholder_rider(car_no: int) -> Rider:
    """出走表が無いときの最小限のRider。後で手で埋める前提。"""
    return Rider(
        car_no=car_no,
        name=f"選手{car_no}",
        score=0.0,
        b_count=0,
        nige=0,
        makuri=0,
        sashi=0,
        mark=0,
        comment="",
        recent_summary="",
        style_tags=[],
    )


# ---------------------------------------------------------------------------
# クイックビルダー
# ---------------------------------------------------------------------------


def _auto_race_id(date: Date, venue: str, race_no: int) -> str:
    return f"{date.strftime('%Y%m%d')}-{venue}-{race_no}"


def _parse_date(s: Optional[str]) -> Date:
    if not s:
        return Date.today()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise LinesParseError(f"日付は YYYY-MM-DD 形式で指定してください: '{s}'") from e


def _gather_cars(lines: list[Line]) -> list[int]:
    out: list[int] = []
    for line in lines:
        out.extend(line.cars)
    return out


def build_quick_input(
    *,
    venue: str,
    race_no: int,
    class_name: str,
    date_str: Optional[str] = None,
    start_time: Optional[str] = None,
    race_id: Optional[str] = None,
    bank_note: Optional[str] = None,
    weather: Optional[str] = None,
    wind_direction: Optional[str] = None,
    wind_speed: float = 0.0,
    rain: float = 0.0,
    wind_note: Optional[str] = None,
    lines_text: Optional[str] = None,
    girls: bool = False,
    car_count: int = 7,
    extra_riders: Optional[Iterable[Rider]] = None,
) -> RaceInput:
    """フラグから RaceInput を組み立てる。

    - girls=True のときは lines を空にする
    - lines が指定された場合はそこから車番を抜き出し、足りない車番は placeholder
    - lines も car_count も無い場合は 1〜car_count を rider に並べる

    Returns: RaceInput
    Raises: LinesParseError （並びや日付の解釈失敗時）
    """
    if race_no < 1 or race_no > 12:
        raise LinesParseError(f"レース番号は1〜12の範囲で指定してください: '{race_no}'")
    if car_count < 1 or car_count > 9:
        raise LinesParseError(f"車番数は1〜9で指定してください: '{car_count}'")

    race_date = _parse_date(date_str)

    lines_obj: list[Line] = []
    if not girls and lines_text:
        lines_obj = parse_lines(lines_text)
    # girls=True で lines_text を渡されたら明示的に無視する旨を伝える
    if girls and lines_text:
        # ガールズではライン無効
        lines_obj = []

    weather_obj: Optional[Weather] = None
    if weather or wind_direction or wind_speed > 0 or rain > 0:
        weather_obj = Weather(
            condition=weather or "不明",
            rain_mm_per_hour=rain,
            wind_direction=wind_direction,
            wind_speed_mps=wind_speed,
            wind_note=wind_note,
        )

    race = RaceInfo(
        race_id=race_id or _auto_race_id(race_date, venue, race_no),
        date=race_date,
        venue=venue,
        race_no=race_no,
        class_name=class_name,
        start_time=start_time,
        is_girls=True if girls else None,
        bank_note=bank_note,
    )

    cars_in_lines = _gather_cars(lines_obj)
    if cars_in_lines:
        car_nos = sorted(set(cars_in_lines))
    else:
        car_nos = list(range(1, car_count + 1))

    riders_map: dict[int, Rider] = {c: build_placeholder_rider(c) for c in car_nos}
    for r in extra_riders or []:
        riders_map[r.car_no] = r

    riders = [riders_map[c] for c in sorted(riders_map.keys())]

    return RaceInput(
        race=race,
        riders=riders,
        lines=lines_obj,
        weather=weather_obj,
        odds=[],
        recent_results=[],
        venue_trend=None,
        user_note=None,
    )
