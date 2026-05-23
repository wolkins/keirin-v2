"""天候レスポンス共通の変換ヘルパ。

- WMO weather code → 日本語 condition
- 風向 degree → 8方位の日本語
- hourly 配列から start_time に最も近い時刻インデックスを選ぶ
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Optional


# WMO weather code を簡略化マッピング。
# 参考: https://open-meteo.com/en/docs (Weather variables / weather_code)
def weather_code_to_condition(code: object) -> str:
    """weather_code → 日本語 condition。未知/None は '不明'。"""
    try:
        c = int(code) if code is not None else None
    except (TypeError, ValueError):
        return "不明"
    if c is None:
        return "不明"
    if c == 0:
        return "晴れ"
    if c in (1, 2):
        return "晴れ"
    if c == 3:
        return "曇り"
    if c in (45, 48):
        return "霧"
    if c in (51, 53, 55, 56, 57):
        return "小雨"
    if c in (61, 63, 65, 66, 67):
        return "雨"
    if c in (80, 81):
        return "雨"
    if c == 82:
        return "強雨"
    if c in (71, 73, 75, 77, 85, 86):
        return "雪"
    if c in (95, 96, 99):
        return "雷雨"
    return "不明"


# 8方位
_COMPASS_8 = ["北", "北東", "東", "南東", "南", "南西", "西", "北西"]


def degree_to_compass(degree: object, *, points: int = 8) -> Optional[str]:
    """0-360 を 8方位の日本語に変換する。

    None / 数値化不能なら None を返す。points は現状 8 のみ対応。
    """
    if degree is None:
        return None
    try:
        d = float(degree) % 360.0
    except (TypeError, ValueError):
        return None
    if points != 8:
        # 将来 16方位対応する場合はここで分岐
        points = 8
    # 各セクター幅 = 45度。北は -22.5〜+22.5
    idx = int((d + 22.5) % 360 // 45)
    return _COMPASS_8[idx]


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None


def pick_hourly_index(
    times: list[str],
    *,
    date: Date | str,
    start_time: Optional[str] = None,
) -> int:
    """hourly.time 配列から、対象時刻に最も近いインデックスを返す。

    start_time が None の場合は **正午(12:00)** を採用する（テスト性のため固定）。
    times が空なら ValueError。
    """
    if not times:
        raise ValueError("hourly.time が空です")

    if isinstance(date, Date):
        date_obj = date
    else:
        date_obj = datetime.strptime(str(date), "%Y-%m-%d").date()

    if start_time:
        try:
            hh, mm = start_time.split(":", 1)
            target = datetime(date_obj.year, date_obj.month, date_obj.day, int(hh), int(mm))
        except (ValueError, AttributeError) as e:
            raise ValueError(f"start_time は HH:MM で指定してください: '{start_time}'") from e
    else:
        target = datetime(date_obj.year, date_obj.month, date_obj.day, 12, 0)

    best_idx = 0
    best_diff = None
    for i, t in enumerate(times):
        dt = _parse_iso(t)
        if dt is None:
            continue
        diff = abs((dt - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_idx = i
    return best_idx


def build_wind_note(direction: Optional[str], speed_mps: Optional[float]) -> Optional[str]:
    """wind_note の自動生成。"""
    if direction is None and (speed_mps is None or speed_mps <= 0):
        return None
    parts: list[str] = []
    if direction:
        parts.append(direction)
    if speed_mps is not None and speed_mps > 0:
        parts.append(f"{speed_mps:.1f}m/s")
    return "".join(parts) if parts else None
