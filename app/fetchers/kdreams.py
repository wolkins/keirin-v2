"""Kドリームス向け Fetcher。

現フェーズで実装するのは「結果ページ」の取得のみ。出走表 / オッズ / 場の傾向は
NotImplementedSource を投げる。

遵守事項:
- サイト規約を尊重する
- 過剰アクセスをしない（HttpClient のレート制限とキャッシュを使う）
- User-Agent を適切に設定する（HttpClient のデフォルト）
- 取得失敗時は ManualFetcher にフォールバックする（呼び出し側で）
- 生HTMLをLLMや上位層へ渡さない（必ず構造化dictに変換する）
- 自動投票・購入処理は絶対に実装しない
- GET のみ。POST は使わない
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Optional

from .base import Fetcher, FetchError, NotImplementedSource, RaceCardData
from .http import HttpClient
from .parsers.kdreams_odds import BET_TYPES, parse_odds_html
from .parsers.kdreams_race_card import parse_race_card_html
from .parsers.kdreams_results import parse_results_html


# 場名 → jo_code。未対応場は FetchError。
# 新規追加分（平塚等）は JKA 公式コードに概ね準拠。既存9場は当初の仮置き値を保持。
# 実サイトの最新コードと一致しない可能性があるため、本番接続時は要検証。
_JO_CODE: dict[str, int] = {
    # 北海道・東北
    "函館": 11,
    "青森": 12,
    "いわき平": 13,
    # 北陸・甲信越
    "弥彦": 21,
    # 関東
    "前橋": 22,
    "取手": 23,
    "宇都宮": 24,
    "大宮": 25,
    "西武園": 26,
    "京王閣": 27,
    "立川": 28,
    "松戸": 31,
    "千葉": 32,
    "川崎": 34,
    "平塚": 35,
    "小田原": 36,
    "伊東温泉": 37,
    # 東海
    "静岡": 38,
    "名古屋": 42,
    "岐阜": 43,
    "大垣": 44,
    "豊橋": 45,
    # 北陸
    "富山": 47,
    "松阪": 48,
    "四日市": 49,
    "福井": 50,
    # 近畿
    "奈良": 53,
    "向日町": 54,
    "和歌山": 55,
    "岸和田": 56,
    # 中国・四国
    "玉野": 61,
    "広島": 62,
    "防府": 63,
    "高松": 71,
    "高知": 74,
    "松山": 75,
    # 九州
    "小倉": 81,
    "久留米": 83,
    "武雄": 84,
    "佐世保": 85,
    "別府": 86,
    "熊本": 87,
}

# 場名 → URLスラッグ（英字）。Kドリームスの URL パスに使う。
_VENUE_SLUG: dict[str, str] = {
    "函館": "hakodate",
    "青森": "aomori",
    "いわき平": "iwakitaira",
    "弥彦": "yahiko",
    "前橋": "maebashi",
    "取手": "toride",
    "宇都宮": "utsunomiya",
    "大宮": "omiya",
    "西武園": "seibuen",
    "京王閣": "keiokaku",
    "立川": "tachikawa",
    "松戸": "matsudo",
    "千葉": "chiba",
    "川崎": "kawasaki",
    "平塚": "hiratsuka",
    "小田原": "odawara",
    "伊東温泉": "itoonsen",
    "静岡": "shizuoka",
    "名古屋": "nagoya",
    "岐阜": "gifu",
    "大垣": "ogaki",
    "豊橋": "toyohashi",
    "富山": "toyama",
    "松阪": "matsusaka",
    "四日市": "yokkaichi",
    "福井": "fukui",
    "奈良": "nara",
    "向日町": "mukomachi",
    "和歌山": "wakayama",
    "岸和田": "kishiwada",
    "玉野": "tamano",
    "広島": "hiroshima",
    "防府": "hofu",
    "高松": "takamatsu",
    "高知": "kochi",
    "松山": "matsuyama",
    "小倉": "kokurakeirin",
    "久留米": "kurume",
    "武雄": "takeo",
    "佐世保": "sasebo",
    "別府": "beppu",
    "熊本": "kumamoto",
}

# Kドリームス（楽天Kドリームス競輪）の実 URL パターン。
# 形式:
#   kaisaiDateId = f"{jo:02d}{YYYYMMDD}{session_no:02d}00"  ← 開催日単位（出走表/結果）
#   raceId       = f"{jo:02d}{YYYYMMDD}{session_no:02d}{race_no:02d}"  ← 1レース単位（オッズ）
# session_no は開催日番号（初日=1, 2日目=2, ...）。
_KDREAMS_HOST = "https://keirin.kdreams.jp"


def _is_not_system_error(body: str) -> bool:
    """Kドリームスのエラーページかどうかを判定する。

    `SYSTEM_ERROR` ID または「エラーが発生しました」を含む HTML はキャッシュしない
    （キャッシュ汚染を避けるため）。
    """
    if not body:
        return False
    if "SYSTEM_ERROR" in body or "エラーが発生しました" in body:
        return False
    return True
_RACE_CARD_URL_TEMPLATE = (
    _KDREAMS_HOST + "/{slug}/racecard/{kaisai_id}/"
)
_RESULTS_URL_TEMPLATE = (
    _KDREAMS_HOST + "/{slug}/raceresult/{kaisai_id}/"
)
_ODDS_URL_TEMPLATE = (
    _KDREAMS_HOST
    + "/{slug}/racedetail/{race_id}/?pageType=odds&kakeshikiType={kakeshiki}"
)

# 内部の英語キー → Kドリームスの kakeshikiType クエリ値
_KAKESHIKI_MAP: dict[str, str] = {
    "trifecta": "3rentan",
    "trio": "3renpuku",
    "exacta": "2tanshou",
}


def resolve_jo_code(venue: str) -> int:
    """場名から jo_code を返す。未対応場名は日本語 FetchError。"""
    if not venue:
        raise FetchError("場名が指定されていません。")
    code = _JO_CODE.get(venue)
    if code is None:
        raise FetchError(
            f"未対応の場名です: '{venue}'。対応場名: {', '.join(_JO_CODE.keys())}"
        )
    return code


def resolve_venue_slug(venue: str) -> str:
    """場名から URL スラッグ（英字）を返す。未対応場名は日本語 FetchError。"""
    if not venue:
        raise FetchError("場名が指定されていません。")
    slug = _VENUE_SLUG.get(venue)
    if slug is None:
        raise FetchError(
            f"未対応の場名です: '{venue}'。対応場名: {', '.join(_VENUE_SLUG.keys())}"
        )
    return slug


def _validate_session_no(session_no: Any) -> int:
    """開催日番号（初日=1, 2日目=2, ...）を 1〜10 で検証。"""
    if session_no is None:
        session_no = 1
    try:
        n = int(session_no)
    except (TypeError, ValueError) as e:
        raise FetchError(
            f"--session-no は整数で指定してください: '{session_no}'"
        ) from e
    if not 1 <= n <= 10:
        raise FetchError(f"--session-no は1〜10の範囲で指定してください: {n}")
    return n


def _build_kaisai_id(jo: int, d: Date, session_no: int) -> str:
    """kaisaiDateId = jo(2桁) + YYYYMMDD + session(2桁) + '00'"""
    return f"{jo:02d}{d.strftime('%Y%m%d')}{session_no:02d}00"


def _build_race_id(jo: int, d: Date, session_no: int, race_no: int) -> str:
    """raceId = jo(2桁) + YYYYMMDD + session(2桁) + race_no(2桁)"""
    return f"{jo:02d}{d.strftime('%Y%m%d')}{session_no:02d}{race_no:02d}"


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


def build_results_url(
    venue: str, date: Date | str, *, session_no: int = 1
) -> str:
    """場名と日付から結果ページURLを生成する。"""
    jo = resolve_jo_code(venue)
    slug = resolve_venue_slug(venue)
    d = _coerce_date(date)
    s = _validate_session_no(session_no)
    return _RESULTS_URL_TEMPLATE.format(
        slug=slug, kaisai_id=_build_kaisai_id(jo, d, s)
    )


def _validate_race_no(race_no: Any) -> int:
    if race_no is None:
        raise FetchError("レース番号が指定されていません。--race-no を指定してください。")
    try:
        n = int(race_no)
    except (TypeError, ValueError) as e:
        raise FetchError(
            f"レース番号は整数で指定してください: '{race_no}'"
        ) from e
    if not 1 <= n <= 12:
        raise FetchError(f"レース番号は1〜12の範囲で指定してください: {n}")
    return n


def build_race_detail_url(
    venue: str, date: Date | str, race_no: Any, *, session_no: int = 1
) -> str:
    """個別レース詳細ページURL。

    Kドリームスの /racedetail/ ページには競走得点・決まり手 (逃/捲/差/マ)
    が含まれている。/racecard/ には無い。
    """
    jo = resolve_jo_code(venue)
    slug = resolve_venue_slug(venue)
    d = _coerce_date(date)
    r = _validate_race_no(race_no)
    s = _validate_session_no(session_no)
    return (
        _KDREAMS_HOST
        + f"/{slug}/racedetail/{_build_race_id(jo, d, s, r)}/"
    )


def build_race_card_url(
    venue: str, date: Date | str, race_no: Any, *, session_no: int = 1
) -> str:
    """場名・日付・レース番号から出走表ページURLを生成する。

    Kドリームスの出走表は開催日単位のページ。`race_no` はパス上には現れず、
    呼び出し側が同一ページから対象レース行を抽出する想定。
    """
    jo = resolve_jo_code(venue)
    slug = resolve_venue_slug(venue)
    d = _coerce_date(date)
    _ = _validate_race_no(race_no)  # 引数検証は維持（パスには含めない）
    s = _validate_session_no(session_no)
    return _RACE_CARD_URL_TEMPLATE.format(
        slug=slug, kaisai_id=_build_kaisai_id(jo, d, s)
    )


def _validate_bet_type(bet_type: Any) -> str:
    if not bet_type:
        raise FetchError(
            f"オッズ種別が指定されていません。サポート対象: {', '.join(BET_TYPES)}"
        )
    bt = str(bet_type).strip().lower()
    if bt not in BET_TYPES:
        raise FetchError(
            f"未対応のオッズ種別: '{bet_type}'。サポート対象: {', '.join(BET_TYPES)}"
        )
    return bt


def build_odds_url(
    venue: str,
    date: Date | str,
    race_no: Any,
    bet_type: Any,
    *,
    session_no: int = 1,
) -> str:
    """場名・日付・レース番号・種別からオッズページURLを生成する。"""
    jo = resolve_jo_code(venue)
    slug = resolve_venue_slug(venue)
    d = _coerce_date(date)
    r = _validate_race_no(race_no)
    bt = _validate_bet_type(bet_type)
    s = _validate_session_no(session_no)
    kakeshiki = _KAKESHIKI_MAP[bt]
    return _ODDS_URL_TEMPLATE.format(
        slug=slug,
        race_id=_build_race_id(jo, d, s, r),
        kakeshiki=kakeshiki,
    )


class KDreamsFetcher(Fetcher):
    source_name = "kdreams"

    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http_client = http_client

    def _not_impl(self, method: str) -> NotImplementedSource:
        return NotImplementedSource(
            f"Kドリームス連携の{method}はまだ未実装です。"
            "現フェーズでは ManualFetcher を使うか、--fallback-input を指定してください。"
        )

    def fetch_race_card(
        self,
        *,
        venue: Optional[str] = None,
        race_no: Optional[int] = None,
        date: Optional[Date] = None,
        session_no: int = 1,
        enrich_stats: bool = False,
        **kwargs: Any,
    ) -> RaceCardData:
        """指定レースの出走表 (RaceInput 互換 dict) を返す。

        - HttpClient 未注入は日本語 FetchError
        - venue / date / race_no の不正値は日本語 FetchError
        - パース失敗・選手0件は日本語 FetchError
        - 生HTMLは内部に閉じ込め、戻り値は構造化dictのみ
        - session_no は開催日番号（初日=1）
        - enrich_stats=True（既定）なら、/racedetail/ から競走得点・決まり手を補完
        """
        if self.http_client is None:
            raise FetchError(
                "HttpClient が未設定です。Kドリームス出走表取得には HttpClient を渡してください。"
            )
        if not venue:
            raise FetchError("場名が指定されていません。--venue を指定してください。")
        d = _coerce_date(date)
        r = _validate_race_no(race_no)
        url = build_race_card_url(venue, d, r, session_no=session_no)
        # SYSTEM_ERROR ページはキャッシュ汚染を避けるため保存しない
        html = self.http_client.get(url, validate_body=_is_not_system_error)
        payload = parse_race_card_html(
            html, venue=venue, date_str=d.strftime("%Y-%m-%d"), race_no=r
        )
        # /racedetail/ から競走得点・決まり手を補完
        if enrich_stats and payload and payload.get("riders"):
            try:
                self._enrich_stats_from_racedetail(
                    payload, venue=venue, date=d, race_no=r, session_no=session_no,
                )
            except Exception:
                # 補完失敗時は黙って数値不足モードで動かす
                pass
        return payload

    def _enrich_stats_from_racedetail(
        self,
        payload: RaceCardData,
        *,
        venue: str,
        date: Date,
        race_no: int,
        session_no: int,
    ) -> int:
        """競走得点を補完取得する。

        補完元:
          1. Kドリームス /racedetail/ （ログイン必要のため通常は失敗）
          2. yen-joy の予想ページ（情報提供サイト・ログイン不要・推奨）

        どちらか取れた方を採用。両方失敗したら 0 を返す（数値不足モードで継続）。

        Returns:
            補完できた選手数（0 なら何も変わらない）
        """
        if self.http_client is None:
            return 0
        # まず Kドリームス /racedetail/ を試す（ログイン必要なので通常は失敗）
        try:
            from .parsers.kdreams_race_detail import (
                parse_race_detail_html,
                merge_stats_into_riders,
            )
            url = build_race_detail_url(
                venue, date, race_no, session_no=session_no,
            )
            html = self.http_client.get(url, validate_body=_is_not_system_error)
            stats = parse_race_detail_html(html)
            if stats:
                matched = merge_stats_into_riders(
                    payload.get("riders") or [], stats,
                )
                if matched > 0:
                    return matched
        except Exception:
            pass

        # yen-joy にフォールバック（ログイン不要、競走得点のみ補完）
        try:
            from .yenjoy import YenJoyFetcher
            yj = YenJoyFetcher(http_client=self.http_client)
            matched = yj.enrich_scores(
                payload, venue=venue, date=date,
                race_no=race_no, session_no=session_no,
            )
            # 補完成功したら user_note の古い注釈を更新
            if matched > 0 and isinstance(payload.get("user_note"), str):
                if "score/B/逃/捲/差/マークは未取得" in payload["user_note"]:
                    payload["user_note"] = (
                        "Kドリームス出走表+yen-joy で取得"
                        "（競走得点はyen-joyから補完。"
                        "B数・決まり手回数は未取得・要手動補完）"
                    )
            return matched
        except Exception:
            return 0

    def fetch_odds(
        self,
        *,
        venue: Optional[str] = None,
        race_no: Optional[int] = None,
        date: Optional[Date] = None,
        bet_type: Optional[str] = None,
        limit: int = 20,
        session_no: int = 1,
        **kwargs: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        """人気上位のオッズを構造化dictで返す。

        bet_type を指定するとそれだけ、未指定なら 3連単/3連複/2車単 を順次取得。
        戻り値の例:
            {
              "trifecta_popular": [{"rank":1, "combination":"5-1-3", "odds":8.5}, ...],
              "trio_popular":     [{"rank":1, "combination":"1=3=5", "odds":4.0}, ...],
              "exacta_popular":   [{"rank":1, "combination":"5-1",   "odds":3.6}, ...]
            }
        bet_type 指定時はそのキーだけが含まれる。
        生HTMLは内部に閉じ込め、戻り値は構造化dictのみ。
        """
        if self.http_client is None:
            raise FetchError(
                "HttpClient が未設定です。Kドリームスオッズ取得には HttpClient を渡してください。"
            )
        if not venue:
            raise FetchError("場名が指定されていません。--venue を指定してください。")
        d = _coerce_date(date)
        r = _validate_race_no(race_no)

        if bet_type is not None:
            targets = [_validate_bet_type(bet_type)]
        else:
            targets = list(BET_TYPES)

        out: dict[str, list[dict[str, Any]]] = {}
        for bt in targets:
            url = build_odds_url(venue, d, r, bt, session_no=session_no)
            html = self.http_client.get(url, validate_body=_is_not_system_error)
            rows = parse_odds_html(html, bet_type=bt, limit=limit)
            out[f"{bt}_popular"] = rows
        return out

    # -----------------------------------------------------------------------
    # 結果ページ取得（試験実装）
    # -----------------------------------------------------------------------

    def fetch_results(
        self,
        *,
        venue: Optional[str] = None,
        date: Optional[Date] = None,
        race_no: Optional[int] = None,
        session_no: int = 1,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """指定日のレース結果を構造化dictのリストとして返す。

        - race_no を指定した場合はそのレースだけ
        - 未確定/開催前/中止のレースはスキップ
        - 生HTMLは内部に閉じ込め、戻り値は構造化dictのみ
        - HttpClient が無い場合は日本語 FetchError
        - session_no は開催日番号（初日=1）
        """
        if self.http_client is None:
            raise FetchError(
                "HttpClient が未設定です。Kドリームス結果取得には HttpClient を渡してください。"
            )
        if not venue:
            raise FetchError("場名が指定されていません。--venue を指定してください。")
        d = _coerce_date(date)
        url = build_results_url(venue, d, session_no=session_no)
        # 通信は HttpClient に集約。SYSTEM_ERROR はキャッシュしない
        html = self.http_client.get(url, validate_body=_is_not_system_error)
        return parse_results_html(
            html, venue=venue, date_str=d.strftime("%Y-%m-%d"), race_no=race_no
        )

    def fetch_venue_trend(
        self,
        *,
        venue: Optional[str] = None,
        date: Optional[Date] = None,
        **kwargs: Any,
    ) -> Optional[dict[str, Any]]:
        raise self._not_impl("fetch_venue_trend")
