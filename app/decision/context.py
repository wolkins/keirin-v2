"""DecisionContext と PurchaseMode (Phase 1).

OutputPlan 生成前にレース全体の購入モードを決定する。
Renderer は plan.purchase_mode を見て文言を分岐する。
低オッズ取得率・低品質時の危険な購入表現は **エンジン側で止める**。

設計指針:
- enum は IntEnum: 値が小さいほど危険側 (SKIP=0 < WATCH_ONLY=1 < TENTATIVE=2 < BUYABLE=3)
- ルールが複数該当する場合、より危険側 (=値が小さい方) を採用する
- 「以上にはしない」 = 上限キャップ (=その値以下の mode しか許可しない)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..models import RaceInput
    from ..output_validation import OddsCoverage


class PurchaseMode(IntEnum):
    """購入モード。値が小さいほど慎重 (危険側)。

    - SKIP: 見送り。購入判断はしない (オッズ再取得後に再検討)
    - WATCH_ONLY: 見送り寄り。参考候補のみ、final_best は watch_only 扱い
    - TENTATIVE: 暫定候補。「購入対象」「一番買いたい」「実購入候補」は出さない
    - BUYABLE: 通常購入候補を許可
    """

    SKIP = 0
    WATCH_ONLY = 1
    TENTATIVE = 2
    BUYABLE = 3


@dataclass
class DecisionContext:
    """レース 1つの購入判断に必要な観測値と派生結果。

    Fields (観測値):
        odds_overall_coverage: 全オッズ取得率 (0.0-1.0)
        honsen_odds_coverage: 本線オッズ取得率
        purchase_odds_coverage: 実購入候補 (final_best+final_osae) 取得率
        data_quality: "high" / "medium" / "low" / "very_low"
        race_complexity: "low" / "medium" / "high" / "very_high"
        is_girls: ガールズか
        is_rookie: 新人戦か
        final_best_count: final_best の点数 (空判定用)

    Fields (派生):
        purchase_mode: derive_purchase_mode の戻り値
        reasons: purchase_mode 決定の根拠 (人間可読)
        warnings: 関連する警告メッセージ
    """

    odds_overall_coverage: float
    honsen_odds_coverage: float
    purchase_odds_coverage: float
    data_quality: str
    race_complexity: str
    is_girls: bool
    is_rookie: bool
    final_best_count: int = 0
    purchase_mode: PurchaseMode = PurchaseMode.BUYABLE
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def derive_purchase_mode(ctx: DecisionContext) -> PurchaseMode:
    """DecisionContext から PurchaseMode を導出する。

    優先度: SKIP > WATCH_ONLY > TENTATIVE > BUYABLE
    複数ルールが該当する場合、より危険側を採用する。

    ルール:
    - odds_overall_coverage < 0.20 → SKIP
    - race_complexity == "very_high" and odds_overall_coverage < 0.40 → SKIP
    - final_best_count == 0 → WATCH_ONLY 以下に
    - data_quality in (low, very_low) → WATCH_ONLY 以下に
    - is_girls or is_rookie かつ data_quality == "low" → WATCH_ONLY 以下に
      (data_quality low の上記ルールで既に満たされるが、明示)
    - honsen_odds_coverage < 0.50 → TENTATIVE 以下に
    - purchase_odds_coverage < 0.40 → TENTATIVE 以下に

    副作用: ctx.purchase_mode / ctx.reasons を書き込む。
    """
    # 強制 SKIP ルール (最優先)
    if ctx.odds_overall_coverage < 0.20:
        ctx.purchase_mode = PurchaseMode.SKIP
        ctx.reasons.append(
            f"全オッズ取得率 {ctx.odds_overall_coverage:.0%} < 20% → 見送り"
        )
        return ctx.purchase_mode

    if (
        ctx.race_complexity == "very_high"
        and ctx.odds_overall_coverage < 0.40
    ):
        ctx.purchase_mode = PurchaseMode.SKIP
        ctx.reasons.append(
            f"レース難度 very_high + 全オッズ取得率 "
            f"{ctx.odds_overall_coverage:.0%} < 40% → 見送り"
        )
        return ctx.purchase_mode

    # 上限キャップを段階的に適用 (危険側を優先)
    cap = PurchaseMode.BUYABLE

    if ctx.final_best_count == 0:
        cap = min(cap, PurchaseMode.WATCH_ONLY)
        ctx.reasons.append("final_best が空 → 見送り寄り")

    if ctx.data_quality in ("low", "very_low"):
        cap = min(cap, PurchaseMode.WATCH_ONLY)
        ctx.reasons.append(
            f"data_quality={ctx.data_quality} → 見送り寄り以下"
        )

    if (ctx.is_girls or ctx.is_rookie) and ctx.data_quality == "low":
        cap = min(cap, PurchaseMode.WATCH_ONLY)
        ctx.reasons.append(
            "ガールズ/新人戦 + data_quality=low → 見送り寄り以下"
        )

    if ctx.honsen_odds_coverage < 0.50:
        cap = min(cap, PurchaseMode.TENTATIVE)
        ctx.reasons.append(
            f"本線オッズ取得率 {ctx.honsen_odds_coverage:.0%} < 50% "
            "→ 暫定以下"
        )

    if ctx.purchase_odds_coverage < 0.40:
        cap = min(cap, PurchaseMode.TENTATIVE)
        ctx.reasons.append(
            f"実購入オッズ取得率 {ctx.purchase_odds_coverage:.0%} < 40% "
            "→ 暫定以下"
        )

    ctx.purchase_mode = cap
    if not ctx.reasons:
        ctx.reasons.append("通常購入可能 (BUYABLE)")
    return ctx.purchase_mode


def build_decision_context(
    *,
    input_data: "RaceInput",
    coverage: "OddsCoverage",
    purchase_coverage_ratio: float,
    data_quality: str,
    race_complexity: str,
    final_best_count: int,
) -> DecisionContext:
    """RaceInput と各観測値から DecisionContext を組み立てる。

    呼び元 (build_output_plan) は既にこれらを計算しているため、
    再計算せずに直接渡してもらう。
    """
    return DecisionContext(
        odds_overall_coverage=coverage.coverage_ratio,
        honsen_odds_coverage=coverage.honsen_coverage_ratio,
        purchase_odds_coverage=purchase_coverage_ratio,
        data_quality=data_quality,
        race_complexity=race_complexity,
        is_girls=bool(input_data.race.resolved_is_girls()),
        is_rookie=bool(input_data.race.resolved_is_rookie()),
        final_best_count=final_best_count,
    )
