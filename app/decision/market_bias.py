"""MarketBiasDecision (Phase 3): 市場偏りを意思決定ルールに統合する.

広島3R / 静岡4R で見えた問題:
- 広島3R: HeadBias=1番頭 5/5 だが AxisBias 無し
  → 1-X-Y の派生候補が `1-4-2 / 1-4-6 / 1-4-7` のように同一 2着軸に
    寄ると危険 (同一軸過多)。
- 静岡4R: 市場が 2-5 軸を強く評価
  → 2-5-* を複数残してよい (AxisBias)

既存 `detect_market_bias` は HeadBias / AxisBias を別フィールドで持つが、
意思決定として「どのレベルの偏りか」を 1 つの bias_type に集約する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from ..models import RaceInput


BiasType = Literal["none", "head", "axis", "strong_axis"]


@dataclass
class MarketBiasDecision:
    """市場偏りに対する判断結果。

    Fields:
        head: 集中している1着車番 (HeadBias)
        head_count: HeadBias の件数
        axis: 集中している (1着, 2着) 軸 (AxisBias)
        axis_count: AxisBias の件数
        bias_type:
            - none: 上位5件が分散
            - head: 同じ1着が3件以上 (AxisBias なし)
            - axis: 同じ1-2着軸が3件以上
            - strong_axis: 同じ1-2着軸が4件以上
        notes: 人間可読な補足
        warnings: 警告メッセージ
    """

    head: Optional[int] = None
    head_count: int = 0
    axis: Optional[tuple[int, int]] = None
    axis_count: int = 0
    bias_type: BiasType = "none"
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def assess_market_bias_decision(
    input_data: "RaceInput",
) -> MarketBiasDecision:
    """RaceInput から MarketBiasDecision を導出する.

    判定ルール (優先度: strong_axis > axis > head > none):
    - axis_count >= 4 → strong_axis
    - axis_count >= 3 → axis
    - head_count >= 3 (かつ axis_count < 3) → head
    - else → none

    既存 `detect_market_bias` の結果を再利用する。
    """
    from ..output_validation import detect_market_bias

    bias = detect_market_bias(input_data)

    head = bias.focused_head
    head_count = bias.focused_count
    axis = bias.focused_axis
    axis_count = bias.focused_axis_count

    if axis_count >= 4 and axis is not None:
        bias_type: BiasType = "strong_axis"
    elif axis_count >= 3 and axis is not None:
        bias_type = "axis"
    elif head_count >= 3 and head is not None:
        bias_type = "head"
    else:
        bias_type = "none"

    result = MarketBiasDecision(
        head=head if head_count >= 3 else None,
        head_count=head_count,
        axis=axis if axis_count >= 3 else None,
        axis_count=axis_count,
        bias_type=bias_type,
    )

    # notes 生成
    if bias_type == "strong_axis" and axis is not None:
        result.notes.append(
            f"市場 1-2着軸が{axis[0]}-{axis[1]}に強く集中 "
            f"({axis_count}/5)。同一軸の複数候補を残します。"
        )
    elif bias_type == "axis" and axis is not None:
        result.notes.append(
            f"市場 1-2着軸が{axis[0]}-{axis[1]}に集中 "
            f"({axis_count}/5)。同一軸の複数候補を許容します。"
        )
    elif bias_type == "head":
        result.notes.append(
            f"市場の1着が{head}番に集中 ({head_count}/5) ですが、"
            "1-2着軸の集中はありません。同一 2着軸への寄せ過ぎを抑制します。"
        )

    return result
