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

from .decision import PurchaseMode
from .models import BetRecommendation, Prediction, RaceInput
from .output_plan import OutputPlan, OutputPlanWarning


# Phase 5 follow-up (2026-05-24): 単独「番手」+助詞 / 数字付き「番手」検出用。
# 「番手頭」「番手差し」は別途 compound でマッチするので、ここでは
# 「番手」直後に助詞 (の/が/から/を/で/は/に/と) や末尾文字が来るパターンを
# 拾う。これにより「番手の浮上」「番手から差し」などが検出される。
# 数字付き (3番手 / 4番手 / 5番手 等) は「ライン3番手」「4番手評価」と
# 重複するが、重複検出は許容 (allow_line_logic=False で本来出るべきでない)。
# codex Phase 6 P2 反映 (2026-05-25): 「番手から」が漏れていたため
# alternation で書き直し。複数文字の助詞 (「から」「まで」「には」等) も
# 拡張可能な形にする。
_STANDALONE_BANTAN_REGEX = re.compile(
    r"番手(?:の|が|を|で|に|と|は|から|まで|より|へ)"
)
_NUMBERED_BANTAN_REGEX = re.compile(r"[3-9]番手")


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
    # Phase 1 (2026-05-24): warning helper から purchase_mode ベースに移行。
    # 旧 has_skip_purchase_warning / has_low_coverage_warning と整合する形で
    # 4段階 (SKIP/WATCH_ONLY/TENTATIVE/BUYABLE) に分岐。
    mode = plan.purchase_mode
    skip_purchase = (mode == PurchaseMode.SKIP)
    watch_only_mode = (mode == PurchaseMode.WATCH_ONLY)
    tentative_mode = (mode == PurchaseMode.TENTATIVE)

    if plan.final_best:
        best_str = ", ".join(b.combination for b in plan.final_best)
        if skip_purchase:
            # SKIP: 購入判断はしない、再取得後に再検討
            parts.append(
                f"購入見送り推奨。候補として {best_str} は残すが、"
                f"購入はオッズ再取得後に判断。"
            )
        elif watch_only_mode:
            # WATCH_ONLY: 見送り寄り (参考候補、厚く買わない)
            parts.append(
                f"見送り寄り。参考候補は {best_str} だが、"
                f"厚く買わない (確認程度)。"
            )
        elif tentative_mode:
            parts.append(
                f"オッズ取得済みの暫定候補は {best_str}。"
                f"ただしオッズ取得率が低いため、購入判断は再確認後。"
            )
        else:  # BUYABLE
            parts.append(f"一番買いたい買い目は {best_str} を中心に据える。")
    elif plan.final_osae:
        # final_best 空 + final_osae あり: 「本線はオッズ確認後判断」と
        # 明示し、「本線は X を中心」とは書かない (osae を本線扱いしない)
        osae_str = ", ".join(b.combination for b in plan.final_osae)
        if skip_purchase:
            parts.append(
                f"購入見送り推奨。押さえ候補 {osae_str} は残すが、"
                f"購入はオッズ再取得後に判断。"
            )
        elif watch_only_mode:
            parts.append(
                f"見送り寄り。参考の押さえ候補は {osae_str} だが、"
                f"厚く買わない。"
            )
        elif tentative_mode:
            parts.append(
                f"オッズ取得率が低いため暫定。押さえ候補は {osae_str} を "
                f"再確認後に判断推奨。"
            )
        else:  # BUYABLE
            parts.append(
                f"本線はオッズ確認後の判断とし、押さえるべき買い目は "
                f"{osae_str} を確認推奨。"
            )
    else:
        # codex P2 反映: final_best/osae 両方空。validator の禁止語
        # (「買える候補」「購入対象」等) を避けるため mode 別に文言を変える。
        if skip_purchase:
            parts.append("見送り。購入候補なし — オッズ再取得後に再検討してください。")
        elif watch_only_mode:
            parts.append("見送り寄り。参考候補なし — オッズ取得後に判断してください。")
        elif tentative_mode:
            parts.append("暫定候補なし — オッズ再確認後に判断してください。")
        else:  # BUYABLE
            parts.append(
                "オッズ取得済みで買える候補なし — オッズ確認後に判断してください。"
            )

    if plan.final_ana:
        longshot_str = ", ".join(b.combination for b in plan.final_ana)
        if skip_purchase or watch_only_mode or tentative_mode:
            # 非 BUYABLE 時は「少額で足す」推奨を弱める
            parts.append(f"穴候補は参考まで: {longshot_str}。")
        else:
            parts.append(f"少額で足す穴は {longshot_str}。")

    if plan.gami_warning:
        gami_str = ", ".join(b.combination for b in plan.gami_warning[:3])
        parts.append(
            f"安い人気筋・ガミ注意は {gami_str} — 厚く買わない (確認程度)。"
        )

    return " ".join(parts)


