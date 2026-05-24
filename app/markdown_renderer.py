"""MarkdownRenderer: OutputPlan から 12項目 Markdown を deterministic 生成。

LLM が返す自然文 (summary_text / trend_text / weather_text / line_text /
reason_texts / reflection_points) は装飾として使うが、買い目の最終分類は
OutputPlan から取り出す。これにより、final_conclusion 等に未登録 combo が
混入する事故を完全に防ぐ。

責務:
- 12項目 (## 1-12) の Markdown 生成
- final_conclusion の deterministic 生成 (LLM の final_conclusion は無視)
- 実購入判断セクションを OutputPlan の final_best/final_osae/... から生成
- 警告 (OutputPlanWarning) を末尾に表示
- レンダ後の Markdown 中に未登録 combo を見つけた場合のフォールバック検証
"""

from __future__ import annotations

import re
from typing import Optional

from .models import BetRecommendation, Prediction, RaceInput
from .output_plan import OutputPlan, OutputPlanWarning


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _format_bet(b: BetRecommendation) -> str:
    """1買い目を 1行 Markdown に整形。"""
    bits = [f"  - 3連単 {b.combination}"]
    if b.reason:
        bits.append(f" / {b.reason}")
    if b.market_odds is not None:
        bits.append(f"  ({b.market_odds:.1f}倍")
        if b.value_label:
            bits.append(f" / {b.value_label}")
        bits.append(")")
    elif b.value_label:
        bits.append(f"  ({b.value_label})")
    else:
        bits.append("  (オッズ未取得・要確認)")
    return "".join(bits)


def _format_bets(bets: list[BetRecommendation]) -> str:
    if not bets:
        return "  （該当なし）"
    return "\n".join(_format_bet(b) for b in bets)


def _format_marks(marks: dict[str, int]) -> str:
    if not marks:
        return "(印なし)"
    order = ["◎", "◯", "▲", "△", "×", "α", "β"]
    parts: list[str] = []
    for mk in order:
        if mk in marks:
            parts.append(f"{mk}: {marks[mk]}")
    for mk, car in marks.items():
        if mk not in order:
            parts.append(f"{mk}: {car}")
    return "  ".join(parts)


def _extract_combos_from_markdown(md: str) -> set[str]:
    """Markdown 中の 3連単 combination (X-Y-Z) を抽出。"""
    return set(re.findall(r"\b\d-\d-\d\b", md))


# ---------------------------------------------------------------------------
# メイン関数
# ---------------------------------------------------------------------------


def render_final_conclusion(plan: OutputPlan) -> str:
    """final_conclusion を OutputPlan からのみ生成する (LLM 出力は完全無視)。

    2026-05-24 文言整合性 (837b8ee 後続レビュー反映):
    - plan.final_best がある場合:
        「一番買いたい買い目は ... を中心に据える」
    - plan.final_best 空 + plan.final_osae あり:
        「本線はオッズ確認後の判断とし、押さえるべき買い目は ...」
    - 両方空:
        「オッズ取得済みで買える候補なし — オッズ確認後に判断」
    - final_ana あり:
        「少額で足す穴は ...」を追記
    - gami_warning あり:
        「安い人気筋・ガミ注意は ... (厚く買わない)」を追記
    """
    parts: list[str] = []

    if plan.final_best:
        best_str = ", ".join(b.combination for b in plan.final_best)
        parts.append(f"一番買いたい買い目は {best_str} を中心に据える。")
    elif plan.final_osae:
        # final_best 空 + final_osae あり: 「本線はオッズ確認後判断」と
        # 明示し、「本線は X を中心」とは書かない (osae を本線扱いしない)
        osae_str = ", ".join(b.combination for b in plan.final_osae)
        parts.append(
            f"本線はオッズ確認後の判断とし、押さえるべき買い目は "
            f"{osae_str} を確認推奨。"
        )
    else:
        parts.append(
            "オッズ取得済みで買える候補なし — オッズ確認後に判断してください。"
        )

    if plan.final_ana:
        longshot_str = ", ".join(b.combination for b in plan.final_ana)
        parts.append(f"少額で足す穴は {longshot_str}。")

    if plan.gami_warning:
        gami_str = ", ".join(b.combination for b in plan.gami_warning[:3])
        parts.append(
            f"安い人気筋・ガミ注意は {gami_str} — 厚く買わない (確認程度)。"
        )

    return " ".join(parts)


