"""RaceRequest: リクエストレースの一意識別 + race_no 不一致検証 (2026-05-25).

目的:
ユーザーが「静岡5R」を依頼したのに出力が「静岡4R」になる事故を防ぐ。
入口側で requested_race_no と fetched_race_no を照合し、不一致なら
例外で停止する。OutputPlan の RACE_NO_MISMATCH は最終防衛。

設計:
- RaceRequest: venue / date / race_no / source を 1 つに束ねる
- RaceNoMismatchError: 不一致時の例外
- validate_race_no_fetch_match: fetch 直後の検証
- validate_race_no_dataset_match: racecard/odds/results 等の dataset 検証
- validate_race_no_output_match: prediction 出力時の検証 (最終防衛)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from typing import Optional


# --- warning code ---------------------------------------------------------
RACE_NO_FETCH_MISMATCH = "RACE_NO_FETCH_MISMATCH"
RACE_NO_DATASET_MISMATCH = "RACE_NO_DATASET_MISMATCH"
RACE_NO_OUTPUT_MISMATCH = "RACE_NO_OUTPUT_MISMATCH"


class RaceNoMismatchError(Exception):
    """race_no が想定と一致しない場合に raise する.

    Attributes:
        code: warning code (RACE_NO_FETCH_MISMATCH 等)
        requested: ユーザーが指定した race_no
        actual: 実際に取得 / 検出された race_no
        source: どこで検出したか (例: "fetch_race_card", "odds", "prediction")
    """

    def __init__(
        self,
        code: str,
        requested: Optional[int],
        actual: Optional[int],
        *,
        source: str = "",
        venue: str = "",
        message_extra: str = "",
    ) -> None:
        self.code = code
        self.requested = requested
        self.actual = actual
        self.source = source
        self.venue = venue
        msg = (
            f"[{code}] race_no 不一致: requested={requested} "
            f"vs actual={actual}"
        )
        if venue:
            msg += f" (venue={venue})"
        if source:
            msg += f" / source={source}"
        if message_extra:
            msg += f" — {message_extra}"
        super().__init__(msg)


@dataclass
class RaceRequest:
    """リクエストレースの一意識別.

    Fields:
        venue: レース場名
        date: 開催日
        race_no: リクエストされた race_no (ユーザー指定)
        source: 取得元 (例: "cli_predict", "streamlit_ui", "manual_json")
    """

    venue: str
    date: Date
    race_no: int
    source: str = "unknown"

    def race_id_prefix(self) -> str:
        """同一レース判定用の race_id プレフィックスを返す.

        例: 'YYYYMMDD-静岡-5'
        """
        ymd = self.date.strftime("%Y%m%d") if isinstance(self.date, Date) else str(self.date).replace("-", "")
        return f"{ymd}-{self.venue}-{self.race_no}"

    def __str__(self) -> str:
        return f"{self.venue} {self.race_no}R ({self.date})"


# --- 検証 helper ----------------------------------------------------------


def validate_race_no_fetch_match(
    request: RaceRequest,
    fetched_race_no: Optional[int],
    *,
    fetcher_name: str = "fetcher",
) -> None:
    """fetch 直後の race_no 検証.

    Args:
        request: ユーザーリクエスト
        fetched_race_no: 取得データ内の race_no
        fetcher_name: 検出元の fetcher 名 (例: "fetch_race_card")

    Raises:
        RaceNoMismatchError: requested != fetched なら raise
    """
    if fetched_race_no is None:
        # 取得データに race_no が含まれていない場合は検証しない (取得自体が
        # race_no を持たない fetcher は対象外)
        return
    if request.race_no != fetched_race_no:
        raise RaceNoMismatchError(
            RACE_NO_FETCH_MISMATCH,
            requested=request.race_no,
            actual=fetched_race_no,
            source=fetcher_name,
            venue=request.venue,
            message_extra=(
                f"取得対象レースがリクエストとズレています。"
                f"買い目生成へ進みません。"
            ),
        )


def validate_race_no_dataset_match(
    request: RaceRequest,
    *,
    racecard_race_no: Optional[int] = None,
    odds_race_no: Optional[int] = None,
    results_race_no: Optional[int] = None,
) -> None:
    """racecard / odds / results 等の dataset 間で race_no が一致するか検証.

    Args:
        request: ユーザーリクエスト
        racecard_race_no: racecard データ内の race_no
        odds_race_no: odds データ内の race_no
        results_race_no: results データ内の race_no

    Raises:
        RaceNoMismatchError: いずれかが request.race_no と異なれば raise。
            複数不一致なら最初に見つかったものを raise。
    """
    pairs = (
        ("racecard", racecard_race_no),
        ("odds", odds_race_no),
        ("results", results_race_no),
    )
    for label, value in pairs:
        if value is None:
            continue
        if value != request.race_no:
            raise RaceNoMismatchError(
                RACE_NO_DATASET_MISMATCH,
                requested=request.race_no,
                actual=value,
                source=label,
                venue=request.venue,
                message_extra=(
                    f"dataset 間で race_no がズレています "
                    f"(requested={request.race_no}, {label}={value})。"
                    f"買い目生成へ進みません。"
                ),
            )


def validate_race_no_output_match(
    input_race_no: Optional[int],
    prediction_race_no: Optional[int],
) -> Optional[str]:
    """出力時 (build_output_plan / render 等) の最終防衛検証.

    Phase 14 後続2 の _check_race_no_consistency と同じ判定だが、
    code を RACE_NO_OUTPUT_MISMATCH に統一する。

    Args:
        input_race_no: input_data.race.race_no
        prediction_race_no: prediction.race_no

    Returns:
        不一致なら警告メッセージ文字列、一致なら None。
        例外は raise しない (最終防衛として warning に留める)。
    """
    if input_race_no is None or prediction_race_no is None:
        return None
    if input_race_no == prediction_race_no:
        return None
    return (
        f"[{RACE_NO_OUTPUT_MISMATCH}] race_no 不一致: "
        f"input_data.race.race_no={input_race_no} "
        f"vs prediction.race_no={prediction_race_no}。"
        f"入口側の検証 (RACE_NO_FETCH_MISMATCH / RACE_NO_DATASET_MISMATCH) "
        f"を素通りした最終防衛。"
    )
