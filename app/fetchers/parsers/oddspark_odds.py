"""オッズパーク競輪オッズページ（人気順表示）の HTML パーサ。

URL パターン (WebFetch で確認済み):
    https://www.oddspark.com/keirin/Odds.do?joCode={jo}&kaisaiBi={YYYYMMDD}
        &raceNo={N}&betType={9|8|6}&viewType=1
    betType: 3連単=9, 3連複=8, 2車単=6
    viewType=1 = 人気順

実HTMLの DOM 構造はパース時に観測した実例に追従する。
当初は試行的に「順位/買い目/オッズ」を持つテーブル行を緩めに拾うヒューリスティック
方式で書き、現実のHTMLを `.cache/keirin/` に取得してから精緻化する。
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Optional

from ..base import FetchError


# Kドリームスパーサと同じ正規化ヘルパを再利用しても良いが、責務分離のため別定義
_DASH_VARIANTS = "‐‑‒–—―−－ー"
_EQ_VARIANTS = "＝"

# 内部の英語キー → Oddspark の betType
BET_TYPE_TO_ODDSPARK: dict[str, int] = {
    "trifecta": 9,
    "trio": 8,
    "exacta": 6,
}


def _norm(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).strip()


_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
_INT_RE = re.compile(r"\d+")


def _parse_odds_value(text: str) -> Optional[float]:
    if not text:
        return None
    t = _norm(text).replace(",", "").replace("倍", "").replace("円", "").replace("¥", "")
    if not t or t in ("-", "—", "−", "*", "−", "−.−", "--"):
        return None
    m = _FLOAT_RE.search(t)
    if not m:
        return None
    try:
        v = float(m.group(0))
    except ValueError:
        return None
    if v <= 0:
        return None
    return v


def _normalize_combination(text: str, *, bet_type: str) -> Optional[str]:
    """買い目文字列を正規化する。3連単/2車単→'-'区切り、3連複→'='区切り。"""
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text)
    for ch in _DASH_VARIANTS:
        t = t.replace(ch, "-")
    for ch in _EQ_VARIANTS:
        t = t.replace(ch, "=")
    t = re.sub(r"\s+", "", t)
    if not t:
        return None
    if bet_type == "trio":
        unified = t.replace("-", "=")
        parts = unified.split("=")
    else:
        unified = t.replace("=", "-")
        parts = unified.split("-")
    nums: list[int] = []
    for p in parts:
        if not p.isdigit():
            return None
        n = int(p)
        if not 1 <= n <= 9:
            return None
        nums.append(n)
    if bet_type in ("trifecta", "trio") and len(nums) != 3:
        return None
    if bet_type == "exacta" and len(nums) != 2:
        return None
    if bet_type == "trio":
        if len(set(nums)) != 3:
            return None
        return "=".join(str(n) for n in nums)
    if len(set(nums)) != len(nums):
        return None
    return ("-" if bet_type != "trio" else "=").join(str(n) for n in nums)


# ---------------------------------------------------------------------------
# 緩いヒューリスティックパーサ
# ---------------------------------------------------------------------------


class _OddsparkOddsParser(HTMLParser):
    """汎用的に <tr>/<td> を走査して、買い目とオッズを含む行を拾う。

    実HTMLが class 属性付きテーブルか、bare table か事前に分からないため、
    行内容（テキスト）から判定する:
      - 1セルに `\\d+-\\d+-\\d+` または `\\d+=\\d+=\\d+` または `\\d+-\\d+` が現れる → 買い目
      - 別セルに小数（オッズ）が現れる → odds
      - 別セルに整数1〜3桁の単独値 → 人気順位（先頭が rank）
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_tr = False
        self._in_td = False
        self._td_buf: list[str] = []
        self._cells: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_tr = True
            self._cells = []
        elif tag in ("td", "th") and self._in_tr:
            self._in_td = True
            self._td_buf = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            if self._in_td:
                self._cells.append(_norm("".join(self._td_buf)))
                self._in_td = False
                self._td_buf = []
        elif tag == "tr":
            if self._in_tr and self._cells:
                self.rows.append(self._cells)
            self._in_tr = False
            self._cells = []

    def handle_data(self, data):
        if self._in_td:
            self._td_buf.append(data)


