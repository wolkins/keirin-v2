"""共通 signals 辞書。

仕様で挙げられた 19種類の signals をキーワード辞書で表現。
複数の情報源（東スポ/WINTICKET/netkeirin/オッズパーク/yenjoy/手入力）から
同じ語彙で signals を抽出できるようにする。
"""

from __future__ import annotations

from typing import Iterable


# 仕様の signals 一覧（19種類）
KNOWN_SIGNALS: tuple[str, ...] = (
    "自力", "前々", "単騎", "自在", "番手", "3番手",
    "地元", "状態良い", "疲れ", "不安", "落車明け",
    "穴評価", "本命評価",
    "差し有力", "先行有力", "位置取り良い",
    "コメント強気", "コメント弱気",
    # 追加（東スポ実HTML向け）
    "追込", "状態普通", "重い",
)


# signal → 検出キーワードのリスト
SIGNAL_KEYWORDS: dict[str, list[str]] = {
    "自力": ["自力", "自力勢", "ライン先頭"],
    "前々": ["前々", "前受け", "早めに動く", "先行"],
    "単騎": ["単騎"],
    "自在": ["自在"],
    "番手": ["番手", "マーク"],
    "3番手": ["3番手", "三番手"],
    "地元": ["地元", "ホーム", "ホームバンク"],
    "状態良い": [
        "状態は良い", "状態良い", "好調", "上々", "良好",
        "状態◎", "状態○",
    ],
    "疲れ": ["疲れ", "疲労", "連戦"],
    "不安": ["不安", "心配", "厳しい"],
    "落車明け": ["落車明け", "落車後", "復帰戦"],
    "穴評価": ["穴", "穴狙い", "波乱", "妙味"],
    "本命評価": ["本命", "中心", "軸"],
    "差し有力": ["差し有力", "差し有利", "差し脚良好", "差し決まる"],
    "先行有力": ["先行有力", "先行有利", "前残り"],
    "位置取り良い": ["位置取りが良い", "位置取り良い", "位置良"],
    "コメント強気": ["強気", "自信", "勝ちに行く", "上向き"],
    "コメント弱気": ["弱気", "厳しい", "様子見", "難しい"],
    # 追加
    "追込": ["追込", "追い込み", "追走"],
    "状態普通": ["状態普通", "ふつう", "並"],
    "重い": ["重い", "重そう", "重め"],
}


def extract_signals(text: str) -> list[str]:
    """テキストから signals を抽出する。

    重複は除去し、KNOWN_SIGNALS の順番で並べる（決定論的）。
    """
    if not text:
        return []
    found: set[str] = set()
    for label, keywords in SIGNAL_KEYWORDS.items():
        if any(k in text for k in keywords):
            found.add(label)
    # KNOWN_SIGNALS の順番で並び替え（決定論的）
    return [s for s in KNOWN_SIGNALS if s in found]


def filter_known_signals(signals: Iterable[str]) -> list[str]:
    """入力 signals のうち、KNOWN_SIGNALS に含まれるものだけを返す。重複除去・順序保持。"""
    seen: set[str] = set()
    out: list[str] = []
    known = set(KNOWN_SIGNALS)
    for s in signals:
        if s in known and s not in seen:
            seen.add(s)
            out.append(s)
    return out
