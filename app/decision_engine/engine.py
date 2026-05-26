"""decision_engine のエントリポイント: OutputPlan から lifecycle を抽出.

Phase 16 (2026-05-26):
`build_decision_engine_data(plan, prediction, input_data)` で OutputPlan に
lifecycle / coverage_metrics / diagnostics を populate する。

設計:
- Step 1+2: 「最終 plan のスナップショット」から lifecycle を抽出する
  (transitions は空。詳細な移動履歴は Step 4+ で _apply_* に hook を入れる)
- 出力 Markdown は変えない (Step 3 で Renderer が切り替わる)
- 既存 plan のフィールドは並走させる (旧 OddsCoverage / warnings は残す)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .candidate_lifecycle import (
    CandidateLifecycle,
    DECISION_STATE_BUYABLE, DECISION_STATE_GAMI_WARNING,
    DECISION_STATE_SKIP, DECISION_STATE_TENTATIVE, DECISION_STATE_WATCH_ONLY,
    DISPLAY_BUCKET_ANA, DISPLAY_BUCKET_DROPPED,
    DISPLAY_BUCKET_GAMI_WARNING, DISPLAY_BUCKET_HONSEN,
    DISPLAY_BUCKET_HONSEN_MIOKURI, DISPLAY_BUCKET_OOANA,
    DISPLAY_BUCKET_OSAE, DISPLAY_BUCKET_WATCH_ONLY,
)
from .coverage_metrics import CoverageMetrics
from .diagnostics import DiagCategory, Diagnostics

if TYPE_CHECKING:
    from ..models import BetRecommendation, Prediction, RaceInput
    from ..output_plan import OutputPlan


# 表示 bucket の優先順位 (高い順)。
# 同一 combination が複数 bucket に出る場合の最終 display_bucket を決める。
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


def _collect_combination_set(bets) -> set[str]:
    return {b.combination for b in bets if getattr(b, "combination", None)}


def _resolve_display_bucket(
    plan: "OutputPlan", combination: str,
) -> str:
    """OutputPlan 内で combination が含まれる bucket のうち最優先のものを返す.

    優先度: honsen > osae > ana > ooana > honsen_miokuri > gami_warning >
            watch_only > dropped
    """
    bucket_combos = {
        DISPLAY_BUCKET_HONSEN: _collect_combination_set(plan.honsen),
        DISPLAY_BUCKET_OSAE: _collect_combination_set(plan.osae),
        DISPLAY_BUCKET_ANA: _collect_combination_set(plan.ana),
        DISPLAY_BUCKET_OOANA: _collect_combination_set(plan.ooana),
        DISPLAY_BUCKET_HONSEN_MIOKURI:
            _collect_combination_set(plan.honsen_miokuri),
        DISPLAY_BUCKET_GAMI_WARNING:
            _collect_combination_set(plan.gami_warning),
        DISPLAY_BUCKET_WATCH_ONLY: _collect_combination_set(plan.watch_only),
    }
    for bucket in _DISPLAY_BUCKET_PRIORITY:
        if combination in bucket_combos.get(bucket, set()):
            return bucket
    return DISPLAY_BUCKET_DROPPED


def _resolve_decision_state(
    plan: "OutputPlan",
    bet: "BetRecommendation",
    display_bucket: str,
) -> str:
    """display_bucket + purchase_mode + value_label から decision_state を決定.

    優先度:
    1. gami_warning bucket → GAMI_WARNING
    2. honsen_miokuri bucket → WATCH_ONLY
    3. watch_only bucket → WATCH_ONLY
    4. final_best/final_osae/final_ana にあり purchase_mode 別:
        - BUYABLE → BUYABLE
        - TENTATIVE → TENTATIVE
        - WATCH_ONLY → WATCH_ONLY
        - SKIP → SKIP
    5. honsen/osae/ana/ooana にあるが final_* に無い → WATCH_ONLY (参考扱い)
    6. dropped → WATCH_ONLY (lifecycle として記録するが coverage に含まない)
    """
    from ..decision import PurchaseMode

    if display_bucket == DISPLAY_BUCKET_GAMI_WARNING:
        return DECISION_STATE_GAMI_WARNING
    if display_bucket in (
        DISPLAY_BUCKET_HONSEN_MIOKURI, DISPLAY_BUCKET_WATCH_ONLY,
    ):
        return DECISION_STATE_WATCH_ONLY

    # final_best / final_osae / final_ana のいずれかに含まれているか
    final_combos = (
        _collect_combination_set(plan.final_best)
        | _collect_combination_set(plan.final_osae)
        | _collect_combination_set(plan.final_ana)
    )
    if bet.combination in final_combos:
        mode = plan.purchase_mode
        if mode == PurchaseMode.BUYABLE:
            return DECISION_STATE_BUYABLE
        if mode == PurchaseMode.TENTATIVE:
            return DECISION_STATE_TENTATIVE
        if mode == PurchaseMode.WATCH_ONLY:
            return DECISION_STATE_WATCH_ONLY
        return DECISION_STATE_SKIP

    # 表示 bucket にあるが final_* に無い → 参考扱い
    return DECISION_STATE_WATCH_ONLY


def _detect_market_bias_heads(input_data: "RaceInput") -> set[int]:
    """input_data から市場偏りの focused head set を返す.

    market_bias_decision を経た plan.market_bias_type / focused_head は
    plan に保存されていないため、ここで再計算する。失敗時は空 set。
    """
    if input_data is None:
        return set()
    try:
        from ..output_validation import detect_market_bias
        bias = detect_market_bias(input_data)
        heads: set[int] = set()
        focused = getattr(bias, "focused_head", None)
        if focused is not None:
            heads.add(focused)
        # axis bias の場合は焦点軸の頭も含める
        focused_axis = getattr(bias, "focused_axis_head", None)
        if focused_axis is not None:
            heads.add(focused_axis)
        return heads
    except Exception:
        return set()


def _is_purchase_bucket(
    plan: "OutputPlan", combination: str,
) -> bool:
    """final_best / final_osae にあるか (final_ana は除外: 穴は購入扱いだが
    coverage には含めない既存ロジックと整合)."""
    final_combos = (
        _collect_combination_set(plan.final_best)
        | _collect_combination_set(plan.final_osae)
    )
    return combination in final_combos


def _is_display_bucket(display_bucket: str) -> bool:
    """display coverage 対象 bucket か (dropped 以外)."""
    return display_bucket != DISPLAY_BUCKET_DROPPED


def _collect_all_bets(plan: "OutputPlan") -> "list[BetRecommendation]":
    """OutputPlan 内の全 BetRecommendation を combination 単位で重複排除して返す.

    優先順位: final_best > final_osae > final_ana > honsen > osae > ana > ooana
            > honsen_miokuri > gami_warning > watch_only

    同 combination で複数 bucket にある場合、先頭優先で 1 件だけ採用する。
    """
    seen: set[str] = set()
    out: "list[BetRecommendation]" = []
    for bucket in (
        plan.final_best, plan.final_osae, plan.final_ana,
        plan.honsen, plan.osae, plan.ana, plan.ooana,
        plan.honsen_miokuri, plan.gami_warning, plan.watch_only,
    ):
        for b in bucket:
            c = getattr(b, "combination", None)
            if not c or c in seen:
                continue
            seen.add(c)
            out.append(b)
    return out


def _populate_diagnostics(
    plan: "OutputPlan",
) -> Diagnostics:
    """OutputPlan の散在情報を Diagnostics に集約.

    Step 1+2: 既存フィールドを並走させながら、新スキーマに写す。
    """
    diag = Diagnostics()
    # warnings
    for w in plan.warnings:
        diag.add(
            DiagCategory.WARNING, w.message,
            code=w.code, severity=w.severity,
        )
    # mark_alignment_notes
    for note in plan.mark_alignment_notes:
        diag.add(DiagCategory.MARK_ALIGNMENT, note)
    # market_bias_notes
    for note in plan.market_bias_notes:
        diag.add(DiagCategory.MARKET_BIAS, note)
    # race_type_policy_notes
    for note in plan.race_type_policy_notes:
        diag.add(DiagCategory.RACE_TYPE_POLICY, note)
    # decision_notes (purchase_mode の根拠)
    for note in plan.decision_notes:
        diag.add(DiagCategory.DECISION_CONTEXT, note)
    # watch_only_reason_groups
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
    """input_data.odds から件数と bet_type 別件数を返す."""
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


def build_decision_engine_data(
    plan: "OutputPlan",
    prediction: "Prediction",
    input_data: "Optional[RaceInput]" = None,
) -> tuple[list[CandidateLifecycle], CoverageMetrics, Diagnostics]:
    """OutputPlan から lifecycle / coverage_metrics / diagnostics を抽出.

    Args:
        plan: 全 _apply_* を経た最終 OutputPlan
        prediction: LLM 出力 (今回は marks / value_label の解釈に使用)
        input_data: RaceInput (market_popular / market_bias の参照に使用)

    Returns:
        (lifecycles, coverage_metrics, diagnostics)

    Step 1+2: 「最終 plan のスナップショット」から lifecycle を構築。
    transitions は空 (Step 4+ で _apply_* に hook を入れる)。
    """
    # 市場偏り頭 (include_in_market_bias_coverage の判定に使う)
    bias_heads = _detect_market_bias_heads(input_data) if input_data else set()

    # final_best/final_osae/final_ana の combination set
    final_best_combos = _collect_combination_set(plan.final_best)
    final_osae_combos = _collect_combination_set(plan.final_osae)
    final_ana_combos = _collect_combination_set(plan.final_ana)

    lifecycles: list[CandidateLifecycle] = []
    for bet in _collect_all_bets(plan):
        combo = bet.combination
        display_bucket = _resolve_display_bucket(plan, combo)
        decision_state = _resolve_decision_state(plan, bet, display_bucket)

        # market_bias coverage:
        # bet の頭 (combination の先頭セグメント) が bias_heads に含まれ
        # かつ visible (dropped 以外) であれば True
        head_part: Optional[int] = None
        if combo and "-" in combo:
            try:
                head_part = int(combo.split("-")[0])
            except ValueError:
                head_part = None
        in_market_bias = (
            head_part is not None
            and head_part in bias_heads
            and display_bucket != DISPLAY_BUCKET_DROPPED
        )

        # display coverage: dropped 以外
        in_display = _is_display_bucket(display_bucket)
        # purchase coverage:
        # - decision_state が BUYABLE/TENTATIVE (実購入想定)
        # - かつ final_best/final_osae にある (final_ana は穴扱いで除外)
        in_purchase = (
            decision_state in (DECISION_STATE_BUYABLE, DECISION_STATE_TENTATIVE)
            and combo in (final_best_combos | final_osae_combos)
        )

        lc = CandidateLifecycle(
            combination=combo,
            visible=in_display,
            display_bucket=display_bucket,
            decision_state=decision_state,
            market_odds=getattr(bet, "market_odds", None),
            include_in_display_coverage=in_display,
            include_in_purchase_coverage=in_purchase,
            include_in_market_bias_coverage=in_market_bias,
            source_rules=tuple(getattr(bet, "source_rules", None) or ()),
            value_label=getattr(bet, "value_label", None),
            gami_risk=float(getattr(bet, "gami_risk", 0.0) or 0.0),
            is_final_best=combo in final_best_combos,
            is_final_osae=combo in final_osae_combos,
            is_final_ana=combo in final_ana_combos,
        )
        lifecycles.append(lc)

    # honsen_real / honsen_cheap の分離 (既存 OddsCoverage と整合)
    # honsen bucket にあって、gami_warning に含まれない / value_label が
    # 「見送り寄り」でない / gami_risk < 0.8 / market_odds >= 5.0 を実購入本線
    honsen_combos = _collect_combination_set(plan.honsen)
    gami_combos = _collect_combination_set(plan.gami_warning)

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
