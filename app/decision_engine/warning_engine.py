"""WarningEngine: CandidateLifecycle ベースで warning を生成.

Phase 16 (2026-05-26): 既存の散在した warning 生成
(MARKET_BIAS_NOT_COVERED / PURCHASE_MODE_VIOLATION / BUCKET_DUPLICATE 等) を
1 箇所に集約する。

判定基準は CandidateLifecycle の boolean フラグのみ参照する。これにより
「同じ buy 目に対して別レイヤーが別判定をする」ブレを構造的に排除する。

Step 1+2 では「lifecycle ベースで warning を *追加* 生成する」役割で、
既存 warning とは並走する。Step 4 で旧経路を deprecate。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Optional

from .candidate_lifecycle import (
    CandidateLifecycle,
    DECISION_STATE_BUYABLE, DECISION_STATE_GAMI_WARNING,
    DECISION_STATE_TENTATIVE,
    DISPLAY_BUCKET_DROPPED,
)

if TYPE_CHECKING:
    from ..output_plan import OutputPlanWarning


# 警告コード (Phase 16 で新規追加 / 細分化)
MARKET_BIAS_PURCHASE_COVERED = "MARKET_BIAS_PURCHASE_COVERED"   # 購入候補で
                                                                # カバー済み
                                                                # (info)
MARKET_BIAS_WATCH_ONLY = "MARKET_BIAS_WATCH_ONLY"               # 参考に
                                                                # あるが
                                                                # 購入には
                                                                # 無い
MARKET_BIAS_NOT_COVERED_V2 = "MARKET_BIAS_NOT_COVERED_V2"       # 表示にすら
                                                                # ない (旧
                                                                # と区別する
                                                                # ため _V2)
BUCKET_DUPLICATE = "BUCKET_DUPLICATE"            # 同 combo が複数 bucket
GAMI_VS_HONSEN_MISMATCH = "GAMI_VS_HONSEN_MISMATCH"  # gami_memo の (本線)
                                                     # と lifecycle 不一致


def _build_warning(code: str, message: str, severity: str = "warning"):
    """OutputPlanWarning を生成する小 helper (循環 import 回避)."""
    from ..output_plan import OutputPlanWarning
    return OutputPlanWarning(code=code, message=message, severity=severity)


def _check_market_bias_coverage(
    lifecycles: "Iterable[CandidateLifecycle]",
) -> "list[OutputPlanWarning]":
    """市場偏りカバー状況を 3 段階に分けて警告.

    A. 購入候補に bias 頭あり → MARKET_BIAS_PURCHASE_COVERED (info、警告なし)
    B. 表示候補に bias 頭あるが購入候補に無い → MARKET_BIAS_WATCH_ONLY
    C. 表示にも bias 頭が無い → MARKET_BIAS_NOT_COVERED_V2 (warning)
    """
    out: "list[OutputPlanWarning]" = []
    market_bias_lcs = [
        lc for lc in lifecycles if lc.include_in_market_bias_coverage
    ]
    if not market_bias_lcs:
        # 市場偏り自体が無い場合は本関数の対象外
        return out

    has_in_purchase = any(
        lc.include_in_purchase_coverage for lc in market_bias_lcs
    )
    has_in_display = any(
        lc.include_in_display_coverage for lc in market_bias_lcs
    )

    if has_in_purchase:
        # info レベル: カバー済み (warning にはしない)
        return out
    if has_in_display:
        combos = ", ".join(
            lc.combination for lc in market_bias_lcs[:3]
            if lc.include_in_display_coverage
        )
        out.append(_build_warning(
            MARKET_BIAS_WATCH_ONLY,
            (
                f"市場偏りに合う候補は表示にありますが、購入候補には"
                f"含まれていません ({combos})。参考表示として扱われます。"
            ),
            severity="info",
        ))
        return out
    # display にも無い (= 全部 dropped)
    combos = ", ".join(lc.combination for lc in market_bias_lcs[:3])
    out.append(_build_warning(
        MARKET_BIAS_NOT_COVERED_V2,
        (
            f"市場偏りに合う候補 ({combos}) が表示にも残っていません。"
            f"市場偏り未カバー状態です。"
        ),
        severity="warning",
    ))
    return out


def _check_bucket_duplicates(
    lifecycles: "Iterable[CandidateLifecycle]",
) -> "list[OutputPlanWarning]":
    """同じ combination が複数の lifecycle になっている場合の警告.

    本来 build_decision_engine_data の `_collect_all_bets` で combination は
    重複排除されるため、ここで検出されたら build_decision_engine_data の
    バグ。Step 4 で実装する予定の「同 combo が plan の honsen と osae の
    両方に出る」型のブレを検出。
    """
    out: "list[OutputPlanWarning]" = []
    seen: dict[str, CandidateLifecycle] = {}
    for lc in lifecycles:
        if lc.combination in seen:
            out.append(_build_warning(
                BUCKET_DUPLICATE,
                (
                    f"combination={lc.combination} が複数 lifecycle に "
                    f"重複しています "
                    f"(bucket: {seen[lc.combination].display_bucket} / "
                    f"{lc.display_bucket})。"
                ),
                severity="warning",
            ))
        else:
            seen[lc.combination] = lc
    return out


def _check_gami_vs_honsen_mismatch(
    lifecycles: "Iterable[CandidateLifecycle]",
    gami_memo: Optional[str],
) -> "list[OutputPlanWarning]":
    """gami_memo の `(本線)` ラベルが lifecycle と一致するか確認.

    平塚10R で「3-4-7(本線): オッズ安め、ガミ警戒」と書かれているが
    lifecycle 上は WATCH_ONLY / GAMI_WARNING だったケースを検出。
    """
    out: "list[OutputPlanWarning]" = []
    if not gami_memo:
        return out

    # 「X-Y-Z(本線):」のような形式から combo を抽出
    import re
    pattern = re.compile(r"(\d-\d-\d)\s*\(本線\)")
    matches = pattern.findall(gami_memo)
    if not matches:
        return out

    state_by_combo = {lc.combination: lc.decision_state for lc in lifecycles}
    mismatched: list[str] = []
    for combo in matches:
        state = state_by_combo.get(combo)
        if state is None:
            continue
        # 「(本線)」とラベルされている → BUYABLE or TENTATIVE であるべき
        if state not in (DECISION_STATE_BUYABLE, DECISION_STATE_TENTATIVE):
            mismatched.append(f"{combo} (state={state})")

    if mismatched:
        out.append(_build_warning(
            GAMI_VS_HONSEN_MISMATCH,
            (
                f"gami_memo の (本線) ラベルが lifecycle と不一致: "
                f"{', '.join(mismatched)}。ガミメモを lifecycle の "
                f"decision_state に合わせてください。"
            ),
            severity="warning",
        ))
    return out


def build_warnings_from_lifecycles(
    lifecycles: "Iterable[CandidateLifecycle]",
    *,
    gami_memo: Optional[str] = None,
) -> "list[OutputPlanWarning]":
    """CandidateLifecycle 群から warning を一括生成.

    Step 1+2 では本関数を呼ばないが、Step 4 で OutputPlan に追加する想定。
    既存 warning との重複検出は呼び出し側 (build_output_plan) で行う。
    """
    out: "list[OutputPlanWarning]" = []
    lifecycle_list = list(lifecycles)
    out.extend(_check_market_bias_coverage(lifecycle_list))
    out.extend(_check_bucket_duplicates(lifecycle_list))
    if gami_memo is not None:
        out.extend(_check_gami_vs_honsen_mismatch(lifecycle_list, gami_memo))
    return out
