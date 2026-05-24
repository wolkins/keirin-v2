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

    武雄12R 後続レビュー反映 (fee60e4 → 2026-05-24):
    - has_skip_purchase_warning() True 時:
        「見送り寄り。候補として X は残すが、購入はオッズ再取得後に判断。」
    - has_low_coverage_warning() True 時 (skip 以外):
        「オッズ取得済みの暫定候補は X。
         ただしオッズ取得率が低いため、購入判断は再確認後。」
    - 低カバレッジ時の final_ana は「穴候補は参考まで」に弱める。
    """
    parts: list[str] = []
    skip_purchase = plan.has_skip_purchase_warning()
    low_coverage = plan.has_low_coverage_warning()

    if plan.final_best:
        best_str = ", ".join(b.combination for b in plan.final_best)
        if skip_purchase:
            # 「見送り寄り」は value_label と衝突するため文言を「見送り推奨」に
            parts.append(
                f"購入見送り推奨。候補として {best_str} は残すが、"
                f"購入はオッズ再取得後に判断。"
            )
        elif low_coverage:
            parts.append(
                f"オッズ取得済みの暫定候補は {best_str}。"
                f"ただしオッズ取得率が低いため、購入判断は再確認後。"
            )
        else:
            parts.append(f"一番買いたい買い目は {best_str} を中心に据える。")
    elif plan.final_osae:
        # final_best 空 + final_osae あり: 「本線はオッズ確認後判断」と
        # 明示し、「本線は X を中心」とは書かない (osae を本線扱いしない)
        # fee60e4 後続レビュー反映: ここでも skip/low 分岐 (網羅漏れ修正)
        osae_str = ", ".join(b.combination for b in plan.final_osae)
        if skip_purchase:
            parts.append(
                f"購入見送り推奨。押さえ候補 {osae_str} は残すが、"
                f"購入はオッズ再取得後に判断。"
            )
        elif low_coverage:
            parts.append(
                f"オッズ取得率が低いため暫定。押さえ候補は {osae_str} を "
                f"再確認後に判断推奨。"
            )
        else:
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
        if skip_purchase or low_coverage:
            # 低カバレッジ時は「少額で足す」推奨を弱める
            parts.append(f"穴候補は参考まで: {longshot_str}。")
        else:
            parts.append(f"少額で足す穴は {longshot_str}。")

    if plan.gami_warning:
        gami_str = ", ".join(b.combination for b in plan.gami_warning[:3])
        parts.append(
            f"安い人気筋・ガミ注意は {gami_str} — 厚く買わない (確認程度)。"
        )

    return " ".join(parts)


def render_purchase_judgement_block(plan: OutputPlan) -> list[str]:
    """実購入判断ブロック (### 実購入判断 配下) を OutputPlan から生成。

    武雄12R 後続レビュー反映 (fee60e4 → 2026-05-24):
    - 通常時:
        「オッズ取得済みで買える候補: X（妙味/本線向き、購入対象）」
    - low coverage:
        「オッズ取得済みの暫定候補: X
          （オッズ取得率が低いため、購入判断は再確認後）」
    - skip purchase (very_high + low coverage or 極めて低カバレッジ):
        「見送り寄り: X（高難度 + 低オッズ取得率のため、購入は控えめ）」
    「購入対象」は low coverage / skip purchase 時には出さない。
    """
    lines: list[str] = []
    skip_purchase = plan.has_skip_purchase_warning()
    low_coverage = plan.has_low_coverage_warning()

    if plan.final_best:
        combos = " / ".join(b.combination for b in plan.final_best)
        if skip_purchase:
            # 「見送り寄り」は value_label と衝突するため「購入見送り推奨」を使用
            lines.append(
                f"- **購入見送り推奨**: {combos}"
                f"（高難度 + 低オッズ取得率のため、購入は控えめ）"
            )
        elif low_coverage:
            lines.append(
                f"- **オッズ取得済みの暫定候補**: {combos}"
                f"（オッズ取得率が低いため、購入判断は再確認後）"
            )
        else:
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
        # fee60e4 後続レビュー反映: skip/low 時は「押さえとして必要」表記を
        # 弱める (「再確認後」「見送り寄り」を明記)
        if skip_purchase:
            lines.append(
                f"- **押さえ候補 (購入見送り推奨)**: {combos}"
                f"（高難度 + 低オッズ取得率のため、購入は控えめ）"
            )
        elif low_coverage:
            lines.append(
                f"- **押さえ暫定候補**: {combos}"
                f"（オッズ取得率が低いため、購入判断は再確認後）"
            )
        else:
            lines.append(f"- **押さえとして必要**: {combos}")

    if plan.final_ana:
        combos = " / ".join(b.combination for b in plan.final_ana)
        if skip_purchase or low_coverage:
            # 低カバレッジ時は「少額で足す」推奨を弱める
            lines.append(f"- **穴候補 (参考まで)**: {combos}")
        else:
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
    # サニタイズ (穴馬→穴目 等 + 新人戦用語置換)
    # 2026-05-24: input_data があれば is_rookie 判定を sanitize に渡す
    from .output_validation import sanitize_prediction
    is_rookie = bool(
        input_data is not None
        and input_data.race.resolved_is_rookie()
    )
    sanitize_prediction(prediction, is_rookie=is_rookie)

    lines: list[str] = []
    lines.append(f"# 予想結果  {prediction.race_id}")
    lines.append("")
    lines.append("## 1. レース概要")
    lines.append(prediction.summary or "(LLM未提供)")
    lines.append("")
    lines.append("## 2. 直近結果からの場の傾向")
    lines.append(prediction.venue_trend_text or "(LLM未提供)")
    # 静岡4R #378: venue_trend に long_term / today があれば併記
    # a122ae1 後続レビュー反映: long_term / today は RaceInput から直接
    # markdown へ流れるため、ガールズ/新人戦サニタイズ経路をすり抜ける。
    # 表示前に sanitize_venue_trend_text でライン前提語を置換する。
    venue_trend_obj = (
        input_data.venue_trend if input_data is not None else None
    )
    if venue_trend_obj is not None:
        from .output_validation import sanitize_venue_trend_text
        is_g = bool(prediction.is_girls)
        if venue_trend_obj.long_term:
            text = sanitize_venue_trend_text(
                venue_trend_obj.long_term, is_girls=is_g, is_rookie=is_rookie,
            )
            lines.append("")
            lines.append(f"- **長期傾向**: {text}")
        if venue_trend_obj.today:
            text = sanitize_venue_trend_text(
                venue_trend_obj.today, is_girls=is_g, is_rookie=is_rookie,
            )
            lines.append(f"- **当日傾向**: {text}")
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
    # 平塚4R/6R 後続レビュー反映 (2026-05-24): low coverage / skip purchase
    # 時は「実購入候補」を出さず「暫定候補」「見送り寄りの参考候補」に弱体化
    # 平塚7R 後続レビュー反映 (2026-05-24, codex P2): plan.honsen は
    # 実購入候補のみ (最大3点契約)、見送り寄りは plan.honsen_miokuri から取る
    lines.append("## 6. 本線")
    honsen_real_with_odds = [
        b for b in plan.honsen if b.market_odds is not None
    ]
    # 見送り寄りは別フィールド (honsen_miokuri) から取得 (display 3点契約維持)
    honsen_miokuri_with_odds = [
        b for b in plan.honsen_miokuri if b.market_odds is not None
    ]
    honsen_no_odds = [b for b in plan.honsen if b.market_odds is None]
    skip_purchase_section = plan.has_skip_purchase_warning()
    low_coverage_section = plan.has_low_coverage_warning()
    if honsen_real_with_odds:
        if skip_purchase_section:
            lines.append("**見送り寄りの参考候補** (購入はオッズ再取得後):")
        elif low_coverage_section:
            lines.append(
                "**オッズ取得済みの暫定候補** (再確認後に判断):"
            )
        else:
            lines.append("**実購入候補** (最大3点):")
        lines.append(_format_bets(honsen_real_with_odds))
    elif not honsen_no_odds and not honsen_miokuri_with_odds:
        lines.append(
            "（本線にオッズ取得済み候補なし。オッズ確認後に判断してください）"
        )
    if honsen_miokuri_with_odds:
        # 「見送り寄り」は実購入候補と区別して参考表示に
        # 説明文から「購入対象」表現を避ける (本文「購入対象」禁止と整合)
        lines.append("")
        lines.append(
            "**見送り寄りの参考候補** (参考表示・厚く買わない):"
        )
        lines.append(_format_bets(honsen_miokuri_with_odds))
    if honsen_no_odds:
        lines.append("")
        # 平塚10R 後続レビュー反映 (2026-05-24): ガールズ・新人戦 or
        # low_coverage では「本線候補」→「上位候補」「暫定候補」に弱体化
        is_rookie_or_girls = bool(
            input_data is not None
            and (
                input_data.race.resolved_is_girls()
                or input_data.race.resolved_is_rookie()
            )
        )
        if is_rookie_or_girls or low_coverage_section:
            lines.append(
                "**オッズ確認後の上位候補** (オッズ取得後に再判断):"
            )
        else:
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
    # codex review 反映 (2026-05-24, #463): 警告セクション開始位置を
    # 文字列検索ではなく list index で確実に保持。LLM 本文や gami_memo に
    # 「### 出力整合性チェック」「### OutputPlan 警告」というマーカー文字列が
    # 含まれていても、本文側を境界と誤判定しない。
    warning_section_start_line: int | None = None
    if input_data is not None:
        from .output_validation import (
            assess_data_quality_breakdown,
            compute_odds_coverage,
            render_odds_coverage_section,
            summarize_market_bias,
            validate_prediction_output,
        )
        # 平塚6R 対応 (2026-05-24): plan を渡して gami_warning を
        # 「実購入本線」から除外し、本線欄表示と取得率集計を整合させる
        coverage = compute_odds_coverage(prediction, plan=plan)
        lines.append("")
        lines.append(render_odds_coverage_section(coverage))
        # codex review 反映 (2026-05-24): coverage を渡して、
        # 低カバレッジ時に data_quality=high を抑制
        # 静岡4R #377: 5項目内訳を併記
        breakdown = assess_data_quality_breakdown(input_data, coverage=coverage)
        quality = breakdown.overall
        lines.append("")
        lines.append(f"### データ品質: **{quality}**")
        lines.extend(breakdown.to_markdown_lines())
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
        # codex review 反映 (2026-05-24): validate / OutputPlan の warning
        # message はサニタイズ対象外。これらは「禁止語が含まれる」と通知する
        # 文書なので、禁止語自体を置換すると警告の意味が壊れる
        # (例: 「『本命ライン』が含まれます」 → 「『本命候補』が含まれます」で意味不明)
        warnings_v = validate_prediction_output(input_data, prediction)
        if warnings_v:
            if warning_section_start_line is None:
                warning_section_start_line = len(lines)
            lines.append("")
            lines.append("### 出力整合性チェック")
            for w in warnings_v:
                lines.append(f"- ⚠️ [{w.code}] {w.message}")
        if plan.warnings:
            if warning_section_start_line is None:
                warning_section_start_line = len(lines)
            lines.append("")
            lines.append("### OutputPlan 警告")
            for w in plan.warnings:
                lines.append(f"- ⚠️ [{w.code}] {w.message}")
        lines.append("---")
    lines.append(
        "（本ツールは予想支援目的のみ。自動投票・購入処理は持ちません）"
    )

    # 平塚10R 後続レビュー反映 (2026-05-24): low coverage 時に value_label
    # 表示と「(本線)」表記を「暫定候補」等に弱体化
    # validate/OutputPlan 警告セクションは検証用なのでサニタイズ対象外
    # (codex 既反映: warning message の禁止語通知を維持)
    if plan.has_low_coverage_warning():
        from .output_validation import sanitize_low_quality_text
        if warning_section_start_line is not None:
            head_text = sanitize_low_quality_text(
                "\n".join(lines[:warning_section_start_line])
            )
            tail_text = "\n".join(lines[warning_section_start_line:])
            md = head_text + "\n" + tail_text if tail_text else head_text
        else:
            md = sanitize_low_quality_text("\n".join(lines))
    else:
        md = "\n".join(lines)

    return md


# ---------------------------------------------------------------------------
# フォールバック検証
# ---------------------------------------------------------------------------


def verify_markdown_combos(md: str, plan: OutputPlan) -> set[str]:
    """Markdown 中の 3連単 combo のうち、OutputPlan に存在しない combo を返す。

    空集合なら整合性 OK。空でなければフォールバック必要。

    検証範囲 (ba87962 後続レビュー反映, 2026-05-24):
        - **開始**: `## 6. 本線` 以降
        - **終了**: `## 11. ガミ回避メモ` の直前
          (ガミ回避メモ・反省ポイントの自然文 combo は LLM 装飾文として
          許容し、未登録扱いしない)
        - `## 11.` が見つからない場合のみ、従来通り末尾フッタ `\n---\n` 前まで
    対象セクション:
        - 本線 / 押さえ / 穴 / 大穴 (## 6 - ## 9)
        - 最終結論 + 実購入判断 (## 10)
    対象外セクション:
        - ガミ回避メモ (## 11) — 「前回 4-3-6 は買わずに失敗」等の自然文
        - 反省ポイント (## 12) — 「3-4-6 を切った反省」等の自然文
        - 末尾フッタ (整合性チェック / 警告セクション)
    """
    # 「## 6. 本線」以降を対象に開始
    if "## 6. 本線" not in md:
        # フォーマット異常時は全体を見る (フォールバック)
        target = md
    else:
        target = md.split("## 6. 本線", 1)[1]
        # 終了: 「## 11. ガミ回避メモ」直前 で切る (優先)
        if "## 11. ガミ回避メモ" in target:
            target = target.split("## 11. ガミ回避メモ", 1)[0]
        elif "\n---\n" in target:
            # ## 11 が無い場合のみ末尾フッタ前まで (codex review 反映)
            target = target.rsplit("\n---\n", 1)[0]
    md_combos = _extract_combos_from_markdown(target)
    plan_combos = plan.all_combos()
    return md_combos - plan_combos
