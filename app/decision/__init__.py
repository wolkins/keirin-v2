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

__all__ = [
    "DecisionContext",
    "PurchaseMode",
    "build_decision_context",
    "derive_purchase_mode",
]
