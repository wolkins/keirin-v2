"""東スポ競輪 Fetcher（補助データ専用・試験実装）。

東スポは予想記事や記者見解を持つ補助情報源で、本システムでは:
- 主データ（出走表/結果/オッズ）: Kドリームス / オッズパーク
- 補助データ（コメント・記者見解・signals）: 東スポ

の二層構成で扱う。

URL構造が公開APIではないため安定しない可能性があるので、現フェーズでは
**URL直接指定** (`--url` 引数経由) を必須とする。
将来的にURLパターンが安定したら `build_tospo_race_url` を実装する。

著作権配慮:
- 取得した HTML は parser 内に閉じ込め、構造化 dict のみ返す
- raw_excerpt は最大50文字（parser側で truncate）
- 全文転載しない。短い要約と signals に変換して保存

遵守事項:
- GET only / ログインしない
- HttpClient/FileCache/RateLimiter 共有
- User-Agent 明示
- 過剰アクセスしない
"""

from __future__ import annotations

from datetime import date as Date
from typing import Any, Optional

from .base import Fetcher, FetchError, NotImplementedSource, RaceCardData
from .http import HttpClient
from .parsers.tospo_notes import parse_tospo_race_notes_html


def build_tospo_race_url(
    venue: Optional[str] = None,
    date: Optional[Date] = None,
    race_no: Optional[int] = None,
) -> str:
    """東スポ予想ページURLを組み立てる（現フェーズでは未実装）。

    URL構造が公開APIでないため、現在は呼び出すと FetchError。
    `fetch_race_notes(..., url='https://...')` で直接URLを渡してください。
    """
    raise FetchError(
        "東スポURLの自動生成は未対応です。"
        "ブラウザで該当ページを開き、`--tospo-url` または "
        "`fetch_race_notes(url=...)` でURLを直接指定してください。"
    )


class TospoFetcher(Fetcher):
    """東スポ用 Fetcher。fetch_race_notes のみ実装。

    fetch_race_card / fetch_odds / fetch_results / fetch_venue_trend は
    全て `NotImplementedSource` を返す。
    """

    source_name = "tospo"

    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http_client = http_client

    def _not_impl(self, method: str) -> NotImplementedSource:
        return NotImplementedSource(
            f"東スポ連携の{method}は未対応です（補助データ専用）。"
            "出走表・結果・オッズは Kドリームス/オッズパーク を使ってください。"
        )

    def fetch_race_card(self, **kwargs: Any) -> RaceCardData:
        raise self._not_impl("fetch_race_card")

    def fetch_odds(self, **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
        raise self._not_impl("fetch_odds")

    def fetch_results(self, **kwargs: Any) -> list[dict[str, Any]]:
        raise self._not_impl("fetch_results")

    def fetch_venue_trend(self, **kwargs: Any) -> Optional[dict[str, Any]]:
        raise self._not_impl("fetch_venue_trend")

    def fetch_race_notes(
        self,
        *,
        venue: Optional[str] = None,
        date: Optional[Date] = None,
        race_no: Optional[int] = None,
        url: Optional[str] = None,
        include_raw_excerpt: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """東スポ予想ページから補助情報の dict を返す。

        Args:
            url: 必須（このフェーズでは自動URL生成は未実装）
            venue/date/race_no: 結果 dict に同梱する識別情報
            include_raw_excerpt: True なら短い引用を含める（既定 False）
        """
        if self.http_client is None:
            raise FetchError(
                "HttpClient が未設定です。東スポ取得には HttpClient を渡してください。"
            )
        if not url:
            raise FetchError(
                "東スポURLが指定されていません。--tospo-url で予想ページURLを直接指定してください。"
            )
        html = self.http_client.get(url)
        date_str: Optional[str] = None
        if date is not None:
            date_str = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)
        return parse_tospo_race_notes_html(
            html,
            venue=venue,
            date=date_str,
            race_no=race_no,
            include_raw_excerpt=include_raw_excerpt,
        )
