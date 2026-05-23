"""LLMクライアントの抽象化レイヤー。

ルール:
- 生HTMLをLLMに渡さない（呼び出し側が必ず構造化JSONを渡す）
- 的中保証/回収率保証のような表現は出力しない
- APIキーは絶対にコードに直書きしない（config 経由で .env / env-var から取る）
- API呼び出し失敗時は日本語の警告を出して MockLLMClient へフォールバックする
"""

from __future__ import annotations

import json
import re
import sys
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from .config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    SUPPORTED_PROVIDERS,
    Settings,
    load_settings,
)
from .models import BetRecommendation, Prediction, RaceInput, RiderScore
from .scoring import build_line_position_map, build_marks


WarningEmitter = Callable[[str], None]


def _default_warn(msg: str) -> None:
    """既定の警告出力（stderrへ日本語で）。"""
    print(msg, file=sys.stderr)


class LLMClient(ABC):
    """LLMクライアントの基本インタフェース。"""

    @abstractmethod
    def generate_prediction(
        self,
        input_data: RaceInput,
        scores: list[RiderScore],
        candidate_bets: dict[str, list[BetRecommendation]],
        prompt: str,
    ) -> Prediction:
        """予想を生成する。"""


# ---------------------------------------------------------------------------
# Mock 実装：API を呼ばず決定論的に Prediction を組み立てる
# ---------------------------------------------------------------------------


class MockLLMClient(LLMClient):
    """API呼び出しなしで動く決定論的クライアント。

    スコアと候補買い目を Prediction にまとめ、文章部分はテンプレート出力する。
    実LLMが失敗した時のフォールバック先でもある。
    """

    def generate_prediction(
        self,
        input_data: RaceInput,
        scores: list[RiderScore],
        candidate_bets: dict[str, list[BetRecommendation]],
        prompt: str,
    ) -> Prediction:
        return _build_deterministic_prediction(input_data, scores, candidate_bets)


def _build_deterministic_prediction(
    input_data: RaceInput,
    scores: list[RiderScore],
    candidate_bets: dict[str, list[BetRecommendation]],
) -> Prediction:
    race = input_data.race
    is_girls = race.resolved_is_girls()
    weather = input_data.weather

    summary = (
        f"{race.date} {race.venue}{race.race_no}R {race.class_name}"
        f"{'（ガールズ）' if is_girls else ''}。"
    )
    if race.start_time:
        summary += f" 発走 {race.start_time}。"
    if race.bank_note:
        summary += f" {race.bank_note}"

    # 数値不足モード（競走得点・B数・決まり手が全部 0）の警告
    from .scoring import detect_score_data_insufficient
    if detect_score_data_insufficient(input_data):
        summary = (
            "[警告] 競走得点・B数・決まり手が未取得のため、"
            "オッズと脚質コメントを強めに参照しています。"
            "出走表が完全に取得できているか確認してください。\n\n"
            + summary
        )

    venue_trend_text = "直近結果からの強い偏りは見られない。"
    if input_data.venue_trend:
        venue_trend_text = input_data.venue_trend.note
        if input_data.venue_trend.favors:
            venue_trend_text += "（傾向タグ: " + ", ".join(input_data.venue_trend.favors) + "）"
    elif input_data.recent_results:
        memos = [r.memo for r in input_data.recent_results if r.memo]
        if memos:
            venue_trend_text = "直近メモ: " + " / ".join(memos[:3])

    if weather is None:
        weather_text = "天候情報なし。風雨補正は行わない。"
    else:
        parts = [f"天候: {weather.condition}"]
        if weather.rain_mm_per_hour > 0:
            parts.append(f"雨量 {weather.rain_mm_per_hour:.1f}mm/h")
        if weather.wind_speed_mps > 0:
            parts.append(
                f"風 {weather.wind_direction or '不明'} {weather.wind_speed_mps:.1f}m/s"
            )
        if weather.wind_note:
            parts.append(weather.wind_note)
        weather_text = " / ".join(parts)
        if weather.wind_speed_mps >= 5.0:
            weather_text += "\n→ 強風帯。番手差し・3番手残り・別線番手・追込を加点。先行末脚リスクを意識。"
        if weather.rain_mm_per_hour >= 1.0:
            weather_text += "\n→ 雨天。前々・番手・3番手・内突き・追走を加点。"

    if is_girls:
        lines_text = "並びなし（ガールズは個人戦扱い）。位置取り・直近着順・得点・自力・安定感で評価。"
    else:
        pos_map = build_line_position_map(input_data.lines)
        chunks = []
        for line in input_data.lines:
            desc = line.description or "-".join(str(c) for c in line.cars)
            chunks.append(f"[{line.line_name}] {desc}")
        lines_text = " / ".join(chunks) if chunks else "ライン情報なし"
        _ = pos_map

    # 本命ライン構造を反映した印（◎=top1, ○=main_second, ▲=main_third or 別線最強）
    # candidate_bets を渡して、△以下を市場+本線出現で重み付けする
    marks = build_marks(scores, input_data, candidate_bets=candidate_bets)
    final_conclusion = _build_final_conclusion(
        scores=scores, candidate_bets=candidate_bets, is_girls=is_girls,
        marks=marks,
    )
    gami_memo = _build_gami_memo(candidate_bets)
    reflection_points = _default_reflection_points(is_girls=is_girls, weather=weather)

    return Prediction(
        race_id=race.race_id,
        venue=race.venue,
        race_no=race.race_no,
        is_girls=is_girls,
        summary=summary,
        venue_trend_text=venue_trend_text,
        weather_text=weather_text,
        lines_text=lines_text,
        marks=marks,
        honsen=candidate_bets.get("本線", []),
        osae=candidate_bets.get("押さえ", []),
        ana=candidate_bets.get("穴", []),
        ooana=candidate_bets.get("大穴", []),
        final_conclusion=final_conclusion,
        gami_memo=gami_memo,
        reflection_points=reflection_points,
        rider_scores=scores,
    )


