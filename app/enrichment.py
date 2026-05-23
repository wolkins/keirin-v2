"""取得した外部データを既存の RaceInput に取り込むためのモジュール。

責務:
- fetch-json (外部取得) と predict (予想生成) の間の橋渡し
- 結果データ（envelope / list / RecentResult）の正規化
- 既存 RaceInput.recent_results との重複除去・件数制限・memo 自動生成

予想ロジックや HTTP 取得処理には依存しない。
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Iterable, Optional, Union

from .models import OddsEntry, RaceInput, RecentResult


class EnrichmentError(ValueError):
    """外部取得結果の取り込みに関するエラー。メッセージは日本語で。"""


# source 名 → memo に書く日本語ラベル
_SOURCE_LABELS: dict[str, str] = {
    "kdreams": "Kドリームス結果",
    "oddspark": "オッズパーク結果",
    "manual": "手入力結果",
}

ResultsInput = Union[
    dict[str, Any],  # envelope
    list[dict[str, Any]],
    list[RecentResult],
]


# ---------------------------------------------------------------------------
# 結果データの正規化
# ---------------------------------------------------------------------------


def _format_payout(payout: Any) -> Optional[str]:
    """払戻金フィールドを 'NN,NNN円' 形式の文字列に整形する。"""
    if payout is None:
        return None
    if isinstance(payout, bool):
        return None
    if isinstance(payout, (int, float)):
        n = int(payout)
        if n <= 0:
            return None
        return f"{n:,}円"
    if isinstance(payout, str):
        s = payout.strip()
        if not s:
            return None
        # 文字列で来た場合は素直に末尾円が付いてればそのまま、
        # 数字のみなら整数化してカンマ付与
        digits = "".join(ch for ch in s if ch.isdigit())
        if digits and digits == s.replace(",", ""):
            try:
                n = int(digits)
                if n <= 0:
                    return None
                return f"{n:,}円"
            except ValueError:
                return s
        return s
    return None


def _auto_memo(result_str: str, payout: Any, label: str) -> str:
    payout_str = _format_payout(payout)
    if payout_str:
        return f"{label}: {result_str} / 払戻 {payout_str}"
    return f"{label}: {result_str}"


def _coerce_date(v: Any) -> Optional[Date]:
    """date / YYYY-MM-DD 文字列 / None を Date or None に。"""
    if v is None or v == "":
        return None
    if isinstance(v, Date):
        return v
    if isinstance(v, str):
        try:
            return datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError as e:
            raise EnrichmentError(
                f"results の date は YYYY-MM-DD で指定してください: '{v}'"
            ) from e
    raise EnrichmentError(f"results の date が解釈できません: {v!r}")


def _normalize_one(
    raw: Any,
    *,
    fallback_venue: Optional[str],
    fallback_date: Optional[str],
    label: str,
) -> RecentResult:
    """1件分の結果データを RecentResult に正規化する。"""
    if isinstance(raw, RecentResult):
        # memo が空なら自動生成（payout は再構築できないので result のみ）
        if not raw.memo:
            raw = raw.model_copy(update={"memo": _auto_memo(raw.result, None, label)})
        return raw
    if not isinstance(raw, dict):
        raise EnrichmentError(
            f"results 要素は dict もしくは RecentResult が必要です: {type(raw).__name__}"
        )
    result_str = raw.get("result")
    if not result_str or not isinstance(result_str, str):
        raise EnrichmentError(
            f"results 要素に必須項目 'result' がありません: {raw!r}"
        )
    date_val = _coerce_date(raw.get("date") or fallback_date)
    venue = raw.get("venue") or fallback_venue
    race_no = raw.get("race_no")
    if race_no is not None:
        try:
            race_no = int(race_no)
        except (TypeError, ValueError) as e:
            raise EnrichmentError(
                f"results 要素の race_no が整数ではありません: {raw!r}"
            ) from e
        if not 1 <= race_no <= 12:
            raise EnrichmentError(
                f"results 要素の race_no は1〜12で指定してください: {race_no}"
            )
    payout = raw.get("payout")
    if payout is not None:
        try:
            payout = int(payout)
            if payout < 0:
                payout = None
        except (TypeError, ValueError):
            payout = None
    memo = raw.get("memo")
    if not memo:
        memo = _auto_memo(result_str, payout, label)
    return RecentResult(
        date=date_val,
        venue=venue,
        race_no=race_no,
        result=result_str,
        memo=memo,
        payout=payout,
    )


def _resolve_label_from_envelope(envelope: dict[str, Any], default: str) -> str:
    src = envelope.get("source")
    if isinstance(src, str) and src in _SOURCE_LABELS:
        return _SOURCE_LABELS[src]
    return default


def normalize_results(
    results: ResultsInput,
    *,
    source_label: str = "外部取得結果",
) -> list[RecentResult]:
    """envelope / list を `RecentResult` のリストに正規化する。"""
    if isinstance(results, dict):
        if results.get("kind") and results.get("kind") != "results":
            raise EnrichmentError(
                f"envelope の kind は 'results' を期待していますが '{results.get('kind')}' でした"
            )
        items = results.get("results")
        if items is None:
            raise EnrichmentError(
                "envelope に 'results' フィールドがありません"
            )
        if not isinstance(items, list):
            raise EnrichmentError(
                "envelope の 'results' フィールドが配列ではありません"
            )
        label = _resolve_label_from_envelope(results, source_label)
        fb_venue = results.get("venue")
        fb_date = results.get("date")
        return [
            _normalize_one(
                r, fallback_venue=fb_venue, fallback_date=fb_date, label=label
            )
            for r in items
        ]
    if isinstance(results, list):
        return [
            _normalize_one(
                r, fallback_venue=None, fallback_date=None, label=source_label
            )
            for r in results
        ]
    raise EnrichmentError(
        f"results は envelope dict / list のいずれか必要です: {type(results).__name__}"
    )


# ---------------------------------------------------------------------------
# RaceInput への取り込み
# ---------------------------------------------------------------------------


def _ensure_race_input(input_data: Union[RaceInput, dict]) -> RaceInput:
    if isinstance(input_data, RaceInput):
        return input_data
    if isinstance(input_data, dict):
        try:
            return RaceInput.model_validate(input_data)
        except Exception as e:
            raise EnrichmentError(
                f"input_data が RaceInput スキーマに合致しません: {e}"
            ) from e
    raise EnrichmentError(
        f"input_data は RaceInput または dict が必要です: {type(input_data).__name__}"
    )


def _key(r: RecentResult) -> tuple:
    return (r.venue, r.date, r.race_no, r.result)


def _sort_recent(items: list[RecentResult]) -> list[RecentResult]:
    """date 降順 → race_no 降順で並べる。None は末尾。"""

    def _date_key(r: RecentResult) -> tuple[int, str]:
        if r.date is None:
            return (1, "")
        return (0, r.date.isoformat())

    def _rno_key(r: RecentResult) -> tuple[int, int]:
        if r.race_no is None:
            return (1, 0)
        return (0, -r.race_no)

    # 日付降順は文字列をそのまま比較して reverse=True
    return sorted(
        items,
        key=lambda r: (_date_key(r), _rno_key(r)),
        reverse=False,
    )


_BET_TYPE_GROUP_LABEL: dict[str, str] = {
    "trifecta_popular": "3連単",
    "trio_popular": "3連複",
    "exacta_popular": "2車単",
    # フラット互換用
    "trifecta": "3連単",
    "trio": "3連複",
    "exacta": "2車単",
}


def _coerce_odds_entry(raw: Any, *, default_bet_type: Optional[str] = None) -> OddsEntry:
    """1件分のオッズデータを OddsEntry に正規化する。"""
    if isinstance(raw, OddsEntry):
        return raw
    if not isinstance(raw, dict):
        raise EnrichmentError(
            f"odds 要素は dict もしくは OddsEntry が必要です: {type(raw).__name__}"
        )
    combo = raw.get("combination")
    if not combo or not isinstance(combo, str):
        raise EnrichmentError(
            f"odds 要素に必須項目 'combination' がありません: {raw!r}"
        )
    odds_v = raw.get("odds")
    if odds_v is None:
        raise EnrichmentError(
            f"odds 要素に必須項目 'odds' がありません: {raw!r}"
        )
    try:
        odds_f = float(odds_v)
    except (TypeError, ValueError) as e:
        raise EnrichmentError(
            f"odds 要素の odds が数値ではありません: {raw!r}"
        ) from e
    bet_type = raw.get("bet_type") or default_bet_type
    if isinstance(bet_type, str):
        bt = bet_type.strip().lower()
        if bt in _BET_TYPE_GROUP_LABEL:
            bet_type = _BET_TYPE_GROUP_LABEL[bt]
    if not bet_type:
        raise EnrichmentError(
            f"odds 要素に bet_type を解決できませんでした: {raw!r}"
        )
    try:
        return OddsEntry(bet_type=bet_type, combination=combo, odds=odds_f)
    except Exception as e:
        raise EnrichmentError(f"odds 要素のバリデーションに失敗: {raw!r} ({e})") from e


def normalize_odds(payload: Any) -> list[OddsEntry]:
    """envelope / グループdict / list を `OddsEntry` のリストへ正規化する。"""
    # envelope
    if isinstance(payload, dict) and payload.get("kind") == "odds":
        inner = payload.get("odds")
        if inner is None:
            raise EnrichmentError("envelope に 'odds' フィールドがありません")
        return normalize_odds(inner)

    # グループdict (例: {"trifecta_popular": [...], "trio_popular": [...]})
    if isinstance(payload, dict):
        out: list[OddsEntry] = []
        for key, items in payload.items():
            if not isinstance(items, list):
                raise EnrichmentError(
                    f"odds の '{key}' の値が配列ではありません"
                )
            label = _BET_TYPE_GROUP_LABEL.get(key.lower())
            # 既に label が見つからない key（例えば日本語 '3連単' を直接キーに使う）も許す
            if label is None and key in ("3連単", "3連複", "2車単", "2車複", "ワイド", "単勝", "複勝"):
                label = key
            for raw in items:
                out.append(_coerce_odds_entry(raw, default_bet_type=label))
        return out

    if isinstance(payload, list):
        return [_coerce_odds_entry(r) for r in payload]

    raise EnrichmentError(
        f"odds は envelope dict / グループdict / list のいずれか必要です: {type(payload).__name__}"
    )


def merge_odds(
    input_data: Union[RaceInput, dict],
    odds: Any,
    *,
    replace: bool = True,
) -> RaceInput:
    """odds を input_data.odds にマージし、新しい RaceInput を返す。

    Args:
        input_data: RaceInput または dict
        odds: envelope dict / グループdict / list / list[OddsEntry]
        replace: True なら既存 odds を置換、False なら追記

    Returns:
        マージ後の RaceInput（バリデーション済み）

    Raises:
        EnrichmentError: 入力不正・必須項目欠如
    """
    base = _ensure_race_input(input_data)
    new_items = normalize_odds(odds)

    if replace:
        merged = new_items
    else:
        # 既存 + 新規。combination + bet_type が同じものは新規で上書き
        existing = list(base.odds)
        new_keys = {(o.bet_type, o.combination) for o in new_items}
        merged = [o for o in existing if (o.bet_type, o.combination) not in new_keys]
        merged.extend(new_items)

    return base.model_copy(update={"odds": merged})


def merge_recent_results(
    input_data: Union[RaceInput, dict],
    results: ResultsInput,
    *,
    max_results: Optional[int] = None,
    dedupe: bool = True,
    source_label: str = "外部取得結果",
) -> RaceInput:
    """results を input_data.recent_results にマージし、新しい RaceInput を返す。

    Args:
        input_data: RaceInput または dict
        results: envelope dict / list[dict] / list[RecentResult]
        max_results: 最終件数の上限（date降順→race_no降順で上位を残す）
        dedupe: True なら (venue, date, race_no, result) の重複を除去し、
                **新規が既存を上書き** する（新しい memo / payout 情報を優先）
        source_label: memo 自動生成時のラベル。envelope に source があれば
                それが優先される

    Returns:
        マージ後の RaceInput（バリデーション済み）

    Raises:
        EnrichmentError: 入力不正・必須項目欠如
    """
    base = _ensure_race_input(input_data)
    new_items = normalize_results(results, source_label=source_label)

    existing: list[RecentResult] = list(base.recent_results)

    if dedupe:
        # 新規が既存を上書きする方針
        new_keys = {_key(r) for r in new_items}
        merged: list[RecentResult] = [r for r in existing if _key(r) not in new_keys]
        merged.extend(new_items)
    else:
        merged = existing + new_items

    # 並びを揃える（日付降順）
    sorted_items = sorted(
        merged,
        key=lambda r: (
            -(int(r.date.strftime("%Y%m%d")) if r.date else 0),
            -(r.race_no or 0),
        ),
    )

    if max_results is not None and max_results >= 0:
        sorted_items = sorted_items[:max_results]

    # 新しい RaceInput を構築（recent_results を差し替える）
    return base.model_copy(update={"recent_results": sorted_items})


_RACE_NOTES_SOURCE_LABELS: dict[str, str] = {
    "tospo": "東スポ",
    "winticket": "WINTICKET",
    "netkeirin": "netkeirin",
    "oddspark": "オッズパーク",
    "yenjoy": "yenjoy",
    "manual_text": "手入力",
    "generic": "補助情報",
}


def _source_label(source: Any) -> str:
    """RaceNotes の source 文字列を日本語ラベルに変換。

    後方互換: source 未指定（旧 Tospo パーサの古い戻り値）は「東スポ」を返す。
    未知のソースは「補助情報」。
    """
    s = (str(source) if source else "").strip().lower()
    if not s:
        return _RACE_NOTES_SOURCE_LABELS["tospo"]  # 後方互換
    return _RACE_NOTES_SOURCE_LABELS.get(s, "補助情報")


def merge_race_notes(
    input_data: Union[RaceInput, dict],
    notes: Any,
) -> RaceInput:
    """補助情報源（東スポ/WINTICKET/手入力等）の RaceNotes を RaceInput にマージする。

    notes は以下のいずれか:
      - dict （旧パーサの戻り値）
      - RaceNotes Pydantic モデル
      - None / 空 → base をそのまま返す

    - rider_notes[].car_no が一致したら Rider.comment / style_tags を更新
      - 既存 comment は **上書きせず** 「[<ソースラベル>] 要約」を末尾に追記
      - signals は style_tags に重複なく追加
    - race_summary / prediction_hint / line_hint は user_note に
      「[<ソースラベル>] ...」プレフィックス付きで追記
    """
    base = _ensure_race_input(input_data)
    if not notes:
        return base

    # Pydantic RaceNotes → dict に正規化
    from app.models import RaceNotes  # 遅延 import（循環参照防止）
    if isinstance(notes, RaceNotes):
        notes = notes.model_dump(mode="json", exclude_none=False)

    if not isinstance(notes, dict):
        raise EnrichmentError(
            f"race_notes は dict / RaceNotes 形式である必要があります: {type(notes).__name__}"
        )

    label = _source_label(notes.get("source"))

    rider_notes = notes.get("rider_notes") or []
    if not isinstance(rider_notes, list):
        raise EnrichmentError("race_notes.rider_notes は list である必要があります")

    # rider_notes を car_no で索引化
    notes_by_car: dict[int, dict[str, Any]] = {}
    for n in rider_notes:
        if not isinstance(n, dict):
            continue
        car = n.get("car_no")
        if isinstance(car, int) and 1 <= car <= 9:
            notes_by_car[car] = n

    new_riders = []
    for rider in base.riders:
        note = notes_by_car.get(rider.car_no)
        if note is None:
            new_riders.append(rider)
            continue
        # comment 追記
        summary = (note.get("comment_summary") or "").strip()
        new_comment = rider.comment or ""
        if summary:
            tag_str = f"[{label}] {summary}"
            if new_comment:
                if tag_str not in new_comment:
                    new_comment = f"{new_comment} ／ {tag_str}"
            else:
                new_comment = tag_str
        # tags 追加（重複除去）
        signals = note.get("signals") or []
        new_tags = list(rider.style_tags or [])
        for s in signals:
            if s not in new_tags:
                new_tags.append(s)
        new_riders.append(
            rider.model_copy(update={"comment": new_comment, "style_tags": new_tags})
        )

    # user_note に race_summary / line_hint / prediction_hint を追記
    parts: list[str] = []
    for key in ("race_summary", "line_hint", "prediction_hint"):
        v = (notes.get(key) or "").strip() if isinstance(notes.get(key), str) else ""
        if v:
            parts.append(v)
    new_user_note = base.user_note
    if parts:
        section_str = f"[{label}] " + " / ".join(parts)
        if new_user_note:
            if section_str not in new_user_note:
                new_user_note = f"{new_user_note} ／ {section_str}"
        else:
            new_user_note = section_str

    return base.model_copy(update={"riders": new_riders, "user_note": new_user_note})
