"""東スポ競輪「予想・記者見解ページ」のHTMLパーサ（補助データ用）。

責務:
- 記者見解の短い要約 (race_summary)
- 並び予想ヒント (line_hint)
- 記者予想 (prediction_hint)
- 各選手の短いコメント要約 (rider_notes)
- signals (自力/前々/単騎/番手/自在/状態良い/不安/重い/疲れ など)

著作権配慮:
- raw_excerpt は最大50文字まで。コメント本文は短い要約・signalsに変換
- LLMに渡すのは comment_summary + signals + race_summary + prediction_hint のみ
- 生HTMLを上位（LLM/UI/storage）に流さない
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Optional

from ..base import FetchError


# 50文字に切り詰める（著作権配慮）
MAX_RAW_EXCERPT_LEN = 50


# signal キーワード辞書: コメント中に含まれていれば signals[] に追加
_SIGNAL_KEYWORDS: dict[str, list[str]] = {
    "自力": ["自力", "自力勢", "ライン先頭"],
    "前々": ["前々", "前受け", "早めに動く", "先行"],
    "単騎": ["単騎"],
    "番手": ["番手", "マーク"],
    "自在": ["自在"],
    "追込": ["追込", "追い込み", "追走"],
    "状態良い": ["状態は良い", "状態良い", "好調", "上々", "良好"],
    "状態普通": ["状態普通", "ふつう", "並"],
    "不安": ["不安", "心配", "厳しい"],
    "重い": ["重い", "重そう"],
    "疲れ": ["疲れ", "疲労"],
}


def _norm(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).strip()


def _truncate(text: str, limit: int = MAX_RAW_EXCERPT_LEN) -> str:
    """50文字を超える場合は末尾に「…」を付けて短縮する。"""
    t = _norm(text)
    if len(t) <= limit:
        return t
    return t[:limit] + "…"


def _extract_signals(text: str) -> list[str]:
    """コメント文字列から signals を抽出する。重複除去。"""
    if not text:
        return []
    found: list[str] = []
    for label, keywords in _SIGNAL_KEYWORDS.items():
        if any(k in text for k in keywords):
            if label not in found:
                found.append(label)
    return found


def _summarize_comment(text: str) -> str:
    """コメントを短い要約に変換する（最大40文字程度）。

    句点で区切り、最初の2文を採用。長すぎる場合は切り詰める。
    """
    t = _norm(text)
    if not t:
        return ""
    # 「。」で区切り最初の2文
    parts = [p for p in re.split(r"[。．]", t) if p.strip()]
    summary = "。".join(parts[:2])
    if summary and not summary.endswith("。"):
        summary += "。"
    # 40文字制限
    if len(summary) > 40:
        summary = summary[:40] + "…"
    return summary


# ---------------------------------------------------------------------------
# fixture-style HTML パーサ
# ---------------------------------------------------------------------------


class _RaceNotesParser(HTMLParser):
    """fixture HTML 用のパーサ。

    想定構造:
      <p class="race-summary">記者見解の要約</p>
      <table class="rider-notes">
        <tr class="rider-row">
          <td class="car-no">5</td>
          <td class="name">長野</td>
          <td class="comment">自力。状態は良い...</td>
        </tr>
        ...
      </table>
      <section class="line-hint"><p>5-1-3 / 7-6 / 4</p></section>
      <section class="prediction-hint"><p>本線は5-1-3...</p></section>
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.race_summary: Optional[str] = None
        self.rider_rows: list[dict[str, str]] = []
        self.line_hint: Optional[str] = None
        self.prediction_hint: Optional[str] = None

        # state
        self._in_race_summary = False
        self._in_rider_row = False
        self._current_cell_kind: Optional[str] = None  # "car-no"/"name"/"comment"
        self._current_row: dict[str, str] = {}
        self._buf: list[str] = []

        # section（line-hint / prediction-hint）
        self._in_line_hint = False
        self._in_prediction_hint = False

    def handle_starttag(self, tag, attrs):
        attrs_d = {k: (v or "") for k, v in attrs}
        cls = (attrs_d.get("class") or "").split()

        if tag == "p" and "race-summary" in cls:
            self._in_race_summary = True
            self._buf = []
        elif tag == "tr" and "rider-row" in cls:
            self._in_rider_row = True
            self._current_row = {}
        elif tag == "td" and self._in_rider_row:
            if "car-no" in cls:
                self._current_cell_kind = "car_no"
            elif "name" in cls:
                self._current_cell_kind = "name"
            elif "comment" in cls:
                self._current_cell_kind = "comment"
            else:
                self._current_cell_kind = None
            self._buf = []
        elif tag == "section" and "line-hint" in cls:
            self._in_line_hint = True
        elif tag == "section" and "prediction-hint" in cls:
            self._in_prediction_hint = True
        elif tag == "p" and (self._in_line_hint or self._in_prediction_hint):
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "p" and self._in_race_summary:
            self.race_summary = _norm("".join(self._buf))
            self._in_race_summary = False
            self._buf = []
        elif tag == "td" and self._in_rider_row and self._current_cell_kind:
            self._current_row[self._current_cell_kind] = _norm("".join(self._buf))
            self._current_cell_kind = None
            self._buf = []
        elif tag == "tr" and self._in_rider_row:
            if self._current_row:
                self.rider_rows.append(self._current_row)
            self._in_rider_row = False
            self._current_row = {}
        elif tag == "p" and self._in_line_hint:
            text = _norm("".join(self._buf))
            if text and self.line_hint is None:
                self.line_hint = text
        elif tag == "p" and self._in_prediction_hint:
            text = _norm("".join(self._buf))
            if text and self.prediction_hint is None:
                self.prediction_hint = text
        elif tag == "section" and self._in_line_hint:
            self._in_line_hint = False
        elif tag == "section" and self._in_prediction_hint:
            self._in_prediction_hint = False

    def handle_data(self, data):
        if (
            self._in_race_summary
            or self._current_cell_kind
            or self._in_line_hint
            or self._in_prediction_hint
        ):
            self._buf.append(data)


