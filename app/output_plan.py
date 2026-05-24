"""OutputPlan: 最終出力の唯一の source of truth (2026-05-24)。

目的:
candidate_bets (Prediction.honsen/osae/ana/ooana) と LLM の final_conclusion が
分離していた結果、最終 Markdown に未登録 combo が混入する事故が発生していた
(例: 静岡4R で本線欄に 2-5-3 等が出ているのに、最終結論で 4-3-6 が突然出る)。

本モジュールは OutputPlan という Pydantic モデルで「表示可能な全買い目」を
一元管理し、MarkdownRenderer が deterministic に Markdown を生成する。

LLM が返す final_conclusion / honsen / osae / ana / ooana は完全に無視され、
自然文 (summary_text / trend_text / weather_text / line_text / reason_texts /
reflection_points) のみが装飾として使われる。

Design:
- OutputPlan は Pydantic BaseModel (シリアライズ可能 / バリデーション可能)
- フィールドはレース種別 (本線/押さえ/穴/大穴) と 実購入判断ブロック
  (final_best / final_osae / final_ana / gami_warning / watch_only) を保持
- warnings は OutputPlanWarning のリスト
- 既存 FinalSelection との互換性: FinalSelection は OutputPlan の dataclass alias
  として残し、段階移行を可能にする
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .decision import PurchaseMode
from .models import BetRecommendation


class OutputPlanWarning(BaseModel):
    """OutputPlan に紐付く警告。"""

    code: str = Field(..., description="警告コード (例: BEST_EMPTY, LOW_ODDS)")
    severity: str = Field(
        default="warning",
        description="severity: 'info' | 'warning' | 'error'",
    )
    message: str = Field(..., description="人間可読な日本語メッセージ")


class OutputPlan(BaseModel):
    """最終出力の唯一の source of truth (2026-05-24)。

    final_conclusion を含む全ての Markdown 表示は本モデルから生成される。
    LLM 出力の final_conclusion / honsen / osae / ana / ooana は無視される。

    フィールド分類:
        - 表示ブロック (## 6-9 セクション相当):
            honsen / osae / ana / ooana
        - 実購入判断ブロック (### 一番買いたい/押さえとして必要 等):
            final_best / final_osae / final_ana / gami_warning / watch_only
        - 警告: warnings
    """

    # ---- 表示ブロック (## 6-9 セクション) ----
    honsen: list[BetRecommendation] = Field(
        default_factory=list,
        description="本線セクション表示用 (最大3点)",
    )
    osae: list[BetRecommendation] = Field(
        default_factory=list,
        description="押さえセクション表示用 (最大4点)",
    )
    # 平塚7R 後続レビュー反映 (2026-05-24, codex P2): 「見送り寄り」を
    # display_honsen に含めると最大3点契約を破るため、別バケットで管理
    honsen_miokuri: list[BetRecommendation] = Field(
        default_factory=list,
        description="「見送り寄り」の本線表示用 (参考表示・購入対象ではない)",
    )
    ana: list[BetRecommendation] = Field(
        default_factory=list,
        description="穴セクション表示用",
    )
    ooana: list[BetRecommendation] = Field(
        default_factory=list,
        description="大穴セクション表示用",
    )

    # ---- 実購入判断ブロック ----
    final_best: list[BetRecommendation] = Field(
        default_factory=list,
        description="一番買いたい買い目 (odds取得済み妙味のみ)",
    )
    final_osae: list[BetRecommendation] = Field(
        default_factory=list,
        description="押さえとして必要 (must_cover_bets 相当)",
    )
    final_ana: list[BetRecommendation] = Field(
        default_factory=list,
        description="少額の穴 (small_longshots 相当)",
    )
    gami_warning: list[BetRecommendation] = Field(
        default_factory=list,
        description="安い人気筋 / ガミ警戒 (cheap_popular_bets 相当)",
    )
    watch_only: list[BetRecommendation] = Field(
        default_factory=list,
        description="参考表示 (確認程度、購入は推奨しない)",
    )

    # ---- 警告 ----
    warnings: list[OutputPlanWarning] = Field(
        default_factory=list,
        description="OutputPlan に紐付く警告リスト",
    )

    # ---- 購入モード (Phase 1, 2026-05-24) ----
    # DecisionContext / derive_purchase_mode の結果。
    # Renderer は本フィールドを見て「購入対象」「暫定候補」「見送り寄り」
    # 「見送り」を切り替える。後追い補正ではなく事前判定。
    model_config = ConfigDict(arbitrary_types_allowed=True)

    purchase_mode: PurchaseMode = Field(
        default=PurchaseMode.BUYABLE,
        description="購入モード (Phase 1)",
    )
    decision_notes: list[str] = Field(
        default_factory=list,
        description="purchase_mode 決定の根拠 (人間可読)",
    )
    decision_warnings: list[str] = Field(
        default_factory=list,
        description="DecisionContext 由来の警告メッセージ",
    )

    # ---- MarkAlignment (Phase 2, 2026-05-24) ----
    # 印 marks と final_best/final_osae の整合性チェック結果。
    # Renderer は notes を表示するだけで、説明文の生成はしない。
    mark_alignment_level: Optional[str] = Field(
        default=None,
        description="aligned / explainable_mismatch / dangerous_mismatch",
    )
    mark_alignment_notes: list[str] = Field(
        default_factory=list,
        description="印と最終候補のズレに対する人間可読な説明",
    )
    mark_alignment_warnings: list[str] = Field(
        default_factory=list,
        description="MarkAlignment 由来の警告メッセージ",
    )

    # ---- MarketBiasDecision (Phase 3, 2026-05-24) ----
    # 市場偏りを意思決定に統合した結果。
    # bias_type=head のときは同一2着軸への寄せ過ぎを抑制する。
    market_bias_type: Optional[str] = Field(
        default=None,
        description="none / head / axis / strong_axis",
    )
    market_bias_notes: list[str] = Field(
        default_factory=list,
        description="市場偏り判定の人間可読な説明",
    )
    market_bias_warnings: list[str] = Field(
        default_factory=list,
        description="MarketBiasDecision 由来の警告メッセージ",
    )

    # ---- バリデーション・ユーティリティ ----

    def all_combos(self) -> set[str]:
        """OutputPlan 内に存在する全 3連単 combination を set で返す。

        MarkdownRenderer のフォールバック判定で「Markdown に出た combo が
        OutputPlan 内に存在するか」を確認する用途。
        """
        combos: set[str] = set()
        for bucket in (
            self.honsen, self.osae, self.ana, self.ooana,
            self.honsen_miokuri,
            self.final_best, self.final_osae, self.final_ana,
            self.gami_warning, self.watch_only,
        ):
            for b in bucket:
                if b.combination:
                    combos.add(b.combination)
        return combos

    def warning_codes(self) -> list[str]:
        return [w.code for w in self.warnings]

    # ---- 文言分岐用 helper (fee60e4 後続レビュー反映, 2026-05-24) ----
    # render_final_conclusion / render_purchase_judgement_block で
    # 「購入対象」「中心に据える」を「暫定候補」「再確認後」「見送り寄り」に
    # 切り替える判定に使う。

    def has_low_coverage_warning(self) -> bool:
        """実購入候補のオッズ取得率が <40% の警告があるか。

        fee60e4 後続レビュー反映: code ベース判定 (文字列依存を緩和)。
        skip purchase も low coverage の一種としてカウントする。
        """
        return any(w.code in _LOW_COVERAGE_CODES for w in self.warnings)

    def has_skip_purchase_warning(self) -> bool:
        """購入見送り推奨レベルの警告があるか (極めて低カバレッジ or
        very_high + 低カバレッジ)。"""
        return any(w.code in _SKIP_PURCHASE_CODES for w in self.warnings)

    def has_high_complexity_warning(self) -> bool:
        """レース難度 high / very_high の警告があるか。"""
        return any(w.code in _HIGH_COMPLEXITY_CODES for w in self.warnings)


# ---------------------------------------------------------------------------
# 互換レイヤー: FinalSelection から OutputPlan への変換
# ---------------------------------------------------------------------------


def from_final_selection(final_sel) -> OutputPlan:
    """final_selection.FinalSelection → OutputPlan 変換。

    既存の build_final_selection を活かしつつ OutputPlan に統合するための
    互換レイヤー。段階移行が完了すれば直接 OutputPlan を返す形に変更可能。

    平塚7R 後続レビュー反映 (2026-05-24, codex P2): final_sel.display_honsen
    が「見送り寄り」を含む可能性があるため、honsen と honsen_miokuri に分離。
    honsen は実購入候補のみ (最大3点契約)、honsen_miokuri は参考表示用。
    """
    # display_honsen から見送り寄りを除外、miokuri_bets を honsen_miokuri に
    display_honsen = list(final_sel.display_honsen)
    honsen_real = [b for b in display_honsen if b.value_label != "見送り寄り"]
    # miokuri_bets (final_selection で別管理) を honsen_miokuri に
    honsen_miokuri = list(
        getattr(final_sel, "miokuri_bets", [])
    )
    return OutputPlan(
        honsen=honsen_real,
        osae=list(final_sel.display_osae),
        honsen_miokuri=honsen_miokuri,
        ana=list(final_sel.display_ana),
        ooana=list(final_sel.display_ooana),
        final_best=list(final_sel.best_bets),
        final_osae=list(final_sel.must_cover_bets),
        final_ana=list(final_sel.small_longshots),
        gami_warning=list(final_sel.cheap_popular_bets),
        watch_only=list(final_sel.watch_only_bets),
        warnings=[
            OutputPlanWarning(
                code=_infer_warning_code(msg),
                severity=_infer_severity(msg),
                message=msg,
            )
            for msg in final_sel.warnings
        ],
    )


def _infer_warning_code(message: str) -> str:
    """warnings 文字列から code を推定する (互換レイヤー暫定実装)。

    fee60e4 後続レビュー (2026-05-24): low coverage / skip purchase /
    race complexity 用のコードを追加。文字列マッチ依存の脆さを軽減するため、
    helper (has_*_warning) は本関数の結果コードで判定する。
    """
    if "実購入候補のオッズ取得率が極めて低い" in message:
        return "PURCHASE_SKIP_RECOMMENDED"
    if "購入見送り推奨" in message:
        return "PURCHASE_SKIP_RECOMMENDED"
    if "実購入候補のオッズ取得率が低い" in message:
        return "LOW_PURCHASE_COVERAGE"
    # 平塚4R 対応 (2026-05-24): honsen 専用カバレッジ + data_quality 警告
    if "本線オッズ取得率が低い" in message:
        return "LOW_HONSEN_COVERAGE"
    if "データ品質が" in message and (
        "low" in message or "very_low" in message
    ):
        return "DATA_QUALITY_LOW"
    if "レース難度 very_high" in message:
        return "RACE_COMPLEXITY_VERY_HIGH"
    if "レース難度 high" in message:
        return "RACE_COMPLEXITY_HIGH"
    if "オッズ取得済みで買える候補なし" in message or "オッズ確認後" in message:
        return "BEST_EMPTY_NO_ODDS"
    if "低配当注意" in message:
        return "LOW_ODDS_WARNING"
    if "市場偏り" in message:
        return "MARKET_BIAS_NOT_COVERED"
    return "INFO"


# 文言判定 helper で使う code 集合 (warning code ベースの分類)
# 平塚4R 対応 (2026-05-24): honsen 専用 coverage / data_quality も
# low_coverage として扱う (文言弱体化対象)
# 平塚7R 対応 (2026-05-24): BEST_EMPTY_NO_ODDS (本線オッズ取得済み 0%) も
# 実質的に購入判断を弱めるべき状態のため low_coverage に追加
_LOW_COVERAGE_CODES = frozenset({
    "LOW_PURCHASE_COVERAGE",
    "PURCHASE_SKIP_RECOMMENDED",
    "LOW_HONSEN_COVERAGE",
    "DATA_QUALITY_LOW",
    "BEST_EMPTY_NO_ODDS",
})
# 平塚7R 対応: best_bets が空で honsen も全 odds=None なら skip_purchase 扱い
_SKIP_PURCHASE_CODES = frozenset({
    "PURCHASE_SKIP_RECOMMENDED",
    "BEST_EMPTY_NO_ODDS",
})
_HIGH_COMPLEXITY_CODES = frozenset({
    "RACE_COMPLEXITY_HIGH",
    "RACE_COMPLEXITY_VERY_HIGH",
})


def _infer_severity(message: str) -> str:
    if "低配当注意" in message:
        return "warning"
    if "オッズ未取得" in message or "オッズ確認後" in message:
        return "warning"
    if "オッズ取得率が低い" in message or "見送り推奨" in message:
        return "warning"
    return "info"


def validate_output_plan(plan: OutputPlan) -> list[OutputPlanWarning]:
    """OutputPlan の整合性を検証して警告リストを返す (武雄12R 安全制御)。

    検証ルール (2026-05-24, 武雄12R 対応):
        - final_best / final_osae ⊆ honsen ∪ osae
        - final_ana ⊆ ana ∪ ooana
        - gami_warning ⊆ honsen ∪ osae (cheap_popular_bets 起点)

    逸脱を検出した場合は警告を返し、副作用として plan を補正する:
        - final_ana に ana/ooana 外の combo があれば、ana に追加する
        - final_best / final_osae に honsen/osae 外があれば、対応するセクション
          に追加する (代わりに final_* から除外する選択もあり得るが、
          表示一貫性を優先して追加する)

    本関数は build_output_plan 直後に呼ばれ、validator として副作用補正も行う。
    """
    warnings: list[OutputPlanWarning] = []

    # 平塚4R/6R 後続レビュー反映 (2026-05-24): 同一 combination が
    # gami_warning / honsen / osae / ana / ooana に複数存在したら 1 つに統合。
    # **優先順位: gami_warning > honsen > osae > ana > ooana**
    # gami_warning は購入候補ではなく参考表示扱いだが、ガミ注意の combo を
    # 本線扱いしないため最優先で印を付ける。本線等の表示は別カテゴリから除外。
    gami_combos = {b.combination for b in plan.gami_warning if b.combination}
    seen_combos: set[str] = set(gami_combos)
    for bucket_name, bucket in (
        ("honsen", plan.honsen),
        ("osae", plan.osae),
        ("ana", plan.ana),
        ("ooana", plan.ooana),
    ):
        kept: list = []
        for b in bucket:
            if not b.combination:
                kept.append(b)
                continue
            if b.combination in seen_combos:
                # 重複: gami_warning か上位カテゴリに存在
                in_gami = b.combination in gami_combos
                msg_src = (
                    "gami_warning (参考表示)" if in_gami else "上位カテゴリ"
                )
                warnings.append(OutputPlanWarning(
                    code="DISPLAY_DUPLICATE_REMOVED",
                    severity="info",
                    message=(
                        f"{bucket_name} の {b.combination} は{msg_src}に"
                        f"既に存在するため重複除外しました。"
                    ),
                ))
                continue
            seen_combos.add(b.combination)
            kept.append(b)
        # 副作用: bucket を kept で置き換える
        bucket.clear()
        bucket.extend(kept)

    honsen_osae_combos = (
        {b.combination for b in plan.honsen}
        | {b.combination for b in plan.osae}
    )
    ana_ooana_combos = (
        {b.combination for b in plan.ana}
        | {b.combination for b in plan.ooana}
    )

    # final_best / final_osae ⊆ honsen ∪ osae
    # codex review 反映: final_osae 欠落は osae に補充 (honsen に追加すると
    # 本線3点制約を破壊する)。final_best は honsen に補充するが、
    # 既に 3 点埋まっていれば osae に補充 (3点制約維持)。
    # fee60e4 後続レビュー反映: 本線3点制約を validator 自身でも守る。
    HONSEN_MAX = 3
    for b in plan.final_best:
        if b.combination and b.combination not in honsen_osae_combos:
            if len(plan.honsen) < HONSEN_MAX:
                plan.honsen.append(b)
                target_name = "honsen"
            else:
                # 本線3点制約 → osae に補充
                plan.osae.append(b)
                target_name = "osae (本線3点制約のため)"
            honsen_osae_combos.add(b.combination)
            warnings.append(OutputPlanWarning(
                code="FINAL_PURCHASE_NOT_IN_DISPLAY",
                severity="warning",
                message=(
                    f"final_best の {b.combination} が honsen/osae に"
                    f"無かったため {target_name} に補充しました。"
                ),
            ))
    for b in plan.final_osae:
        if b.combination and b.combination not in honsen_osae_combos:
            plan.osae.append(b)
            honsen_osae_combos.add(b.combination)
            warnings.append(OutputPlanWarning(
                code="FINAL_PURCHASE_NOT_IN_DISPLAY",
                severity="warning",
                message=(
                    f"final_osae の {b.combination} が honsen/osae に"
                    f"無かったため osae に補充しました。"
                ),
            ))

    # final_ana ⊆ ana ∪ ooana (武雄12R 1-4-7 ケース対策)
    # 平塚4R 後続レビュー反映 (2026-05-24): combo が honsen/osae に既に
    # 存在する場合は ana に追加しない (上位カテゴリ優先の重複禁止を維持)。
    for b in plan.final_ana:
        if not b.combination:
            continue
        if b.combination in honsen_osae_combos:
            # 上位カテゴリ (honsen/osae) に既存 → 重複除外、ana に追加しない
            continue
        if b.combination not in ana_ooana_combos:
            # 表示一貫性のため、ana に追加 (穴セクションを増やす)
            plan.ana.append(b)
            ana_ooana_combos.add(b.combination)
            warnings.append(OutputPlanWarning(
                code="FINAL_ANA_NOT_IN_DISPLAY",
                severity="warning",
                message=(
                    f"final_ana の {b.combination} が ana/ooana に"
                    f" 無かったため ana に補充しました。"
                ),
            ))

    # gami_warning ⊆ honsen ∪ osae の旧 invariant は削除 (平塚6R 後続レビュー反映)
    # ガミ注意 combo は専用セクション (## 6. 本線 配下の安い人気筋
    # サブセクション / ### 実購入判断 配下の「安い人気筋」枠) で表示する。
    # honsen/osae に補充すると、上で除外したばかりの combo が「押さえ」として
    # 復活して gami_warning と重複表示される (本線扱いされてしまう)。
    # gami_warning の表示は markdown_renderer 側のセクション表示で担保する。

    return warnings


def build_output_plan(
    prediction,
    input_data,
) -> OutputPlan:
    """Prediction + RaceInput から OutputPlan を deterministic に構築する。

    内部的には final_selection.build_final_selection を呼んで OutputPlan に
    変換する。将来的に build_output_plan が独立した実装になっても、本関数の
    シグネチャは維持する。

    2026-05-24 (武雄12R 対応): 生成直後に validate_output_plan を呼んで
    「final_* に表示外 combo が含まれる」事故を検出・補正する。
    """
    from .final_selection import build_final_selection
    final_sel = build_final_selection(prediction, input_data)
    plan = from_final_selection(final_sel)
    # 武雄12R 対応: 生成直後の整合性検証 (副作用補正含む)
    validation_warnings = validate_output_plan(plan)
    plan.warnings.extend(validation_warnings)
    # Phase 1 (2026-05-24): DecisionContext / PurchaseMode を計算して
    # plan に書き込む。Renderer は plan.purchase_mode を見て分岐する。
    _apply_decision_context(plan, prediction, input_data)
    # Phase 2 (2026-05-24): 印 marks と final_* の整合性をチェックして
    # plan に書き込む。dangerous_mismatch の場合 MARK_FINAL_MISMATCH
    # warning を追加する。
    _apply_mark_alignment(plan, prediction, input_data)
    # Phase 3 (2026-05-24): 市場偏り MarketBiasDecision を計算し、
    # HeadBias-only の場合は同一2着軸への寄せ過ぎを抑制する。
    _apply_market_bias_decision(plan, input_data)
    return plan


def _apply_mark_alignment(plan: OutputPlan, prediction, input_data) -> None:
    """Phase 2: assess_mark_alignment を実行して plan に書き込む.

    - alignment_level / notes / warnings をフィールドに反映
    - notes は plan.decision_notes にも追記 (Renderer での集約用)
    - dangerous_mismatch のときは plan.warnings に MARK_FINAL_MISMATCH を追加
    """
    from .decision import assess_mark_alignment

    result = assess_mark_alignment(prediction, plan, input_data)
    plan.mark_alignment_level = result.alignment_level
    plan.mark_alignment_notes = list(result.notes)
    plan.mark_alignment_warnings = list(result.warnings)
    # notes は decision_notes にも追記 (UI 表示の集約点)
    if result.notes:
        plan.decision_notes.extend(result.notes)
    # PostRenderValidator (Phase 2): dangerous_mismatch は warning として
    # plan.warnings に記録される。Markdown 警告セクションに表示される。
    if result.alignment_level == "dangerous_mismatch":
        message = (
            result.warnings[0] if result.warnings
            else f"◎{result.top_mark_car}が最終候補に絡まず説明理由なし"
        )
        plan.warnings.append(OutputPlanWarning(
            code="MARK_FINAL_MISMATCH",
            severity="warning",
            message=message,
        ))


def _apply_market_bias_decision(plan: OutputPlan, input_data) -> None:
    """Phase 3: 市場偏り判定 + HeadBias-only 同一軸制限.

    - bias_type を判定して plan.market_bias_type / notes / warnings に書き込む
    - bias_type=head のとき final_best+final_osae+final_ana で同一 (head, 2着)
      軸を最大1点に制限 (抑制した候補は watch_only に移す)
    - axis / strong_axis では制限しない (同一軸複数候補を許可)
    - purchase_mode=SKIP は対象外 (制限ロジックを適用しても効果が小さい)
    """
    if input_data is None:
        return
    from .decision import (
        PurchaseMode, assess_market_bias_decision,
    )

    bias = assess_market_bias_decision(input_data)
    plan.market_bias_type = bias.bias_type
    plan.market_bias_notes = list(bias.notes)
    plan.market_bias_warnings = list(bias.warnings)
    # notes は decision_notes にも集約
    if bias.notes:
        plan.decision_notes.extend(bias.notes)

    # SKIP は制限を適用しても効果が小さい (final_* は参考扱い)
    if plan.purchase_mode == PurchaseMode.SKIP:
        return

    # HeadBias-only のときだけ同一2着軸制限を適用
    if bias.bias_type != "head" or bias.head is None:
        return
    _restrict_same_axis_under_head_bias(plan, head=bias.head)


def _restrict_same_axis_under_head_bias(
    plan: OutputPlan, *, head: int,
) -> None:
    """HeadBias-only のとき、final_best+final_osae+final_ana で同一 1-2着軸を
    最大1点に制限する。抑制した候補は plan.watch_only に移す。

    判定対象:
    - final_best / final_osae / final_ana の combination から (1着, 2着)
      ペアを抽出し、HeadBias 頭 (=head) + 同じ 2着 が複数あれば 1 点目を
      残して 2 点目以降を watch_only に追い出す

    制限後、decision_notes に「HeadBiasのみのため同一軸過多を抑制」を残す。
    """
    def _parts(combo: str):
        if not combo or combo.count("-") != 2:
            return None
        try:
            a, b, c = combo.split("-")
            return (int(a), int(b), int(c))
        except ValueError:
            return None

    removed_count = 0
    # codex P1 反映: seen_axes は **3 バケット全体** で共有する。
    # バケットごとに初期化すると final_best=1-4-2, final_osae=1-4-6,
    # final_ana=1-4-7 のように 3 バケットに 1-4 軸が散らばっていると
    # 全部残ってしまう。
    seen_axes: set[tuple[int, int]] = set()
    suppressed: list = []  # 抑制した候補を順番に保持 (Renderer 表示用)

    for bucket_name in ("final_best", "final_osae", "final_ana"):
        bucket = getattr(plan, bucket_name)
        kept = []
        for b in bucket:
            parts = _parts(b.combination)
            if parts is None:
                kept.append(b)
                continue
            axis_pair = (parts[0], parts[1])
            # head 頭 + 同じ 2着 軸が複数 → 2 点目以降は watch_only へ
            if parts[0] == head:
                if axis_pair in seen_axes:
                    # 抑制対象。watch_only への重複追加は防ぐ
                    if not any(
                        wb.combination == b.combination
                        for wb in plan.watch_only
                    ):
                        suppressed.append(b)
                    removed_count += 1
                    continue
                seen_axes.add(axis_pair)
            kept.append(b)
        setattr(plan, bucket_name, kept)

    # codex P2 反映: 抑制候補は watch_only の **先頭** に挿入する。
    # Renderer は watch_only[:2] しか表示しないため、市場偏り由来の
    # 移動候補を確実に見えるようにする。
    if suppressed:
        plan.watch_only[:] = suppressed + list(plan.watch_only)

    if removed_count > 0:
        message = (
            f"HeadBias({head}番頭) のみで AxisBias 無しのため、"
            f"同一2着軸への寄せ過ぎを抑制 ({removed_count}点を参考候補へ移動)。"
        )
        plan.market_bias_notes.append(message)
        plan.decision_notes.append(message)


def _apply_decision_context(plan: OutputPlan, prediction, input_data) -> None:
    """build_output_plan の末尾で呼ばれる DecisionContext 適用処理 (Phase 1).

    既に計算済みの観測値 (coverage / data_quality / race_complexity) を
    DecisionContext に渡して derive_purchase_mode を呼ぶ。結果は plan に
    書き込む (purchase_mode / decision_notes / decision_warnings)。
    """
    from .decision import (
        PurchaseMode,
        build_decision_context,
        derive_purchase_mode,
    )
    from .output_validation import (
        assess_data_quality,
        assess_race_complexity,
        compute_odds_coverage,
    )

    if input_data is None:
        # input_data が無いケース (テスト等) は BUYABLE で固定
        return

    coverage = compute_odds_coverage(prediction, plan=plan)
    quality = assess_data_quality(input_data, coverage=coverage)
    complexity = assess_race_complexity(input_data)

    # codex P1 反映 (2026-05-24): 実購入候補 coverage の母集団は
    # **pre-filter** の prediction.honsen + prediction.osae を使う。
    # plan.final_best + plan.final_osae は final_selection で低カバレッジ
    # 候補を落とした **後** の集合なので、これを母集団にすると coverage が
    # 誤って高く出て purchase_mode=BUYABLE に昇格し、本来 SKIP すべき
    # ケースが買い目扱いされる。
    purchase_bets = list(prediction.honsen) + list(prediction.osae)
    if purchase_bets:
        with_odds = sum(
            1 for b in purchase_bets if b.market_odds is not None
        )
        purchase_ratio = with_odds / len(purchase_bets)
    else:
        purchase_ratio = 0.0

    ctx = build_decision_context(
        input_data=input_data,
        coverage=coverage,
        purchase_coverage_ratio=purchase_ratio,
        data_quality=quality,
        race_complexity=complexity,
        final_best_count=len(plan.final_best),
    )
    derive_purchase_mode(ctx)

    # codex P1 反映: 安全網。final_selection 由来の warning を
    # purchase_mode の cap として反映する (purchase_odds_coverage 計算の
    # 取りこぼしへの二重防御)。
    # マッピング:
    # - PURCHASE_SKIP_RECOMMENDED → SKIP (「購入見送り推奨」)
    # - BEST_EMPTY_NO_ODDS → WATCH_ONLY (本線にオッズ取得済みなし)
    # - DATA_QUALITY_LOW → WATCH_ONLY
    #     (Phase 2 前小修正 2026-05-24: derive 本体の data_quality=low
    #      ルールと温度感を揃える。LOW_PURCHASE_COVERAGE 等の coverage 系と
    #      は意味が異なるため別 cap)
    # - LOW_PURCHASE_COVERAGE / LOW_HONSEN_COVERAGE → TENTATIVE (暫定)
    warning_codes = {w.code for w in plan.warnings}
    if "PURCHASE_SKIP_RECOMMENDED" in warning_codes:
        ctx.purchase_mode = min(ctx.purchase_mode, PurchaseMode.SKIP)
        ctx.reasons.append(
            "plan.warnings に PURCHASE_SKIP_RECOMMENDED → 見送り"
        )
    if "BEST_EMPTY_NO_ODDS" in warning_codes:
        ctx.purchase_mode = min(ctx.purchase_mode, PurchaseMode.WATCH_ONLY)
        ctx.reasons.append(
            "plan.warnings に BEST_EMPTY_NO_ODDS → 見送り寄り以下"
        )
    if "DATA_QUALITY_LOW" in warning_codes:
        ctx.purchase_mode = min(ctx.purchase_mode, PurchaseMode.WATCH_ONLY)
        ctx.reasons.append(
            "plan.warnings に DATA_QUALITY_LOW → 見送り寄り以下"
        )
    low_codes = {"LOW_PURCHASE_COVERAGE", "LOW_HONSEN_COVERAGE"}
    if warning_codes & low_codes:
        ctx.purchase_mode = min(ctx.purchase_mode, PurchaseMode.TENTATIVE)
        ctx.reasons.append(
            f"plan.warnings に {sorted(warning_codes & low_codes)} → 暫定以下"
        )

    plan.purchase_mode = ctx.purchase_mode
    plan.decision_notes = list(ctx.reasons)
    plan.decision_warnings = list(ctx.warnings)
