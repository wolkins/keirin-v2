"""Kドリームス /racedetail/{race_id}/ ページ用パーサ。

このページには競走得点・決まり手 (逃/捲/差/マ) が含まれている
（/racecard/ ページには無い）。

各選手は1つの <tr> に並んでおり、典型的な <td> の並びは:
  [..., 選手名(改行で県/年齢/期), 段位, 脚質, ?競走得点, 競走得点,
   逃, 捲, 差, マ, 1着, 2着, 3着, 着外, 出走数, 勝率, 2連率, 3連率]

選手名は <br> を含むこともある（例: "高津 晃治<br>岡　山/46/87"）。
そのため、選手名で行をマッチし、後続の数値リストから順序で取り出す。

実 HTML の class 名や順序が変わっても、テキストレベルの抽出で動くよう
HTMLParser + テキスト連結 + 数値スキャンで構成する。
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Optional


_DIGIT_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


class _RiderRowsExtractor(HTMLParser):
    """<tr> ごとにテキストを連結して行配列に格納する。

    <br> や入れ子の <span> は無視。テキストは半角スペース連結。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0  # tr の深さ
        self._cells: list[str] = []  # 現在の tr の td テキスト
        self._cell_buf: list[str] = []
        self._in_td = False
        self._in_tr = False
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "tr":
            self._in_tr = True
            self._cells = []
        elif tag in ("td", "th") and self._in_tr:
            self._in_td = True
            self._cell_buf = []
        elif tag == "br" and self._in_td:
            # <br> はテキスト区切り
            self._cell_buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._in_tr:
            if self._cells:
                self.rows.append(self._cells)
            self._in_tr = False
            self._cells = []
        elif tag in ("td", "th") and self._in_td:
            text = " ".join(self._cell_buf).strip()
            self._cells.append(_norm(text))
            self._in_td = False
            self._cell_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._cell_buf.append(data)


def parse_race_detail_html(html: str) -> dict[str, dict[str, Any]]:
    """racedetail HTML から各選手の競走得点・決まり手を抽出。

    Args:
        html: /racedetail/{race_id}/ の HTML

    Returns:
        ``{選手名: {"score": float, "nige": int, "makuri": int,
                   "sashi": int, "mark": int}}``
        取れた選手のみ。B数はこのページには無いので含まない。

    実装方針:
        - <tr> ごとに td テキストを集める
        - 各 td テキスト内で、最初に「日本語名 (空白) 日本語名」が出てくる行を
          選手行と判定
        - 選手名の後ろから数値を順に拾い、競走得点（小数）と決まり手4個を抽出
    """
    if not html:
        return {}
    parser = _RiderRowsExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return {}

    result: dict[str, dict[str, Any]] = {}
    name_re = re.compile(r"^([一-鿿々]{1,5})\s*([一-鿿々]{1,5})")

    for cells in parser.rows:
        # 選手名を含む td を探す（県名と一緒に書かれているケース多い）
        rider_name: Optional[str] = None
        for c in cells:
            m = name_re.match(c)
            if m:
                # 「高津 晃治 岡 山/46/87」のような文字列 → 「高津 晃治」
                rider_name = f"{m.group(1)} {m.group(2)}"
                break
        if not rider_name:
            continue

        # この行の数値を全部拾う
        numbers: list[float] = []
        for c in cells:
            for m in _DIGIT_RE.finditer(c):
                try:
                    numbers.append(float(m.group(0)))
                except ValueError:
                    pass
        if len(numbers) < 5:
            continue

        # 競走得点（小数点を持つ最初の数値、80〜120の範囲）を見つける
        score: Optional[float] = None
        score_idx: Optional[int] = None
        for i, n in enumerate(numbers):
            if 50.0 <= n <= 130.0 and n != int(n):
                score = n
                score_idx = i
                break
        if score is None or score_idx is None:
            continue

        # score の直後4つを 逃/捲/差/マ とする
        kimarite = numbers[score_idx + 1 : score_idx + 5]
        if len(kimarite) < 4:
            continue

        result[rider_name] = {
            "score": score,
            "nige": int(kimarite[0]),
            "makuri": int(kimarite[1]),
            "sashi": int(kimarite[2]),
            "mark": int(kimarite[3]),
        }

    return result


def normalize_name(name: str) -> str:
    """選手名の比較用正規化。全角/半角スペース除去、NFKC 正規化。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name))


def merge_stats_into_riders(
    riders: list[dict[str, Any]],
    stats_by_name: dict[str, dict[str, Any]],
) -> int:
    """parse_race_detail_html の戻り値を riders dict に適用する（破壊的）。

    名前完全一致 or スペース除去後の一致でマッチング。

    Returns:
        補完できた選手数
    """
    normalized = {normalize_name(k): v for k, v in stats_by_name.items()}
    matched = 0
    for r in riders:
        name = r.get("name") or ""
        key = normalize_name(name)
        if key in normalized:
            stats = normalized[key]
            r["score"] = float(stats.get("score") or 0.0)
            r["nige"] = int(stats.get("nige") or 0)
            r["makuri"] = int(stats.get("makuri") or 0)
            r["sashi"] = int(stats.get("sashi") or 0)
            r["mark"] = int(stats.get("mark") or 0)
            # 補完したら stats_missing は False に
            r["stats_missing"] = False
            matched += 1
    return matched
