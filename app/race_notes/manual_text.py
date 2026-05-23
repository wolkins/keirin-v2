"""手入力テキストから RaceNotes を生成するパーサ。

最優先実装。ユーザーが新聞・予想記事・公式コメントをコピペで貼り付けることを想定。

サポートする入力形式（柔軟、ヘッダは任意）:

    場名: 松山
    日付: 2026-05-22
    R: 10

    並び: 5-1-3 / 6-4 / 7
    記者見解: 本線は5-1。穴は6-4

    5 長野魅切 自力。状態は良い。前々に踏める。
    1 久樹 長野マーク。番手。差し脚良好。
    3 山本 3番手。位置取り良い。
    ...

著作権配慮:
- 全文を raw_excerpt に保存しない
- comment_summary は最大120文字（Pydantic で強制）
- LLM には要約と signals のみ
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date as Date
from datetime import datetime
from typing import Optional

from app.models import RaceNotes, RiderNote

from .signals import extract_signals


class ManualTextParseError(ValueError):
    """手入力テキストのパース失敗。"""


_HEADER_VENUE = re.compile(r"^\s*(?:場名|場所|venue)\s*[:：]\s*(.+)\s*$", re.IGNORECASE)
_HEADER_DATE = re.compile(r"^\s*(?:日付|日時|date)\s*[:：]\s*(.+)\s*$", re.IGNORECASE)
_HEADER_RACE_NO = re.compile(
    r"^\s*(?:R|レース|race[\s_-]?no|レース番号)\s*[:：]\s*(\d+)\s*R?\s*$",
    re.IGNORECASE,
)
_HEADER_LINE = re.compile(r"^\s*(?:並び|並び予想|ライン|line)\s*[:：]\s*(.+)\s*$", re.IGNORECASE)
_HEADER_PREDICTION = re.compile(
    r"^\s*(?:記者見解|記者予想|予想|本紙の見解|本紙見解|prediction)\s*[:：]\s*(.+)\s*$",
    re.IGNORECASE,
)
_HEADER_RACE_SUMMARY = re.compile(
    r"^\s*(?:見解|要約|race[\s_]?summary|レース概要)\s*[:：]\s*(.+)\s*$",
    re.IGNORECASE,
)

# 「車番 名前 コメント」の行
# 例: "5 長野魅切 自力。状態は良い。前々に踏める。"
# 例: "5番 長野 自力。"
# 例: "①長野 自力。"
_RIDER_LINE = re.compile(
    r"^\s*"
    r"(?:[①-⑨]|(\d)番?)"  # 車番
    r"\s+(\S+?)\s+(.+)$"
)
# 丸数字 → 数字のマッピング
_CIRCLE_DIGITS = {
    "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5,
    "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9,
}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip()


def _coerce_date(value: str) -> Optional[Date]:
    s = _norm(value or "")
    if not s:
        return None
    # YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_car_no(line: str) -> tuple[Optional[int], str]:
    """先頭の車番（丸数字 or 数字）を切り出して (car_no, 残り) を返す。

    NFKC 正規化後は丸数字 ① → 1 になるため、いずれも「先頭の1桁数字」を抽出する。
    対応する書式:
        "5 長野魅切 自力。"      （数字 + 空白）
        "5番 池部 自力。"        （数字 + 番 + 空白）
        "5長野 自力"             （数字 + 日本語、丸数字由来）
    """
    stripped = line.lstrip()
    if not stripped:
        return None, line
    # 先頭1桁数字 + (任意 "番") + (任意空白) + 1文字以上
    m = re.match(r"^(\d)(?:番)?\s*(\S.*)$", stripped)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 9:
            return n, m.group(2)
    return None, line


def parse_race_notes_text(
    text: str,
    *,
    source: str = "manual_text",
    venue: Optional[str] = None,
    date: Optional[Date | str] = None,
    race_no: Optional[int] = None,
) -> RaceNotes:
    """手入力テキストから RaceNotes を生成する。

    Args:
        text: 入力テキスト
        source: 情報源（manual_text / tospo / winticket 等）。明示があれば優先
        venue / date / race_no: テキスト内のヘッダ行より優先。未指定なら本文ヘッダから読む

    Raises:
        ManualTextParseError: テキストが空または rider_notes が1件も取れない
    """
    if not text or not text.strip():
        raise ManualTextParseError("入力テキストが空です。")

    venue_out: Optional[str] = venue
    date_out: Optional[Date] = (
        date if isinstance(date, Date) else _coerce_date(date) if isinstance(date, str) else None
    )
    race_no_out: Optional[int] = race_no
    line_hint: Optional[str] = None
    prediction_hint: Optional[str] = None
    race_summary: Optional[str] = None

    rider_notes: list[RiderNote] = []

    for raw_line in text.splitlines():
        line = _norm(raw_line)
        if not line:
            continue

        # ヘッダ行
        m = _HEADER_VENUE.match(line)
        if m and not venue_out:
            venue_out = m.group(1).strip() or None
            continue
        m = _HEADER_DATE.match(line)
        if m and not date_out:
            date_out = _coerce_date(m.group(1))
            continue
        m = _HEADER_RACE_NO.match(line)
        if m and not race_no_out:
            try:
                n = int(m.group(1))
                if 1 <= n <= 12:
                    race_no_out = n
            except ValueError:
                pass
            continue
        m = _HEADER_LINE.match(line)
        if m and not line_hint:
            line_hint = m.group(1).strip()[:200] or None
            continue
        m = _HEADER_PREDICTION.match(line)
        if m and not prediction_hint:
            prediction_hint = m.group(1).strip()[:300] or None
            continue
        m = _HEADER_RACE_SUMMARY.match(line)
        if m and not race_summary:
            race_summary = m.group(1).strip()[:300] or None
            continue

        # 選手行
        car_no, rest = _parse_car_no(line)
        if car_no is None:
            # 認識できない行はスキップ（ヘッダ未対応 or 空白行など）
            continue
        # rest を "名前 コメント" として分割（最初の空白で区切る）
        rest = rest.strip()
        if not rest:
            continue
        # 名前部とコメント部を分離
        # スペース or 全角スペース or タブで2分割
        parts = re.split(r"\s+", rest, maxsplit=1)
        if len(parts) == 1:
            name = parts[0][:50]
            comment = ""
        else:
            name = parts[0][:50]
            comment = parts[1]
        # コメントから signals 抽出
        signals = extract_signals(comment)
        # 既存の同 car_no があれば追記でなく後勝ち
        rider_notes = [n for n in rider_notes if n.car_no != car_no]
        rider_notes.append(
            RiderNote(
                car_no=car_no,
                name=name or None,
                comment_summary=comment[:120],
                signals=signals,
                confidence=1.0,  # 手入力は信頼度 1.0
            )
        )

    if not rider_notes and not (race_summary or prediction_hint or line_hint):
        raise ManualTextParseError(
            "テキストから選手コメント/見解を1件も抽出できませんでした。"
            "入力形式を確認してください（例: '5 長野魅切 自力。状態良い。'）。"
        )

    # car_no で並び替え
    rider_notes.sort(key=lambda n: n.car_no)

    return RaceNotes(
        source=source,  # type: ignore[arg-type]
        venue=venue_out,
        date=date_out,
        race_no=race_no_out,
        race_summary=race_summary,
        rider_notes=rider_notes,
        line_hint=line_hint,
        prediction_hint=prediction_hint,
    )
