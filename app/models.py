"""Pydanticモデル定義。

手入力JSONのバリデーション、スコアリング結果、予想結果、反省ログなど
本MVPで扱うすべてのデータ構造をここで定義する。
"""

from __future__ import annotations

from datetime import date as Date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# 入力モデル（手入力JSON）
# ---------------------------------------------------------------------------


class RaceInfo(BaseModel):
    """レース情報。"""

    model_config = ConfigDict(extra="forbid")

    race_id: str = Field(..., description="レース一意ID。例: 20260522-ogaki-1")
    date: Date
    venue: str = Field(..., description="場名。例: 大垣")
    race_no: int = Field(..., ge=1, le=12)
    class_name: str = Field(..., description="クラス。例: A級一般 / S級 / ガールズ")
    start_time: Optional[str] = Field(None, description="発走時刻。例: 10:53")
    is_girls: Optional[bool] = Field(
        None,
        description=(
            "ガールズ競輪フラグ。未指定の場合は class_name から自動判定される。"
        ),
    )
    bank_note: Optional[str] = Field(None, description="バンク特性メモ")
    bank_length: Optional[int] = Field(
        None,
        ge=200,
        le=600,
        description="バンク周長(m)。333/400/500 で補正分岐。",
    )
    bank_style: Optional[str] = Field(
        None,
        description="バンク特性。差し有利 / 先行有利 / 中立 など",
    )

    def resolved_is_girls(self) -> bool:
        if self.is_girls is not None:
            return self.is_girls
        return "ガールズ" in self.class_name or "L級" in self.class_name

    def resolved_is_rookie(self) -> bool:
        """新人戦・男予2 等の個人戦扱いか判定。

        以下の class_name を含む場合に True:
          - "新人"（新人戦全般）
          - "男予"（男子予選2: 新人/個人戦扱い）
          - "ルーキー"
          - "Sガールズ" は除外（ガールズで個別判定）
        """
        cn = self.class_name or ""
        if not cn:
            return False
        for kw in ("新人", "男予", "ルーキー", "Sチャレンジ"):
            if kw in cn:
                return True
        return False


class Rider(BaseModel):
    """出走選手。"""

    model_config = ConfigDict(extra="forbid")

    car_no: int = Field(..., ge=1, le=9)
    name: str
    score: float = Field(0.0, description="競走得点")
    b_count: int = Field(0, ge=0, description="B（バック数）")
    nige: int = Field(0, ge=0, description="逃げ回数")
    makuri: int = Field(0, ge=0, description="捲り回数")
    sashi: int = Field(0, ge=0, description="差し回数")
    mark: int = Field(0, ge=0, description="マーク回数")
    comment: Optional[str] = Field(None, description="脚質コメント。例: 自力 / 番手 / 追込")
    recent_summary: Optional[str] = Field(None, description="直近内容まとめ")
    style_tags: list[str] = Field(
        default_factory=list,
        description="自在/逃げ/捲り/差し/追込/単騎などのタグ",
    )
    stats_missing: bool = Field(
        False,
        description=(
            "競走得点・B数・決まり手が取得できなかったか。"
            "True の場合、score/b_count/nige/makuri/sashi/mark は 0 でも "
            "「データなし」を意味する（真の0ではない）。"
            "数値不足モード判定 (detect_score_data_insufficient) で使う。"
        ),
    )


class Line(BaseModel):
    """ライン構成。ガールズでは使用しない。"""

    model_config = ConfigDict(extra="forbid")

    line_name: str
    cars: list[int] = Field(..., min_length=1)
    description: Optional[str] = None

    @field_validator("cars")
    @classmethod
    def _unique(cls, v: list[int]) -> list[int]:
        if len(set(v)) != len(v):
            raise ValueError("ライン内で同じ車番が重複しています")
        return v


class Weather(BaseModel):
    """天候情報。"""

    model_config = ConfigDict(extra="forbid")

    condition: str = Field("不明", description="晴れ/曇り/雨/小雨など")
    rain_mm_per_hour: float = Field(0.0, ge=0.0)
    wind_direction: Optional[str] = Field(None, description="風向。北/東/南西など")
    wind_speed_mps: float = Field(0.0, ge=0.0)
    wind_note: Optional[str] = None
    temperature_c: Optional[float] = Field(None, description="気温（摂氏）")