def _build_final_conclusion(
    *,
    scores: list[RiderScore],
    candidate_bets: dict[str, list[BetRecommendation]],
    is_girls: bool,
    marks: Optional[dict[str, int]] = None,
) -> str:
    """最終結論を組み立てる。

    『対抗』は単純なスコア2位ではなく、印の○（marks["◯"]）または
    本線1点目の2着車番に一致させる。
    こうすることで「印では○9なのに最終結論文では対抗7」のような矛盾を防ぐ。
    """
    if not scores:
        return "選手情報が不足しているため結論は出せない。"
    ordered = sorted(scores, key=lambda x: x.total(), reverse=True)
    by_car = {s.car_no: s for s in scores}
    top = ordered[0]

    # 対抗の決定: 優先順位 = 印の○ > 本線1点目の2着車 > スコア2位
    second_car: Optional[int] = None
    if marks and marks.get("◯") and marks["◯"] != top.car_no:
        second_car = marks["◯"]
    if second_car is None:
        honsen_bets = candidate_bets.get("本線", [])
        if honsen_bets:
            parts = honsen_bets[0].combination.split("-")
            if len(parts) >= 2:
                try:
                    cand = int(parts[1])
                    if cand != top.car_no:
                        second_car = cand
                except ValueError:
                    pass
    if second_car is None and len(ordered) > 1:
        second_car = ordered[1].car_no

    second = by_car.get(second_car) if second_car else None
    msg = f"スコア最上位は{top.car_no}番({top.name})。"
    if second:
        msg += f"対抗は{second.car_no}番({second.name})。"
    honsen = candidate_bets.get("本線", [])
    if honsen:
        msg += " 本線は " + ", ".join(b.combination for b in honsen) + " を中心に据える。"
    ana = candidate_bets.get("穴", [])
    if ana:
        msg += " 配当狙いとして " + ", ".join(b.combination for b in ana[:2]) + " を少額で残す。"
    if is_girls:
        msg += " ガールズなのでラインに依存せず個別の安定感で組み立てる。"
    return msg