def _render_watch_only_breakdown(plan: OutputPlan) -> list[str]:
    """Phase 8 (2026-05-25): watch_only_reason_groups を理由別に表示する.

    各 group ごとに最大 2 点まで表示。Renderer は説明文を生成するだけで、
    分類自体は OutputPlan 側 (watch_only_reason_groups) で完結している。

    表示順 (固定): line_source_filtered → market_bias_suppressed →
                  max_final_best_overflow → gami_warning → low_quality_watch →
                  manual_watch
    """
    # reason group → 表示ラベル
    # Phase 10 (2026-05-25): 「ライン由来のため除外」が新人戦サニタイズの
    # 禁止語「ライン」に引っかかるため、中立表現「構造前提のため除外」に
    # 変更。意味は同じ (line_* / separate_* タグ候補が allow_line_logic=
    # False で除外された)。
    label_map = {
        "line_source_filtered": "構造前提のため除外",
        "market_bias_suppressed": "市場偏りの同一軸過多で抑制",
        "max_final_best_overflow": "点数上限で移動",
        "gami_warning": "ガミ注意",
        "low_quality_watch": "低品質のため参考",
        "manual_watch": "手動参考",
    }
    out: list[str] = []
    # 固定順で iterate
    # codex P2 反映 (Phase 8, 2026-05-25): "gami_warning" は最終結論・
    # 実購入判断・本文の「安い人気筋」セクションで既に表示されているため、
    # 内訳セクションでは **表示しない** (3 重表示を避ける)。データとしては
    # watch_only_reason_groups['gami_warning'] に保持される (テスト/API 用)。
    for group_key in (
        "line_source_filtered",
        "market_bias_suppressed",
        "max_final_best_overflow",
        # "gami_warning" は表示除外 (codex P2 反映)
        "low_quality_watch",
        "manual_watch",
    ):
        group = plan.watch_only_reason_groups.get(group_key)
        if not group:
            continue
        label = label_map.get(group_key, group_key)
        combos = " / ".join(b.combination for b in group[:2])
        out.append(f"- **{label}**: {combos}")
    return out


