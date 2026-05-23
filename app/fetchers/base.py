"""外部データ取得の抽象基底。

重要原則:
- 取得処理と予想処理を密結合させない。Fetcher は構造化データ（dict/モデル）
  だけを返し、生HTMLは内部に閉じ込める。
- 失敗時は FetchError（日本語メッセージ）を投げる。
- 自動投票・購入・サイトログインは一切実装しない。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date as Date
from typing import Any, Optional


# Race input 互換 dict として返す型（簡略化のため Any）
RaceCardData = dict[str, Any]


class FetchError(Exception):
    """外部取得に関連するエラー全般。メッセージは必ず日本語で。"""


class NotImplementedSource(FetchError):
    """まだ未実装のソースが指定されたときに投げるエラー。"""


class Fetcher(ABC):
    """Fetcher の抽象基底。

    各メソッドは構造化データを返す。生HTMLを返してはならない。
    取得処理に失敗した場合は FetchError を投げる。
    """

    #: ソース識別子（例: 'manual', 'kdreams', 'oddspark'）
    source_name: str = "abstract"

    @abstractmethod
    def fetch_race_card(
        self,
        *,
        venue: Optional[str] = None,
        race_no: Optional[int] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> RaceCardData:
        """出走表 + ライン + 当日条件をまとめた RaceInput 互換 dict を返す。

        実装は HTMLパーサや API クライアントを内部に閉じ込め、結果は
        必ず構造化された dict として返すこと。
        """

    @abstractmethod
    def fetch_odds(
        self,
        *,
        venue: Optional[str] = None,
        race_no: Optional[int] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> "list[dict[str, Any]] | dict[str, list[dict[str, Any]]]":
        """オッズ情報を返す。

        戻り値はサブクラスにより異なるが、構造化された dict / list[dict] を返す。
        現状の代表形式:
          - list[dict] — フラットな OddsEntry 互換リスト
          - dict[str, list[dict]] — {"trifecta_popular": [...], ...} のグループ形式
        """

    @abstractmethod
    def fetch_results(
        self,
        *,
        venue: Optional[str] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """直近結果のリストを返す（RecentResult互換のdict）。"""

    @abstractmethod
    def fetch_venue_trend(
        self,
        *,
        venue: Optional[str] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> Optional[dict[str, Any]]:
        """当日の場の傾向（VenueTrend互換のdict）を返す。無ければ None。"""

    def fetch_race_notes(
        self,
        *,
        venue: Optional[str] = None,
        date: Optional[Date] = None,
        race_no: Optional[int] = None,
        url: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """補助情報（記者見解・選手コメント要約等）の構造化 dict を返す。

        既定は未実装 (`NotImplementedSource`)。
        東スポなど補助 Fetcher のみがオーバーライドする。
        """
        raise NotImplementedSource(
            f"{self.source_name} は fetch_race_notes に未対応です。"
        )
