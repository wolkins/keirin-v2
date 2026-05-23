"""Kドリームスのオッズページ HTMLパーサ（試験実装）。

対象: 3連単 / 3連複 / 2車単 の **人気上位N件**。
全オッズの完全取得は目的外。ガミ回避・本線/穴の評価・人気偏りの確認用。

期待するHTML構造（fixture参照）::

    <table class="odds-table" data-bet-type="trifecta">
      <tr class="odds-row" data-rank="1">
        <td class="rank">1</td>
        <td class="combination">5-1-3</td>
        <td class="odds">8.5</td>
      </tr>
      ...
    </table>

サイト構造変更時はこのパーサだけ差し替える。
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Optional

from ..base import FetchError


# 内部用の英語キー
BET_TYPES = ("trifecta", "trio", "exacta")

# 英語キー → 日本語ラベル（OddsEntry.bet_type と一致させる）
BET_TYPE_LABEL = {
    "trifecta": "3連単",
    "trio": "3連複",
    "exacta": "2車単",
}

# 各種ダッシュとイコール
_DASH_VARIANTS = "‐‑‒–—―−－ー"
_EQ_VARIANTS = "＝"

# 未確定・空欄
_PENDING_TOKENS = ("-", "—", "−", "未確定", "発走前", "中止", "TBD", "")


def _classes(attrs: dict[str, str]) -> set[str]:
    cls = attrs.get("class") or ""
    return set(cls.split())


def _norm_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).strip()


def _normalize_combination(text: str, *, bet_type: str) -> Optional[str]:
    """combination 文字列を統一形式に正規化する。

    3連単/2車単 → '-' 区切り
    3連複       → '=' 区切り
    数値以外の組合せや欠損は None。
    """
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text)
    for ch in _DASH_VARIANTS:
        t = t.replace(ch, "-")
    for ch in _EQ_VARIANTS:
        t = t.replace(ch, "=")
    # 空白除去
    t = re.sub(r"\s+", "", t)
    if not t or t in _PENDING_TOKENS:
        return None

    sep_internal = "=" if bet_type == "trio" else "-"
    # 区切りを統一: 一度両方を一旦パイプ化してから sep_internal に揃える
    parts: list[str]
    if bet_type == "trio":
        # 3連複は = 区切りが正。- も = として扱う
        unified = t.replace("-", "=")
        parts = unified.split("=")
    else:
        # 3連単/2車単は - 区切り
        unified = t.replace("=", "-")
        parts = unified.split("-")

    # 各要素は1〜9の整数のはず
    nums: list[int] = []
    for p in parts:
        p = p.strip()
        if not p or not p.isdigit():
            return None
        n = int(p)
        if not 1 <= n <= 9:
            return None
        nums.append(n)

    if bet_type == "trifecta" and len(nums) != 3:
        return None
    if bet_type == "trio" and len(nums) != 3:
        return None
    if bet_type == "exacta" and len(nums) != 2:
        return None

    if bet_type == "trio":
        # 3連複は重複なし
        if len(set(nums)) != 3:
            return None
        return "=".join(str(n) for n in nums)
    else:
        # 同じ車番が重複していたら無効
        if len(set(nums)) != len(nums):
            return None
        return sep_internal.join(str(n) for n in nums)


_ODDS_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _normalize_odds(text: str) -> Optional[float]:
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text)
    t = t.replace(",", "").replace("倍", "").replace("円", "").replace("¥", "").strip()
    if not t or t in _PENDING_TOKENS:
        return None
    m = _ODDS_FLOAT_RE.search(t)
    if not m:
        return None
    try:
        v = float(m.group(0))
    except ValueError:
        return None
    if v <= 0:
        return None
    return v


def _parse_rank(text: str, attr: Optional[str]) -> Optional[int]:
    for src in (attr, text):
        if not src:
            continue
        t = _norm_text(src)
        m = _ODDS_FLOAT_RE.search(t)
        if not m:
            continue
        try:
            return int(float(m.group(0)))
        except ValueError:
            continue
    return None


class _OddsTableParser(HTMLParser):
    """odds-table 内の odds-row を集めるパーサ。"""

    _FIELDS = ("rank", "combination", "odds")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_table = 0
        self._row: Optional[dict[str, str]] = None
        self._field: Optional[str] = None
        self._buf: list[str] = []
        self._td_depth = 0
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs_raw: list[tuple[str, Optional[str]]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_raw}
        cls = _classes(attrs)
        if tag == "table" and "odds-table" in cls:
            self._in_table += 1
            return
        if not self._in_table:
            return
        if tag == "tr" and "odds-row" in cls:
            self._row = {"_data_rank": attrs.get("data-rank", "")}
            return
        if tag == "td" and self._row is not None:
            self._td_depth += 1
            for f in self._FIELDS:
                if f in cls:
                    self._field = f
                    self._buf = []
                    return

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag == "td":
            if self._field and self._td_depth > 0 and self._row is not None:
                self._row[self._field] = "".join(self._buf).strip()
                self._field = None
                self._buf = []
            self._td_depth = max(0, self._td_depth - 1)
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._field = None
            self._buf = []
            self._td_depth = 0
        elif tag == "table":
            self._in_table = max(0, self._in_table - 1)

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._buf.append(data)


def parse_odds_html(
    html: str,
    *,
    bet_type: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """オッズHTMLから人気上位N件の構造化dictリストを返す。

    Args:
        html: オッズページのHTML本文
        bet_type: 'trifecta' / 'trio' / 'exacta'
        limit: 取得件数上限（rank昇順で先頭N件）

    Returns:
        list[dict]: 各要素は {"rank": int, "combination": str, "odds": float}
        未確定・不正な行はスキップ。

    Raises:
        FetchError: HTMLが空、または odds-table が見つからない場合、bet_type不正
    """
    if bet_type not in BET_TYPES:
        raise FetchError(
            f"未対応のオッズ種別: '{bet_type}'。サポート対象: {', '.join(BET_TYPES)}"
        )
    if html is None or len(html.strip()) < 10:
        raise FetchError("オッズHTMLが空または不正です。")

    p = _OddsTableParser()
    try:
        p.feed(html)
        p.close()
    except Exception as e:
        raise FetchError(
            f"オッズHTMLのパースに失敗しました: {type(e).__name__}: {e}"
        ) from e

    if not p.rows:
        # Kドリームスの racedetail/?pageType=odds は静的HTMLにオッズデータを
        # 含まず、ログイン or JavaScript レンダリングが必要。
        # その場合はエラーHTMLが返ってくるので、ユーザーに具体的な原因を伝える。
        if "SYSTEM_ERROR" in html or "エラーが発生しました" in html:
            raise FetchError(
                "Kドリームスのオッズページは静的HTMLには含まれていません "
                "（ログインまたはJavaScript描画が必要）。"
                "オッズは別途手入力するか、別ソースの取得を検討してください。"
            )
        raise FetchError(
            "オッズテーブルが見つかりませんでした。サイト構造変更の可能性があります。"
        )

    out: list[dict[str, Any]] = []
    for row in p.rows:
        rank = _parse_rank(row.get("rank", ""), row.get("_data_rank"))
        if rank is None:
            continue
        combo = _normalize_combination(row.get("combination", ""), bet_type=bet_type)
        if combo is None:
            continue
        odds = _normalize_odds(row.get("odds", ""))
        if odds is None:
            continue
        out.append({"rank": rank, "combination": combo, "odds": odds})

    # rank 昇順で安定化
    out.sort(key=lambda r: r["rank"])
    if limit > 0:
        out = out[:limit]
    return out
