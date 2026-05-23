"""yen-joy (https://www.yen-joy.net/) からの競走得点補完取得。

このサイトは未ログインで競走得点（4ヶ月得点）が公開されている **情報提供サイト**
（自動投票機能なし）。Kドリームスの /racedetail/ がログイン必須のため、
このサイトを経由して score を補完する。

注意:
- 認証情報・Cookie・POST は使わない。GET のみ
- レート制限・キャッシュは HttpClient 経由で必ず通す
- ブラウザ風 User-Agent を必要とする
- サイト規約を尊重し、過剰アクセスしない
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta
from typing import Any, Optional

from .base import FetchError
from .http import HttpClient
from .kdreams import resolve_jo_code  # jo コード解決を再利用
from .parsers.yenjoy_race import (
    merge_yenjoy_scores_into_riders,
    parse_yenjoy_race_html,
    parse_yenjoy_strategies,
)


# yen-joy はブラウザ風 User-Agent を必要とする（カスタムUAは弾かれる場合がある）
_YENJOY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_YENJOY_HOST = "https://www.yen-joy.net"


def _coerce_date(d: Date | str | None) -> Date:
    if d is None:
        raise FetchError("日付が指定されていません")
    if isinstance(d, Date):
        return d
    try:
        return datetime.strptime(str(d), "%Y-%m-%d").date()
    except ValueError as e:
        raise FetchError(f"日付は YYYY-MM-DD 形式で指定してください: '{d}'") from e


def _build_yenjoy_url_raw(
    venue: str, initial_date: Date, target_date: Date, race_no: int,
) -> str:
    """yen-joy URL を初日/当日を別々に指定して生成。"""
    jo = resolve_jo_code(venue)
    return (
        f"{_YENJOY_HOST}/kaisai/race/forecast/detail/"
        f"{initial_date.strftime('%Y%m')}/{jo:02d}/"
        f"{initial_date.strftime('%Y%m%d')}/{target_date.strftime('%Y%m%d')}/{race_no}"
    )


def build_yenjoy_race_url(
    venue: str,
    date: Date | str,
    race_no: int,
    *,
    session_no: int = 1,
) -> str:
    """yen-joy の出走表ページURL。

    URL 形式:
        /kaisai/race/forecast/detail/{年月}/{場ID:02d}/{初日YYYYMMDD}/{当日YYYYMMDD}/{R}

    Args:
        date: **開催初日の日付**（Kドリームスの kaisai_id と同様）
        session_no: 開催の何日目か（初日=1, 2日目=2, ...）
    """
    jo = resolve_jo_code(venue)
    initial_d = _coerce_date(date)
    target_d = initial_d + timedelta(days=int(session_no) - 1)
    return (
        f"{_YENJOY_HOST}/kaisai/race/forecast/detail/"
        f"{initial_d.strftime('%Y%m')}/{jo:02d}/"
        f"{initial_d.strftime('%Y%m%d')}/{target_d.strftime('%Y%m%d')}/{race_no}"
    )


class YenJoyFetcher:
    """yen-joy からの競走得点補完取得。

    使い方:
        fetcher = YenJoyFetcher(http_client=client)
        fetcher.enrich_scores(payload, venue="武雄", date=..., race_no=7, session_no=3)
    """

    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http_client = http_client

    def fetch_scores(
        self,
        *,
        venue: str,
        date: Date | str,
        race_no: int,
        session_no: int = 1,
    ) -> list[Optional[float]]:
        """yen-joy から車番順の競走得点を取得。

        yen-joy の URL 規則は「初日日付」「当日日付」のペアだが、
        Kドリームスの session_no 体系とサイト間でズレることがある
        （例: Kドリームスは 5/21 初日+3日目、yen-joy は 5/23 初日扱い）。
        そのため複数の URL 候補を試して、最初に成功した方を採用する。

        Returns:
            車番順の score リスト。取得失敗時は空リスト。
        """
        if self.http_client is None:
            return []
        initial = _coerce_date(date)
        # target = 予想したい日（= initial + session_no - 1）
        target = initial + timedelta(days=int(session_no) - 1)
        # 候補1: 予想したい日を yen-joy の初日として使う（最も成功率が高い）
        # 候補2: Kドリームス互換（initial=連戦初日, target=当日）
        candidates = []
        if target != initial:
            candidates.append((target, target))   # /target/target
        candidates.append((initial, target))      # /initial/target

        # yen-joy はブラウザ風UAでないと弾く。UA を一時的に切替
        original_ua = self.http_client.user_agent
        try:
            self.http_client.user_agent = _YENJOY_USER_AGENT
            for init_d, tgt_d in candidates:
                url = _build_yenjoy_url_raw(venue, init_d, tgt_d, race_no)
                try:
                    html = self.http_client.get(url)
                except Exception:
                    continue
                scores = parse_yenjoy_race_html(html)
                if scores:
                    # 後で戦法ラベルも参照できるよう内部に保存
                    self._last_html = html
                    return scores
        finally:
            self.http_client.user_agent = original_ua
        self._last_html = None
        return []

    def fetch_strategies(
        self,
        *,
        venue: str,
        date: Date | str,
        race_no: int,
        session_no: int = 1,
    ) -> list[Optional[str]]:
        """戦法ラベル（追捲/自在/逃捲 等）を車番順で取得。

        通常 fetch_scores 直後に呼ぶと内部キャッシュを再利用するため軽い。
        """
        if getattr(self, "_last_html", None):
            return parse_yenjoy_strategies(self._last_html)
        # キャッシュ無しなら再取得
        if self.http_client is None:
            return []
        initial = _coerce_date(date)
        target = initial + timedelta(days=int(session_no) - 1)
        candidates = []
        if target != initial:
            candidates.append((target, target))
        candidates.append((initial, target))
        original_ua = self.http_client.user_agent
        try:
            self.http_client.user_agent = _YENJOY_USER_AGENT
            for init_d, tgt_d in candidates:
                url = _build_yenjoy_url_raw(venue, init_d, tgt_d, race_no)
                try:
                    html = self.http_client.get(url)
                except Exception:
                    continue
                strategies = parse_yenjoy_strategies(html)
                if strategies:
                    return strategies
        finally:
            self.http_client.user_agent = original_ua
        return []

    def enrich_scores(
        self,
        payload: dict[str, Any],
        *,
        venue: str,
        date: Date | str,
        race_no: int,
        session_no: int = 1,
    ) -> int:
        """payload の riders に競走得点 + 戦法推定の決まり手を補完する。

        - score: yen-joy の 4ヶ月得点（実数値）
        - 決まり手 (nige/makuri/sashi/mark) + b_count: 戦法ラベルからの **推定値**
          （注: 実数ではなく目安。score の代わりに用途特化補正に使う）

        Returns:
            補完した選手数
        """
        scores = self.fetch_scores(
            venue=venue, date=date, race_no=race_no, session_no=session_no,
        )
        strategies: list = []
        if scores:
            # 同じ HTML から戦法ラベルも取得（キャッシュヒット）
            strategies = self.fetch_strategies(
                venue=venue, date=date, race_no=race_no, session_no=session_no,
            )
        if not scores and not strategies:
            return 0
        return merge_yenjoy_scores_into_riders(
            payload.get("riders") or [],
            scores,
            strategies_by_car_index=strategies or None,
        )
