"""日本の競輪場のバンク情報マッピング。

データソース: Wikipedia「競輪場」（2025年5月時点の現存43場）
各場の周長は固定情報。バンク特性（差し有利/先行有利）は時期で変わるためベース値のみ。

ユーザーが `prepare-json --bank-length` / `--bank-style` を明示した場合は
そちらが優先される。未登録の場名は `None` を返す。
"""

from __future__ import annotations

from typing import Any, Optional


# bank_length: 周長 (m)
# bank_style: 任意。"差し有利" / "先行有利" / "中立" / None
# - 「500mは差し有利」は一般論として広く語られる傾向で、ベース値として記載
# - 確信が無い場合は None
_BANK_DB: dict[str, dict[str, Any]] = {
    # --- 250m バンク（屋内木製） ---
    "千葉": {"bank_length": 250, "bank_style": None},  # TIPSTAR DOME CHIBA

    # --- 333m バンク ---
    "松戸": {"bank_length": 333, "bank_style": None},
    "小田原": {"bank_length": 333, "bank_style": None},
    "伊東": {"bank_length": 333, "bank_style": None},        # 伊東温泉
    "伊東温泉": {"bank_length": 333, "bank_style": None},    # 別表記
    "富山": {"bank_length": 333, "bank_style": None},
    "奈良": {"bank_length": 333, "bank_style": None},
    "防府": {"bank_length": 333, "bank_style": None},

    # --- 335m バンク（前橋ドーム） ---
    "前橋": {"bank_length": 335, "bank_style": None},  # ヤマダグリーンドーム前橋（屋内）

    # --- 400m バンク（最多） ---
    "函館": {"bank_length": 400, "bank_style": None},
    "青森": {"bank_length": 400, "bank_style": None},
    "いわき平": {"bank_length": 400, "bank_style": None},
    "弥彦": {"bank_length": 400, "bank_style": None},
    "取手": {"bank_length": 400, "bank_style": None},
    "西武園": {"bank_length": 400, "bank_style": None},
    "京王閣": {"bank_length": 400, "bank_style": None},
    "立川": {"bank_length": 400, "bank_style": None},
    "川崎": {"bank_length": 400, "bank_style": None},
    "平塚": {"bank_length": 400, "bank_style": None},
    "静岡": {"bank_length": 400, "bank_style": None},
    "豊橋": {"bank_length": 400, "bank_style": None},
    "名古屋": {"bank_length": 400, "bank_style": None},
    "岐阜": {"bank_length": 400, "bank_style": None},
    "大垣": {"bank_length": 400, "bank_style": None},
    "四日市": {"bank_length": 400, "bank_style": None},
    "松阪": {"bank_length": 400, "bank_style": None},
    "福井": {"bank_length": 400, "bank_style": None},
    "京都向日町": {"bank_length": 400, "bank_style": None},
    "向日町": {"bank_length": 400, "bank_style": None},        # 別表記
    "岸和田": {"bank_length": 400, "bank_style": None},
    "和歌山": {"bank_length": 400, "bank_style": None},
    "玉野": {"bank_length": 400, "bank_style": None},
    "広島": {"bank_length": 400, "bank_style": None},
    "高松": {"bank_length": 400, "bank_style": None},
    "小松島": {"bank_length": 400, "bank_style": None},
    "松山": {"bank_length": 400, "bank_style": None},
    "小倉": {"bank_length": 400, "bank_style": None},  # 北九州メディアドーム（屋内）
    "久留米": {"bank_length": 400, "bank_style": None},
    "武雄": {"bank_length": 400, "bank_style": None},
    "佐世保": {"bank_length": 400, "bank_style": None},
    "別府": {"bank_length": 400, "bank_style": None},
    "熊本": {"bank_length": 400, "bank_style": None},

    # --- 500m バンク（現存3場）---
    # 500mバンクは「差し有利」とよく語られるが時期で変動するため弱めに記載
    "大宮": {"bank_length": 500, "bank_style": "差し有利"},
    "宇都宮": {"bank_length": 500, "bank_style": "差し有利"},
    "高知": {"bank_length": 500, "bank_style": "差し有利"},
}


def get_bank_info(venue: Optional[str]) -> Optional[dict[str, Any]]:
    """場名からバンク情報を取得する。未登録なら None。"""
    if not venue:
        return None
    info = _BANK_DB.get(venue.strip())
    if info is None:
        return None
    return dict(info)  # 防御コピー


def list_known_venues() -> list[str]:
    """マッピング登録済み場名の一覧（テスト・デバッグ用）。

    別表記（伊東/伊東温泉、向日町/京都向日町）も含むため
    実競輪場の数(43)より少し多い。
    """
    return sorted(_BANK_DB.keys())


# 主表記の正規化マップ（別表記 → 主表記）。
# UI セレクトボックス等では主表記の43場だけ提示する。
_ALIAS_TO_CANONICAL: dict[str, str] = {
    "伊東温泉": "伊東",
    "向日町": "京都向日町",
}


def canonical_venue(venue: Optional[str]) -> Optional[str]:
    """別表記を主表記に正規化する。未登録の場名はそのまま返す。"""
    if not venue:
        return venue
    v = venue.strip()
    return _ALIAS_TO_CANONICAL.get(v, v)


# 地域順（北→南）で並んだ主表記43場の一覧。
# Wikipedia「競輪場」の地域順に従う（UI セレクトボックスで自然に並ぶ）。
_CANONICAL_VENUES_ORDERED: list[str] = [
    # 北海道・東北
    "函館", "青森", "いわき平",
    # 北関東
    "弥彦", "前橋", "取手", "宇都宮", "大宮", "西武園",
    # 南関東
    "京王閣", "立川", "松戸", "千葉", "川崎", "平塚", "小田原", "伊東",
    # 中部
    "静岡", "豊橋", "名古屋", "岐阜", "大垣", "四日市", "松阪", "富山", "福井",
    # 関西
    "京都向日町", "奈良", "和歌山", "岸和田",
    # 中国・四国
    "玉野", "広島", "防府", "高松", "小松島", "松山", "高知",
    # 九州
    "小倉", "久留米", "武雄", "佐世保", "別府", "熊本",
]


def list_canonical_venues() -> list[str]:
    """主表記の43場を地域順（北→南）で返す。UIセレクトボックス用。"""
    return list(_CANONICAL_VENUES_ORDERED)
