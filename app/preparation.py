"""prepare-json: 外部取得 + 天候マージ + recent_results 取り込みを1回でまとめる。

責務:
- 外部 Fetcher を呼んで RaceInput ベース（出走表+ライン）を取得する
- weather / wind / rain / bank 引数で RaceInput を上書きする
- 同日の結果を recent_results に取り込む（対象 race_no より前のレースのみ）
- 取得失敗時は日本語警告を出してフォールバックや継続を判断する

予想ロジック・HTTPクライアントの詳細には踏み込まず、各レイヤを橋渡しする。
"""

from __future__ import annotations

import json
import sys
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .bank_info import get_bank_info
from .enrichment import EnrichmentError, merge_odds, merge_race_notes, merge_recent_results
from .weather import WeatherFetchError, build_weather_provider
from .fetchers import (
    FetchError,
    HttpClient,
    ManualFetcher,
    NotImplementedSource,
    OddsParkFetcher,
    SUPPORTED_SOURCES,
    build_fetcher,
)
from .models import RaceInput


WarningEmitter = Callable[[str], None]


class PreparationError(ValueError):
    """prepare-json まわりのエラー。メッセージは日本語で。"""


def _default_warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def _coerce_date(date_str: Optional[str]) -> Date:
    if not date_str:
        raise PreparationError("日付が指定されていません (YYYY-MM-DD)。")
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as e:
        raise PreparationError(
            f"日付は YYYY-MM-DD 形式で指定してください: '{date_str}'"
        ) from e


def _validate_race_no(race_no: Any) -> int:
    if race_no is None:
        raise PreparationError("レース番号が指定されていません。")
    try:
        n = int(race_no)
    except (TypeError, ValueError) as e:
        raise PreparationError(
            f"レース番号は整数で指定してください: '{race_no}'"
        ) from e
    if not 1 <= n <= 12:
        raise PreparationError(f"レース番号は1〜12の範囲で指定してください: {n}")
    return n


def _apply_weather_overrides(
    data: dict[str, Any],
    *,
    weather: Optional[str],
    rain: Optional[float],
    wind_direction: Optional[str],
    wind_speed: Optional[float],
    wind_note: Optional[str],
) -> dict[str, Any]:
    """race_card dict の weather フィールドを上書きする。"""
    has_any = any(
        v is not None for v in (weather, rain, wind_direction, wind_speed, wind_note)
    )
    if not has_any:
        return data
    existing = data.get("weather") or {}
    base: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    if weather is not None:
        base["condition"] = weather
    if rain is not None:
        base["rain_mm_per_hour"] = rain
    if wind_direction is not None:
        base["wind_direction"] = wind_direction
    if wind_speed is not None:
        base["wind_speed_mps"] = wind_speed
    if wind_note is not None:
        base["wind_note"] = wind_note
    if "condition" not in base or not base.get("condition"):
        base["condition"] = "不明"
    data["weather"] = base
    return data


def _apply_bank_override(
    data: dict[str, Any],
    bank_note: Optional[str],
    bank_length: Optional[int] = None,
    bank_style: Optional[str] = None,
) -> dict[str, Any]:
    if not (bank_note or bank_length or bank_style):
        return data
    race = data.setdefault("race", {})
    if bank_note:
        existing = race.get("bank_note") or ""
        if existing and existing != bank_note:
            race["bank_note"] = f"{existing} / {bank_note}"
        else:
            race["bank_note"] = bank_note
    if bank_length is not None:
        race["bank_length"] = bank_length
    if bank_style:
        race["bank_style"] = bank_style
    return data


def _fetch_race_card_with_fallback(
    *,
    source: str,
    venue: str,
    race_no: int,
    parsed_date: Date,
    http_client: Optional[HttpClient],
    fallback_input: Optional[Path],
    warn: WarningEmitter,
    session_no: int = 1,
) -> tuple[dict[str, Any], Any]:
    """出走表を取得する。失敗時はフォールバックを試す。

    Returns: (race_card_dict, fetcher_for_results)
        fetcher_for_results は、後段で同じ fetcher を再利用するために返す。
        フォールバックに切り替わった場合は ManualFetcher を返す。
    """
    try:
        primary = build_fetcher(
            source,
            http_client=http_client,
            manual_input_path=(
                str(fallback_input)
                if (source == "manual" and fallback_input)
                else None
            ),
        )
    except FetchError as e:
        raise PreparationError(str(e)) from e

    primary_err: Optional[Exception] = None
    try:
        # 本番経路では /racedetail/ から競走得点・決まり手を補完取得する
        data = primary.fetch_race_card(
            venue=venue, race_no=race_no, date=parsed_date,
            session_no=session_no, enrich_stats=True,
        )
        return data, primary
    except (FetchError, NotImplementedSource) as e:
        primary_err = e
        warn(f"[警告] 出走表の取得に失敗しました: {e}")

    if fallback_input is None:
        raise PreparationError(
            f"出走表の取得に失敗しました: {primary_err}。"
            "--fallback-input を指定するとフォールバックできます。"
        )

    warn(f"[案内] フォールバック手入力JSONを使用します: {fallback_input}")
    try:
        fb = ManualFetcher(input_path=fallback_input)
        data = fb.fetch_race_card(
            venue=venue, race_no=race_no, date=parsed_date
        )
    except FetchError as e:
        raise PreparationError(
            f"フォールバックも失敗: {e} (一次原因: {primary_err})"
        ) from e
    return data, fb


