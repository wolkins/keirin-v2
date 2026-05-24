"""決定レイヤー (Phase 1).

OutputPlan 生成前にレース全体の購入モードを決定する。
Renderer/Sanitizer で後追い補正するのではなく、エンジン側で
「買えるのか、暫定か、見送り寄りか」を一貫して決める。
"""

from .context import (
    DecisionContext,
    PurchaseMode,
    build_decision_context,
    derive_purchase_mode,
)
from .mark_alignment import (
    MarkAlignmentResult,
    assess_mark_alignment,
)
from .market_bias import (
    BiasType,
    MarketBiasDecision,
    assess_market_bias_decision,
)
from .race_type_policy import (
    RaceType,
    RaceTypePolicy,
    resolve_race_type_policy,
)

__all__ = [
    "DecisionContext",
    "PurchaseMode",
    "build_decision_context",
    "derive_purchase_mode",
    "MarkAlignmentResult",
    "assess_mark_alignment",
    "BiasType",
    "MarketBiasDecision",
    "assess_market_bias_decision",
    "RaceType",
    "RaceTypePolicy",
    "resolve_race_type_policy",
]
