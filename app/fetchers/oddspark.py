"""オッズパーク向け Fetcher（試験実装）。

オッズパーク競輪のオッズページは静的HTMLにオッズデータを含んでおり、
Kドリームスがログイン/JS依存なのに対し、人気順表示が取得しやすい。

URL パターン:
    https://www.oddspark.com/keirin/Odds.do?joCode={jo}&kaisaiBi={YYYYMMDD}
        &raceNo={N}&betType={9|8|6}&viewType=1

betType: 3連単=9 / 3連複=8 / 2車単=6
viewType=1 = 人気順表示

joCode は Kドリームスと同じ JKA 共通コード（例: 平塚=35）。

遵守事項:
- 自動投票・購入処理は実装しない
- ログインしない（GET のみ）
- レート制限・キャッシュ・User-Agent を必ず通す
- 生HTMLは Fetcher 内に閉じ込め、構造化dictだけ返す
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Optional

from .base import Fetcher, FetchError, NotImplementedSource, RaceCardData
from .http import HttpClient
from .kdreams import resolve_jo_code, _validate_race_no, _validate_bet_type
from .parsers.oddspark_odds import BET_TYPE_TO_ODDSPARK, parse_oddspark_odds_html


_ODDSPARK_HOST = "https://www.oddspark.com"
_ODDS_URL_TEMPLATE = (
    _ODDSPARK_HOST + "/keirin/Odds.do?joCode={jo}&kaisaiBi={day}&raceNo={race}"
    "&betType={bet_type_code}&viewType=1"
)


def _coerce_date(date: Date | str | None) -> Date:
    if date is None:
        raise FetchError("日付が指定されていません (YYYY-MM-DD)。")
    if isinstance(date, Date):
        return date
    try:
        return datetime.strptime(str(date), "%Y-%m-%d").date()
    except ValueError as e:
        raise FetchError(
            f"日付は YYYY-MM-DD 形式で指定してください: '{date}'"
        ) from e


def build_oddspark_odds_url(
    venue: str, date: Date | str, race_no: Any, bet_type: Any
) -> str:
    """オッズパーク3連単/3連複/2車単オッズのURLを生成する。"""
    jo = resolve_jo_code(venue)
    d = _coerce_date(date)
    r = _validate_race_no(race_no)
    bt = _validate_bet_type(bet_type)
    code = BET_TYPE_TO_ODDSPARK.get(bt)
    if code is None:
        raise FetchError(
            f"オッズパーク非対応のオッズ種別: '{bet_type}'"
        )
    return _ODDS_URL_TEMPLATE.format(
        jo=jo, day=d.strftime("%Y%m%d"), race=r, bet_type_code=code
    )


class OddsParkFetcher(Fetcher):
    """オッズパーク用 Fetcher。fetch_odds のみ実装。"""

    source_name = "oddspark"

    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http_client = http_client

    def _not_impl(self, method: str) -> NotImplementedSource:
        return NotImplementedSource(
            f"オッズパーク連携の{method}はまだ未実装です。"
            "現フェーズでは ManualFetcher を使うか、--fallback-input を指定してください。"
        )

    def fetch_race_card(self, **kwargs: Any) -> RaceCardData:
        raise self._not_impl("fetch_race_card")

    def fetch_results(self, **kwargs: Any) -> list[dict[str, Any]]:
        raise self._not_impl("fetch_results")

    def fetch_venue_trend(self, **kwargs: Any) -> Optional[dict[str, Any]]:
        raise self._not_impl("fetch_venue_trend")

    def fetch_odds(
        self,
        *,
        venue: Optional[str] = None,
        race_no: Optional[int] = None,
        date: Optional[Date] = None,
        bet_type: Optional[str] = None,
        limit: int = 20,
        **kwargs: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        """人気上位のオッズを構造化dictで返す。

        - Kドリームスと同じ戻り値形式:
            {"trifecta_popular": [...], "trio_popular": [...], "exacta_popular": [...]}
        - bet_type 指定時はそのキーだけ。
        - **複数種別取得時に一部が失敗しても他種別の結果は維持** する（部分成功OK）。
        - 全種別が失敗した場合のみ FetchError を投げる。
        """
        if self.http_client is None:
            raise FetchError(
                "HttpClient が未設定です。オッズパーク取得には HttpClient を渡してください。"
            )
        if not venue:
            raise FetchError("場名が指定されていません。--venue を指定してください。")
        d = _coerce_date(date)
        r = _validate_race_no(race_no)

        if bet_type is not None:
            targets = [_validate_bet_type(bet_type)]
            allow_partial = False
        else:
            targets = list(BET_TYPE_TO_ODDSPARK.keys())
            allow_partial = True

        out: dict[str, list[dict[str, Any]]] = {}
        errors: list[str] = []
        for bt in targets:
            url = build_oddspark_odds_url(venue, d, r, bt)
            try:
                html = self.http_client.get(url)
                rows = parse_oddspark_odds_html(html, bet_type=bt, limit=limit)
            except FetchError as e:
                errors.append(f"{bt}: {e}")
                if allow_partial:
                    continue
                raise
            out[f"{bt}_popular"] = rows

        if not out:
            raise FetchError(
                "オッズパークから全種別の取得に失敗しました: " + " / ".join(errors)
            )
        return out