def _build_gami_memo(candidate_bets: dict[str, list[BetRecommendation]]) -> str:
    gamis: list[str] = []
    for cat in ("本線", "押さえ"):
        for b in candidate_bets.get(cat, []):
            if b.gami_risk >= 0.6:
                gamis.append(f"{b.combination}({cat}): オッズ安め、ガミ警戒")
            elif b.gami_risk >= 0.4:
                gamis.append(f"{b.combination}({cat}): やや低配当、点数を絞る")
    if not gamis:
        return "ガミリスクは低め。点数を増やしすぎないことを意識。"
    return "\n".join("- " + g for g in gamis)


def _default_reflection_points(*, is_girls: bool, weather) -> list[str]:
    # ガールズと通常戦で完全に分岐（ライン表現を混ぜない）
    if is_girls:
        pts = [
            "本線が外れた場合は中穴2着・追走型3着の評価が低くなかったか確認",
            "本命1着固定に寄りすぎて対抗頭の波乱を軽視していないか点数を見直す",
            "穴/大穴を広げすぎてガミになっていないか確認",
            "得点・直近着順・位置取り・自力/追走の重みづけが妥当だったかを再評価",
        ]
        if weather and weather.wind_speed_mps >= 5.0:
            pts.append("強風時に前々型/追走型の評価が妥当だったか検証")
        if weather and weather.rain_mm_per_hour >= 1.0:
            pts.append("雨時に前々に踏める選手の評価が妥当だったか確認")
        return pts

    # 通常戦（ライン有）
    pts = [
        "本線が外れた場合は別線番手と3番手の2-3着上がりを軽視していないか確認",
        "穴/大穴を広げすぎてガミになっていないか点数を見直す",
        "市場人気が特定頭・特定ラインに集中している場合、候補昇格が十分だったか確認",
    ]
    if weather and weather.wind_speed_mps >= 5.0:
        pts.append("強風時の先行残り/番手差しのバランスが妥当だったか検証")
    if weather and weather.rain_mm_per_hour >= 1.0:
        pts.append("雨補正が足りていたか（前々・番手・3番手の評価）を確認")
    return pts


# ---------------------------------------------------------------------------
# 実LLM応答（JSON）→ Prediction へのマージ
# ---------------------------------------------------------------------------


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """応答テキストからJSONオブジェクトを抽出してdictで返す。

    コードフェンスや前後の説明文が混ざっていても、最初のオブジェクトを取り出す。
    抽出/パースに失敗した場合は ValueError を投げる。
    """
    if not text:
        raise ValueError("空のレスポンス")
    candidate = text.strip()
    # コードフェンスを除去
    if candidate.startswith("```"):
        # ```json ... ``` のような場合の中身を抜く
        candidate = re.sub(r"^```[a-zA-Z]*\n", "", candidate)
        candidate = re.sub(r"\n```\s*$", "", candidate)
    # まず全体パースを試す
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # 失敗したら本文から { ... } を抜き出す
    m = _JSON_BLOCK_RE.search(candidate)
    if not m:
        raise ValueError("JSONオブジェクトが応答に含まれていません")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("JSONがオブジェクトではありません")
    return obj


def _coerce_bets(items: Any, category: str) -> list[BetRecommendation]:
    """LLM応答の bets 配列を BetRecommendation のリストに変換する。"""
    if not isinstance(items, list):
        return []
    out: list[BetRecommendation] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        combo = raw.get("combination")
        reason = raw.get("reason", "")
        if not combo:
            continue
        try:
            out.append(
                BetRecommendation(
                    category=category,  # type: ignore[arg-type]
                    bet_type=str(raw.get("bet_type") or "3連単"),
                    combination=str(combo),
                    reason=str(reason),
                    gami_risk=float(raw.get("gami_risk") or 0.0),
                )
            )
        except Exception:
            continue
    return out


