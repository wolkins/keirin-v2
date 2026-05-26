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


# Phase 16 follow-up (2026-05-26): value_label の慎重度ランク.
# merge 時に「より慎重な側」を採用するための順序。数値大 = より慎重。
# 「見送り寄り」が最も慎重 (購入を強く控えるべき)。
_VALUE_LABEL_CONSERVATIVENESS = {
    "見送り寄り": 5,
    "ガミ注意": 4,
    "暫定候補": 3,
    "本線向き": 2,
    "妙味あり": 1,
}


def merge_value_label(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """2 つの value_label のうち、より慎重な方を返す.

    None と非 None の場合は非 None 側を返す (情報を残す)。両方 None なら
    None。順序辞書 _VALUE_LABEL_CONSERVATIVENESS にないラベルは 0 扱い。
    """
    if a is None:
        return b
    if b is None:
        return a
    rank_a = _VALUE_LABEL_CONSERVATIVENESS.get(a, 0)
    rank_b = _VALUE_LABEL_CONSERVATIVENESS.get(b, 0)
    return a if rank_a >= rank_b else b


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

    # Phase 16 follow-up (2026-05-26): 同 combination が複数 display bucket に
    # ある場合の所属集合。BUCKET_DUPLICATE 検出に使う。
    # 含まれる bucket: honsen / osae / ana / ooana / honsen_miokuri /
    # gami_warning / watch_only
    # 含まれない: final_best / final_osae / final_ana (判断ブロックは
    # display bucket と独立)
    bucket_memberships: frozenset[str] = field(default_factory=frozenset)

    # Phase 16 follow-up: MarketBias coverage の match type
    # ("head" / "axis" / "strong_axis" / None)
    market_bias_match_type: Optional[str] = None

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
