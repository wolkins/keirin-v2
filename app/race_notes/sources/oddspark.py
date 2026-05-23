"""オッズパーク予想記事用 RaceNotes 取得スタブ。

オッズパークはオッズ取得は別途実装済み（app/fetchers/oddspark.py）。
予想記事のコメント取得はまだ未実装。
"""

from __future__ import annotations

from app.fetchers.base import NotImplementedSource


def fetch_race_notes(*args, **kwargs):
    raise NotImplementedSource(
        "オッズパーク予想記事の自動取得は未実装です。"
        "コメント本文を `parse-race-notes --source oddspark --input ファイル` で取り込んでください。"
    )