class OddsEntry(BaseModel):
    """買い目1点のオッズ。"""

    model_config = ConfigDict(extra="forbid")

    bet_type: Literal["3連単", "3連複", "2車単", "2車複", "ワイド", "単勝", "複勝"]
    combination: str = Field(..., description="例: 5-1-3 / 5=1=3 / 5")
    odds: float = Field(..., ge=1.0)


class VenueTrend(BaseModel):
    """場の当日傾向。"""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(..., description="例: 番手差し決まりやすい")
    favors: list[str] = Field(
        default_factory=list,
        description="加点したい脚質タグ（例: 番手, 3番手, 追込）",
    )


class RecentResult(BaseModel):
    """直近結果。"""

    model_config = ConfigDict(extra="forbid")

    date: Optional[Date] = None
    venue: Optional[str] = None
    race_no: Optional[int] = Field(None, ge=1, le=12)
    result: str = Field(..., description="例: 5-1-3")
    memo: Optional[str] = None
    payout: Optional[int] = Field(None, ge=0, description="3連単払戻金（円）")


# ---------------------------------------------------------------------------
# RaceNotes（補助情報: 選手コメント・記者見解・並び予想ヒント）
# ---------------------------------------------------------------------------
#
# 著作権配慮:
# - raw_excerpt は最大50文字（Pydantic で強制）
# - comment_summary は最大120文字
# - race_summary は最大300文字
# - LLMには要約と signals のみ渡し、生本文は流さない
# - 全文転載しない方針を型レベルで担保


_RACE_NOTES_SOURCES = Literal[
    "tospo", "winticket", "netkeirin", "oddspark", "yenjoy", "manual_text", "generic",
]


class RiderNote(BaseModel):
    """補助情報源から取得した、1選手分の短い情報。"""

    model_config = ConfigDict(extra="forbid")

    car_no: int = Field(..., ge=1, le=9)
    name: Optional[str] = Field(None, max_length=50)
    comment_summary: str = Field(
        "", max_length=120, description="短い要約（最大120文字）"
    )
    signals: list[str] = Field(
        default_factory=list,
        description="自力/前々/単騎/番手/状態良い 等の特徴量タグ",
    )
    confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="情報源の信頼度（0.0〜1.0）。手入力なら 1.0、自動抽出なら可変",
    )
    raw_excerpt: Optional[str] = Field(
        None, max_length=50,
        description="必要最小限の短い引用（最大50文字）。既定では使わない",
    )


class RaceNotes(BaseModel):
    """補助情報源（東スポ/WINTICKET等）から得た構造化メモ。

    主データ（Kドリームス出走表・オッズパーク・Open-Meteo）に対する
    "コメント・記者見解" の補助情報源。
    """

    model_config = ConfigDict(extra="forbid")

    source: _RACE_NOTES_SOURCES = Field(
        ..., description="情報源。tospo/winticket/netkeirin/oddspark/yenjoy/manual_text/generic",
    )
    venue: Optional[str] = None
    date: Optional[Date] = None
    race_no: Optional[int] = Field(None, ge=1, le=12)
    race_summary: Optional[str] = Field(
        None, max_length=300,
        description="記者見解の短い要約（最大300文字）",
    )
    rider_notes: list[RiderNote] = Field(default_factory=list)
    line_hint: Optional[str] = Field(
        None, max_length=200,
        description="並び予想ヒント。例: '5-1-3 / 6-4 / 7'",
    )
    prediction_hint: Optional[str] = Field(
        None, max_length=300,
        description="記者予想ヒント。例: '本線は5-1、穴は6-4'",
    )