def render_purchase_judgement_block(plan: OutputPlan) -> list[str]:
    """実購入判断ブロック (### 実購入判断 配下) を OutputPlan から生成。"""
    lines: list[str] = []

    if plan.final_best:
        combos = " / ".join(b.combination for b in plan.final_best)
        lines.append(
            f"- **オッズ取得済みで買える候補**: {combos}"
            f"（妙味/本線向き、購入対象）"
        )
    else:
        lines.append(
            "- **オッズ取得済みで買える候補**: 該当なし → "
            "オッズ確認後に判断"
        )

    if plan.final_osae:
        combos = " / ".join(b.combination for b in plan.final_osae)
        lines.append(f"- **押さえとして必要**: {combos}")

    if plan.final_ana:
        combos = " / ".join(b.combination for b in plan.final_ana)
        lines.append(f"- **少額の穴**: {combos}（1点までを目安に）")

    if plan.gami_warning:
        combos = " / ".join(
            f"{b.combination}({b.market_odds:.1f}倍)"
            if b.market_odds is not None else b.combination
            for b in plan.gami_warning[:3]
        )
        lines.append(
            f"- **安い人気筋**: {combos} は売れすぎ / ガミ注意 → 厚く買わない"
        )

    if plan.watch_only:
        combos = " / ".join(b.combination for b in plan.watch_only[:2])
        lines.append(f"- **参考表示 (確認程度)**: {combos}")

    return lines