_COMBO_LIKE_RE = re.compile(r"^\s*\d(?:[\-=]\d){1,2}\s*$")


# ---------------------------------------------------------------------------
# 実 Oddspark 用パーサ
# ---------------------------------------------------------------------------


class _OddsparkRealParser(HTMLParser):
    """実 Oddspark Odds.do ページの買い目テーブルを走査する。

    観測した HTML 構造::

        <h4>人気順</h4>
        <table class="tb50 ...">
          <tr><th>順位</th><th>車番</th><th>オッズ</th></tr>
          <tr>
            <td>1</td>
            <td>
              <ul class="trio">
                <li class="n5">&nbsp;</li>
                <li>→</li>
                <li class="n1">&nbsp;</li>
                <li>→</li>
                <li class="n7">&nbsp;</li>
              </ul>
            </td>
            <td><span class="tx_blue">16.6</span></td>
          </tr>
          ...
        </table>
        ...
        <h4>高オッズ順</h4>
        <table class="tb50 ...">...</table>

    ページには「人気順」「高オッズ順」の2セクションがあるため、
    h4 見出しでセクションを判定し **「人気順」のみ採用** する。
    """

    _TABLE_CLASS_RE = re.compile(r"(?:^|\s)tb50(?:\s|$)")
    _N_CLASS_RE = re.compile(r"^n(\d)$")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_table = 0
        self._in_tr = False
        self._tr_text: list[str] = []
        self._tr_cars: list[int] = []
        # h4 セクション
        self._in_h4 = False
        self._h4_buf: list[str] = []
        # True なら現在 popular セクション、False なら他（高オッズ順など）
        self._in_popular_section = True  # 最初の h4 が来るまでは popular とみなす
        self.rows: list[dict[str, Any]] = []

    def handle_starttag(self, tag, attrs):
        attrs_d = {k: (v or "") for k, v in attrs}
        cls = attrs_d.get("class") or ""

        if tag == "h4":
            self._in_h4 = True
            self._h4_buf = []
            return
        if tag == "table" and self._TABLE_CLASS_RE.search(cls):
            self._in_table += 1
            return
        if not self._in_table:
            return
        if tag == "tr":
            self._in_tr = True
            self._tr_text = []
            self._tr_cars = []
            return
        if tag == "li" and self._in_tr:
            for c in cls.split():
                m = self._N_CLASS_RE.match(c)
                if m:
                    n = int(m.group(1))
                    if 1 <= n <= 9:
                        self._tr_cars.append(n)
                    break

    def handle_endtag(self, tag):
        if tag == "h4":
            if self._in_h4:
                head = "".join(self._h4_buf).strip()
                # 「人気順」を含み「高オッズ」を含まないなら popular
                if "高オッズ" in head:
                    self._in_popular_section = False
                elif "人気" in head:
                    self._in_popular_section = True
                self._in_h4 = False
                self._h4_buf = []
            return
        if tag == "table":
            self._in_table = max(0, self._in_table - 1)
            return
        if not self._in_table:
            return
        if tag == "tr" and self._in_tr:
            if self._in_popular_section:
                self.rows.append({
                    "text": "".join(self._tr_text),
                    "cars": list(self._tr_cars),
                })
            self._in_tr = False
            self._tr_text = []
            self._tr_cars = []

    def handle_data(self, data):
        if self._in_h4:
            self._h4_buf.append(data)
        if self._in_tr:
            self._tr_text.append(data)


_TEXT_FLOAT_RE = re.compile(r"\d+(?:\.\d+)?")