def _merge_llm_response(
    base: Prediction, payload: dict[str, Any]
) -> Prediction:
    """決定論的に組んだ Prediction に LLM応答を上書きマージする。

    **マージ対象は文章化フィールドのみ**:
      summary / venue_trend_text / weather_text / lines_text
      final_conclusion / gami_memo / reflection_points

    **以下は LLM 応答で上書きしない（アプリ側の決定論的計算結果を保持）**:
      marks（印）
      rider_scores（スコア）
      honsen / osae / ana / ooana（買い目候補）

    買い目を LLM に任せると、ライン構造優先のロジックや上限制御が無視されたり、
    別線スコア反映が崩れる。**買い目はアプリ側で固定**し、LLM には文章化と
    最終結論のテキスト調整だけを任せる方針。
    """
    merged = base.model_copy(deep=True)
    for key in (
        "summary",
        "venue_trend_text",
        "weather_text",
        "lines_text",
        "final_conclusion",
        "gami_memo",
    ):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            setattr(merged, key, val.strip())

    pts = payload.get("reflection_points")
    if isinstance(pts, list):
        merged.reflection_points = [str(x) for x in pts if str(x).strip()]

    # 買い目 (honsen/osae/ana/ooana) は LLM 応答で上書きしない。
    # LLM が誤って bets を再生成しても無視する。
    return merged


# ---------------------------------------------------------------------------
# フォールバック共通
# ---------------------------------------------------------------------------


class _FallbackMixin:
    """API失敗時のMockフォールバック処理を共通化するミックスイン。"""

    provider_label: str = "LLM"

    def __init__(self, *, warn: WarningEmitter = _default_warn) -> None:
        self._warn = warn

    def _fallback(
        self,
        input_data: RaceInput,
        scores: list[RiderScore],
        candidate_bets: dict[str, list[BetRecommendation]],
        reason: str,
    ) -> Prediction:
        self._warn(
            f"[警告] {self.provider_label} 呼び出しに失敗したため Mock にフォールバックします: {reason}"
        )
        return MockLLMClient().generate_prediction(
            input_data, scores, candidate_bets, prompt=""
        )


# ---------------------------------------------------------------------------
# OpenAI 実装
# ---------------------------------------------------------------------------


class OpenAIClient(_FallbackMixin, LLMClient):
    """OpenAI API でJSON応答をもらい、Predictionにマージする。"""

    provider_label = "OpenAI"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        warn: WarningEmitter = _default_warn,
    ) -> None:
        super().__init__(warn=warn)
        self.api_key = api_key
        self.model = model or DEFAULT_OPENAI_MODEL

    def generate_prediction(
        self,
        input_data: RaceInput,
        scores: list[RiderScore],
        candidate_bets: dict[str, list[BetRecommendation]],
        prompt: str,
    ) -> Prediction:
        base = _build_deterministic_prediction(input_data, scores, candidate_bets)
        if not self.api_key:
            return self._fallback(
                input_data,
                scores,
                candidate_bets,
                "OPENAI_API_KEY が設定されていません",
            )
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return self._fallback(
                input_data,
                scores,
                candidate_bets,
                "openai パッケージが見つかりません (`pip install openai`)",
            )
        try:
            client = OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "あなたは競輪予想の文章化担当です。"
                            "買い目（honsen/osae/ana/ooana）は **アプリ側で固定済みのため**、"
                            "あなたは **絶対に書き換えないでください**。"
                            "印（marks）も書き換えません。"
                            "あなたの仕事は、与えられた候補を踏まえた **文章化と最終結論の整理だけ** です。"
                            "新しい combination を作らないこと。すべての出力は日本語のJSONで返してください。"
                            "的中保証・回収率保証の表現は禁止です。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content or ""
        except Exception as e:  # ネットワーク/認証/レート など
            return self._fallback(
                input_data, scores, candidate_bets, f"API例外: {type(e).__name__}: {e}"
            )
        try:
            payload = _extract_json(content)
        except (ValueError, json.JSONDecodeError) as e:
            return self._fallback(
                input_data, scores, candidate_bets, f"JSON応答のパース失敗: {e}"
            )
        return _merge_llm_response(base, payload)


# ---------------------------------------------------------------------------
# Anthropic 実装
# ---------------------------------------------------------------------------