def validate_line_terms_when_not_allowed(
    plan: OutputPlan, md_body: str,
) -> list[OutputPlanWarning]:
    """Phase 5: policy.allow_line_logic=False のとき本文にライン用語が
    残っていたら LINE_TERMS_LEAKED warning を返す.

    対象禁止語 (本文限定、警告セクション以前):
    - 本命ライン / 番手頭 / 番手差し / 別線番手 / ライン3番手 / 4番手評価
    - 単独「番手」(数字付きは別ロジックで処理されるため、単独だけチェック)

    Renderer/Sanitizer の置換が漏れたケースを検出するセーフティネット。
    template fallback はせず、可視化のみ (sanitize_low_quality_text と同様)。
    """
    policy = getattr(plan, "_race_type_policy", None)
    if policy is None or policy.allow_line_logic:
        return []

    out: list[OutputPlanWarning] = []
    # 長い表現を先にチェック (短い表現に部分マッチしないように)
    forbidden_compound = (
        # codex P2 反映 (Phase 5, 2026-05-24): 仕様12 / 強風補正 由来の
        # compound 表現も検出語に追加。「本線」「本命」単独はカテゴリ名・
        # 中立語と衝突するため compound のみ。
        "本命ライン",
        "本命先頭",
        "本線先頭",
        "本線2位",
        "番手頭",
        "番手差し",
        "別線番手",
        "別線自力",
        "別線決着",
        "ライン3番手",
        "4番手評価",
        "3車ライン",
        "4車ライン",
        "4番手流れ込み",
    )
    for word in forbidden_compound:
        if word in md_body:
            out.append(OutputPlanWarning(
                code="LINE_TERMS_LEAKED",
                severity="warning",
                message=(
                    f"race_type={plan.race_type} "
                    f"(allow_line_logic=False) なのに本文に「{word}」が"
                    f"残っています。"
                ),
            ))

    # Phase 5 follow-up (2026-05-24): 単独「番手」+助詞の検出。
    # 「番手頭」「番手差し」は上の compound で既に検出済み (重複検出は OK)。
    # 「3番手」「4番手」「5番手」など数字付きも、allow_line_logic=False では
    # 個人戦の用語に置換されるべきため検出する。
    # 通常戦への影響を避けるため、本関数自体が allow_line_logic=False の
    # ときだけ呼ばれる前提。
    standalone_bantan = _STANDALONE_BANTAN_REGEX.search(md_body)
    if standalone_bantan is not None:
        out.append(OutputPlanWarning(
            code="LINE_TERMS_LEAKED",
            severity="warning",
            message=(
                f"race_type={plan.race_type} "
                f"(allow_line_logic=False) なのに本文に単独「番手」"
                f"({standalone_bantan.group(0)}) が残っています。"
            ),
        ))
    numbered_bantan = _NUMBERED_BANTAN_REGEX.search(md_body)
    if numbered_bantan is not None:
        out.append(OutputPlanWarning(
            code="LINE_TERMS_LEAKED",
            severity="warning",
            message=(
                f"race_type={plan.race_type} "
                f"(allow_line_logic=False) なのに本文に数字付き「番手」"
                f"({numbered_bantan.group(0)}) が残っています。"
            ),
        ))
    return out


