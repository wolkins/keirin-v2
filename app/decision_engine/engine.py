"""decision_engine のエントリポイント: OutputPlan から lifecycle を抽出.

Phase 16 (2026-05-26):
`build_decision_engine_data(plan, prediction, input_data)` で OutputPlan に
lifecycle / coverage_metrics / diagnostics を populate する。

Phase 16 follow-up (2026-05-26): レビュー指摘を受けて修正:
- P1: 同 combination が複数 bucket にある場合、merge して 1 lifecycle にする
  (source_rules union / gami_risk max / value_label 慎重側)
- P1: decision_state は慎重側を最優先 (gami_warning > watch_only > buyable)
- P1: bucket_memberships で全 bucket 所属を記録
- P2: MarketBias coverage を bias_type 別 (HeadBias: 頭一致 /
  AxisBias / StrongAxisBias: 1-2着軸一致)
- P2: market_bias_match_type を lifecycle に追加
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from .candidate_lifecycle import (
    CandidateLifecycle,
    DECISION_STATE_BUYABLE, DECISION_STATE_GAMI_WARNING,
    DECISION_STATE_SKIP, DECISION_STATE_TENTATIVE, DECISION_STATE_WATCH_ONLY,
    DISPLAY_BUCKET_ANA, DISPLAY_BUCKET_DROPPED,
    DISPLAY_BUCKET_GAMI_WARNING, DISPLAY_BUCKET_HONSEN,
    DISPLAY_BUCKET_HONSEN_MIOKURI, DISPLAY_BUCKET_OOANA,
    DISPLAY_BUCKET_OSAE, DISPLAY_BUCKET_WATCH_ONLY,
    merge_value_label,
)
from .coverage_metrics import CoverageMetrics
from .diagnostics import DiagCategory, Diagnostics

if TYPE_CHECKING:
    from ..models import BetRecommendation, Prediction, RaceInput
    from ..output_plan import OutputPlan


# 表示 bucket の優先順位 (高い順)。display_bucket を決めるときの順序。
# 「表示位置として最も上位の bucket」を選ぶための優先度。
# decision_state とは独立 (慎重側優先は別ロジックで決定)。
_DISPLAY_BUCKET_PRIORITY = (
    DISPLAY_BUCKET_HONSEN,           # 0
    DISPLAY_BUCKET_OSAE,             # 1
    DISPLAY_BUCKET_ANA,              # 2
    DISPLAY_BUCKET_OOANA,            # 3
    DISPLAY_BUCKET_HONSEN_MIOKURI,   # 4
    DISPLAY_BUCKET_GAMI_WARNING,     # 5
    DISPLAY_BUCKET_WATCH_ONLY,       # 6
    DISPLAY_BUCKET_DROPPED,          # 7
)


# bucket_memberships に含める display bucket 名 (final_* は判断ブロック
# なので含めない)
_DISPLAY_BUCKETS_FOR_MEMBERSHIP = frozenset({
    DISPLAY_BUCKET_HONSEN,
    DISPLAY_BUCKET_OSAE,
    DISPLAY_BUCKET_ANA,
    DISPLAY_BUCKET_OOANA,
    DISPLAY_BUCKET_HONSEN_MIOKURI,
    DISPLAY_BUCKET_GAMI_WARNING,
    DISPLAY_BUCKET_WATCH_ONLY,
})


# ---------------------------------------------------------------------------
# Merge 型の bet 情報 (1 combination 単位に統合した dict)
# ---------------------------------------------------------------------------


def _collect_merged_bets(plan: "OutputPlan") -> dict[str, dict[str, Any]]:
    """OutputPlan 全 bucket を combination 単位で merge.

    Phase 16 follow-up: 単純 dedupe ではなく、同 combination の情報を統合
    する。bucket ごとに source_rules / value_label / gami_risk / market_odds
    が違う場合、後続 bucket の情報を取りこぼさない。

    Returns:
        combination → {
            "bet": 代表 BetRecommendation (最初に見つけたもの),
            "source_rules": set[str] (union),
            "market_odds": Optional[float] (any non-null),
            "value_label": Optional[str] (慎重側),
            "gami_risk": float (max),
            "reason": str (代表),
            "bucket_memberships": set[str] (全 display bucket),
            "is_final_best" / "is_final_osae" / "is_final_ana": bool,
        }
    """
    # display bucket (memberships に含める対象) — 順序は first-seen の確定用
    display_bucket_map = (
        (DISPLAY_BUCKET_HONSEN, plan.honsen),
        (DISPLAY_BUCKET_OSAE, plan.osae),
        (DISPLAY_BUCKET_ANA, plan.ana),
        (DISPLAY_BUCKET_OOANA, plan.ooana),
        (DISPLAY_BUCKET_HONSEN_MIOKURI, plan.honsen_miokuri),
        (DISPLAY_BUCKET_GAMI_WARNING, plan.gami_warning),
        (DISPLAY_BUCKET_WATCH_ONLY, plan.watch_only),
    )
    # final bucket (memberships には含めず、フラグだけ立てる)
    final_combos = {
        "is_final_best": {
            b.combination for b in plan.final_best if b.combination
        },
        "is_final_osae": {
            b.combination for b in plan.final_osae if b.combination
        },
        "is_final_ana": {
            b.combination for b in plan.final_ana if b.combination
        },
    }

    merged: dict[str, dict[str, Any]] = {}

    def _add_or_merge(bucket_name: str, bet: "BetRecommendation") -> None:
        combo = getattr(bet, "combination", None)
        if not combo:
            return
        source_rules_iter = getattr(bet, "source_rules", None) or ()
        odds = getattr(bet, "market_odds", None)
        vl = getattr(bet, "value_label", None)
        gr = float(getattr(bet, "gami_risk", 0.0) or 0.0)
        reason = getattr(bet, "reason", None)

        if combo not in merged:
            merged[combo] = {
                "bet": bet,
                "source_rules": set(source_rules_iter),
                "market_odds": odds,
                "value_label": vl,
                "gami_risk": gr,
                "reason": reason,
                "bucket_memberships": {bucket_name},
                "is_final_best": combo in final_combos["is_final_best"],
                "is_final_osae": combo in final_combos["is_final_osae"],
                "is_final_ana": combo in final_combos["is_final_ana"],
            }
            return

        m = merged[combo]
        m["source_rules"].update(source_rules_iter)
        if m["market_odds"] is None and odds is not None:
            m["market_odds"] = odds
        m["value_label"] = merge_value_label(m["value_label"], vl)
        if gr > m["gami_risk"]:
            m["gami_risk"] = gr
        m["bucket_memberships"].add(bucket_name)

    for bucket_name, bets in display_bucket_map:
        for bet in bets:
            _add_or_merge(bucket_name, bet)
    return merged


def _resolve_display_bucket(bucket_memberships: set[str]) -> str:
    """bucket_memberships の中で最も優先度の高い bucket を表示位置にする.

    どの display bucket にも所属していない場合 (final_* のみのケースは
    現状の collect 設計ではあり得ないが念のため) は DROPPED。
    """
    for bucket in _DISPLAY_BUCKET_PRIORITY:
        if bucket in bucket_memberships:
            return bucket
    return DISPLAY_BUCKET_DROPPED


def _resolve_decision_state(
    plan: "OutputPlan",
    merged_info: dict[str, Any],
) -> str:
    """Phase 16 follow-up: 慎重側を最優先で決定.

    優先順位 (上ほど優先):
    1. GAMI_WARNING: bucket=gami_warning / source_rules に
       gami_warning|low_odds / value_label=ガミ注意 / 市場オッズ低 + gami_risk 高
    2. WATCH_ONLY: bucket=watch_only / honsen_miokuri /
       value_label=見送り寄り / source_rules に watch_only
    3. final_best/final_osae/final_ana にあり purchase_mode 別:
        - BUYABLE → BUYABLE
        - TENTATIVE → TENTATIVE
        - WATCH_ONLY → WATCH_ONLY
        - SKIP → SKIP
    4. それ以外 → WATCH_ONLY (display にはあるが final_* に無い参考扱い)
    """
    from ..decision import PurchaseMode

    bucket_memberships = merged_info["bucket_memberships"]
    source_rules: set[str] = merged_info["source_rules"]
    value_label = merged_info["value_label"]
    gami_risk = merged_info["gami_risk"]
    market_odds = merged_info["market_odds"]

    # 1. GAMI_WARNING (最優先)
    is_gami = (
        DISPLAY_BUCKET_GAMI_WARNING in bucket_memberships
        or "gami_warning" in source_rules
        or "low_odds" in source_rules
        or value_label == "ガミ注意"
        or (
            market_odds is not None
            and market_odds < 5.0
            and gami_risk >= 0.8
        )
    )
    if is_gami:
        return DECISION_STATE_GAMI_WARNING

    # 2. WATCH_ONLY (gami の次に慎重)
    is_watch = (
        DISPLAY_BUCKET_WATCH_ONLY in bucket_memberships
        or DISPLAY_BUCKET_HONSEN_MIOKURI in bucket_memberships
        or value_label == "見送り寄り"
        or "watch_only" in source_rules
    )
    if is_watch:
        return DECISION_STATE_WATCH_ONLY

    # 3. final_* にある → purchase_mode 別
    in_final = (
        merged_info["is_final_best"]
        or merged_info["is_final_osae"]
        or merged_info["is_final_ana"]
    )
    if in_final:
        mode = plan.purchase_mode
        if mode == PurchaseMode.BUYABLE:
            return DECISION_STATE_BUYABLE
        if mode == PurchaseMode.TENTATIVE:
            return DECISION_STATE_TENTATIVE
        if mode == PurchaseMode.WATCH_ONLY:
            return DECISION_STATE_WATCH_ONLY
        return DECISION_STATE_SKIP

    # 4. display にあるが final_* に無い → WATCH_ONLY
    return DECISION_STATE_WATCH_ONLY


# ---------------------------------------------------------------------------
# MarketBias coverage: bias_type 別判定 (Phase 16 follow-up)
# ---------------------------------------------------------------------------


def _build_market_bias_matcher(
    input_data: "Optional[RaceInput]",
) -> Callable[[str], tuple[bool, Optional[str]]]:
    """combination 文字列を受け取って (match, match_type) を返す関数を構築.

    優先順位:
    - has_axis_focus=True (Axis/StrongAxis): 1着+2着 が focused_axis に一致
      なら True ("axis")
    - has_head_focus=True かつ axis_focus 無し: 1着が focused_head に
      一致なら True ("head")
    - どちらも無い: 常に False
    """
    if input_data is None:
        return lambda combo: (False, None)
    try:
        from ..output_validation import detect_market_bias
        bias = detect_market_bias(input_data)
    except Exception:
        return lambda combo: (False, None)

    has_axis = bias.has_axis_focus
    has_head = bias.has_head_focus
    focused_axis = bias.focused_axis if has_axis else None
    focused_head = bias.focused_head if has_head else None

    if not has_axis and not has_head:
        return lambda combo: (False, None)

    def matcher(combo: str) -> tuple[bool, Optional[str]]:
        if not combo or "-" not in combo:
            return False, None
        parts = combo.split("-")
        if len(parts) < 2:
            return False, None
        try:
            head = int(parts[0])
            second = int(parts[1])
        except ValueError:
            return False, None
        # Axis を優先 (より具体的な制約)
        if focused_axis is not None:
            ax_head, ax_second = focused_axis
            if head == ax_head and second == ax_second:
                # StrongAxis は AxisBias の強化版 (focused_axis_count 高い)
                match_type = "axis"
                return True, match_type
            # Axis があるが一致しない場合、Head 判定はしない
            # (AxisBias 環境では head 一致だけでは coverage と見なさない)
            return False, None
        # Head のみ
        if focused_head is not None and head == focused_head:
            return True, "head"
        return False, None

    return matcher


# ---------------------------------------------------------------------------
# Diagnostics populate
# ---------------------------------------------------------------------------


def _populate_diagnostics(plan: "OutputPlan") -> Diagnostics:
    """OutputPlan の散在情報を Diagnostics に集約."""
    diag = Diagnostics()
    for w in plan.warnings:
        diag.add(
            DiagCategory.WARNING, w.message,
            code=w.code, severity=w.severity,
        )
    for note in plan.mark_alignment_notes:
        diag.add(DiagCategory.MARK_ALIGNMENT, note)
    for note in plan.market_bias_notes:
        diag.add(DiagCategory.MARKET_BIAS, note)
    for note in plan.race_type_policy_notes:
        diag.add(DiagCategory.RACE_TYPE_POLICY, note)
    for note in plan.decision_notes:
        diag.add(DiagCategory.DECISION_CONTEXT, note)
    for group_key, bets in plan.watch_only_reason_groups.items():
        if not bets:
            continue
        combos = ", ".join(b.combination for b in bets[:5])
        diag.add(
            DiagCategory.WATCH_ONLY_REASON,
            f"[{group_key}] {combos}",
        )
    return diag


def _summarize_market_popular(
    input_data: "Optional[RaceInput]",
) -> tuple[int, dict[str, int]]:
    if input_data is None:
        return 0, {}
    odds_list = getattr(input_data, "odds", None) or []
    total = len(odds_list)
    by_type: dict[str, int] = {}
    for o in odds_list:
        bt = getattr(o, "bet_type", None)
        if bt:
            by_type[bt] = by_type.get(bt, 0) + 1
    return total, by_type


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def build_decision_engine_data(
    plan: "OutputPlan",
    prediction: "Prediction",
    input_data: "Optional[RaceInput]" = None,
) -> tuple[list[CandidateLifecycle], CoverageMetrics, Diagnostics]:
    """OutputPlan から lifecycle / coverage_metrics / diagnostics を抽出.

    Phase 16 follow-up (2026-05-26): merge 型 collect + 慎重側優先 state +
    bias_type 別 MarketBias 判定。
    """
    bias_matcher = _build_market_bias_matcher(input_data)

    merged = _collect_merged_bets(plan)
    lifecycles: list[CandidateLifecycle] = []

    for combo, info in merged.items():
        bucket_memberships = info["bucket_memberships"]
        display_bucket = _resolve_display_bucket(bucket_memberships)
        decision_state = _resolve_decision_state(plan, info)

        # market_bias coverage: bias_type 別判定
        bias_match, match_type = bias_matcher(combo)
        in_market_bias = (
            bias_match and display_bucket != DISPLAY_BUCKET_DROPPED
        )

        # display coverage: dropped 以外
        in_display = display_bucket != DISPLAY_BUCKET_DROPPED
        # purchase coverage: state が BUYABLE/TENTATIVE かつ
        # final_best/final_osae にある (final_ana は穴扱いで除外)
        in_purchase = (
            decision_state in (
                DECISION_STATE_BUYABLE, DECISION_STATE_TENTATIVE,
            )
            and (info["is_final_best"] or info["is_final_osae"])
        )

        lc = CandidateLifecycle(
            combination=combo,
            visible=in_display,
            display_bucket=display_bucket,
            decision_state=decision_state,
            market_odds=info["market_odds"],
            include_in_display_coverage=in_display,
            include_in_purchase_coverage=in_purchase,
            include_in_market_bias_coverage=in_market_bias,
            source_rules=tuple(sorted(info["source_rules"])),
            bucket_memberships=frozenset(
                bucket_memberships & _DISPLAY_BUCKETS_FOR_MEMBERSHIP
            ),
            market_bias_match_type=match_type if bias_match else None,
            value_label=info["value_label"],
            gami_risk=info["gami_risk"],
            is_final_best=info["is_final_best"],
            is_final_osae=info["is_final_osae"],
            is_final_ana=info["is_final_ana"],
        )
        lifecycles.append(lc)

    # honsen_real / honsen_cheap の分離
    honsen_combos = {
        b.combination for b in plan.honsen if b.combination
    }
    gami_combos = {
        b.combination for b in plan.gami_warning if b.combination
    }

    def _is_cheap(lc: CandidateLifecycle) -> bool:
        if lc.value_label == "見送り寄り":
            return True
        if lc.gami_risk >= 0.8:
            return True
        if lc.market_odds is not None and lc.market_odds < 5.0:
            return True
        if lc.combination in gami_combos:
            return True
        return False

    honsen_lcs = [lc for lc in lifecycles if lc.combination in honsen_combos]
    honsen_real_lcs = [lc for lc in honsen_lcs if not _is_cheap(lc)]
    honsen_cheap_lcs = [lc for lc in honsen_lcs if _is_cheap(lc)]

    market_popular_total, market_popular_by_type = (
        _summarize_market_popular(input_data)
    )

    metrics = CoverageMetrics.from_lifecycles(
        lifecycles,
        market_popular_total=market_popular_total,
        market_popular_by_bet_type=market_popular_by_type,
        honsen_real_lifecycles=honsen_real_lcs,
        honsen_cheap_lifecycles=honsen_cheap_lcs,
    )
    diag = _populate_diagnostics(plan)
    return lifecycles, metrics, diag
