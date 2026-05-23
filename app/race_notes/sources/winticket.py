"""WINTICKET 用 RaceNotes 取得スタブ。

現フェーズでは未実装。手入力テキストを `parse_race_notes_text(source='winticket')`
で渡してください。
"""

from __future__ import annotations

from app.fetchers.base import NotImplementedSource


def fetch_race_notes(*args, **kwargs):
    raise NotImplementedSource(
        "WINTICKET 自動取得は未実装です。"
        "コメント本文を `parse-race-notes --source winticket --input ファイル` で取り込んでください。"
    )