def prepare_race_input(
    *,
    source: str,
    venue: str,
    date_str: str,
    race_no: Any,
    http_client: Optional[HttpClient] = None,
    weather: Optional[str] = None,
    rain: Optional[float] = None,
    wind_direction: Optional[str] = None,
    wind_speed: Optional[float] = None,
    wind_note: Optional[str] = None,
    bank_note: Optional[str] = None,
    include_results: bool = True,
    results_race_no: Optional[int] = None,
    max_results: Optional[int] = None,
    include_odds: bool = False,
    odds_bet_type: Optional[str] = None,
    odds_limit: int = 20,
    odds_source: Optional[str] = None,
    weather_source: Optional[str] = None,
    start_time: Optional[str] = None,
    session_no: int = 1,
    bank_length: Optional[int] = None,
    bank_style: Optional[str] = None,
    include_tospo_notes: bool = False,
    tospo_url: Optional[str] = None,
    fallback_input: Optional[Path] = None,
    warn: WarningEmitter = _default_warn,
) -> RaceInput:
    """外部取得から RaceInput をまとめて構築する。

    手順:
        1. fetcher.fetch_race_card を呼ぶ（失敗時は ManualFetcher へフォールバック）
        2. weather / rain / wind / bank 引数で race_card dict を上書き
        3. RaceInput.model_validate
        4. include_results=True なら fetch_results を呼んで recent_results に取り込む
           - results_race_no 指定があればそのレースのみ
           - そうでなければ race_no 未満のレースのみ（未来結果の混入を防ぐ）
           - 取得失敗は警告のみで race_card 側は維持

    Returns:
        RaceInput（バリデーション済み）

    Raises:
        PreparationError: 入力不正・race_card取得失敗（fallbackも失敗）
    """
    if not source or source.strip().lower() not in SUPPORTED_SOURCES:
        raise PreparationError(
            f"未対応のソース: '{source}'。サポート対象: {', '.join(SUPPORTED_SOURCES)}"
        )
    src = source.strip().lower()
    if not venue:
        raise PreparationError("場名が指定されていません。")
    parsed_date = _coerce_date(date_str)
    target_race_no = _validate_race_no(race_no)
    if results_race_no is not None:
        results_race_no = _validate_race_no(results_race_no)

    card_data, fetcher = _fetch_race_card_with_fallback(
        source=src,
        venue=venue,
        race_no=target_race_no,
        parsed_date=parsed_date,
        http_client=http_client,
        fallback_input=fallback_input,
        warn=warn,
        session_no=session_no,
    )

    # ---- 天候APIから取得（指定時のみ） -----------------------------------
    if weather_source and weather_source.strip().lower() == "open-meteo":
        try:
            provider = build_weather_provider(weather_source, http_client=http_client)
            w = provider.fetch_weather(
                venue=venue, date=parsed_date, start_time=start_time
            )
            card_data["weather"] = json.loads(w.model_dump_json())
        except WeatherFetchError as e:
            warn(
                f"[警告] 天候APIの取得に失敗しました（手入力があればそれを使います）: {e}"
            )
        except Exception as e:
            warn(
                f"[警告] 天候APIで予期しない例外: {type(e).__name__}: {e}"
            )

    # ---- 手入力で上書き（API結果より優先）----------------------------------
    card_data = _apply_weather_overrides(
        card_data,
        weather=weather,
        rain=rain,
        wind_direction=wind_direction,
        wind_speed=wind_speed,
        wind_note=wind_note,
    )
    # ---- バンク情報自動補完（venue → bank_length/bank_style） ------------
    # ユーザーが --bank-length / --bank-style を明示した場合はそちらが優先。
    # 何も指定が無く、bank_info.py に登録されている場名なら、その固定値を採用。
    auto_bank = get_bank_info(venue) or {}
    auto_bank_length = (
        bank_length
        if bank_length is not None
        else auto_bank.get("bank_length")
    )
    auto_bank_style = (
        bank_style
        if bank_style is not None
        else auto_bank.get("bank_style")
    )
    if (
        bank_length is None
        and auto_bank_length is not None
        and auto_bank
    ):
        warn(
            f"[案内] バンク情報を自動補完: {venue} → "
            f"周長 {auto_bank_length}m"
            + (f" / {auto_bank_style}" if auto_bank_style else "")
        )

    card_data = _apply_bank_override(
        card_data, bank_note,
        bank_length=auto_bank_length, bank_style=auto_bank_style,
    )

    try:
        ri = RaceInput.model_validate(card_data)
    except Exception as e:
        raise PreparationError(
            f"取得結果が RaceInput スキーマに合致しません: {e}"
        ) from e

    # ---- 結果取り込み ---------------------------------------------------
    if include_results:
        items: Optional[list[dict[str, Any]]] = None
        try:
            items = fetcher.fetch_results(
                venue=venue,
                race_no=results_race_no,
                date=parsed_date,
                session_no=session_no,
            )
        except (FetchError, NotImplementedSource) as e:
            warn(f"[警告] 結果の取得に失敗しました（出走表は使用継続）: {e}")
            items = None
        except TypeError:
            # ManualFetcher 等で session_no を受け付けないケース
            try:
                items = fetcher.fetch_results(
                    venue=venue, race_no=results_race_no, date=parsed_date
                )
            except (FetchError, NotImplementedSource) as e:
                warn(f"[警告] 結果の取得に失敗しました（出走表は使用継続）: {e}")
                items = None

        if items:
            # race_no より前のレースのみ取り込む（未来結果の混入を防ぐ）
            if results_race_no is None:
                items = [
                    r for r in items
                    if isinstance(r, dict)
                    and (r.get("race_no") or 0) < target_race_no
                ]
            if items:
                try:
                    ri = merge_recent_results(
                        ri,
                        {
                            "source": getattr(fetcher, "source_name", src),
                            "kind": "results",
                            "venue": venue,
                            "date": date_str,
                            "results": items,
                        },
                        max_results=max_results,
                    )
                except EnrichmentError as e:
                    warn(f"[警告] 結果の取り込みに失敗しました: {e}")

    # ---- オッズ取り込み -------------------------------------------------
    if include_odds:
        # odds_source 指定があれば別 Fetcher を使う（既定: race_card と同じソース）
        odds_fetcher = fetcher
        chosen_odds_src = (odds_source or "").strip().lower() if odds_source else None
        if chosen_odds_src and chosen_odds_src != getattr(fetcher, "source_name", ""):
            if chosen_odds_src == "oddspark":
                odds_fetcher = OddsParkFetcher(http_client=http_client)
            elif chosen_odds_src == "kdreams":
                from .fetchers import KDreamsFetcher  # 遅延 import
                odds_fetcher = KDreamsFetcher(http_client=http_client)
            else:
                warn(
                    f"[警告] 未対応のオッズ取得元: '{odds_source}'。既定のソースを使います。"
                )

        odds_payload: Any = None
        try:
            odds_payload = odds_fetcher.fetch_odds(
                venue=venue,
                race_no=target_race_no,
                date=parsed_date,
                bet_type=odds_bet_type,
                limit=odds_limit,
                session_no=session_no,
            )
        except (FetchError, NotImplementedSource) as e:
            warn(f"[警告] オッズの取得に失敗しました（出走表は使用継続）: {e}")
            odds_payload = None
        except TypeError:
            # Fetcher側が bet_type/limit/session_no kwargs を受け付けない場合のフォールバック
            try:
                odds_payload = odds_fetcher.fetch_odds(
                    venue=venue,
                    race_no=target_race_no,
                    date=parsed_date,
                    bet_type=odds_bet_type,
                    limit=odds_limit,
                )
            except (FetchError, NotImplementedSource) as e:
                warn(f"[警告] オッズの取得に失敗しました（出走表は使用継続）: {e}")
                odds_payload = None

        if odds_payload:
            try:
                ri = merge_odds(ri, odds_payload, replace=True)
            except EnrichmentError as e:
                warn(f"[警告] オッズの取り込みに失敗しました: {e}")

    # ---- 東スポ補助情報の取り込み（任意） ---------------------------------
    if include_tospo_notes:
        if not tospo_url:
            warn(
                "[警告] --tospo-notes が有効ですが --tospo-url が指定されていません。"
                "東スポ補助情報はスキップします。"
            )
        elif http_client is None:
            warn("[警告] HttpClient が無いため東スポ取得をスキップします。")
        else:
            from .fetchers import TospoFetcher  # 遅延 import
            tospo_payload = None
            try:
                tospo_fetcher = TospoFetcher(http_client=http_client)
                tospo_payload = tospo_fetcher.fetch_race_notes(
                    venue=venue,
                    date=parsed_date,
                    race_no=target_race_no,
                    url=tospo_url,
                )
            except (FetchError, NotImplementedSource) as e:
                warn(f"[警告] 東スポ補助情報の取得に失敗しました（出走表は使用継続）: {e}")
            except Exception as e:
                warn(
                    f"[警告] 東スポ補助情報で想定外エラー（出走表は使用継続）: "
                    f"{type(e).__name__}: {e}"
                )

            if tospo_payload:
                try:
                    ri = merge_race_notes(ri, tospo_payload)
                except EnrichmentError as e:
                    warn(f"[警告] 東スポ補助情報の取り込みに失敗しました: {e}")

    return ri
