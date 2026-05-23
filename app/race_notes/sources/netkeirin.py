"""netkeirin 用 RaceNotes 取得スタブ。

現フェーズでは未実装。
"""

from __future__ import annotations

from app.fetchers.base import NotImplementedSource


def fetch_race_notes(*args, **kwargs):
    raise NotImplementedSource(
        "netkeirin 自動取得は未実装です。"
        "コメント本文を `parse-race-notes --source netkeirin --input ファイル` で取り込んでください。"
    )
