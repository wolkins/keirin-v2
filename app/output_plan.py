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

    # ---- Phase 8 (2026-05-25): 移動理由別グループ ----
    # watch_only に集まる候補を「なぜ参考扱いになったか」で分類する。
    # 既存 watch_only は互換維持 (Renderer/テストが参照)。
    # キー: line_source_filtered / market_bias_suppressed /
    #       max_final_best_overflow / gami_warning / low_quality_watch /
    #       manual_watch
    watch_only_reason_groups: dict[str, list[BetRecommendation]] = Field(
        default_factory=dict,
        description="watch_only 候補を移動理由別に分類した dict",
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

    # ---- RaceTypePolicy (Phase 4, 2026-05-24) ----
    # レース種別ごとの policy (normal_line / girls / rookie / girls_rookie)。
    # decision_context / renderer が参照して文言・cap を切り替える。
    race_type: Optional[str] = Field(
        default=None,
        description="normal_line / girls / rookie / girls_rookie",
    )
    race_type_policy_notes: list[str] = Field(
        default_factory=list,
        description="RaceTypePolicy の説明 (Renderer 表示用)",
    )

    # ---- バリデーション・ユーティリティ ----

    def all_combos(self) -> set[str]:
        """OutputPlan 内に存在する全 3連単 combination を set で返す。

        MarkdownRenderer のフォールバック判定で「Markdown に出た combo が
        OutputPlan 内に存在するか」を確認する用途。

        Phase 8 codex P2 反映 (2026-05-25): watch_only_reason_groups の
        combo も含める。Renderer の「参考候補の内訳」で表示される combo を
        verify_markdown_combos が未登録と誤判定しないようにする。
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
        # Phase 8: reason_groups も走査 (manual_watch / low_quality_watch
        # など、将来 watch_only に入らない group も拾う)
        for group in self.watch_only_reason_groups.values():
            for b in group:
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
    # Phase 4 (2026-05-24): RaceTypePolicy を最初に解決して plan に書き込む。
    # 後段の decision_context / market_bias_decision / mark_alignment が
    # plan.race_type / plan._race_type_policy を参照できる。
    _apply_race_type_policy(plan, input_data)
    # Phase 7 (2026-05-25) codex P1 反映: allow_line_logic=False のとき
    # source_rules に line_*/separate_* タグを持つ候補を構造的に除外する。
    # **decision_context の前** に実行することで、coverage / purchase_coverage
    # が filter 後の母集団で計算され、purchase_mode が正しい値になる。
    # 文字列検出 (validate_line_terms_when_not_allowed) は最終防衛線として
    # 残す。
    _apply_line_source_rules_filter(plan)
    # Phase 13 (2026-05-25): source_rules に gami_warning / low_odds を
    # 持つ candidate を購入候補から「安い人気筋」へ分離する。
    # **decision_context の前** に実行することで、gami 候補を除外した後の
    # 母集団で purchase_mode / coverage を評価する。
    _apply_gami_source_rules_filter(plan)
    # Phase 1 (2026-05-24): DecisionContext / PurchaseMode を計算して
    # plan に書き込む。Renderer は plan.purchase_mode を見て分岐する。
    _apply_decision_context(plan, prediction, input_data)
    # Phase 3 (2026-05-24): 市場偏り MarketBiasDecision を計算し、
    # HeadBias-only の場合は同一2着軸への寄せ過ぎを抑制する。
    # 注意 (Phase 3 後続レビュー反映, 2026-05-24): MarketBias 制限が
    # final_best/final_osae/final_ana を watch_only に移動させるため、
    # mark_alignment はこの **後** に評価する。さもないと「◎7 が
    # market_bias 制限前は final_osae にあった」状態で aligned 判定
    # してしまい、最終 plan と整合しない notes が残る。
    _apply_market_bias_decision(plan, input_data)
    # Phase 4 後続レビュー反映 (2026-05-24): policy.max_final_best で
    # final_best の点数を制限する (girls_rookie は 2点まで等)。
    # purchase_mode と HeadBias 制限が確定した **後** に実行することで、
    # 既に削られた final_best に対して過剰な制限をかけない。
    _apply_max_final_best_limit(plan)
    # Phase 2 (2026-05-24): 印 marks と final_* の整合性をチェックして
    # plan に書き込む。dangerous_mismatch の場合 MARK_FINAL_MISMATCH
    # warning を追加する。market_bias / max_final_best 制限後の最終
    # final_* を見る。
    _apply_mark_alignment(plan, prediction, input_data)
    # Phase 7 codex P2 反映 (2026-05-25): 全後段処理 (max_final_best /
    # market_bias / mark_alignment) を経たあとに最終 leak check。
    # 将来後段で 7 バケットへ line 候補を戻す変更が入っても検出できる。
    _check_line_source_rules_leak(plan)
    # Phase 8 (2026-05-25): gami_warning を reason_groups にも反映
    # (watch_only_reason_groups["gami_warning"])。これにより Renderer の
    # 「参考候補の内訳」表示で gami 注意候補も理由別に見える。
    # watch_only には移動しない (gami_warning は専用バケットを維持)。
    if plan.gami_warning:
        group = plan.watch_only_reason_groups.setdefault(
            "gami_warning", []
        )
        existing_combos = {b.combination for b in group}
        for b in plan.gami_warning:
            if b.combination not in existing_combos:
                group.append(b)
                existing_combos.add(b.combination)
    return plan


def _add_to_watch_only_with_reason(
    plan: OutputPlan,
    bet: BetRecommendation,
    reason_group: str,
    *,
    prepend: bool = False,
) -> bool:
    """Phase 8: watch_only に候補を追加し、watch_only_reason_groups にも反映.

    重複防止:
    - watch_only 内で同じ combination は1回だけ追加
    - 各 reason_group 内でも同じ combination は1回だけ追加
      (異なる group 間の重複は許容、複数理由で抑制された可能性あり)

    Args:
        plan: 対象 OutputPlan
        bet: 追加候補
        reason_group: "line_source_filtered" / "market_bias_suppressed" /
            "max_final_best_overflow" / "gami_warning" / etc.
        prepend: True なら watch_only の先頭に挿入。デフォルトは末尾追加。

    Returns:
        新規に watch_only に追加されたか (重複だった場合は False)
    """
    existing_combos = {b.combination for b in plan.watch_only}
    added_to_watch = False
    if bet.combination not in existing_combos:
        if prepend:
            plan.watch_only.insert(0, bet)
        else:
            plan.watch_only.append(bet)
        added_to_watch = True

    # reason_group には watch_only に既にあっても (別 group 経由でも) 記録
    # codex P2 反映: prepend=True のとき group も先頭挿入することで
    # watch_only と group の表示順を一致させる
    group = plan.watch_only_reason_groups.setdefault(reason_group, [])
    group_combos = {b.combination for b in group}
    if bet.combination not in group_combos:
        if prepend:
            group.insert(0, bet)
        else:
            group.append(bet)
    return added_to_watch


def _apply_gami_source_rules_filter(plan: OutputPlan) -> None:
    """Phase 13 (2026-05-25): source_rules に gami_warning / low_odds を
    持つ候補を購入候補から構造的に分離する.

    対象バケット (purchase 経路から除外):
    - honsen / osae / ana / ooana
    - final_best / final_osae / final_ana

    移動先:
    - plan.gami_warning (専用バケット)
    - plan.watch_only_reason_groups["gami_warning"] (Phase 8 reason group)

    combination 重複防止: gami_warning に既存があれば追加しない。

    Phase 12 で `_push` / `_push_osae` が odds<5 の候補に自動付与する
    gami_warning / low_odds タグを起点とする。
    """
    from .decision import is_gami_source

    moved_count = 0
    moved: list[BetRecommendation] = []
    target_buckets = (
        "honsen", "osae", "ana", "ooana",
        "final_best", "final_osae", "final_ana",
    )
    for bucket_name in target_buckets:
        bucket = getattr(plan, bucket_name)
        kept = []
        for b in bucket:
            if is_gami_source(b.source_rules):
                moved.append(b)
                moved_count += 1
                continue
            kept.append(b)
        setattr(plan, bucket_name, kept)

    if not moved:
        return

    # first-seen dedupe (Phase 8 の codex P2 反映と同じパターン)
    seen_in_moved: set[str] = set()
    dedupe_moved: list[BetRecommendation] = []
    for b in moved:
        if b.combination not in seen_in_moved:
            seen_in_moved.add(b.combination)
            dedupe_moved.append(b)

    # plan.gami_warning に append (重複防止)
    existing_gami_combos = {b.combination for b in plan.gami_warning}
    for b in dedupe_moved:
        if b.combination not in existing_gami_combos:
            plan.gami_warning.append(b)
            existing_gami_combos.add(b.combination)

    # watch_only_reason_groups["gami_warning"] に直接追加 (Phase 13 codex P2
    # 反映: helper を使うと plan.watch_only にも入って Renderer 二重表示に
    # なるため、reason_groups のみに記録する。group 内 dedupe あり)。
    group = plan.watch_only_reason_groups.setdefault("gami_warning", [])
    group_combos = {b.combination for b in group}
    for b in dedupe_moved:
        if b.combination not in group_combos:
            group.append(b)
            group_combos.add(b.combination)

    message = (
        f"source_rules に gami_warning/low_odds を持つ {moved_count} 点を"
        f"購入候補から gami_warning + "
        f"watch_only_reason_groups['gami_warning'] に分離"
    )
    plan.decision_notes.append(message)

    # Phase 6 codex P2 と同じ: filter で final_best が空になったら cap
    from .decision import PurchaseMode
    if not plan.final_best and plan.purchase_mode > PurchaseMode.WATCH_ONLY:
        plan.purchase_mode = PurchaseMode.WATCH_ONLY
        plan.decision_notes.append(
            "安い人気筋をガミ注意に分離したため、購入候補なし → 見送り寄りに cap"
        )


def _is_line_source_tag(tags) -> bool:
    """source_rules がライン由来か判定する.

    Phase 9 codex P2 反映 (2026-05-25): decision/source_rules.is_line_source
    に委譲。両者が同じ判定ロジックを共有する。
    """
    from .decision import is_line_source
    return is_line_source(tags)


def _apply_line_source_rules_filter(plan: OutputPlan) -> None:
    """Phase 6 (2026-05-24) / Phase 7 (2026-05-25):
    allow_line_logic=False のとき source_rules に line_* タグを持つ候補を
    構造的に除外する.

    対象バケット (Phase 7 で拡張):
    - display sections: honsen / osae / ana / ooana
    - 実購入判断: final_best / final_osae / final_ana

    除外した候補は **watch_only の先頭 (prepend)** に追加。
    Renderer が watch_only[:N] しか表示しない場合でも、除外された候補が
    見えるようにする (Phase 3 market_bias / Phase 4 max_final_best と同様)。

    line_* タグ (prefix で判定):
    - line_third / line_fourth_flow / line_spec12 / line_direct /
      line_second_head / separate_line / separate_second / line_weather /
      line_trend など

    文字列検出 (validate_line_terms_when_not_allowed) は最終防衛線として
    残す。本関数は構造的フィルタとして candidates の段階で除外する。
    """
    policy = getattr(plan, "_race_type_policy", None)
    if policy is None or policy.allow_line_logic:
        return

    moved_count = 0
    moved: list = []  # Phase 8: reason group 用に順序保持
    # display sections と final_* を全部対象に
    target_buckets = (
        "honsen", "osae", "ana", "ooana",
        "final_best", "final_osae", "final_ana",
    )

    for bucket_name in target_buckets:
        bucket = getattr(plan, bucket_name)
        kept = []
        for b in bucket:
            if _is_line_source_tag(b.source_rules):
                # line 由来候補 → 一旦 moved に保持 (順序保持して prepend)
                moved.append(b)
                moved_count += 1
                continue
            kept.append(b)
        setattr(plan, bucket_name, kept)

    # 移動した候補を watch_only の **先頭** に prepend + reason group 登録
    # Phase 8: helper 経由で line_source_filtered group にも追加
    # codex P2 反映: moved を first-seen で dedupe してから処理する。
    # 同一 combo が複数バケットにあると watch_only と reason_group の
    # 表示順がズレるため。
    if moved:
        seen_in_moved: set[str] = set()
        dedupe_moved = []
        for b in moved:
            if b.combination not in seen_in_moved:
                seen_in_moved.add(b.combination)
                dedupe_moved.append(b)
        # prepend するため逆順に insert(0, ...) する
        for b in reversed(dedupe_moved):
            _add_to_watch_only_with_reason(
                plan, b, "line_source_filtered", prepend=True,
            )

    if moved_count > 0:
        message = (
            f"race_type={plan.race_type} (allow_line_logic=False): "
            f"source_rules=line_* の候補 {moved_count} 点を "
            f"honsen/osae/ana/ooana/final_* から watch_only に移動 (構造的除外)"
        )
        plan.decision_notes.append(message)
        plan.race_type_policy_notes.append(message)

    # codex P2 反映 (Phase 6, 2026-05-25): filter で final_best が空に
    # なった場合、derive_purchase_mode の final_best_count==0 ルールが
    # 既に走り終わっているため purchase_mode が BUYABLE のまま残るリスクが
    # ある。filter 後に再評価して WATCH_ONLY 以下にキャップする。
    from .decision import PurchaseMode
    if not plan.final_best and plan.purchase_mode > PurchaseMode.WATCH_ONLY:
        plan.purchase_mode = PurchaseMode.WATCH_ONLY
        plan.decision_notes.append(
            "line 構造的除外で final_best が空 → 見送り寄りに cap"
        )

    # Phase 7 (2026-05-25): leak check は build_output_plan の **末尾** で
    # 別途呼ぶように移動 (codex P2 反映)。本関数からは外す。


def _check_line_source_rules_leak(plan: OutputPlan) -> None:
    """Phase 7: filter 後でも line_* タグが残っていたら warning を出す.

    対象: honsen / osae / ana / ooana / final_best / final_osae / final_ana。
    watch_only / honsen_miokuri / gami_warning は除外 (参考表示扱い)。
    """
    leaked = []
    target_buckets = (
        "honsen", "osae", "ana", "ooana",
        "final_best", "final_osae", "final_ana",
    )
    for bucket_name in target_buckets:
        bucket = getattr(plan, bucket_name)
        for b in bucket:
            if _is_line_source_tag(b.source_rules):
                leaked.append((bucket_name, b.combination))
    if leaked:
        plan.warnings.append(OutputPlanWarning(
            code="LINE_SOURCE_RULES_LEAKED",
            severity="warning",
            message=(
                f"allow_line_logic=False で line_* タグの候補が "
                f"{len(leaked)} 件残っています: "
                f"{', '.join(f'{n}:{c}' for n, c in leaked[:5])}"
            ),
        ))


def _apply_max_final_best_limit(plan: OutputPlan) -> None:
    """Phase 4 後続: policy.max_final_best で final_best の点数を制限する.

    超過分の移動先:
    - purchase_mode in (WATCH_ONLY, SKIP): plan.watch_only に prepend
      (既存の参考候補扱いと矛盾しないように)
    - それ以外 (BUYABLE/TENTATIVE): plan.final_osae の末尾に append
      (実購入候補としては残るが、押さえ扱いに格下げ)

    重複防止: 移動先に同じ combination が既にあれば追加しない。
    """
    from .decision import PurchaseMode

    policy = getattr(plan, "_race_type_policy", None)
    if policy is None:
        return
    max_count = policy.max_final_best
    if max_count is None or len(plan.final_best) <= max_count:
        return

    overflow = list(plan.final_best[max_count:])
    plan.final_best = list(plan.final_best[:max_count])

    if plan.purchase_mode in (PurchaseMode.WATCH_ONLY, PurchaseMode.SKIP):
        # Phase 8: helper 経由で watch_only + reason_group に追加
        # codex P2 反映: overflow を first-seen で dedupe してから処理
        seen_in_over: set[str] = set()
        dedupe_over = []
        for b in overflow:
            if b.combination not in seen_in_over:
                seen_in_over.add(b.combination)
                dedupe_over.append(b)
        for b in reversed(dedupe_over):
            _add_to_watch_only_with_reason(
                plan, b, "max_final_best_overflow", prepend=True,
            )
    else:
        # BUYABLE/TENTATIVE は final_osae に格下げ
        existing_combos = {b.combination for b in plan.final_osae}
        to_add = [
            b for b in overflow if b.combination not in existing_combos
        ]
        plan.final_osae.extend(to_add)

    message = (
        f"race_type={policy.race_type}: final_best を最大 "
        f"{max_count} 点に制限 ({len(overflow)} 点を"
        f"{'watch_only' if plan.purchase_mode in (PurchaseMode.WATCH_ONLY, PurchaseMode.SKIP) else 'final_osae'}へ移動)"
    )
    plan.decision_notes.append(message)
    plan.race_type_policy_notes.append(message)


def _apply_race_type_policy(plan: OutputPlan, input_data) -> None:
    """Phase 4: RaceTypePolicy を解決して plan に書き込む.

    - plan.race_type に種別ラベルを記録
    - plan.race_type_policy_notes に policy の説明を記録
    - plan._race_type_policy (private 属性) に policy インスタンスを保持
      (後段の _apply_decision_context 等が参照する)
    """
    if input_data is None:
        return
    from .decision import resolve_race_type_policy

    policy = resolve_race_type_policy(input_data)
    plan.race_type = policy.race_type
    plan.race_type_policy_notes = list(policy.notes)
    # private 属性として policy インスタンスを保持。Pydantic v2 の
    # ConfigDict(arbitrary_types_allowed=True) 配下なので setattr で OK。
    # __dict__ に直接代入することで Pydantic のシリアライズ対象外にする。
    object.__setattr__(plan, "_race_type_policy", policy)


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
                    suppressed.append(b)
                    removed_count += 1
                    continue
                seen_axes.add(axis_pair)
            kept.append(b)
        setattr(plan, bucket_name, kept)

    # codex P2 反映 (Phase 3) + Phase 8: 抑制候補は watch_only の **先頭**
    # に挿入する。Renderer は watch_only[:2] しか表示しないため、市場偏り
    # 由来の移動候補を確実に見えるようにする。
    # Phase 8: helper 経由で market_bias_suppressed reason group にも追加。
    # codex P2 反映 (Phase 8): suppressed を first-seen で dedupe。
    if suppressed:
        seen_in_supp: set[str] = set()
        dedupe_supp = []
        for b in suppressed:
            if b.combination not in seen_in_supp:
                seen_in_supp.add(b.combination)
                dedupe_supp.append(b)
        for b in reversed(dedupe_supp):
            _add_to_watch_only_with_reason(
                plan, b, "market_bias_suppressed", prepend=True,
            )

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
    # Phase 13 codex P1 反映 (2026-05-25): gami_warning / low_odds 由来の
    # 候補は本来「購入対象外」なので、purchase_coverage の母集団から
    # 除外する。gami 除外前なら 2/4=50% で BUYABLE に寄ったケースが、
    # gami 除外後では 1/3=33% で TENTATIVE cap になる。
    from .decision import is_gami_source
    purchase_bets = [
        b for b in (list(prediction.honsen) + list(prediction.osae))
        if not is_gami_source(b.source_rules)
    ]
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

    # Phase 4 (2026-05-24, 後続レビュー反映): RaceTypePolicy を反映。
    # - force_watch_only_when_low_quality=True かつ data_quality in (low/very_low)
    #   → WATCH_ONLY 以下にキャップ (ガールズ/新人戦は低品質時に強制見送り寄り)
    # - low_coverage_threshold は **WATCH_ONLY cap** の閾値
    #   (girls / rookie / girls_rookie はいずれも 0.25。coverage<0.25 で
    #    WATCH_ONLY 以下にキャップ。SKIP は derive 本体の固定 0.20 で判定)
    # - low_quality_max_purchase_mode で低品質時の cap を上書き
    policy = getattr(plan, "_race_type_policy", None)
    if policy is not None:
        if policy.force_watch_only_when_low_quality and quality in (
            "low", "very_low"
        ):
            ctx.purchase_mode = min(
                ctx.purchase_mode, PurchaseMode.WATCH_ONLY,
            )
            ctx.reasons.append(
                f"race_type={policy.race_type}: "
                f"data_quality={quality} で強制 WATCH_ONLY 以下"
            )
        # codex P1 反映 (2026-05-24): policy.low_coverage_threshold を
        # 「derive 本体閾値 0.20 より厳しい (= より大きい)」場合だけ追加 cap
        # する。girls/rookie/girls_rookie=0.25 では coverage<0.25 で WATCH_ONLY、
        # normal_line=0.20 では derive 本体の SKIP ルールに任せる (重複なし)。
        DERIVE_BUILTIN_THRESHOLD = 0.20
        if (
            policy.low_coverage_threshold > DERIVE_BUILTIN_THRESHOLD
            and coverage.coverage_ratio < policy.low_coverage_threshold
        ):
            ctx.purchase_mode = min(
                ctx.purchase_mode, PurchaseMode.WATCH_ONLY,
            )
            ctx.reasons.append(
                f"race_type={policy.race_type}: 全オッズ取得率 "
                f"{coverage.coverage_ratio:.0%} < 種別閾値 "
                f"{policy.low_coverage_threshold:.0%} → 見送り寄り"
            )
        if quality in ("low", "very_low"):
            ctx.purchase_mode = min(
                ctx.purchase_mode, policy.low_quality_max_purchase_mode,
            )

    plan.purchase_mode = ctx.purchase_mode
    # Phase 13 codex P2 反映 (2026-05-25): 既存 decision_notes を上書きせず
    # 末尾に append する。gami filter / line filter が先に追加した
    # 「N点を分離」note を保持。
    plan.decision_notes.extend(ctx.reasons)
    plan.decision_warnings = list(ctx.warnings)
