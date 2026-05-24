"""OutputPlan: 最終出力の唯一の source of truth (2026-05-24)。

目的:
candidate_bets (Prediction.honsen/osae/ana/ooana) と LLM の final_conclusion が
分離していた結果、最終 Markdown に未登録 combo が混入する事故が発生していた
(例: 静岡4R で本線欄に 2-5-3 等が出ているのに、最終結論で 4-3-6 が突然出る)。

本モジュールは OutputPlan という Pydantic モデルで「表示可能な全買い目」を
一元管理し、MarkdownRenderer が deterministic に Markdown を生成する。

LLM が返す final_conclusion / honsen / osae / ana / ooana は完全に無視され、
自然文 (summary_text / trend_text / weather_text / line_text / reason_texts /
reflection_points) のみが装飾として使われる。

Design:
- OutputPlan は Pydantic BaseModel (シリアライズ可能 / バリデーション可能)
- フィールドはレース種別 (本線/押さえ/穴/大穴) と 実購入判断ブロック
  (final_best / final_osae / final_ana / gami_warning / watch_only) を保持
- warnings は OutputPlanWarning のリスト
- 既存 FinalSelection との互換性: FinalSelection は OutputPlan の dataclass alias
  として残し、段階移行を可能にする
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .models import BetRecommendation


class OutputPlanWarning(BaseModel):
    """OutputPlan に紐付く警告。"""

    code: str = Field(..., description="警告コード (例: BEST_EMPTY, LOW_ODDS)")
    severity: str = Field(
        default="warning",
        description="severity: 'info' | 'warning' | 'error'",
    )
    message: str = Field(..., description="人間可読な日本語メッセージ")


class OutputPlan(BaseModel):
    """最終出力の唯一の source of truth (2026-05-24)。

    final_conclusion を含む全ての Markdown 表示は本モデルから生成される。
    LLM 出力の final_conclusion / honsen / osae / ana / ooana は無視される。

    フィールド分類:
        - 表示ブロック (## 6-9 セクション相当):
            honsen / osae / ana / ooana
        - 実購入判断ブロック (### 一番買いたい/押さえとして必要 等):
            final_best / final_osae / final_ana / gami_warning / watch_only
        - 警告: warnings
    """

    # ---- 表示ブロック (## 6-9 セクション) ----
    honsen: list[BetRecommendation] = Field(
        default_factory=list,
        description="本線セクション表示用 (最大3点)",
    )
    osae: list[BetRecommendation] = Field(
        default_factory=list,
        description="押さえセクション表示用 (最大4点)",
    )
    ana: list[BetRecommendation] = Field(
        default_factory=list,
        description="穴セクション表示用",
    )
    ooana: list[BetRecommendation] = Field(
        default_factory=list,
        description="大穴セクション表示用",
    )

    # ---- 実購入判断ブロック ----
    final_best: list[BetRecommendation] = Field(
        default_factory=list,
        description="一番買いたい買い目 (odds取得済み妙味のみ)",
    )
    final_osae: list[BetRecommendation] = Field(
        default_factory=list,
        description="押さえとして必要 (must_cover_bets 相当)",
    )
    final_ana: list[BetRecommendation] = Field(
        default_factory=list,
        description="少額の穴 (small_longshots 相当)",
    )
    gami_warning: list[BetRecommendation] = Field(
        default_factory=list,
        description="安い人気筋 / ガミ警戒 (cheap_popular_bets 相当)",
    )
    watch_only: list[BetRecommendation] = Field(
        default_factory=list,
        description="参考表示 (確認程度、購入は推奨しない)",
    )

    # ---- 警告 ----
    warnings: list[OutputPlanWarning] = Field(
        default_factory=list,
        description="OutputPlan に紐付く警告リスト",
    )

    # ---- バリデーション・ユーティリティ ----

    def all_combos(self) -> set[str]:
        """OutputPlan 内に存在する全 3連単 combination を set で返す。

        MarkdownRenderer のフォールバック判定で「Markdown に出た combo が
        OutputPlan 内に存在するか」を確認する用途。
        """
        combos: set[str] = set()
        for bucket in (
            self.honsen, self.osae, self.ana, self.ooana,
            self.final_best, self.final_osae, self.final_ana,
            self.gami_warning, self.watch_only,
        ):
            for b in bucket:
                if b.combination:
                    combos.add(b.combination)
        return combos

    def warning_codes(self) -> list[str]:
        return [w.code for w in self.warnings]


# ---------------------------------------------------------------------------
# 互換レイヤー: FinalSelection から OutputPlan への変換
# ---------------------------------------------------------------------------


def from_final_selection(final_sel) -> OutputPlan:
    """final_selection.FinalSelection → OutputPlan 変換。

    既存の build_final_selection を活かしつつ OutputPlan に統合するための
    互換レイヤー。段階移行が完了すれば直接 OutputPlan を返す形に変更可能。
    """
    return OutputPlan(
        honsen=list(final_sel.display_honsen),
        osae=list(final_sel.display_osae),
        ana=list(final_sel.display_ana),
        ooana=list(final_sel.display_ooana),
        final_best=list(final_sel.best_bets),
        final_osae=list(final_sel.must_cover_bets),
        final_ana=list(final_sel.small_longshots),
        gami_warning=list(final_sel.cheap_popular_bets),
        watch_only=list(final_sel.watch_only_bets),
        warnings=[
            OutputPlanWarning(
                code=_infer_warning_code(msg),
                severity=_infer_severity(msg),
                message=msg,
            )
            for msg in final_sel.warnings
        ],
    )


def _infer_warning_code(message: str) -> str:
    """warnings 文字列から code を推定する (互換レイヤー暫定実装)。"""
    if "オッズ取得済みで買える候補なし" in message or "オッズ確認後" in message:
        return "BEST_EMPTY_NO_ODDS"
    if "低配当注意" in message:
        return "LOW_ODDS_WARNING"
    if "市場偏り" in message:
        return "MARKET_BIAS_NOT_COVERED"
    return "INFO"


def _infer_severity(message: str) -> str:
    if "低配当注意" in message:
        return "warning"
    if "オッズ未取得" in message or "オッズ確認後" in message:
        return "warning"
    return "info"


def build_output_plan(
    prediction,
    input_data,
) -> OutputPlan:
    """Prediction + RaceInput から OutputPlan を deterministic に構築する。

    内部的には final_selection.build_final_selection を呼んで OutputPlan に
    変換する。将来的に build_output_plan が独立した実装になっても、本関数の
    シグネチャは維持する。
    """
    from .final_selection import build_final_selection
    final_sel = build_final_selection(prediction, input_data)
    return from_final_selection(final_sel)