def render_output_plan(
    plan: OutputPlan,
    prediction: Prediction,
    input_data: Optional[RaceInput] = None,
) -> str:
    """OutputPlan から 12項目 Markdown を deterministic 生成する。

    Args:
        plan: 唯一の source of truth (買い目分類)
        prediction: LLM の自然文 (summary / trend / weather / lines /
                    reflection_points / marks) を借りる
        input_data: オッズ取得率 / データ品質 / 市場偏り / 整合性警告に使う

    Returns:
        12項目形式の Markdown 文字列
    """
    # サニタイズ (穴馬→穴目 等)
    from .output_validation import sanitize_prediction
    sanitize_prediction(prediction)

    lines: list[str] = []
    lines.append(f"# 予想結果  {prediction.race_id}")
    lines.append("")
    lines.append("## 1. レース概要")
    lines.append(prediction.summary or "(LLM未提供)")
    lines.append("")
    lines.append("## 2. 直近結果からの場の傾向")
    lines.append(prediction.venue_trend_text or "(LLM未提供)")
    lines.append("")
    lines.append("## 3. 天候・雨・風補正")
    lines.append(prediction.weather_text or "(LLM未提供)")
    lines.append("")
    lines.append("## 4. 並び")
    lines.append(prediction.lines_text or "(LLM未提供)")
    lines.append("")
    lines.append("## 5. 印")
    lines.append(_format_marks(prediction.marks))
    lines.append("")

    # 本線セクション: OutputPlan.honsen を「実購入候補 (odds取得済み)」と
    # 「オッズ確認後の本線候補 (odds=None)」に分けて表示
    lines.append("## 6. 本線")
    honsen_with_odds = [b for b in plan.honsen if b.market_odds is not None]
    honsen_no_odds = [b for b in plan.honsen if b.market_odds is None]
    if honsen_with_odds:
        lines.append("**実購入候補** (最大3点):")
        lines.append(_format_bets(honsen_with_odds))
    elif not honsen_no_odds:
        lines.append(
            "（本線にオッズ取得済みの実購入候補なし。オッズ確認後に判断してください）"
        )
    if honsen_no_odds:
        lines.append("")
        lines.append("**オッズ確認後の本線候補** (オッズ取得後に再判断):")
        lines.append(_format_bets(honsen_no_odds))
    lines.append("")

    lines.append("## 7. 押さえ")
    lines.append(_format_bets(plan.osae))
    lines.append("")

    lines.append("## 8. 穴")
    lines.append(_format_bets(plan.ana))
    lines.append("")

    lines.append("## 9. 大穴")
    lines.append(_format_bets(plan.ooana))
    lines.append("")

    # 10. 最終結論 - OutputPlan からのみ生成 (LLM final_conclusion は無視)
    lines.append("## 10. 最終結論")
    lines.append(render_final_conclusion(plan))
    lines.append("")
    lines.append("### 実購入判断")
    lines.extend(render_purchase_judgement_block(plan))
    lines.append("")

    lines.append("## 11. ガミ回避メモ")
    lines.append(prediction.gami_memo or "(該当なし)")
    lines.append("")
    lines.append("## 12. 結果入力後に保存すべき反省ポイント")
    if prediction.reflection_points:
        for pt in prediction.reflection_points:
            lines.append(f"- {pt}")
    else:
        lines.append("- （該当なし）")
    lines.append("")

    # 末尾: オッズ取得率 / データ品質 / 市場偏り / 整合性 / OutputPlan 警告
    lines.append("---")
    if input_data is not None:
        from .output_validation import (
            assess_data_quality,
            compute_odds_coverage,
            render_odds_coverage_section,
            summarize_market_bias,
            validate_prediction_output,
        )
        coverage = compute_odds_coverage(prediction)
        lines.append("")
        lines.append(render_odds_coverage_section(coverage))
        quality = assess_data_quality(input_data)
        lines.append("")
        lines.append(f"### データ品質: **{quality}**")
        if quality in ("low", "very_low"):
            lines.append(
                "- データ不足のため買い目を広げすぎず、"
                "オッズ取得済み買い目を優先してください"
            )
        bias = summarize_market_bias(input_data)
        if bias:
            lines.append("")
            lines.append("### 市場の偏り")
            lines.append(f"- {bias}")
        # validate_prediction_output: OutputPlan 経由なので
        # CONCLUSION_COMBO_UNREGISTERED は出ない想定
        warnings_v = validate_prediction_output(input_data, prediction)
        if warnings_v:
            lines.append("")
            lines.append("### 出力整合性チェック")
            for w in warnings_v:
                lines.append(f"- ⚠️ [{w.code}] {w.message}")
        if plan.warnings:
            lines.append("")
            lines.append("### OutputPlan 警告")
            for w in plan.warnings:
                lines.append(f"- ⚠️ [{w.code}] {w.message}")
        lines.append("---")
    lines.append(
        "（本ツールは予想支援目的のみ。自動投票・購入処理は持ちません）"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# フォールバック検証
# ---------------------------------------------------------------------------


def verify_markdown_combos(md: str, plan: OutputPlan) -> set[str]:
    """Markdown 中の 3連単 combo のうち、OutputPlan に存在しない combo を返す。

    空集合なら整合性 OK。空でなければフォールバック必要。

    codex review 反映 (2026-05-24): 並び表記 (`[本命] 1-2-3` 等) の
    line_text や reason 文を含めると誤検出するため、検証対象を
    「## 6. 本線」以降〜「---」(末尾フッタ) までに絞る。具体的には
    買い目セクション (本線/押さえ/穴/大穴) と結論部 (最終結論/実購入判断) のみ。
    """
    # 「## 6. 本線」以降〜末尾フッタ直前までを対象に
    if "## 6. 本線" not in md:
        # フォーマット異常時は全体を見る (フォールバック)
        target = md
    else:
        target = md.split("## 6. 本線", 1)[1]
        # 末尾の「---」フッタ以降は除外 (整合性警告セクション自体が誤検出される
        # ことを防ぐため)
        if "\n---\n" in target:
            target = target.rsplit("\n---\n", 1)[0]
    # さらに「## 4. 並び」を含む lines_text 由来の誤検出を防ぐため、
    # `## 4. 並び` セクションは除外 (上の split で既に除外済みだが念のため)
    md_combos = _extract_combos_from_markdown(target)
    plan_combos = plan.all_combos()
    return md_combos - plan_combos
