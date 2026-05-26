"""CandidateLifecycle: 1 combination の最終状態 + 移動履歴を保持.

Phase 16 (2026-05-26): 同じ買い目に対して「表示候補 / 購入候補 / 参考候補 /
ガミ候補 / coverage に含める候補 / market_bias をカバーした候補 / warning
判定に使う候補」が別々に判断されているのを統一する。

CandidateLifecycle は 1 combination = 1 台帳。各 boolean フラグ
(include_in_*_coverage) を見れば「どの母集団に含まれるか」が一意に決まる。
WarningEngine は lifecycle を見て warnings を生成する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# 定数: decision_state
# ---------------------------------------------------------------------------

DECISION_STATE_BUYABLE = "buyable"            # 実購入推奨
DECISION_STATE_TENTATIVE = "tentative"        # 暫定 (low coverage 等)
DECISION_STATE_WATCH_ONLY = "watch_only"      # 参考表示
DECISION_STATE_SKIP = "skip"                  # 見送り
DECISION_STATE_GAMI_WARNING = "gami_warning"  # ガミ警戒

_VALID_DECISION_STATES = frozenset({
    DECISION_STATE_BUYABLE, DECISION_STATE_TENTATIVE,
    DECISION_STATE_WATCH_ONLY, DECISION_STATE_SKIP,
    DECISION_STATE_GAMI_WARNING,
})


# ---------------------------------------------------------------------------
# 定数: display_bucket
# ---------------------------------------------------------------------------

DISPLAY_BUCKET_HONSEN = "honsen"
DISPLAY_BUCKET_HONSEN_MIOKURI = "honsen_miokuri"
DISPLAY_BUCKET_OSAE = "osae"
DISPLAY_BUCKET_ANA = "ana"
DISPLAY_BUCKET_OOANA = "ooana"
DISPLAY_BUCKET_GAMI_WARNING = "gami_warning"
DISPLAY_BUCKET_WATCH_ONLY = "watch_only"
DISPLAY_BUCKET_DROPPED = "dropped"

_VALID_DISPLAY_BUCKETS = frozenset({
    DISPLAY_BUCKET_HONSEN, DISPLAY_BUCKET_HONSEN_MIOKURI,
    DISPLAY_BUCKET_OSAE, DISPLAY_BUCKET_ANA, DISPLAY_BUCKET_OOANA,
    DISPLAY_BUCKET_GAMI_WARNING, DISPLAY_BUCKET_WATCH_ONLY,
    DISPLAY_BUCKET_DROPPED,
})


# ---------------------------------------------------------------------------
# Transition
# ---------------------------------------------------------------------------


@dataclass
class Transition:
    """候補が移動した記録 (元 bucket → 先 bucket + 理由).

    Step 1+2 では使用箇所がほぼなく、Step 4 (WarningEngine) で「なぜ参考に
    移ったか」を診断するために使う想定。
    """

    from_bucket: str
    to_bucket: str
    reason: str       # "gami_filter" / "market_bias_suppressed" / etc.
    stage: str        # "_apply_gami_source_rules_filter" など発生段階


# ---------------------------------------------------------------------------
# CandidateLifecycle
# ---------------------------------------------------------------------------


@dataclass
class CandidateLifecycle:
    """1 combination の最終状態台帳.

    例 (静岡6R の 2-7-1):
        CandidateLifecycle(
            combination="2-7-1",
            visible=True,
            display_bucket="honsen_miokuri",
            decision_state="watch_only",
            market_odds=6.1,
            include_in_display_coverage=True,
            include_in_purchase_coverage=False,
            include_in_market_bias_coverage=True,
            source_rules=("market_head", "market_pair", "odds_available"),
            transitions=[],
        )

    これにより:
    - 表示候補 coverage: True で集計
    - 購入候補 coverage: False で除外
    - 市場偏り coverage: True で MARKET_BIAS_NOT_COVERED 警告を抑制
    """

    combination: str
    visible: bool
    display_bucket: str             # _VALID_DISPLAY_BUCKETS のいずれか
    decision_state: str             # _VALID_DECISION_STATES のいずれか
    market_odds: Optional[float] = None

    # coverage 集計の母集団フラグ (Phase 16 の根幹)
    include_in_display_coverage: bool = False
    include_in_purchase_coverage: bool = False
    include_in_market_bias_coverage: bool = False

    source_rules: tuple[str, ...] = field(default_factory=tuple)
    transitions: list[Transition] = field(default_factory=list)

    # 補足情報 (診断用)
    value_label: Optional[str] = None   # 妙味あり / 本線向き / 見送り寄り
    gami_risk: float = 0.0
    is_final_best: bool = False         # plan.final_best に含まれる
    is_final_osae: bool = False         # plan.final_osae に含まれる
    is_final_ana: bool = False          # plan.final_ana に含まれる

    def __post_init__(self) -> None:
        if self.display_bucket not in _VALID_DISPLAY_BUCKETS:
            raise ValueError(
                f"invalid display_bucket: {self.display_bucket!r} "
                f"(valid: {sorted(_VALID_DISPLAY_BUCKETS)})"
            )
        if self.decision_state not in _VALID_DECISION_STATES:
            raise ValueError(
                f"invalid decision_state: {self.decision_state!r} "
                f"(valid: {sorted(_VALID_DECISION_STATES)})"
            )

    @property
    def has_odds(self) -> bool:
        return self.market_odds is not None

    def __repr__(self) -> str:
        flags = []
        if self.include_in_display_coverage:
            flags.append("display")
        if self.include_in_purchase_coverage:
            flags.append("purchase")
        if self.include_in_market_bias_coverage:
            flags.append("market_bias")
        flags_str = "|".join(flags) if flags else "none"
        return (
            f"CandidateLifecycle({self.combination} "
            f"bucket={self.display_bucket} state={self.decision_state} "
            f"odds={self.market_odds} coverage={flags_str})"
        )
