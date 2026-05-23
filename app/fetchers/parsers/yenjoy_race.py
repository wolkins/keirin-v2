"""yen-joy (https://www.yen-joy.net/) のレース予想ページ用パーサ。

URL: /kaisai/race/forecast/detail/{YYYYMM}/{jo:02d}/{初日YYYYMMDD}/{当日YYYYMMDD}/{R}

このページはログイン不要で、競走得点（4ヶ月得点）が選手ごとに表示されている。
出走表は 9車分の数値が **列ごと** に横並びになっている形式:

    脚力 87 84 84 82 86 84 85 86 86    （9車分の脚力指数）
    ４ヶ月得点 109.71 106.31 106.36 104.58 ...  （9車分の競走得点）

このパーサは「4ヶ月得点」ラベル直後の9個の小数を車番順 (1〜9) の選手得点として取り出す。
B数・決まり手の数値はこのページには無いため、score のみ補完できる。
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Optional


def _html_to_text(html: str) -> str:
    """HTML からタグを除去してテキスト化（連続空白圧縮）。"""
    # HTMLParser で HTML entity デコード + テキスト抽出
    class _Extract(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

        def handle_starttag(self, tag: str, attrs: list) -> None:
            # ブロック要素・改行系は空白で区切る
            if tag in ("br", "tr", "p", "div", "td", "th", "li"):
                self.parts.append(" ")

    p = _Extract()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = unicodedata.normalize("NFKC", "".join(p.parts))
    return re.sub(r"\s+", " ", text)


_FOUR_MONTH_LABEL_RE = re.compile(r"(4|４)\s*ヶ月得点|4ヶ月得点")
_DECIMAL_RE = re.compile(r"\d{2,3}\.\d{1,3}")

# 「戦法 上がりタイム秒」のパターン
# 順序重要: 2文字の合成形（追捲/逃捲/捲逃/追込）を先に並べる
_STRATEGY_PATTERNS = ("追捲", "逃捲", "捲逃", "差捲", "追込", "捲追", "逃込",
                      "自在", "両", "追", "逃", "捲", "差", "マ")
_STRATEGY_RE = re.compile(
    r"(" + "|".join(_STRATEGY_PATTERNS) + r")\s+(\d{1,2}\.\d{1,2})\s*秒"
)


# 戦法ラベル → (nige, makuri, sashi, mark, b_count) の推定値
#
# 設計方針:
# - 専一型（逃/捲/差/追/マ）は対応する決まり手を大きく（7〜8）
# - 合成型（追捲/逃捲/捲逃）は2成分に振り分け
# - 自在/両は全体的に小さく
# - b_count は逃げ成分に比例（先行＝バック取り）
_STRATEGY_STAT_MAP: dict[str, tuple[int, int, int, int, int]] = {
    # (nige, makuri, sashi, mark, b_count)
    "逃":   (8, 2, 0, 0, 4),
    "捲":   (2, 8, 0, 0, 2),
    "差":   (0, 0, 7, 2, 0),
    "追":   (0, 0, 2, 7, 0),
    "マ":   (0, 0, 2, 7, 0),
    "自在": (2, 3, 2, 2, 1),
    "両":   (2, 3, 2, 2, 1),
    "逃捲": (5, 5, 0, 0, 3),
    "捲逃": (4, 6, 0, 0, 2),
    "捲追": (1, 5, 2, 2, 1),
    "追捲": (1, 4, 2, 4, 1),
    "追込": (0, 0, 3, 7, 0),
    "差捲": (1, 4, 4, 1, 1),
    "逃込": (3, 2, 2, 3, 1),
}


def infer_stats_from_strategy(label: str) -> Optional[tuple[int, int, int, int, int]]:
    """戦法ラベル（追捲/自在/逃 等）から (nige, makuri, sashi, mark, b_count) の推定値を返す。

    Returns:
        マッピング有り: 推定値タプル
        マッピング無し: None
    """
    return _STRATEGY_STAT_MAP.get(label)


def parse_yenjoy_strategies(html: str) -> list[Optional[str]]:
    """yen-joy HTML から戦法ラベルを抽出。

    yen-joy では戦法と上がりタイムが「追捲 10.8 秒」のように並び、
    各レース 9車分（または7車分）が **車番 9→1 の逆順** で記載される。

    Returns:
        車番順（1〜N）の戦法ラベル。N未満の場合や未取得は None。
    """
    if not html:
        return []
    text = _html_to_text(html)
    raw = []
    for m in _STRATEGY_RE.finditer(text):
        raw.append(m.group(1))
    if not raw:
        return []
    # yen-joy は車番 9→1 の逆順表示なので反転して 1→N にする
    in_car_order = list(reversed(raw))
    return in_car_order


def parse_yenjoy_race_html(html: str) -> list[Optional[float]]:
    """yen-joy HTML から車番順の競走得点を返す。

    Returns:
        車番 1〜9 (race の場合 7車 or 9車) の score リスト。
        取れなかった選手は None。

        例: [109.71, 106.31, 106.36, 104.58, 107.59, 106.33, 109.23]
    """
    if not html:
        return []
    text = _html_to_text(html)

    # 「4ヶ月得点」ラベルを探し、直後の小数9個を取り出す
    label_match = _FOUR_MONTH_LABEL_RE.search(text)
    if not label_match:
        # ラベルが無い場合は、ページの先頭 50000 文字以内で最初の9連続小数を探す
        candidates = _DECIMAL_RE.findall(text[:50000])
        # 競走得点と思われる範囲 (50.0〜130.0) でフィルタ
        plausible = [float(c) for c in candidates if 50.0 <= float(c) <= 130.0]
        return plausible[:9] if len(plausible) >= 7 else []

    # ラベルの後ろ 2000 文字以内で最初の連続小数列を探す
    after = text[label_match.end():label_match.end() + 2000]
    candidates: list[float] = []
    for m in _DECIMAL_RE.finditer(after):
        try:
            v = float(m.group(0))
            if 50.0 <= v <= 130.0:
                candidates.append(v)
                if len(candidates) >= 9:
                    break
        except ValueError:
            continue
    return candidates


def merge_yenjoy_scores_into_riders(
    riders: list[dict],
    scores_by_car_index: list[Optional[float]],
    strategies_by_car_index: Optional[list[Optional[str]]] = None,
) -> int:
    """yen-joy で取れた競走得点 + 戦法ラベル推定を riders に適用（破壊的）。

    車番順（1〜9）で順に対応付ける。

    - score: yen-joy の 4ヶ月得点（実数値）
    - strategies: yen-joy の戦法ラベル（追捲/自在/逃捲 等）から
      決まり手 (nige/makuri/sashi/mark) と B数を **推定値で補完**
      （実数値ではない点に注意）

    Returns:
        補完した選手数
    """
    if not scores_by_car_index and not strategies_by_car_index:
        return 0
    sorted_riders = sorted(riders, key=lambda r: int(r.get("car_no") or 99))
    matched = 0
    strategies = strategies_by_car_index or []
    for i, rider in enumerate(sorted_riders):
        applied = False
        # score 補完
        if i < len(scores_by_car_index):
            sc = scores_by_car_index[i]
            if sc is not None and sc > 0:
                rider["score"] = float(sc)
                applied = True
        # 戦法ラベル → 決まり手推定（既存値が 0 のときだけ補完）
        if i < len(strategies):
            label = strategies[i]
            if label:
                inferred = infer_stats_from_strategy(label)
                if inferred is not None:
                    n, mk, sh, mr, b = inferred
                    # 既存値があれば尊重（手入力 or 他ソースの実数値を上書きしない）
                    if rider.get("nige", 0) == 0:
                        rider["nige"] = n
                    if rider.get("makuri", 0) == 0:
                        rider["makuri"] = mk
                    if rider.get("sashi", 0) == 0:
                        rider["sashi"] = sh
                    if rider.get("mark", 0) == 0:
                        rider["mark"] = mr
                    if rider.get("b_count", 0) == 0:
                        rider["b_count"] = b
                    # style_tags に戦法ラベルを追加（重複避ける）
                    tags = rider.setdefault("style_tags", [])
                    if label not in tags:
                        tags.append(label)
                    applied = True
        if applied:
            rider["stats_missing"] = False
            matched += 1
    return matched
