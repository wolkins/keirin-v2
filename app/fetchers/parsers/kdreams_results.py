"""Kドリームスの「結果ページ」HTMLパーサ（試験実装）。

依存は Python 標準ライブラリのみ。BeautifulSoup は使わず、
`html.parser.HTMLParser` を用いて結果テーブルを安全に抽出する。

期待する最小HTML構造（fixture 参照）::

    <table class="race-results">
      <tr class="race-row" data-race-no="1">
        <td class="race-no">1R</td>
        <td class="result">5-6-2</td>
        <td class="payout">12,340円</td>
      </tr>
      ...
    </table>

サイト構造の変更に強くなるよう、`class` 属性とトークン一致でフィールドを拾う。
パースできない、結果テーブルが空の場合は FetchError を投げる。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

from ..base import FetchError


# 3連単/2連単問わず、ハイフン区切りの数値結果のみを許可
_RESULT_RE = re.compile(r"^\d+-\d+-\d+$|^\d+-\d+$|^\d+$")
# 払戻金: 12,340円 / ¥12,340 / 1234 / 12,340 円 等から数字だけ取り出す
_PAYOUT_DIGIT_RE = re.compile(r"[0-9]+")

# 未確定とみなす表記
_PENDING_TOKENS = ("-", "—", "−", "未確定", "発走前", "中止", "TBD", "")


def _classes(attrs: dict[str, str]) -> set[str]:
    cls = attrs.get("class") or ""
    return set(cls.split())


class _ResultsTableParser(HTMLParser):
    """結果テーブルの行をdictとして集めるHTMLパーサ。"""

    _FIELDS = ("race-no", "result", "payout")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._row: Optional[dict[str, str]] = None
        self._field: Optional[str] = None
        self._buf: list[str] = []
        # td入れ子防止用に簡易ネストカウンタ
        self._td_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_d = {k: (v or "") for k, v in attrs}
        cls = _classes(attrs_d)
        if tag == "tr" and "race-row" in cls:
            rno = attrs_d.get("data-race-no", "").strip()
            self._row = {"race_no_attr": rno}
        elif tag == "td" and self._row is not None:
            self._td_depth += 1
            for f in self._FIELDS:
                if f in cls:
                    self._field = f
                    self._buf = []
                    return

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            if self._field and self._td_depth > 0:
                text = "".join(self._buf).strip()
                self._row[self._field] = text if self._row is not None else text
                self._field = None
                self._buf = []
            self._td_depth = max(0, self._td_depth - 1)
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._field = None
            self._buf = []
            self._td_depth = 0

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._buf.append(data)


def _normalize_result(text: str) -> Optional[str]:
    """'5-6-2' のような結果文字列を返す。未確定や不正は None。"""
    if not text:
        return None
    t = text.strip()
    # 全角ハイフン/ダッシュをASCIIに寄せる
    # FF0D (全角ハイフン), U+2010〜U+2015, U+2212(minus), U+30FC(長音)
    for ch in "‐‑‒–—―−－ー":
        t = t.replace(ch, "-")
    # 全角数字を半角に
    t = t.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if t in _PENDING_TOKENS:
        return None
    if not _RESULT_RE.match(t):
        return None
    return t


def _parse_payout(text: str) -> Optional[int]:
    """払戻金テキストから整数を取り出す。取れなければ None。"""
    if not text:
        return None
    if text.strip() in _PENDING_TOKENS:
        return None
    digits = "".join(_PAYOUT_DIGIT_RE.findall(text.replace(",", "")))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _parse_race_no(row: dict[str, str]) -> Optional[int]:
    """data-race-no 属性 → td.race-no テキスト の順で race_no を確定する。"""
    raw = row.get("race_no_attr") or row.get("race-no") or ""
    raw = raw.strip()
    if not raw:
        return None
    # "1R" や "1番" などから数字だけ抜く
    digits = "".join(_PAYOUT_DIGIT_RE.findall(raw))
    if not digits:
        return None
    try:
        n = int(digits)
    except ValueError:
        return None
    if not 1 <= n <= 12:
        return None
    return n


def parse_results_html(
    html: str,
    *,
    venue: str,
    date_str: str,
    race_no: Optional[int] = None,
) -> list[dict]:
    """結果ページHTMLを RecentResult 互換 dict のリストへ変換する。

    fixture-style（class="race-row" + data-race-no）を試し、見つからなければ
    実 Kドリームス HTML（<span class="num">NR</span> + <table class="order_table">）
    にフォールバックする。
    """
    if html is None or len(html.strip()) < 10:
        raise FetchError("結果HTMLが空または不正です。")

    # --- fixture-style ---
    parser = _ResultsTableParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        parser = None  # type: ignore[assignment]

    if parser and parser.rows:
        out: list[dict] = []
        any_valid_for_fixture = False
        for row in parser.rows:
            rno = _parse_race_no(row)
            if rno is None:
                continue
            any_valid_for_fixture = True
            if race_no is not None and rno != race_no:
                continue
            result = _normalize_result(row.get("result", ""))
            if result is None:
                continue
            payout = _parse_payout(row.get("payout", ""))
            out.append({
                "date": date_str,
                "venue": venue,
                "race_no": rno,
                "result": result,
                "payout": payout,
                "memo": "Kドリームス結果ページから取得",
            })
        # fixture-style で行が見つかれば（フィルタで0件でも）その結果を返す
        if any_valid_for_fixture:
            return out

    # --- 実 Kドリームス-style ---
    real = _parse_real_kdreams_results(
        html, venue=venue, date_str=date_str, race_no=race_no
    )
    if real:
        return real
    if real == []:
        # 取得自体は成功したがフィルタで残らなかった
        return []
    raise FetchError(
        "結果テーブルが見つかりませんでした。サイト構造変更の可能性があります。"
    )


# ---------------------------------------------------------------------------
# 実 Kドリームス用パーサ
# ---------------------------------------------------------------------------


class _RealKdreamsResultsParser(HTMLParser):
    """実 Kドリームス結果ページパーサ。

    HTML構造（観測済み）:
      - 各レースは <p class="race"><span class="num">1R</span>...> 続く
      - 着順テーブル: <table class="order_table">
        - データ行の各 td: <p><span class="num n{N}">N</span><span>選手名</span></p>
        - 先頭3列の n{N} を取って "1着-2着-3着" を構成
      - 払戻テーブル: <table class="refund_table">
        - 3連単（連勝 単）: <dl class="cf"><dt>1-3-7</dt><dd>1,080円<span>(2)</span></dd></dl>
    """

    _RACE_HEADER_RE = re.compile(r"^\s*(\d{1,2})\s*R\b")
    _TR_N_RE = re.compile(r"^n(\d)$")
    _TRI_RE = re.compile(r"^\s*(\d)-(\d)-(\d)\s*$")
    _PAYOUT_AMOUNT_RE = re.compile(r"([\d,]+)円")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_race_no: Optional[int] = None
        self._span_num_buf: Optional[list[str]] = None
        # order_table のセル走査用
        self._in_order_table = 0
        self._order_row_finished: dict[int, list[int]] = {}  # race_no → [1着,2着,3着]
        self._order_cells_so_far: list[int] = []
        self._in_order_td = False
        self._current_td_car: Optional[int] = None
        # refund_table dl 走査用
        self._in_refund = 0
        self._in_dt = False
        self._dt_buf: list[str] = []
        self._in_dd = False
        self._dd_buf: list[str] = []
        self._last_dt = ""
        self._payouts: dict[int, int] = {}  # race_no → 3連単払戻

    def handle_starttag(self, tag, attrs_raw):
        attrs = {k: (v or "") for k, v in attrs_raw}
        cls = set((attrs.get("class") or "").split())

        # レース番号
        if tag == "span" and "num" in cls and not self._in_order_td:
            # td 内の num n{N} ではなく、ヘッダの 1R/2R 用
            self._span_num_buf = []
            return

        # tables
        if tag == "table" and "order_table" in cls:
            self._in_order_table += 1
            self._order_cells_so_far = []
            return
        if tag == "table" and "refund_table" in cls:
            self._in_refund += 1
            return

        # order_table 内のセル
        if self._in_order_table and tag == "td":
            self._in_order_td = True
            self._current_td_car = None
            return
        if self._in_order_table and tag == "span":
            # <span class="num n{N}"> から車番抽出
            if "num" in cls:
                for c in cls:
                    m = self._TR_N_RE.match(c)
                    if m:
                        self._current_td_car = int(m.group(1))
                        break
            return

        # refund_table 内
        if self._in_refund:
            if tag == "dt":
                self._in_dt = True
                self._dt_buf = []
            elif tag == "dd":
                self._in_dd = True
                self._dd_buf = []

    def handle_endtag(self, tag):
        if tag == "span" and self._span_num_buf is not None:
            text = "".join(self._span_num_buf).strip().replace("\n", "")
            m = self._RACE_HEADER_RE.match(text)
            if m:
                self.current_race_no = int(m.group(1))
                self._order_cells_so_far = []
            self._span_num_buf = None
            return

        if self._in_order_table:
            if tag == "td":
                if self._in_order_td and self._current_td_car is not None:
                    self._order_cells_so_far.append(self._current_td_car)
                self._in_order_td = False
                self._current_td_car = None
                return
            if tag == "table":
                # 着順テーブル終了：先頭3つを 1-2-3 着とする
                if self.current_race_no is not None and len(self._order_cells_so_far) >= 3:
                    self._order_row_finished[self.current_race_no] = (
                        self._order_cells_so_far[:3]
                    )
                self._in_order_table = max(0, self._in_order_table - 1)
                self._order_cells_so_far = []
                return

        if self._in_refund:
            if tag == "dt":
                if self._in_dt:
                    self._last_dt = "".join(self._dt_buf).strip()
                    self._in_dt = False
            elif tag == "dd":
                if self._in_dd:
                    text = "".join(self._dd_buf).strip()
                    m_combo = self._TRI_RE.match(self._last_dt)
                    if m_combo and self.current_race_no is not None:
                        m_pay = self._PAYOUT_AMOUNT_RE.search(text)
                        if m_pay:
                            try:
                                amount = int(m_pay.group(1).replace(",", ""))
                                self._payouts[self.current_race_no] = amount
                            except ValueError:
                                pass
                    self._in_dd = False
            elif tag == "table":
                self._in_refund = max(0, self._in_refund - 1)

    def handle_data(self, data):
        if self._span_num_buf is not None:
            self._span_num_buf.append(data)
        if self._in_dt:
            self._dt_buf.append(data)
        if self._in_dd:
            self._dd_buf.append(data)


def _parse_real_kdreams_results(
    html: str,
    *,
    venue: str,
    date_str: str,
    race_no: Optional[int],
) -> Optional[list[dict]]:
    """実 Kドリームス結果ページパーサ。

    成功時はリスト（フィルタで0件も含む）、失敗時は None を返す。
    """
    p = _RealKdreamsResultsParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return None
    if not p._order_row_finished:
        return None

    out: list[dict] = []
    for rno, cars in sorted(p._order_row_finished.items()):
        if race_no is not None and rno != race_no:
            continue
        if len(cars) < 3:
            continue
        result = f"{cars[0]}-{cars[1]}-{cars[2]}"
        out.append({
            "date": date_str,
            "venue": venue,
            "race_no": rno,
            "result": result,
            "payout": p._payouts.get(rno),
            "memo": "Kドリームス結果ページから取得",
        })
    return out