def _parse_oddspark_real(
    html: str, *, bet_type: str, limit: int
) -> Optional[list[dict[str, Any]]]:
    """実 Oddspark HTML の <ul class="trio">/<li class="n{N}"> 形式をパース。

    成功時は dict のリスト、買い目検出失敗時は None を返す。
    """
    p = _OddsparkRealParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return None
    if not p.rows:
        return None

    expected_cars = 2 if bet_type == "exacta" else 3
    out: list[dict[str, Any]] = []
    for row in p.rows:
        cars = row["cars"]
        if len(cars) != expected_cars:
            continue
        if len(set(cars)) != len(cars):
            continue
        text = row["text"]
        floats = _TEXT_FLOAT_RE.findall(text)
        if not floats:
            continue
        # 1番目（整数）が rank、最後の小数（オッズ）が odds
        rank: Optional[int] = None
        odds: Optional[float] = None
        for tok in floats:
            if "." in tok:
                try:
                    odds = float(tok)
                except ValueError:
                    pass
            else:
                if rank is None:
                    try:
                        rank = int(tok)
                    except ValueError:
                        pass
        if rank is None or odds is None or odds <= 0:
            continue

        if bet_type == "trio":
            # 3連複は順序無視。昇順に揃えて重複防止
            sorted_cars = sorted(cars)
            combo = "=".join(str(c) for c in sorted_cars)
        else:
            combo = "-".join(str(c) for c in cars)
        out.append({"rank": rank, "combination": combo, "odds": odds})

    if not out:
        return None
    out.sort(key=lambda r: r["rank"])
    if limit > 0:
        out = out[:limit]
    return out


def parse_oddspark_odds_html(
    html: str,
    *,
    bet_type: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """オッズパーク人気順ページから rank/combination/odds のリストを返す。

    実 Oddspark HTML 用パーサで試し、ダメなら fixture-style の緩いヒューリスティックに
    フォールバックする。

    Args:
        html: ページHTML本文
        bet_type: 'trifecta' / 'trio' / 'exacta'
        limit: 上位件数

    Raises:
        FetchError: HTMLが空、または有効な行が0件
    """
    if bet_type not in ("trifecta", "trio", "exacta"):
        raise FetchError(
            f"未対応のオッズ種別: '{bet_type}'。trifecta/trio/exacta のみ。"
        )
    if html is None or len(html.strip()) < 10:
        raise FetchError("オッズパークのHTMLが空または不正です。")

    # 認証要求ページや障害ページの検出
    if (
        "ログイン" in html
        and "オッズ" not in html[:5000]
    ):
        raise FetchError(
            "オッズパークのオッズページがログイン要求になっています。"
            "ブラウザでアクセスできるか、別のレース番号で試してください。"
        )

    # --- 実 Oddspark-style ---
    real = _parse_oddspark_real(html, bet_type=bet_type, limit=limit)
    if real:
        return real

    # --- fixture-style（ヒューリスティック） ---
    p = _OddsparkOddsParser()
    try:
        p.feed(html)
        p.close()
    except Exception as e:
        raise FetchError(
            f"オッズパークHTMLのパースに失敗しました: {type(e).__name__}: {e}"
        ) from e

    candidates: list[dict[str, Any]] = []
    for cells in p.rows:
        combo: Optional[str] = None
        odds: Optional[float] = None
        rank: Optional[int] = None
        for c in cells:
            if combo is None and _COMBO_LIKE_RE.match(c):
                combo = _normalize_combination(c, bet_type=bet_type)
                if combo:
                    continue
            if odds is None:
                v = _parse_odds_value(c)
                if v is not None and v >= 1.0 and v < 1_000_000:
                    if "." in c or v >= 100 or (combo is not None and rank is not None):
                        odds = v
                        continue
        if combo is None or odds is None:
            continue
        for c in cells[:2]:
            t = _norm(c)
            if t.isdigit():
                n = int(t)
                if 1 <= n <= 300:
                    rank = n
                    break
        candidates.append({
            "rank": rank if rank is not None else len(candidates) + 1,
            "combination": combo,
            "odds": odds,
        })

    if not candidates:
        raise FetchError(
            "オッズパークページから買い目/オッズを検出できませんでした。"
            "サイト構造変更、または対象レースのオッズ未公開の可能性があります。"
        )

    candidates.sort(key=lambda r: r["rank"])
    if limit > 0:
        candidates = candidates[:limit]
    for i, c in enumerate(candidates, start=1):
        c["rank"] = i
    return candidates