# ---------------------------------------------------------------------------
# 公開API
# ---------------------------------------------------------------------------


def parse_tospo_race_notes_html(
    html: str,
    *,
    venue: Optional[str] = None,
    date: Optional[str] = None,
    race_no: Optional[int] = None,
    include_raw_excerpt: bool = False,
) -> dict[str, Any]:
    """東スポ予想ページの HTML を構造化 dict に変換する。

    Args:
        html: ページの HTML 本文
        venue / date / race_no: 結果 dict に同梱する識別情報（任意）
        include_raw_excerpt: True にすると rider_notes[].raw_excerpt を含める。
                             既定 False（著作権配慮: 全文転載しない）

    Raises:
        FetchError: HTML が空、または rider_notes が1件も取れなかったとき
    """
    if html is None or len(html.strip()) < 10:
        raise FetchError("東スポHTMLが空または不正です。")

    p = _RaceNotesParser()
    try:
        p.feed(html)
        p.close()
    except Exception as e:
        raise FetchError(
            f"東スポHTMLのパースに失敗しました: {type(e).__name__}: {e}"
        ) from e

    rider_notes: list[dict[str, Any]] = []
    for row in p.rider_rows:
        car_no_str = row.get("car_no", "")
        if not car_no_str.isdigit():
            continue
        car = int(car_no_str)
        if not 1 <= car <= 9:
            continue
        name = row.get("name", "")
        comment = row.get("comment", "")
        signals = _extract_signals(comment)
        summary = _summarize_comment(comment)
        entry: dict[str, Any] = {
            "car_no": car,
            "name": name,
            "comment_summary": summary,
            "signals": signals,
        }
        if include_raw_excerpt:
            entry["raw_excerpt"] = _truncate(comment, MAX_RAW_EXCERPT_LEN)
        rider_notes.append(entry)

    if not rider_notes:
        raise FetchError(
            "東スポページから選手コメントを1件も検出できませんでした。"
            "サイト構造変更の可能性があります。"
        )

    out: dict[str, Any] = {
        "source": "tospo",
        "venue": venue,
        "date": date,
        "race_no": race_no,
        "race_summary": p.race_summary,
        "rider_notes": rider_notes,
        "line_hint": p.line_hint,
        "prediction_hint": p.prediction_hint,
    }
    return out
