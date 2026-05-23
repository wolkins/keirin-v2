"""共通 RaceNotes パッケージ。

複数の補助情報源（東スポ/WINTICKET/netkeirin/オッズパーク/yenjoy/手入力）
から得られるコメント・記者見解・並び予想ヒントを、共通の RaceNotes 構造に
正規化するためのパッケージ。

責務:
- signals 辞書を一元管理（app/race_notes/signals.py）
- 手入力テキストのパース（app/race_notes/manual_text.py）
- 既存 dict (Tospo パーサ等) を RaceNotes Pydantic モデルに変換
- ソース別 fetcher スタブ（app/race_notes/sources/）

著作権配慮:
- raw 本文は保存しない
- raw_excerpt は最大50文字
- LLM には要約・signals のみ渡す（生本文は流さない）
- 全サイトの完全 fetcher 実装は段階的に追加。最優先は manual_text。
"""

from .signals import (
    KNOWN_SIGNALS,
    SIGNAL_KEYWORDS,
    extract_signals,
)
from .converters import dict_to_race_notes, race_notes_to_dict
from .manual_text import (
    parse_race_notes_text,
    ManualTextParseError,
)

__all__ = [
    "KNOWN_SIGNALS",
    "SIGNAL_KEYWORDS",
    "extract_signals",
    "dict_to_race_notes",
    "race_notes_to_dict",
    "parse_race_notes_text",
    "ManualTextParseError",
]
