"""MarkAlignment (Phase 2): 印 marks と final_best/osae の整合性チェック.

広島3R 風シナリオ:
- ◎7 (単騎)、final_best=1-2-4 → ユーザーから見ると「印は7なのに買い目は1頭?」
  → エンジン側で「単騎 + 市場が1番頭 + 暫定 mode」と説明できれば
    explainable_mismatch とし、整合性 warning は出さない。

レベル:
- aligned: ◎が final_best/final_osae の1着または2着に含まれる
- explainable_mismatch: ◎不在だが、単騎・市場偏り・WATCH_ONLY/SKIP 等で
  説明できる
- dangerous_mismatch: ◎不在で、説明理由もなく、BUYABLE/TENTATIVE のとき
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from ..models import Prediction, RaceInput
    from ..output_plan import OutputPlan


AlignmentLevel = Literal[
    "aligned", "explainable_mismatch", "dangerous_mismatch",
]


@dataclass
class MarkAlignmentResult:
    """印と final_* の整合性チェック結果。"""

    top_mark_car: Optional[int]
    final_best_heads: set[int] = field(default_factory=set)
    final_best_seconds: set[int] = field(default_factory=set)
    final_osae_heads: set[int] = field(default_factory=set)
    final_osae_seconds: set[int] = field(default_factory=set)
    top_mark_in_final_best: bool = False
    top_mark_in_final_osae: bool = False
    top_mark_in_any_final: bool = False
    is_top_mark_single: bool = False
    alignment_level: AlignmentLevel = "aligned"
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _extract_top_mark(marks: dict[str, int]) -> Optional[int]:
    """marks から ◎ (本命) を抽出する。複数キー形式に対応。"""
    if not marks:
        return None
    # 既存形式: { "◎": 7, "○": 3, ... }
    if "◎" in marks:
        return marks["◎"]
    # 互換: { "honmei": 7 } 形式 (将来用)
    if "honmei" in marks:
        return marks["honmei"]
    return None


def _split_combo(combo: str) -> Optional[tuple[int, int, int]]:
    """3連単 combination を (head, second, third) に分解。"""
    if not combo or "-" not in combo:
        return None
    parts = combo.split("-")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError):
        return None


def _heads_and_seconds(bets) -> tuple[set[int], set[int]]:
    """買い目リストから 1着車番 set と 2着車番 set を抽出。"""
    heads: set[int] = set()
    seconds: set[int] = set()
    for b in bets:
        parts = _split_combo(b.combination)
        if parts is None:
            continue
        heads.add(parts[0])
        seconds.add(parts[1])
    return heads, seconds


def _is_car_in_single_line(car: int, input_data) -> bool:
    """指定車番が単騎 (cars 長 1) のラインに属するか。

    codex P2 反映: ガールズ/新人戦のように **全ライン** が単騎 (len==1) の
    場合は「単騎」を説明理由として扱わない (race 全体が個人戦なので、
    特定車番だけの単騎評価ではないため)。
    """
    if input_data is None or not input_data.lines:
        return False
    # 全ラインが単騎なら個人戦扱い → False を返す
    if all(len(line.cars) <= 1 for line in input_data.lines):
        return False
    for line in input_data.lines:
        if car in line.cars and len(line.cars) == 1:
            return True
    return False


def assess_mark_alignment(
    prediction: "Prediction",
    plan: "OutputPlan",
    input_data: Optional["RaceInput"],
) -> MarkAlignmentResult:
    """印 marks と final_best / final_osae の整合性を判定する。

    判定ロジック:
    1. ◎を marks から抽出
    2. final_best / final_osae の頭・2着車番集合を作る
    3. ◎の所在を確認 → aligned / mismatch
    4. mismatch のとき、説明可能な理由を探す:
       - ◎が単騎
       - market_bias が別車番頭に強い
       - purchase_mode が WATCH_ONLY / SKIP
       - ◎の買い目が market_odds=None
    5. 説明できれば explainable_mismatch、できなければ dangerous_mismatch

    notes は人間可読の説明 (decision_notes に流す用)。
    """
    from .context import PurchaseMode

    top_mark = _extract_top_mark(prediction.marks or {})
    fb_heads, fb_seconds = _heads_and_seconds(plan.final_best)
    fo_heads, fo_seconds = _heads_and_seconds(plan.final_osae)

    result = MarkAlignmentResult(
        top_mark_car=top_mark,
        final_best_heads=fb_heads,
        final_best_seconds=fb_seconds,
        final_osae_heads=fo_heads,
        final_osae_seconds=fo_seconds,
    )

    if top_mark is None:
        # ◎が無い場合は判定スキップ (aligned 扱い、notes は空)
        # codex P2 反映: 既存 fixture (marks={}) で「### 印と買い目の補足」
        # セクションが出るのを防ぐため notes を追加しない (静かにスキップ)。
        result.alignment_level = "aligned"
        return result

    result.top_mark_in_final_best = (
        top_mark in fb_heads or top_mark in fb_seconds
    )
    result.top_mark_in_final_osae = (
        top_mark in fo_heads or top_mark in fo_seconds
    )
    result.top_mark_in_any_final = (
        result.top_mark_in_final_best or result.top_mark_in_final_osae
    )
    result.is_top_mark_single = _is_car_in_single_line(top_mark, input_data)

    if result.top_mark_in_any_final:
        result.alignment_level = "aligned"
        return result

    # ---- mismatch ----
    # 説明理由を集める
    reasons: list[str] = []

    if result.is_top_mark_single:
        reasons.append(
            f"◎{top_mark}は単騎評価のため、ライン援護が無く、買い目では"
            f"2着/3着または参考候補寄りに扱います。"
        )
        # 単騎◎の頭買い目が ana / ooana / watch_only にあるか
        all_followup = (
            list(plan.ana) + list(plan.ooana) + list(plan.watch_only)
        )
        followup_heads = {
            parts[0] for b in all_followup
            if (parts := _split_combo(b.combination)) is not None
        }
        if top_mark in followup_heads:
            reasons.append(
                f"◎{top_mark}単騎の頭買い目は穴/参考候補に含まれています。"
            )

    # market_bias 由来の説明
    # codex P2 反映: final_best だけでなく final_osae の頭も対象に含める。
    # 市場偏り頭が押さえに残っているケースも「市場側を採用」と説明できる。
    if input_data is not None:
        try:
            from ..output_validation import detect_market_bias
            bias = detect_market_bias(input_data)
            covered_heads = fb_heads | fo_heads
            if (
                bias.has_head_focus
                and bias.focused_head is not None
                and bias.focused_head != top_mark
                and bias.focused_head in covered_heads
            ):
                reasons.append(
                    f"◎{top_mark}評価だが、市場は{bias.focused_head}番頭"
                    f"に集中しており、オッズ取得済み候補では"
                    f"{bias.focused_head}頭を暫定上位にしています。"
                )
        except Exception:
            # detect_market_bias 失敗時は無視 (説明なし)
            pass

    # purchase_mode 由来の説明
    # Phase 15 (2026-05-25): 非 BUYABLE 時は「購入対象」「実購入対象」等の
    # 禁止語を一切出さない。否定文 (「ではなく」) であっても validator は
    # 部分一致で検出するため、中立な「参考表示」のみで完結させる。
    mode = plan.purchase_mode
    if mode in (PurchaseMode.WATCH_ONLY, PurchaseMode.SKIP):
        reasons.append(
            f"purchase_mode={mode.name} のため、final_* は参考表示です。"
            f"オッズ再取得後に判断してください。"
        )

    # ◎を含む候補は odds 未取得のみ、かつ final_best には odds 取得済みが
    # あるなら「オッズ取得済みを優先した結果」と説明できる。
    pred_combos_with_top_mark = []
    for bucket in (
        prediction.honsen, prediction.osae,
        prediction.ana, prediction.ooana,
    ):
        for b in bucket:
            parts = _split_combo(b.combination)
            if parts is not None and top_mark in parts:
                pred_combos_with_top_mark.append(b)
    if pred_combos_with_top_mark:
        top_mark_all_no_odds = all(
            b.market_odds is None for b in pred_combos_with_top_mark
        )
        final_best_has_odds = any(
            b.market_odds is not None for b in plan.final_best
        )
        if top_mark_all_no_odds and final_best_has_odds:
            reasons.append(
                f"◎{top_mark}を含む候補はオッズ未取得のみで、"
                f"final_best はオッズ取得済みを優先した結果です。"
            )

    result.notes.extend(reasons)

    if reasons:
        result.alignment_level = "explainable_mismatch"
    else:
        result.alignment_level = "dangerous_mismatch"
        result.warnings.append(
            f"◎{top_mark}が final_best / final_osae の頭・2着に絡まない"
            f"のに説明可能な理由が見当たりません (BUYABLE/TENTATIVE)。"
        )

    return result
