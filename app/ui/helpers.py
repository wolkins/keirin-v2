"""Web UI のロジック層（Streamlit 非依存）。

責務:
- UI から渡された dict (raw inputs) を既存関数の呼び出しに変換する
- 既存関数を import して直接呼ぶ（subprocess で CLI を叩かない）
- エラー時は日本語メッセージに変換して返す

このモジュールは streamlit に依存しないため、pytest で単体テスト可能。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from app import storage as storage_module
from app.cli import render_prediction
from app.enrichment import EnrichmentError
from app.fetchers import FileCache, HttpClient, RateLimiter
from app.fetchers.cache import DEFAULT_CACHE_DIR, DEFAULT_TTL_SECONDS
from app.storage import DEFAULT_DB_PATH

DEFAULT_LLM_PROVIDER = "openai"
from app.llm_client import build_default_client
from app.models import (
    Prediction,
    RaceInput,
    Reflection,
)
from app.preparation import PreparationError, prepare_race_input
from app.prompt_builder import build_full_prompt
from app.reflection import build_reflection
from app.reporting import build_performance_report
from app.scoring import (
    apply_bank_signals,
    apply_f2_signals,
    apply_grade_signals,
    apply_home_area_signals,
    apply_market_signals,
    apply_reflection_signals,
    apply_tospo_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    compute_scores,
    gami_inflation_from_reflections,
)
from app.value_analysis import (
    annotate_prediction_with_value,
    promote_oddful_to_honsen,
    promote_oddful_to_osae,
)


# ---------------------------------------------------------------------------
# 戻り値型
# ---------------------------------------------------------------------------


@dataclass
class PrepareResult:
    """prepare の結果。失敗時は ri=None, warnings/errors に日本語メッセージ。"""

    ri: Optional[RaceInput] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class PredictResult:
    """predict の結果。失敗時は prediction=None。"""

    prediction: Optional[Prediction] = None
    markdown: str = ""
    used_reflections: int = 0
    provider: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ResultSaveResponse:
    reflection: Optional[Reflection] = None
    saved: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 入力解析・エラー整形
# ---------------------------------------------------------------------------


def parse_date_input(value: Any) -> str:
    """UI から渡された日付 (date / datetime / str) を YYYY-MM-DD に正規化。"""
    if isinstance(value, Date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.date().strftime("%Y-%m-%d")
    s = str(value or "").strip()
    if not s:
        raise ValueError("日付が指定されていません (YYYY-MM-DD)")
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(
            f"日付は YYYY-MM-DD 形式で指定してください: '{value}'"
        ) from e
    return s


def format_error_message(exc: BaseException) -> str:
    """例外を日本語の説明文字列に変換する。"""
    name = type(exc).__name__
    text = str(exc).strip()
    if isinstance(exc, ValidationError):
        # Pydantic のエラーは長くなるので、最初の数件をピックアップ
        try:
            errs = exc.errors()
        except Exception:
            return f"スキーマ検証エラー: {text}"
        lines = []
        for e in errs[:5]:
            loc = ".".join(str(x) for x in e.get("loc", []))
            msg = e.get("msg", "")
            lines.append(f"  - {loc}: {msg}")
        more = "" if len(errs) <= 5 else f" ほか{len(errs) - 5}件"
        return f"JSON スキーマ検証エラー（{len(errs)}件{more}）:\n" + "\n".join(lines)
    if isinstance(exc, PreparationError):
        return f"取得・準備に失敗しました: {text}"
    if isinstance(exc, EnrichmentError):
        return f"データ取り込みエラー: {text}"
    if isinstance(exc, json.JSONDecodeError):
        return f"JSON の構文エラー: {text}"
    if isinstance(exc, FileNotFoundError):
        return f"ファイルが見つかりません: {text}"
    return f"エラー ({name}): {text}"


# ---------------------------------------------------------------------------
# JSON バリデーション / ダンプ
# ---------------------------------------------------------------------------


def validate_uploaded_json(text: str) -> tuple[Optional[RaceInput], list[str]]:
    """アップロードされた JSON 文字列を RaceInput に変換する。

    Returns:
        (RaceInput or None, errors)
    """
    if not text or not text.strip():
        return None, ["JSON が空です。"]
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        return None, [format_error_message(e)]
    try:
        ri = RaceInput.model_validate(raw)
    except ValidationError as e:
        return None, [format_error_message(e)]
    return ri, []


def race_input_to_json_text(ri: RaceInput) -> str:
    """RaceInput を整形済み JSON 文字列に変換（UTF-8、indent=2）。"""
    raw = json.loads(ri.model_dump_json())
    return json.dumps(raw, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# build_*_kwargs: UI 入力 → 既存関数 kwargs
# ---------------------------------------------------------------------------


def build_prepare_kwargs(inputs: dict[str, Any]) -> dict[str, Any]:
    """UI からの inputs dict を prepare_race_input の kwargs に変換。

    必須: source / venue / date / race_no
    任意: weather, rain, wind_direction, wind_speed, wind_note, weather_source,
          start_time, session_no, bank_note, bank_length, bank_style,
          include_results, results_race_no, max_results,
          include_odds, odds_bet_type, odds_limit, odds_source
    """
    out: dict[str, Any] = {
        "source": str(inputs.get("source") or "manual").lower(),
        "venue": inputs.get("venue") or "",
        "date_str": parse_date_input(inputs.get("date")),
        "race_no": int(inputs.get("race_no") or 0),
    }
    # optional フィールド
    for key in (
        "weather", "rain", "wind_direction", "wind_speed", "wind_note",
        "weather_source", "start_time", "bank_note", "bank_length", "bank_style",
        "results_race_no", "max_results",
        "odds_bet_type", "odds_limit", "odds_source",
        "tospo_url",
    ):
        if key in inputs and inputs[key] not in (None, ""):
            out[key] = inputs[key]
    # bool フラグ
    if "include_results" in inputs:
        out["include_results"] = bool(inputs["include_results"])
    if "include_odds" in inputs:
        out["include_odds"] = bool(inputs["include_odds"])
    if "include_tospo_notes" in inputs:
        out["include_tospo_notes"] = bool(inputs["include_tospo_notes"])
    if "session_no" in inputs and inputs["session_no"] not in (None, ""):
        out["session_no"] = int(inputs["session_no"])
    return out


def build_predict_kwargs(inputs: dict[str, Any]) -> dict[str, Any]:
    """UI inputs から predict 用のオプション dict を取り出す。"""
    return {
        "provider": str(inputs.get("provider") or DEFAULT_LLM_PROVIDER).lower(),
        "use_reflections": bool(inputs.get("use_reflections", True)),
        "reflection_limit": int(inputs.get("reflection_limit") or 5),
        "value_analysis": bool(inputs.get("value_analysis", True)),
        "save": bool(inputs.get("save", False)),
    }


# ---------------------------------------------------------------------------
# prepare / predict のラッパー
# ---------------------------------------------------------------------------


def _build_http_client(
    *, use_cache: bool = True, cache_ttl: Optional[int] = None,
    rate_limit_seconds: float = 1.0,
    refresh_cache: bool = False,
) -> HttpClient:
    cache = FileCache(
        cache_dir=DEFAULT_CACHE_DIR,
        ttl_seconds=int(cache_ttl) if cache_ttl else DEFAULT_TTL_SECONDS,
        enabled=use_cache,
    )
    rl = RateLimiter(min_interval_seconds=float(rate_limit_seconds))
    return HttpClient(
        cache=cache, rate_limiter=rl, force_refresh=bool(refresh_cache),
    )


def prepare_from_ui_inputs(
    inputs: dict[str, Any],
    *,
    http_client: Optional[HttpClient] = None,
) -> PrepareResult:
    """UI inputs から prepare_race_input を呼ぶ。

    既存 CLI のパイプラインをそのまま使う。HTTP は HttpClient 共有。

    auto_session_search=True なら、SYSTEM_ERROR で失敗した場合に
    session_no を 2,3,4,5 と順に試して見つかれば使う。
    """
    auto_search = bool(inputs.get("auto_session_search", False))

    if auto_search:
        # 「予想したい日」を起点に、(target - (s-1)日 を初日, session_no=s) の
        # 組み合わせを s=1..5 で試す。最初に成功したものを返す。
        from datetime import timedelta
        try:
            target_date = parse_date_input(inputs.get("date"))
        except ValueError as e:
            r = PrepareResult()
            r.errors.append(format_error_message(e))
            return r
        target_dt = datetime.strptime(target_date, "%Y-%m-%d").date()

        attempted: list[str] = []
        last_result: Optional[PrepareResult] = None
        for s in (1, 2, 3, 4, 5):
            initial_date = target_dt - timedelta(days=s - 1)
            attempt_inputs = dict(inputs)
            attempt_inputs["date"] = initial_date.strftime("%Y-%m-%d")
            attempt_inputs["session_no"] = s
            # 自動探索中は no_meet_index を回避（古い記録に阻まれない）
            attempt_inputs["refresh_cache"] = True
            attempt_label = f"(date={attempt_inputs['date']}, session={s})"
            attempted.append(attempt_label)
            r = _do_prepare(attempt_inputs, http_client=http_client)
            if r.ri is not None:
                # race.date / race.race_id は「予想したい日」に書き換える
                # （Kドリームス URL は内部の session_no で正しく組まれているので無影響）
                ri = r.ri
                if ri.race.date != target_dt:
                    new_race_id = (
                        f"{target_dt.strftime('%Y%m%d')}-{ri.race.venue}-"
                        f"{ri.race.race_no}"
                    )
                    ri.race = ri.race.model_copy(update={
                        "date": target_dt,
                        "race_id": new_race_id,
                    })
                    r.ri = ri
                r.warnings.append(
                    f"[案内] 自動探索成功: 初日={attempt_inputs['date']} / "
                    f"session_no={s}（{s}日目=予想したい日 {target_date}）"
                )
                return r
            last_result = r

        # 全部失敗
        if last_result is not None:
            last_result.errors.append(
                "自動探索 (初日逆算) で以下を試しましたがすべて失敗しました:\n"
                + "\n".join(f"  - {a}" for a in attempted)
                + "\n→ 場名・日付・開催の有無を確認してください"
            )
            return last_result

    return _do_prepare(inputs, http_client=http_client)


def _do_prepare(
    inputs: dict[str, Any],
    *,
    http_client: Optional[HttpClient] = None,
) -> PrepareResult:
    """単発の prepare 実行。auto_session_search なしのパス。"""
    result = PrepareResult()
    try:
        kwargs = build_prepare_kwargs(inputs)
    except ValueError as e:
        result.errors.append(format_error_message(e))
        return result

    client = http_client or _build_http_client(
        use_cache=bool(inputs.get("use_cache", True)),
        cache_ttl=inputs.get("cache_ttl"),
        rate_limit_seconds=float(inputs.get("rate_limit_seconds", 1.0)),
        refresh_cache=bool(inputs.get("refresh_cache", False)),
    )

    # 開催なしインデックスを尊重（refresh_cache か no-cache 時はスキップ）
    if (
        not inputs.get("refresh_cache")
        and bool(inputs.get("use_cache", True))
    ):
        from app.no_meet_index import NoMeetIndex
        from app.fetchers.cache import DEFAULT_CACHE_DIR
        no_meet = NoMeetIndex(DEFAULT_CACHE_DIR)
        if no_meet.is_known_no_meet(
            kwargs["venue"], kwargs["date_str"], int(kwargs.get("session_no", 1)),
        ):
            result.warnings.append(
                f"「開催なし」が記録済み: {kwargs['venue']} {kwargs['date_str']} "
                f"(session_no={kwargs.get('session_no', 1)})。"
                "強制再取得するには「強制再取得 (refresh-cache)」を ON にしてください。"
            )
            return result

    fallback = inputs.get("fallback_input")
    fallback_path = Path(fallback) if fallback else None

    def _warn(msg: str) -> None:
        result.warnings.append(msg)

    try:
        ri = prepare_race_input(
            http_client=client,
            fallback_input=fallback_path,
            warn=_warn,
            **kwargs,
        )
    except PreparationError as e:
        err_msg = format_error_message(e)
        result.errors.append(err_msg)
        # 「開催なし」エラー検出時はインデックスに記録
        if "SYSTEM_ERROR" in str(e) or "開催が無い" in str(e):
            from app.no_meet_index import NoMeetIndex
            from app.fetchers.cache import DEFAULT_CACHE_DIR
            try:
                NoMeetIndex(DEFAULT_CACHE_DIR).record_no_meet(
                    kwargs["venue"], kwargs["date_str"],
                    int(kwargs.get("session_no", 1)),
                )
                result.warnings.append(
                    "「開催なし」をインデックスに記録しました。"
                    "次回以降は通信せずに即時スキップされます（強制再取得で解除）。"
                )
            except Exception:
                pass
        return result
    except Exception as e:  # 想定外の例外も日本語化
        result.errors.append(format_error_message(e))
        return result
    result.ri = ri
    return result


def predict_from_race_input(
    ri: RaceInput,
    *,
    provider: str = DEFAULT_LLM_PROVIDER,
    use_reflections: bool = True,
    reflection_limit: int = 5,
    value_analysis: bool = True,
    save: bool = False,
    db_path: Optional[Path] = None,
    bet_budget: Optional[int] = None,
) -> PredictResult:
    """RaceInput から予想を生成する（CLI predict 相当）。

    bet_budget: 目標合計買い目点数（10〜30）。None で既定 (合計~20点)。
    """
    out = PredictResult(provider=provider)
    try:
        # 反省ログを取得
        if use_reflections:
            store = storage_module.Storage(db_path or DEFAULT_DB_PATH)
            reflections = store.get_relevant_reflections(
                ri, limit=int(reflection_limit)
            )
            out.used_reflections = len(reflections)
        else:
            reflections = []

        # スコアリング・補正パイプライン
        scores = compute_scores(ri)
        apply_reflection_signals(scores, reflections, ri)
        apply_bank_signals(scores, ri)
        apply_wind_extra_signals(scores, ri)
        apply_trend_signals(scores, ri)
        apply_tospo_signals(scores, ri)
        apply_grade_signals(scores, ri)
        apply_f2_signals(scores, ri)
        apply_home_area_signals(scores, ri)
        apply_market_signals(scores, ri.odds)

        bets = build_candidate_bets(
            ri, scores,
            gami_inflation=gami_inflation_from_reflections(reflections),
            target_total=bet_budget,
        )

        prompt = build_full_prompt(ri, scores, bets, reflections=reflections)
        client = build_default_client(provider)
        prediction = client.generate_prediction(ri, scores, bets, prompt)

        if value_analysis:
            annotate_prediction_with_value(prediction, scores, ri.odds)
            promote_oddful_to_osae(prediction)
            promote_oddful_to_honsen(prediction)

        if save:
            store = storage_module.Storage(db_path or DEFAULT_DB_PATH)
            store.save_prediction(prediction)

        out.prediction = prediction
        out.markdown = render_prediction(prediction)
    except Exception as e:
        out.errors.append(format_error_message(e))
    return out


def prediction_to_markdown(p: Prediction) -> str:
    """Prediction を Markdown に変換（既存 render_prediction を流用）。"""
    return render_prediction(p)


# ---------------------------------------------------------------------------
# 結果入力
# ---------------------------------------------------------------------------


def save_result_from_ui(
    *,
    race_id: str,
    result_str: str,
    input_json_text: Optional[str] = None,
    note: str = "",
    db_path: Optional[Path] = None,
) -> ResultSaveResponse:
    """結果を保存し、反省ログを生成する。

    Args:
        race_id: 予想時の race_id
        result_str: '1-3-7' のような結果文字列
        input_json_text: アップロード/現在の入力JSON（あれば反省カテゴリの精度向上に使う）
        note: 自由メモ
    """
    resp = ResultSaveResponse()
    if not race_id or not race_id.strip():
        resp.errors.append("race_id が空です。")
        return resp
    if not result_str or not result_str.strip():
        resp.errors.append("結果が空です（例: 1-3-7）。")
        return resp

    input_data: Optional[RaceInput] = None
    if input_json_text:
        input_data, errs = validate_uploaded_json(input_json_text)
        if errs:
            resp.warnings.extend(errs)

    store = storage_module.Storage(db_path or DEFAULT_DB_PATH)
    prediction = store.get_prediction(race_id)
    if prediction is None:
        resp.errors.append(
            f"予想が見つかりません: race_id={race_id}（先に予想を生成してください）"
        )
        return resp

    try:
        store.save_result(race_id, result_str.strip())
        reflection = build_reflection(
            prediction=prediction,
            actual_result=result_str.strip(),
            input_data=input_data,
            note=note,
        )
        store.save_reflection(reflection)
        resp.reflection = reflection
        resp.saved = True
    except Exception as e:
        resp.errors.append(format_error_message(e))
    return resp


# ---------------------------------------------------------------------------
# 反省ログ・成績レポート
# ---------------------------------------------------------------------------


def parse_and_merge_race_notes_text(
    *,
    text: str,
    source: str,
    venue: Optional[str],
    date_str: Optional[str],
    race_no: Optional[int],
    ri: RaceInput,
) -> tuple[Optional[RaceInput], list[str]]:
    """手入力テキストから RaceNotes をパースして RaceInput にマージ。

    Returns: (merged_RaceInput or None, errors)
    """
    from app.enrichment import EnrichmentError, merge_race_notes
    from app.race_notes import ManualTextParseError, parse_race_notes_text

    if not text or not text.strip():
        return None, ["テキストが空です。"]
    try:
        notes = parse_race_notes_text(
            text, source=source, venue=venue, date=date_str, race_no=race_no,
        )
    except ManualTextParseError as e:
        return None, [format_error_message(e)]
    try:
        merged = merge_race_notes(ri, notes)
    except EnrichmentError as e:
        return None, [format_error_message(e)]
    return merged, []


def save_race_input_to_disk(
    ri: RaceInput,
    *,
    base_dir: Optional[Path] = None,
) -> Path:
    """RaceInput を tmp/{venue}_{date}_{NN}r.json に保存する。

    既存ファイルがあれば上書き。保存先パスを返す。
    venue / date / race_no は ri.race から取得。
    """
    tmp_dir = (base_dir or Path("tmp")).resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)

    venue = ri.race.venue or "race"
    date_str = ri.race.date.strftime("%Y-%m-%d")
    race_no = int(ri.race.race_no)
    # 危険文字を取り除く（OS互換性確保）
    import re
    safe_venue = re.sub(r"[^\w぀-ヿ一-鿿]+", "", venue) or "race"
    out_path = tmp_dir / f"{safe_venue}_{date_str}_{race_no:02d}r.json"

    text = race_input_to_json_text(ri)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def find_existing_race_input(
    venue: str,
    date_str: str,
    race_no: int,
    *,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """サイドバー設定（場名/日付/R番号）から、ディスクの既存 JSON パスを探す。

    探索パス（順番に確認）:
    1. tmp/{venue}_{date}_{NN}r.json （prepare-json 自動命名）
    2. tmp/{venue}_{date}_{N}r.json （0埋め無し）

    見つからなければ None。
    """
    if not venue or not date_str or not race_no:
        return None
    tmp_dir = (base_dir or Path("tmp")).resolve()
    if not tmp_dir.exists():
        return None
    # 0埋め2桁 (CLI が生成するパターン) を最優先
    candidate = tmp_dir / f"{venue}_{date_str}_{int(race_no):02d}r.json"
    if candidate.exists():
        return candidate
    # 0埋め無し
    candidate2 = tmp_dir / f"{venue}_{date_str}_{int(race_no)}r.json"
    if candidate2.exists():
        return candidate2
    return None


def list_existing_race_inputs(
    *,
    base_dir: Optional[Path] = None,
    limit: int = 30,
) -> list[Path]:
    """tmp/ 配下の RaceInput JSON ファイル一覧を新しい順で返す（最大 limit 件）。"""
    tmp_dir = (base_dir or Path("tmp")).resolve()
    if not tmp_dir.exists():
        return []
    files = sorted(
        tmp_dir.glob("*r.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[:limit]


def list_recent_predictions(
    *,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> list[tuple[str, str]]:
    """DB に保存されている直近の予想 (race_id, 表示用ラベル) を返す。

    UI のセレクトボックス用。created_at DESC 順。
    """
    store = storage_module.Storage(db_path or DEFAULT_DB_PATH)
    preds = store.list_predictions(limit=int(limit))
    out: list[tuple[str, str]] = []
    for p in preds:
        # 表示ラベル: "20260522-平塚-6 (平塚 6R)" のような形
        venue = p.venue or ""
        race_no = p.race_no
        label = f"{p.race_id}  ({venue} {race_no}R)" if venue else p.race_id
        out.append((p.race_id, label))
    return out


def list_reflections_from_ui(
    *,
    venue: Optional[str] = None,
    weather_condition: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> list[Reflection]:
    """UI のフィルタから反省ログを取得。"""
    store = storage_module.Storage(db_path or DEFAULT_DB_PATH)
    return store.list_reflections(
        venue=venue or None,
        weather_condition=weather_condition or None,
        limit=int(limit),
    )


def build_report_from_ui_filters(
    *,
    venue: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    weather_condition: Optional[str] = None,
    limit_reflections: int = 10,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """UI フィルタから成績レポート dict を作る。"""
    store = storage_module.Storage(db_path or DEFAULT_DB_PATH)
    return build_performance_report(
        store,
        venue=venue or None,
        from_date=from_date or None,
        to_date=to_date or None,
        weather_condition=weather_condition or None,
        limit_reflections=int(limit_reflections),
    )