def validate_purchase_mode_markdown(
    plan: OutputPlan, md_body: str,
) -> list[OutputPlanWarning]:
    """purchase_mode と Markdown 本文の整合性をチェックする (Phase 1).

    purchase_mode != BUYABLE のとき、本文に強い購入表現が残っていたら
    warning を返す。template fallback はせず、警告セクションでの可視化に
    留める (Renderer の文言分岐バグを検知するためのセーフティネット)。

    禁止語:
    - 共通 (非 BUYABLE で禁止):
        購入対象 / 実購入対象 / 一番買いたい / 実購入候補
    - WATCH_ONLY / SKIP で追加禁止:
        買える候補 / 本線向き

    Phase 15 (2026-05-25): 「実購入対象」を basic_forbidden に追加。
    否定文 (「実購入対象ではなく参考表示」など) でも禁止語が含まれていれば
    検出する。意味が「否定」であっても、購入を想起させる強い語を非 BUYABLE
    で出さない方針 (購入を控える文脈で禁止語を出さない)。
    """
    out: list[OutputPlanWarning] = []
    mode = plan.purchase_mode
    if mode == PurchaseMode.BUYABLE:
        return out

    # 並び順は「長い語を先に」: 「実購入対象」が「購入対象」より先にあると
    # 「実購入対象」の検出が「購入対象」検出に隠れず、message が両方出る。
    # 部分一致による多重検出は OK (同じ warning code でも個別に message を
    # 出した方が原因箇所を特定しやすい)。
    basic_forbidden = (
        "実購入対象", "実購入候補", "購入対象", "一番買いたい",
    )
    for word in basic_forbidden:
        if word in md_body:
            out.append(OutputPlanWarning(
                code="PURCHASE_MODE_VIOLATION",
                severity="warning",
                message=(
                    f"purchase_mode={mode.name} なのに本文に「{word}」が"
                    f"残っています。Renderer 分岐の確認が必要です。"
                ),
            ))

    if mode in (PurchaseMode.WATCH_ONLY, PurchaseMode.SKIP):
        strict_forbidden = ("買える候補", "本線向き")
        for word in strict_forbidden:
            if word in md_body:
                out.append(OutputPlanWarning(
                    code="PURCHASE_MODE_VIOLATION_STRICT",
                    severity="warning",
                    message=(
                        f"purchase_mode={mode.name} なのに本文に「{word}」"
                        f"が残っています。"
                    ),
                ))
    return out


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
    # Phase 1 (2026-05-24): purchase_mode ベース 4段階分岐
    mode = plan.purchase_mode
    skip_purchase = (mode == PurchaseMode.SKIP)
    watch_only_mode = (mode == PurchaseMode.WATCH_ONLY)
    tentative_mode = (mode == PurchaseMode.TENTATIVE)

    if plan.final_best:
        combos = " / ".join(b.combination for b in plan.final_best)
        if skip_purchase:
            lines.append(
                f"- **購入見送り推奨**: {combos}"
                f"（高難度 + 低オッズ取得率のため、購入は控えめ）"
            )
        elif watch_only_mode:
            lines.append(
                f"- **見送り寄りの参考候補**: {combos}"
                f"（厚く買わない・確認程度）"
            )
        elif tentative_mode:
            lines.append(
                f"- **オッズ取得済みの暫定候補**: {combos}"
                f"（オッズ取得率が低いため、購入判断は再確認後）"
            )
        else:  # BUYABLE
            lines.append(
                f"- **オッズ取得済みで買える候補**: {combos}"
                f"（妙味/本線向き、購入対象）"
            )
    else:
        # codex P2 反映: final_best 空のときも mode 別に。「買える候補」は
        # WATCH_ONLY / SKIP の禁止語なので使わない。
        if skip_purchase:
            lines.append(
                "- **購入見送り推奨**: 該当なし → オッズ再取得後に再検討"
            )
        elif watch_only_mode:
            lines.append(
                "- **参考候補**: 該当なし → オッズ取得後に判断"
            )
        elif tentative_mode:
            lines.append(
                "- **暫定候補**: 該当なし → オッズ再確認後に判断"
            )
        else:  # BUYABLE
            lines.append(
                "- **オッズ取得済みで買える候補**: 該当なし → "
                "オッズ確認後に判断"
            )

    if plan.final_osae:
        combos = " / ".join(b.combination for b in plan.final_osae)
        if skip_purchase:
            lines.append(
                f"- **押さえ候補 (購入見送り推奨)**: {combos}"
                f"（高難度 + 低オッズ取得率のため、購入は控えめ）"
            )
        elif watch_only_mode:
            lines.append(
                f"- **押さえ候補 (見送り寄り)**: {combos}"
                f"（厚く買わない・確認程度）"
            )
        elif tentative_mode:
            lines.append(
                f"- **押さえ暫定候補**: {combos}"
                f"（オッズ取得率が低いため、購入判断は再確認後）"
            )
        else:  # BUYABLE
            lines.append(f"- **押さえとして必要**: {combos}")

    if plan.final_ana:
        combos = " / ".join(b.combination for b in plan.final_ana)
        if skip_purchase or watch_only_mode or tentative_mode:
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
        # 09077c2 後続レビュー反映: venue_trend は input_data 由来なので、
        # ガールズ判定も input_data.race.resolved_is_girls() を優先する。
        # prediction.is_girls が False/未設定でも、input_data.race が
        # ガールズなら venue_trend サニタイズを適用する。
        is_g = bool(
            prediction.is_girls
            or (
                input_data is not None
                and input_data.race.resolved_is_girls()
            )
        )
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
    # Phase 2 (2026-05-24): 印と買い目のズレに対する補足説明。
    # MarkAlignment は OutputPlan 側で生成し、Renderer は表示するだけ。
    if plan.mark_alignment_notes:
        lines.append("")
        lines.append("### 印と買い目の補足")
        for note in plan.mark_alignment_notes:
            lines.append(f"- {note}")
    # Phase 3 (2026-05-24): 市場偏り判定の補足。MarketBiasDecision は
    # OutputPlan 側で生成し、Renderer は表示するだけ。
    if plan.market_bias_notes:
        lines.append("")
        lines.append("### 市場偏りの補足")
        for note in plan.market_bias_notes:
            lines.append(f"- {note}")
    # Phase 4 (2026-05-24): RaceTypePolicy の補足。種別ごとの方針を
    # ユーザーが確認できるようにする。Renderer は表示するだけ。
    if plan.race_type and plan.race_type_policy_notes:
        lines.append("")
        lines.append(f"### レース種別: {plan.race_type}")
        for note in plan.race_type_policy_notes:
            lines.append(f"- {note}")
    # Phase 8 (2026-05-25): 参考候補の内訳 (watch_only_reason_groups)。
    # 各 reason group ごとに最大2点まで表示。空 group はスキップ。
    if plan.watch_only_reason_groups:
        breakdown_lines = _render_watch_only_breakdown(plan)
        if breakdown_lines:
            lines.append("")
            lines.append("### 参考候補の内訳")
            lines.extend(breakdown_lines)
    lines.append("")
    lines.append("### 実購入判断")
    lines.extend(render_purchase_judgement_block(plan))
    lines.append("")

    lines.append("## 11. ガミ回避メモ")
    lines.append(prediction.gami_memo or "(該当なし)")
    # Phase 14 後続2 (2026-05-25): plan.gami_warning に低オッズ候補があれば
    # 「N-N-N(N.N倍)は売れすぎ。厚く買わない」を追記する。
    # 既存 gami_memo は LLM 自然文。ここで構造的な情報を補強する。
    if plan.gami_warning:
        low_odds_combos = [
            b for b in plan.gami_warning
            if b.market_odds is not None and b.market_odds < 5.0
        ]
        if low_odds_combos:
            lines.append("")
            lines.append("**安い人気筋 (gami_warning):**")
            for b in low_odds_combos[:5]:
                lines.append(
                    f"- {b.combination}({b.market_odds:.1f}倍)は売れすぎ。"
                    f"厚く買わない"
                )
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
            render_coverage_metrics_section,
            render_market_odds_status_section,
            render_odds_coverage_section,
            summarize_market_bias,
            validate_prediction_output,
        )
        # 平塚6R 対応 (2026-05-24): plan を渡して gami_warning を
        # 「実購入本線」から除外し、本線欄表示と取得率集計を整合させる
        coverage = compute_odds_coverage(prediction, plan=plan)
        lines.append("")
        # Phase 16 (2026-05-26): plan.coverage_metrics があれば
        # CandidateLifecycle 経由の新セクションを使う。layout は旧と互換。
        # 旧 OddsCoverage は data_quality 判定など他箇所でも使うため計算は
        # 残す。
        if plan.coverage_metrics is not None:
            lines.append(render_coverage_metrics_section(plan.coverage_metrics))
        else:
            lines.append(render_odds_coverage_section(coverage))
        # Phase 15 (2026-05-25): 市場人気オッズ取得状況を別セクションで
        # 表示。候補買い目オッズが 0/8 でも、市場人気オッズが取得済み
        # なら矛盾に見えない形にする。
        market_section = render_market_odds_status_section(input_data)
        if market_section:
            lines.append("")
            lines.append(market_section)
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
        # Phase 1 (2026-05-24): purchase_mode 整合性チェック。
        # この時点で lines は ## 1 ~ ## 12 + フッタ前まで構築済み。
        # codex P2 反映 (2026-05-24): sanitize_low_quality_text の影響を
        # 受ける文言 (「本線向き」「(本線)」等) を事前にサニタイズした
        # 状態で検査する。これにより最終 Markdown に存在しない違反を
        # 誤検知しない。
        # Phase 16 Step 5A (2026-05-26): 「### 候補買い目オッズ取得率」
        # 以降は coverage / 統計セクションで、ラベル「実購入候補オッズ」
        # 等が出ることがある。本来の検査対象は「本文 (## 1-12)」なので、
        # coverage section 開始位置までを検査範囲にする。
        body_md_for_check = "\n".join(lines)
        coverage_section_marker = "### 候補買い目オッズ取得率"
        if coverage_section_marker in body_md_for_check:
            body_md_for_check = body_md_for_check.split(
                coverage_section_marker, 1,
            )[0]
        if plan.has_low_coverage_warning():
            from .output_validation import sanitize_low_quality_text
            body_md_for_check = sanitize_low_quality_text(body_md_for_check)
        mode_violations = validate_purchase_mode_markdown(
            plan, body_md_for_check,
        )
        if mode_violations:
            plan.warnings.extend(mode_violations)
        # Phase 5 (2026-05-24): allow_line_logic=False のとき line 用語が
        # 本文に残っていないかチェック。検出してもサニタイズ等の補正は
        # しない (Renderer 補正に頼らない、可視化のみ)。
        line_violations = validate_line_terms_when_not_allowed(
            plan, body_md_for_check,
        )
        if line_violations:
            plan.warnings.extend(line_violations)
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