class AnthropicClient(_FallbackMixin, LLMClient):
    """Anthropic Messages API でJSON応答をもらい、Predictionにマージする。"""

    provider_label = "Anthropic"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        warn: WarningEmitter = _default_warn,
    ) -> None:
        super().__init__(warn=warn)
        self.api_key = api_key
        self.model = model or DEFAULT_ANTHROPIC_MODEL

    def generate_prediction(
        self,
        input_data: RaceInput,
        scores: list[RiderScore],
        candidate_bets: dict[str, list[BetRecommendation]],
        prompt: str,
    ) -> Prediction:
        base = _build_deterministic_prediction(input_data, scores, candidate_bets)
        if not self.api_key:
            return self._fallback(
                input_data,
                scores,
                candidate_bets,
                "ANTHROPIC_API_KEY が設定されていません",
            )
        try:
            import anthropic  # type: ignore
        except ImportError:
            return self._fallback(
                input_data,
                scores,
                candidate_bets,
                "anthropic パッケージが見つかりません (`pip install anthropic`)",
            )
        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=(
                    "あなたは競輪予想の文章化担当です。"
                    "買い目（honsen/osae/ana/ooana）は **アプリ側で固定済みのため**、"
                    "あなたは **絶対に書き換えないでください**。"
                    "印（marks）も書き換えません。"
                    "あなたの仕事は、与えられた候補を踏まえた **文章化と最終結論の整理だけ** です。"
                    "新しい combination を作らないこと。"
                    "出力は日本語のJSONオブジェクトのみで、コードフェンスや説明文を含めないでください。"
                    "的中保証・回収率保証の表現は禁止です。"
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            # content は TextBlock のリスト
            parts: list[str] = []
            for block in message.content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            content = "\n".join(parts)
        except Exception as e:
            return self._fallback(
                input_data, scores, candidate_bets, f"API例外: {type(e).__name__}: {e}"
            )
        try:
            payload = _extract_json(content)
        except (ValueError, json.JSONDecodeError) as e:
            return self._fallback(
                input_data, scores, candidate_bets, f"JSON応答のパース失敗: {e}"
            )
        return _merge_llm_response(base, payload)


# ---------------------------------------------------------------------------
# ファクトリ
# ---------------------------------------------------------------------------


class UnknownProviderError(ValueError):
    """サポートしていないLLMプロバイダ名が指定されたときのエラー。"""


def build_client(
    provider: str,
    *,
    settings: Optional[Settings] = None,
    warn: WarningEmitter = _default_warn,
) -> LLMClient:
    """provider名とSettingsからLLMClientを構築する。

    - "mock" / "" / None → MockLLMClient
    - "openai"  → OpenAIClient（APIキー無ければ呼び出し時に Mock へフォールバック）
    - "anthropic" → AnthropicClient（同上）
    - 上記以外 → UnknownProviderError（日本語メッセージ）
    """
    p = (provider or "mock").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise UnknownProviderError(
            f"未知のLLMプロバイダ: '{provider}'. "
            f"サポート対象: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    if p == "mock":
        return MockLLMClient()

    cfg = settings or load_settings(override_provider=p)
    if p == "openai":
        if not cfg.openai_api_key:
            warn(
                "[警告] OPENAI_API_KEY が未設定のため Mock にフォールバックします。"
                ".env または環境変数で OPENAI_API_KEY を設定してください。"
            )
            return MockLLMClient()
        return OpenAIClient(api_key=cfg.openai_api_key, model=cfg.openai_model, warn=warn)
    if p == "anthropic":
        if not cfg.anthropic_api_key:
            warn(
                "[警告] ANTHROPIC_API_KEY が未設定のため Mock にフォールバックします。"
                ".env または環境変数で ANTHROPIC_API_KEY を設定してください。"
            )
            return MockLLMClient()
        return AnthropicClient(
            api_key=cfg.anthropic_api_key, model=cfg.anthropic_model, warn=warn
        )
    # 到達不能（上で弾いている）
    raise UnknownProviderError(f"未知のLLMプロバイダ: '{provider}'")


def build_default_client(
    provider: str = "mock",
    *,
    settings: Optional[Settings] = None,
    warn: WarningEmitter = _default_warn,
) -> LLMClient:
    """既存呼び出しとの後方互換のためのエイリアス。"""
    return build_client(provider, settings=settings, warn=warn)
