"""Kドリームスの「出走表ページ」HTMLパーサ（試験実装）。

依存は Python 標準ライブラリのみ。BeautifulSoup は使わず `html.parser`。

期待するHTML構造（fixture 参照）::

    <div class="race-info">
      <span class="class-name">A級一般</span>
      <span class="start-time">10:53</span>
    </div>
    <table class="riders-table">
      <tr class="rider-row" data-car-no="1">
        <td class="car-no">1</td>
        <td class="name">楢原悠斗</td>
        <td class="score">83.20</td>
        <td class="b-count">1</td>
        <td class="nige">0</td>
        <td class="makuri">0</td>
        <td class="sashi">4</td>
        <td class="mark">5</td>
        <td class="comment">番手</td>
        <td class="recent">前節2-3-2着</td>
      </tr>
      ...
    </table>
    <div class="lines">
      <div class="line" data-line-name="九州">⑤池部－①楢原－③平</div>
      <div class="line" data-line-name="単騎">⑦白井</div>
    </div>

サイト構造変更時はこのパーサだけ差し替えれば良い。
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Optional

from ..base import FetchError


# 取得対象のセル class 名。HTML 側がこの class を持っていない場合は
# 推定にフォールバックする。
_RIDER_FIELDS = (
    "car-no",
    "name",
    "score",
    "b-count",
    "nige",
    "makuri",
    "sashi",
    "mark",
    "comment",
    "recent",
)

# 全角ハイフン/ダッシュ正規化用
_DASH_VARIANTS = "‐‑‒–—―−－ー"

# 丸付き数字 ①〜⑨ を半角数字へ
_CIRCLED_DIGITS = str.maketrans(
    {
        "①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5",
        "⑥": "6", "⑦": "7", "⑧": "8", "⑨": "9",
        # 黒丸（時々ある）
        "❶": "1", "❷": "2", "❸": "3", "❹": "4", "❺": "5",
        "❻": "6", "❼": "7", "❽": "8", "❾": "9",
    }
)

_DIGIT_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _classes(attrs: dict[str, str]) -> set[str]:
    cls = attrs.get("class") or ""
    return set(cls.split())


def _norm_text(text: Optional[str]) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    return t.strip()


def _parse_int(text: str) -> Optional[int]:
    """文字列から最初の整数を返す。取れなければ None。"""
    t = _norm_text(text)
    if not t:
        return None
    m = _DIGIT_RE.search(t)
    if not m:
        return None
    try:
        return int(float(m.group(0)))
    except ValueError:
        return None


# 競走得点・決まり手の複数表記
_SCORE_LABELS = ("競走得点", "得点")
_KIMARITE_LABELS = {
    "nige": ("逃げ", "逃"),
    "makuri": ("捲り", "捲"),
    "sashi": ("差し", "差"),
    "mark": ("マーク", "マ"),
}
_B_LABELS = ("B", "バック")


def extract_stats_from_text(text: str) -> dict[str, Optional[float]]:
    """選手データテキスト(1行 or HTML テキスト) から競走得点・B・決まり手を抽出。

    例:
      "競走得点：109.78 B 3 逃14 捲5 差2 マ0"
      "得点 100.54 逃0 捲0 差5 マ2"
      "109.78 B3 逃14"
    のような複数表記に対応する。

    Returns:
        {"score": float?, "b_count": int?, "nige": int?, "makuri": int?,
         "sashi": int?, "mark": int?}
        各値は取得できない場合 None。
    """
    out: dict[str, Optional[float]] = {
        "score": None, "b_count": None,
        "nige": None, "makuri": None, "sashi": None, "mark": None,
    }
    if not text:
        return out
    norm = _norm_text(text)

    # 競走得点: "競走得点：109.78" / "得点 100.54"
    for label in _SCORE_LABELS:
        m = re.search(
            rf"{re.escape(label)}\s*[:：]?\s*(\d+(?:\.\d+)?)",
            norm,
        )
        if m:
            try:
                out["score"] = float(m.group(1))
                break
            except ValueError:
                pass
    # 「101.45 ... 」のように label なしで先頭にスコア相当の小数があれば
    if out["score"] is None:
        m = re.match(r"\s*(\d{2,3}\.\d{1,3})", norm)
        if m:
            try:
                out["score"] = float(m.group(1))
            except ValueError:
                pass

    # B 回数: "B 3" / "バック 5"
    for label in _B_LABELS:
        m = re.search(rf"{re.escape(label)}\s*(\d+)", norm)
        if m:
            try:
                out["b_count"] = int(m.group(1))
                break
            except ValueError:
                pass

    # 決まり手: "逃14 捲5 差2 マ0"
    for key, labels in _KIMARITE_LABELS.items():
        for label in labels:
            # ラベル + 数字 (例: "逃14")
            m = re.search(rf"{re.escape(label)}\s*(\d+)", norm)
            if m:
                try:
                    out[key] = int(m.group(1))
                    break
                except ValueError:
                    pass
    return out


def _parse_int_or_zero(text: str) -> int:
    """整数フィールド用。'-' や空文字は 0 として扱う。"""
    n = _parse_int(text)
    return n if n is not None else 0


def _parse_float(text: str) -> Optional[float]:
    t = _norm_text(text)
    if not t:
        return None
    m = _DIGIT_RE.search(t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _parse_car_no(text: str, attr: Optional[str]) -> Optional[int]:
    """車番。data-car-no 属性 → セルテキスト の順で取る。1〜9に限定。"""
    for src in (attr, text):
        if not src:
            continue
        n = _parse_int(src)
        if n is not None and 1 <= n <= 9:
            return n
    return None


def _extract_line_cars(text: str) -> list[int]:
    """ライン表記文字列から車番のリストを返す。

    例: '⑤池部－①楢原－③平' → [5, 1, 3]
        '⑦単騎'             → [7]
    """
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.translate(_CIRCLED_DIGITS)
    for ch in _DASH_VARIANTS:
        normalized = normalized.replace(ch, "-")
    # 車番は1桁(1〜9)なので1桁ずつ走査する。
    # こうすることで '①①②' のように区切り無しの並びにも対応できる。
    seen: list[int] = []
    for ch in normalized:
        if ch.isdigit():
            n = int(ch)
            if 1 <= n <= 9 and n not in seen:
                seen.append(n)
    return seen


# ---------------------------------------------------------------------------
# HTML 解析
# ---------------------------------------------------------------------------


class _RaceCardParser(HTMLParser):
    """出走表ページのパーサ。

    対応セクション:
      - <div class="race-info"> 内の class-name / start-time
      - <table class="riders-table"> 内の rider-row
      - <div class="lines"> 内の各 .line
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # race-info
        self._in_race_info = 0
        self._in_class_name = 0
        self._in_start_time = 0
        self._class_name_buf: list[str] = []
        self._start_time_buf: list[str] = []
        # riders
        self._in_riders_table = 0
        self._row: Optional[dict[str, Any]] = None
        self._field: Optional[str] = None
        self._field_buf: list[str] = []
        self._td_depth = 0
        self.rows: list[dict[str, Any]] = []
        # lines
        self._in_lines_block = 0
        self._current_line: Optional[dict[str, Any]] = None
        self._line_buf: list[str] = []
        self.lines: list[dict[str, Any]] = []

    # ---- starttag ----
    def handle_starttag(self, tag: str, attrs_raw: list[tuple[str, Optional[str]]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_raw}
        cls = _classes(attrs)

        # race-info ブロック
        if tag == "div" and "race-info" in cls:
            self._in_race_info += 1
            return
        if self._in_race_info:
            if "class-name" in cls:
                self._in_class_name += 1
                return
            if "start-time" in cls:
                self._in_start_time += 1
                return

        # riders-table
        if tag == "table" and "riders-table" in cls:
            self._in_riders_table += 1
            return
        if self._in_riders_table:
            if tag == "tr" and "rider-row" in cls:
                self._row = {"_data_car_no": attrs.get("data-car-no", "")}
                return
            if tag == "td" and self._row is not None:
                self._td_depth += 1
                for f in _RIDER_FIELDS:
                    if f in cls:
                        self._field = f
                        self._field_buf = []
                        return

        # lines block
        if tag == "div" and "lines" in cls:
            self._in_lines_block += 1
            return
        if self._in_lines_block:
            if tag == "div" and "line" in cls:
                self._current_line = {
                    "line_name": attrs.get("data-line-name", "").strip() or None,
                }
                self._line_buf = []

    # ---- endtag ----
    def handle_endtag(self, tag: str) -> None:
        # race-info
        if self._in_race_info:
            if self._in_class_name and tag in ("span", "div"):
                # span を閉じれば class-name 確定
                self._in_class_name = max(0, self._in_class_name - 1)
            if self._in_start_time and tag in ("span", "div"):
                self._in_start_time = max(0, self._in_start_time - 1)
            if tag == "div":
                self._in_race_info = max(0, self._in_race_info - 1)

        # riders-table
        if self._in_riders_table:
            if tag == "td":
                if self._field and self._td_depth > 0 and self._row is not None:
                    text = "".join(self._field_buf).strip()
                    self._row[self._field] = text
                    self._field = None
                    self._field_buf = []
                self._td_depth = max(0, self._td_depth - 1)
            elif tag == "tr" and self._row is not None:
                self.rows.append(self._row)
                self._row = None
                self._field = None
                self._field_buf = []
                self._td_depth = 0
            elif tag == "table":
                self._in_riders_table = max(0, self._in_riders_table - 1)

        # lines block
        if self._in_lines_block:
            if tag == "div" and self._current_line is not None:
                text = "".join(self._line_buf).strip()
                self._current_line["text"] = text
                self.lines.append(self._current_line)
                self._current_line = None
                self._line_buf = []
            elif tag == "div" and self._current_line is None:
                # block自体の閉じタグ
                self._in_lines_block = max(0, self._in_lines_block - 1)

    # ---- data ----
    def handle_data(self, data: str) -> None:
        if self._in_class_name:
            self._class_name_buf.append(data)
        if self._in_start_time:
            self._start_time_buf.append(data)
        if self._field is not None:
            self._field_buf.append(data)
        if self._current_line is not None:
            self._line_buf.append(data)

    # ---- helper ----
    def class_name(self) -> Optional[str]:
        s = _norm_text("".join(self._class_name_buf))
        return s or None

    def start_time(self) -> Optional[str]:
        s = _norm_text("".join(self._start_time_buf))
        return s or None


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------


def _build_rider(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    car_no = _parse_car_no(row.get("car-no", ""), row.get("_data_car_no"))
    if car_no is None:
        return None
    name = _norm_text(row.get("name", "")) or f"選手{car_no}"
    comment = _norm_text(row.get("comment", "")) or None
    recent = _norm_text(row.get("recent", "")) or None
    # 数値情報が取得できたか判定
    score_val = _parse_float(row.get("score", ""))
    b_count_val = _parse_int(row.get("b-count", ""))
    nige_val = _parse_int(row.get("nige", ""))
    makuri_val = _parse_int(row.get("makuri", ""))
    sashi_val = _parse_int(row.get("sashi", ""))
    mark_val = _parse_int(row.get("mark", ""))
    # 全て None なら stats_missing
    stats_missing = all(
        v is None for v in (
            score_val, b_count_val, nige_val, makuri_val, sashi_val, mark_val,
        )
    )
    return {
        "car_no": car_no,
        "name": name,
        "score": score_val if score_val is not None else 0.0,
        "b_count": b_count_val if b_count_val is not None else 0,
        "nige": nige_val if nige_val is not None else 0,
        "makuri": makuri_val if makuri_val is not None else 0,
        "sashi": sashi_val if sashi_val is not None else 0,
        "mark": mark_val if mark_val is not None else 0,
        "comment": comment,
        "recent_summary": recent,
        "style_tags": [],
        "stats_missing": stats_missing,
    }


def _build_lines(parsed_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, ln in enumerate(parsed_lines, start=1):
        text = ln.get("text") or ""
        cars = _extract_line_cars(text)
        if not cars:
            continue
        name = ln.get("line_name")
        if not name:
            if len(cars) == 1:
                name = "単騎"
            else:
                name = f"ライン{i}"
        out.append(
            {
                "line_name": name,
                "cars": cars,
                "description": text or None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 実 Kドリームス用パーサ（class属性なし、列位置で抽出）
# ---------------------------------------------------------------------------

# 実 Kドリームス出走表ページの列構成
# (header順): 予想 / 好気合 / 総評 / 枠番 / 車番 / 選手名 / 府県 / 年齢 / 期別 / 級班 / 脚質
_REAL_COL_CAR_NO = 4
_REAL_COL_NAME = 5
_REAL_COL_PREF = 6
_REAL_COL_AGE = 7
_REAL_COL_TERM = 8
_REAL_COL_RANK = 9   # 級班 (A1, L1 等)
_REAL_COL_STYLE = 10  # 脚質 (逃, 追, 両, 自, 先, 捲)


_STYLE_TAG_MAP: dict[str, list[str]] = {
    "逃": ["先行", "自力"],
    "先": ["先行", "自力"],
    "捲": ["捲り", "自力"],
    "自": ["自力"],
    "追": ["番手", "追込"],
    "差": ["差し"],
    "両": ["自在"],
}


class _RealKdreamsRaceCardParser(HTMLParser):
    """実 Kドリームス出走表ページ用パーサ。

    実HTML構造（観測済み）:
      - 各レースは <ul class="racecard_list"><li> 内
      - レース番号: <span class="num">1R</span>
      - クラス名: <span class="name">Ａ級特予選</span>
      - 発走時刻: <dl><dt>発走</dt><dd>16:09</dd></dl>
      - 出走表テーブル: <div class="racecard_table"><table>...
      - ライダー行: <tr class="n1">  ← クラス名末尾が車番
      - 選手名: <td class="rider">
      - 府県/年齢/期別/級班/脚質: rider 以降の bare <td>
      - 並び予想: <dl class="line_position"><dt>並び予想</dt><dd>...
        ライン区切り: <span class="icon_p space">  ← 空 span が区切り
    """

    _RACE_HEADER_RE = re.compile(r"^\s*(\d{1,2})\s*R\b")
    _TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
    _TR_CAR_NO_RE = re.compile(r"^n(\d)$")

    def __init__(self, target_race_no: int) -> None:
        super().__init__(convert_charrefs=True)
        self.target_race_no = target_race_no
        self.current_race_no: Optional[int] = None
        # span 検出（race header / class_name）
        self._span_num_buf: Optional[list[str]] = None
        self._span_name_buf: Optional[list[str]] = None
        # dt/dd（発走時刻）
        self._in_dt = False
        self._dt_buf: list[str] = []
        self._last_dt: str = ""
        self._in_dd = False
        self._dd_buf: list[str] = []
        # table row / td
        self._tr_classes: set[str] = set()
        self._in_tr = False
        self._td_buf: Optional[list[str]] = None
        self._td_class: str = ""
        self._tr_cells: list[tuple[str, str]] = []  # (class, text)
        # line_position
        self._in_line_dd = False
        self._line_groups: list[list[int]] = []  # 並び予想：1ライン = [車番...]
        self._current_line_group: list[int] = []
        self._line_pn_buf: Optional[list[str]] = None  # <span class="p00X">N</span>
        self._line_text_buf: list[str] = []
        # outputs
        self.rider_rows: list[dict[str, str]] = []
        self.start_time: Optional[str] = None
        self.class_name: Optional[str] = None

    # ---- starttag ----
    def handle_starttag(self, tag: str, attrs_raw: list[tuple[str, Optional[str]]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_raw}
        cls = set((attrs.get("class") or "").split())

        # race header / class_name
        if tag == "span" and "num" in cls and not self._in_tr:
            self._span_num_buf = []
            return
        if tag == "span" and "name" in cls and not self._in_tr:
            self._span_name_buf = []
            return

        # 発走 dt/dd
        if tag == "dt":
            self._in_dt = True
            self._dt_buf = []
            return
        if tag == "dd":
            self._in_dd = True
            self._dd_buf = []
            # 並び予想の dd 検出（直前の dt が "並び予想"）
            if self._last_dt and "並び予想" in self._last_dt and self.current_race_no == self.target_race_no:
                self._in_line_dd = True
                self._current_line_group = []
                self._line_groups = []
                self._line_text_buf = []
            return

        # 並び予想内の span
        if self._in_line_dd and tag == "span":
            # space クラスはライン区切り
            if "space" in cls:
                # 現在のグループを確定
                if self._current_line_group:
                    self._line_groups.append(self._current_line_group)
                    self._current_line_group = []
            # 車番を表す span（p00X, p001 等）。NFKC で数字に正規化された後の数字を拾うため、
            # 専用バッファを使って単独の数字文字 1〜9 を取得する
            elif cls and any(c.startswith("p00") for c in cls):
                # p001-p009 → 1-9 の車番
                for c in cls:
                    if c.startswith("p00") and len(c) >= 4 and c[3:].isdigit():
                        n = int(c[3:])
                        if 1 <= n <= 9 and n not in self._current_line_group:
                            self._current_line_group.append(n)
                        break
            return

        # table row
        if tag == "tr":
            self._in_tr = True
            self._tr_classes = cls
            self._tr_cells = []
            return
        if (tag == "td" or tag == "th") and self._in_tr:
            self._td_buf = []
            # td のクラスを記録（rider/num/bracket など）
            classes = list(cls)
            self._td_class = classes[0] if classes else ""
            return

    # ---- endtag ----
    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            if self._span_num_buf is not None:
                text = _norm_text("".join(self._span_num_buf))
                m = self._RACE_HEADER_RE.match(text)
                if m:
                    self.current_race_no = int(m.group(1))
                self._span_num_buf = None
            if self._span_name_buf is not None:
                text = _norm_text("".join(self._span_name_buf))
                if self.current_race_no == self.target_race_no and text and not self.class_name:
                    self.class_name = text
                self._span_name_buf = None
            return

        if tag == "dt":
            if self._in_dt:
                self._last_dt = _norm_text("".join(self._dt_buf))
                self._in_dt = False
                self._dt_buf = []
            return

        if tag == "dd":
            if self._in_dd:
                text = _norm_text("".join(self._dd_buf))
                if (
                    self.current_race_no == self.target_race_no
                    and self._last_dt == "発走"
                ):
                    m = self._TIME_RE.match(text)
                    if m:
                        self.start_time = f"{int(m.group(1)):02d}:{m.group(2)}"
                if self._in_line_dd:
                    # 残っているグループを確定
                    if self._current_line_group:
                        self._line_groups.append(self._current_line_group)
                    self._in_line_dd = False
                    self._current_line_group = []
                self._in_dd = False
                self._dd_buf = []
            return

        if tag == "td" or tag == "th":
            if self._in_tr and self._td_buf is not None:
                text = _norm_text("".join(self._td_buf))
                self._tr_cells.append((self._td_class, text))
                self._td_buf = None
                self._td_class = ""
            return

        if tag == "tr":
            if self._in_tr:
                self._process_row(self._tr_classes, self._tr_cells)
            self._in_tr = False
            self._tr_classes = set()
            self._tr_cells = []
            return

    def handle_data(self, data: str) -> None:
        if self._span_num_buf is not None:
            self._span_num_buf.append(data)
        if self._span_name_buf is not None:
            self._span_name_buf.append(data)
        if self._in_dt:
            self._dt_buf.append(data)
        if self._in_dd:
            self._dd_buf.append(data)
        if self._td_buf is not None:
            self._td_buf.append(data)
        if self._in_line_dd:
            self._line_text_buf.append(data)

    # ---- ライダー行処理 ----
    def _process_row(self, tr_classes: set[str], cells: list[tuple[str, str]]) -> None:
        if self.current_race_no != self.target_race_no:
            return
        # tr class="n{N}" を検出してライダー行と認識
        car_no = None
        for c in tr_classes:
            m = self._TR_CAR_NO_RE.match(c)
            if m:
                car_no = int(m.group(1))
                break
        if car_no is None or not 1 <= car_no <= 9:
            return
        # cells から取り出し
        name = ""
        pref = ""
        age = ""
        term = ""
        rank = ""
        style = ""
        # rider 以降の bare td の順序: pref / age / term / rank / style
        bare_after_rider: list[str] = []
        seen_rider = False
        for tcls, ttext in cells:
            if tcls == "rider":
                name = ttext
                seen_rider = True
                continue
            if seen_rider and tcls == "":
                bare_after_rider.append(ttext)
        if len(bare_after_rider) >= 5:
            pref = bare_after_rider[0]
            age = bare_after_rider[1]
            term = bare_after_rider[2]
            rank = bare_after_rider[3]
            style = bare_after_rider[4]
        elif len(bare_after_rider) >= 1:
            pref = bare_after_rider[0]
            age = bare_after_rider[1] if len(bare_after_rider) > 1 else ""
            term = bare_after_rider[2] if len(bare_after_rider) > 2 else ""
            rank = bare_after_rider[3] if len(bare_after_rider) > 3 else ""
            style = bare_after_rider[4] if len(bare_after_rider) > 4 else ""
        if not name:
            return
        self.rider_rows.append({
            "car_no": str(car_no),
            "name": name,
            "pref": pref,
            "age": age,
            "term": term,
            "rank": rank,
            "style": style,
        })

    def line_groups(self) -> list[list[int]]:
        return self._line_groups

    def line_text(self) -> str:
        return "".join(self._line_text_buf).strip()


def _build_rider_real(row: dict[str, str]) -> Optional[dict[str, Any]]:
    car_no = _parse_int(row.get("car_no", ""))
    if car_no is None or not 1 <= car_no <= 9:
        return None
    style_text = row.get("style", "")[:1]  # 1文字目
    tags = list(_STYLE_TAG_MAP.get(style_text, []))
    pref = row.get("pref", "").strip()
    rank = row.get("rank", "").strip()
    comment_parts = [s for s in (pref, rank, row.get("style", "").strip()) if s]
    comment = " / ".join(comment_parts) if comment_parts else None
    return {
        "car_no": car_no,
        "name": row.get("name") or f"選手{car_no}",
        # 詳細スコアは /racecard/ ページに無いため未取得。
        # /racedetail/{race_id} で別途補完すべき。
        "score": 0.0,
        "b_count": 0,
        "nige": 0,
        "makuri": 0,
        "sashi": 0,
        "mark": 0,
        "comment": comment,
        "recent_summary": None,
        "style_tags": tags,
        # 数値情報は未取得である旨を明示（数値不足モード判定で使われる）
        "stats_missing": True,
    }


def _is_girls_class(class_name: Optional[str], riders: list[dict[str, Any]]) -> Optional[bool]:
    if class_name and "ガールズ" in class_name:
        return True
    if class_name and ("L級" in class_name or class_name.startswith("Ｌ")):
        return True
    # 選手の comment に L1/L2 が含まれていれば girls
    for r in riders:
        c = r.get("comment") or ""
        if "L1" in c or "L2" in c:
            return True
    return None


def _parse_real_kdreams(
    html: str,
    *,
    venue: str,
    date_str: str,
    race_no: int,
) -> Optional[dict[str, Any]]:
    """実 Kドリームス出走表ページ用パーサ。

    対象レースが見つからない / ライダー0件の場合は None を返す。
    """
    p = _RealKdreamsRaceCardParser(target_race_no=race_no)
    try:
        p.feed(html)
        p.close()
    except Exception:
        return None

    riders: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in p.rider_rows:
        rider = _build_rider_real(row)
        if rider is None:
            continue
        if rider["car_no"] in seen:
            continue
        seen.add(rider["car_no"])
        riders.append(rider)
    riders.sort(key=lambda r: r["car_no"])
    if not riders:
        return None

    # 並び予想: HTML 内の <span class="space"> 区切りでライングループが取れている
    lines_out: list[dict[str, Any]] = []
    line_no = 0
    for group in p.line_groups():
        cars_valid = [c for c in group if c in seen]
        if not cars_valid:
            continue
        if len(cars_valid) == 1:
            name = "単騎"
        else:
            line_no += 1
            name = f"ライン{line_no}"
        lines_out.append({
            "line_name": name,
            "cars": cars_valid,
            "description": None,
        })

    is_girls = _is_girls_class(p.class_name, riders)

    yyyymmdd = date_str.replace("-", "")
    race_id = f"{yyyymmdd}-{venue}-{race_no}"
    race = {
        "race_id": race_id,
        "date": date_str,
        "venue": venue,
        "race_no": race_no,
        "class_name": p.class_name or "不明",
        "start_time": p.start_time,
        "is_girls": is_girls,
        "bank_note": None,
    }
    return {
        "race": race,
        "riders": riders,
        "lines": lines_out,
        "weather": None,
        "odds": [],
        "recent_results": [],
        "venue_trend": None,
        "user_note": (
            "Kドリームス出走表から自動取得（score/B/逃/捲/差/マークは未取得・要手動補完）"
        ),
    }


# ---------------------------------------------------------------------------
# 公開関数（fixture-style → 実 Kドリームス-style のフォールバック）
# ---------------------------------------------------------------------------


def parse_race_card_html(
    html: str,
    *,
    venue: str,
    date_str: str,
    race_no: int,
) -> dict[str, Any]:
    """出走表HTMLを RaceInput 互換 dict へ変換する。

    まず fixture 風（class属性ベース）を試し、選手が取れなければ
    実 Kドリームス風（列位置ベース）にフォールバックする。

    Raises:
        FetchError: HTMLが空、どちらのパーサでも riders が取れない場合
    """
    if html is None or len(html.strip()) < 10:
        raise FetchError("出走表HTMLが空または不正です。")

    # --- 開催なし / 認証エラー検出（Kドリームスのエラーページ） ---
    if ("SYSTEM_ERROR" in html or "エラーが発生しました" in html) and (
        "racecard_list" not in html and "rider-row" not in html
    ):
        raise FetchError(
            "Kドリームスから SYSTEM_ERROR ページが返されました。"
            "対象日にこの場の開催が無いか、URLパラメータが正しくない可能性があります。"
            "場名・日付・開催日番号(--session-no)を確認してください。"
        )

    # --- fixture-style ---
    p = _RaceCardParser()
    try:
        p.feed(html)
        p.close()
    except Exception as e:
        # 致命でない場合は実Kドリームス用にフォールバックさせる
        p = None  # type: ignore[assignment]

    riders: list[dict[str, Any]] = []
    seen_cars: set[int] = set()
    if p is not None:
        for raw in p.rows:
            rider = _build_rider(raw)
            if rider is None:
                continue
            if rider["car_no"] in seen_cars:
                continue
            seen_cars.add(rider["car_no"])
            riders.append(rider)
        riders.sort(key=lambda r: r["car_no"])

    if riders:
        lines = _build_lines(p.lines) if p else []
        valid_cars = set(seen_cars)
        cleaned_lines: list[dict[str, Any]] = []
        for ln in lines:
            cars = [c for c in ln["cars"] if c in valid_cars]
            if not cars:
                continue
            ln = {**ln, "cars": cars}
            cleaned_lines.append(ln)

        yyyymmdd = date_str.replace("-", "")
        race_id = f"{yyyymmdd}-{venue}-{race_no}"
        race = {
            "race_id": race_id,
            "date": date_str,
            "venue": venue,
            "race_no": race_no,
            "class_name": p.class_name() or "不明",
            "start_time": p.start_time(),
            "is_girls": None,
            "bank_note": None,
        }
        return {
            "race": race,
            "riders": riders,
            "lines": cleaned_lines,
            "weather": None,
            "odds": [],
            "recent_results": [],
            "venue_trend": None,
            "user_note": None,
        }

    # --- 実 Kドリームス-style にフォールバック ---
    real = _parse_real_kdreams(html, venue=venue, date_str=date_str, race_no=race_no)
    if real is not None:
        return real

    raise FetchError(
        "出走表から選手が1人も取得できませんでした。サイト構造変更の可能性があります。"
    )