class RaceInput(BaseModel):
    """手入力JSON全体。"""

    model_config = ConfigDict(extra="forbid")

    race: RaceInfo
    riders: list[Rider] = Field(..., min_length=1)
    lines: list[Line] = Field(default_factory=list)
    weather: Optional[Weather] = None
    odds: list[OddsEntry] = Field(default_factory=list)
    recent_results: list[RecentResult] = Field(default_factory=list)
    venue_trend: Optional[VenueTrend] = None
    user_note: Optional[str] = None

    def rider_by_car(self, car_no: int) -> Optional[Rider]:
        for r in self.riders:
            if r.car_no == car_no:
                return r
        return None


# ---------------------------------------------------------------------------
# 出力モデル（予想）
# ---------------------------------------------------------------------------


class RiderScore(BaseModel):
    """1選手分のスコアリング結果。"""

    car_no: int
    name: str
    win_score: float = 0.0
    second_score: float = 0.0
    third_score: float = 0.0
    line_strength: float = 0.0
    weather_bonus: float = 0.0
    wind_bonus: float = 0.0
    odds_value_score: float = 0.0
    trend_bonus: float = 0.0
    reflection_bonus: float = 0.0
    risk_score: float = 0.0
    gami_risk: float = 0.0
    reasons: list[str] = Field(default_factory=list)

    def total(self) -> float:
        return (
            self.win_score
            + self.second_score * 0.6
            + self.third_score * 0.4
            + self.line_strength
            + self.weather_bonus
            + self.wind_bonus
            + self.trend_bonus
            + self.reflection_bonus
            - self.risk_score
        )


class BetRecommendation(BaseModel):
    """買い目推奨。"""

    category: Literal["本線", "押さえ", "穴", "大穴"]
    bet_type: str = Field(default="3連単")
    combination: str
    reason: str
    gami_risk: float = 0.0
    # オッズ妙味分析（後方互換のため Optional・既定 None）
    market_odds: Optional[float] = None
    market_rank: Optional[int] = None
    predicted_strength: Optional[float] = None
    value_score: Optional[float] = None
    value_label: Optional[str] = None


class Prediction(BaseModel):
    """予想結果。LLM文章化とスコアの両方を保持する。"""

    race_id: str
    venue: str
    race_no: int
    is_girls: bool
    summary: str = Field(default="", description="レース概要")
    venue_trend_text: str = Field(default="", description="直近結果からの場の傾向")
    weather_text: str = Field(default="", description="天候・雨・風補正テキスト")
    lines_text: str = Field(default="", description="並び（ガールズでは未使用）")
    marks: dict[str, int] = Field(
        default_factory=dict,
        description="印 (◎○▲△× などの記号 → 車番)",
    )
    honsen: list[BetRecommendation] = Field(default_factory=list)
    osae: list[BetRecommendation] = Field(default_factory=list)
    ana: list[BetRecommendation] = Field(default_factory=list)
    ooana: list[BetRecommendation] = Field(default_factory=list)
    final_conclusion: str = ""
    gami_memo: str = ""
    reflection_points: list[str] = Field(default_factory=list)
    rider_scores: list[RiderScore] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 反省ログ
# ---------------------------------------------------------------------------


ReflectionCategory = Literal[
    "的中",
    "買い目にはあったが本線ではなかった",
    "本線番手を過信",
    "別線番手を軽視",
    "3番手の伸びを軽視",
    "風補正不足",
    "雨補正不足",
    "ガールズの位置取り評価不足",
    "本命自力の過信",
    "穴を広げすぎてガミリスク増加",
    "本命ラインの3着を固定しすぎた",
    "別線番手の2着上がりを軽視した",
    "3番手の2着上がりを軽視した",
]


class Reflection(BaseModel):
    """結果入力後の反省ログ。"""

    race_id: str
    venue: str
    race_no: int
    is_girls: bool
    weather_condition: Optional[str] = None
    wind_speed_mps: float = 0.0
    rain_mm_per_hour: float = 0.0
    class_name: Optional[str] = None
    predicted_honsen: list[str] = Field(default_factory=list)
    actual_result: str
    categories: list[str] = Field(default_factory=list)
    note: str = ""
    # 保存時には埋めず、ロード時にDBの値を後付けで入れる
    created_at: Optional[str] = None
