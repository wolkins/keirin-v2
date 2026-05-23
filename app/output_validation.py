"""予想出力前の整合性チェック + データ品質判定 + オッズ取得率（要件8-10,16）。

docs/race_type_policy.md フェーズ追加: 出力品質を担保するレイヤー。

公開API:
- `assess_data_quality(input_data) -> Literal["high","medium","low","very_low"]`
- `compute_odds_coverage(prediction) -> dict`
- `validate_prediction_output(input_data, prediction) -> list[Warning]`
- `sanitize_prediction_text(md) -> str` (穴馬→穴目 など)
- `summarize_market_bias(input_data) -> Optional[str]`
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from .models import BetRecommendation, Prediction, RaceInput


# ---------------------------------------------------------------------------
# 要件10: data_quality 判定
# ---------------------------------------------------------------------------

DataQuality = Literal["high", "medium", "low", "very_low"]


def assess_data_quality(input_data: RaceInput) -> DataQuality:
    """RaceInput のデータ品質を 4段階で評価する（要件10）。

    判定基準:
        - high: score / 決まり手 / odds / recent_results が揃っている
        - medium: score と odds はあるが、決まり手が欠損
        - low: score または odds が欠損
        - very_low: score も odds も不足

    Returns:
        "high" / "medium" / "low" / "very_low"
    """
    riders = input_data.riders or []
    if not riders:
        return "very_low"

    # スコア（競走得点）と決まり手の取得状況
    valid_riders = [r for r in riders if not r.stats_missing]
    score_ratio = len(valid_riders) / len(riders) if riders else 0.0
    kimarite_ratio = sum(
        1 for r in valid_riders
        if (r.nige + r.makuri + r.sashi + r.mark) > 0
    ) / len(riders) if riders else 0.0

    has_odds = bool(input_data.odds)
    has_recent = bool(input_data.recent_results)

    score_present = score_ratio >= 0.8
    odds_present = has_odds

    if not score_present and not odds_present:
        return "very_low"
    if not score_present or not odds_present:
        return "low"
    if kimarite_ratio < 0.5 or not has_recent:
        return "medium"
    return "high"


# ---------------------------------------------------------------------------
# 要件9: オッズ取得率
# ---------------------------------------------------------------------------


def _is_cheap_pop(bet: BetRecommendation) -> bool:
    """安い人気筋・ガミ注意 判定 (要件1)。"""
    if bet.value_label == "見送り寄り":
        return True
    if bet.gami_risk >= 0.8:
        return True
    if bet.market_odds is not None and bet.market_odds < 5.0:
        return True
    return False


@dataclass
class OddsCoverage:
    total: int
    with_odds: int
    honsen_total: int
    honsen_with_odds: int
    # 要件1: 実購入本線と安い人気筋を分離
    honsen_real_total: int = 0       # 安い人気筋を除いた本線
    honsen_real_with_odds: int = 0
    honsen_cheap_total: int = 0      # 安い人気筋の本線
    honsen_cheap_with_odds: int = 0

    @property
    def coverage_ratio(self) -> float:
        return self.with_odds / self.total if self.total else 0.0

    @property
    def honsen_coverage_ratio(self) -> float:
        return (
            self.honsen_with_odds / self.honsen_total
            if self.honsen_total else 0.0
        )

    @property
    def honsen_real_coverage_ratio(self) -> float:
        """実購入本線のオッズ取得率 (安い人気筋を除く)。"""
        return (
            self.honsen_real_with_odds / self.honsen_real_total
            if self.honsen_real_total else 0.0
        )

    @property
    def has_warning(self) -> bool:
        """実購入本線オッズ取得率0% は警告対象 (要件1で安い人気筋を除く)。

        honsen_real_total が設定されていればそれを優先、未設定なら honsen 全体で判定。
        """
        if self.honsen_real_total > 0:
            return self.honsen_real_with_odds == 0
        # フォールバック (honsen_real_total 未設定の手動構築用)
        return self.honsen_total > 0 and self.honsen_with_odds == 0


def compute_odds_coverage(prediction: Prediction) -> OddsCoverage:
    """予想全体のオッズ取得率を計算する（要件9 + 要件1で実購入/安い人気筋分離）。"""
    all_bets = (
        list(prediction.honsen) + list(prediction.osae)
        + list(prediction.ana) + list(prediction.ooana)
    )
    total = len(all_bets)
    with_odds = sum(1 for b in all_bets if b.market_odds is not None)
    honsen_total = len(prediction.honsen)
    honsen_with_odds = sum(
        1 for b in prediction.honsen if b.market_odds is not None
    )
    # 実購入本線 (安い人気筋を除く)
    honsen_real = [b for b in prediction.honsen if not _is_cheap_pop(b)]
    honsen_cheap = [b for b in prediction.honsen if _is_cheap_pop(b)]
    return OddsCoverage(
        total=total, with_odds=with_odds,
        honsen_total=honsen_total, honsen_with_odds=honsen_with_odds,
        honsen_real_total=len(honsen_real),
        honsen_real_with_odds=sum(
            1 for b in honsen_real if b.market_odds is not None
        ),
        honsen_cheap_total=len(honsen_cheap),
        honsen_cheap_with_odds=sum(
            1 for b in honsen_cheap if b.market_odds is not None
        ),
    )


def render_odds_coverage_section(coverage: OddsCoverage) -> str:
    """オッズ取得率セクションの Markdown を返す（要件1で分離表示）。"""
    lines = ["### オッズ取得率"]
    lines.append(
        f"- オッズ取得済み: {coverage.with_odds}/{coverage.total}点 "
        f"({coverage.coverage_ratio:.0%})"
    )
    # 実購入本線と安い人気筋を分離
    if coverage.honsen_cheap_total > 0:
        lines.append(
            f"- **実購入本線**オッズ取得済み: "
            f"{coverage.honsen_real_with_odds}/{coverage.honsen_real_total}点 "
            f"({coverage.honsen_real_coverage_ratio:.0%})"
        )
        lines.append(
            f"- 安い人気筋オッズ取得済み: "
            f"{coverage.honsen_cheap_with_odds}/{coverage.honsen_cheap_total}点 "
            f"(参考表示・厚く買わない)"
        )
    else:
        # 安い人気筋が無い場合は従来表示
        lines.append(
            f"- 本線オッズ取得済み: "
            f"{coverage.honsen_with_odds}/{coverage.honsen_total}点 "
            f"({coverage.honsen_coverage_ratio:.0%})"
        )
    if coverage.has_warning:
        lines.append(
            "- **⚠️ 注意**: 実購入本線のオッズが未取得のため、"
            "購入前に必ず確認してください"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 要件11: 市場オッズの偏り
# ---------------------------------------------------------------------------


@dataclass
class MarketBias:
    """市場の偏り検出結果（構造化）。"""
    focused_head: Optional[int] = None     # 集中する頭車番
    focused_count: int = 0                 # 集中件数
    total_top: int = 5                     # 観察件数 (デフォルト 3連単上位5)
    description: Optional[str] = None      # 人間可読な説明
    top_sangle_combos: list[str] = None    # 観察した3連単 上位combo
    cheapest_focused_odds: Optional[float] = None  # 集中頭の最安オッズ (要件2)

    def __post_init__(self):
        if self.top_sangle_combos is None:
            self.top_sangle_combos = []

    @property
    def has_head_focus(self) -> bool:
        """1頭集中 (>= 3/5件) があるか。"""
        return self.focused_count >= 3

    @property
    def is_focused_head_cheap(self) -> bool:
        """集中頭の最安オッズが 5倍未満 (=厚く買うとガミる可能性) か。"""
        return (
            self.cheapest_focused_odds is not None
            and self.cheapest_focused_odds < 5.0
        )


def detect_market_bias(input_data: RaceInput) -> MarketBias:
    """3連単上位5件の頭分布から市場偏りを検出して MarketBias を返す（要件1,11）。"""
    if not input_data.odds:
        return MarketBias()
    sangle = [o for o in input_data.odds if o.bet_type == "3連単"]
    if not sangle:
        return MarketBias()
    sangle_sorted = sorted(sangle, key=lambda o: o.odds or 999.0)[:5]
    # codex review 反映: parse 失敗 odds をスキップすると sangle_sorted と
    # heads の長さがずれるため、(odds_entry, head) ペアで同期管理する
    parsed: list[tuple] = []  # [(odds_entry, head)]
    for o in sangle_sorted:
        if not o.combination or "-" not in o.combination:
            continue
        try:
            head = int(o.combination.split("-")[0])
            parsed.append((o, head))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return MarketBias()
    heads = [h for _, h in parsed]
    combos = [o.combination for o, _ in parsed]
    from collections import Counter
    head_counts = Counter(heads)
    top_head, top_count = head_counts.most_common(1)[0]
    description = None
    cheapest_focused_odds: Optional[float] = None
    if top_count >= 3:
        # 集中頭の最安オッズ取得 (parsed ペアで安全に対応付け)
        focused_odds = [
            o.odds for o, h in parsed
            if h == top_head and o.odds is not None
        ]
        if focused_odds:
            cheapest_focused_odds = min(focused_odds)
        # 説明文 (要件2: オッズが安い場合は「厚く買わない」を明記)
        base = (
            f"市場（3連単人気上位{len(heads)}件）は **{top_head}番頭** に集中"
            f"（{top_count}/{len(heads)}件）"
        )
        if cheapest_focused_odds is not None and cheapest_focused_odds < 5.0:
            description = (
                f"{base}。**ただし最安{cheapest_focused_odds:.1f}倍と"
                f"オッズが安いため厚く買わない**"
            )
        elif cheapest_focused_odds is not None:
            description = (
                f"{base}（最安{cheapest_focused_odds:.1f}倍）"
            )
        else:
            description = base
    else:
        # 3連複の集中で代替説明
        trio = [o for o in input_data.odds if o.bet_type == "3連複"]
        if trio:
            trio_sorted = sorted(trio, key=lambda o: o.odds or 999.0)[:3]
            if trio_sorted and trio_sorted[0].odds and trio_sorted[0].odds < 3.0:
                description = (
                    f"市場（3連複最安）{trio_sorted[0].combination} "
                    f"({trio_sorted[0].odds:.1f}倍) に人気集中"
                )
    return MarketBias(
        focused_head=top_head if top_count >= 3 else None,
        focused_count=top_count if top_count >= 3 else 0,
        total_top=len(heads),
        description=description,
        top_sangle_combos=combos,
        cheapest_focused_odds=cheapest_focused_odds,
    )


def summarize_market_bias(input_data: RaceInput) -> Optional[str]:
    """市場偏りの説明文を返す（後方互換）。"""
    return detect_market_bias(input_data).description


# ---------------------------------------------------------------------------
# 要件8,16: validate_prediction_output() 整合性チェック
# ---------------------------------------------------------------------------


@dataclass
class ValidationWarning:
    code: str
    message: str
    severity: str = "warning"  # "warning" | "error" | "info"


def validate_prediction_output(
    input_data: RaceInput,
    prediction: Prediction,
) -> list[ValidationWarning]:
    """予想出力の整合性をチェック（要件8,16）。

    検出項目:
        - 本線がすべて market_odds=None
        - 一番買いたい買い目に「見送り寄り」が含まれる
        - 一番買いたい買い目に gami_risk >= 0.8 が含まれる
        - honsen と final_conclusion の買い目が一致していない
        - 実購入判断「本線として有力」が honsen に存在しない
        - ガールズなのに「番手」「別線番手」「本命ライン」表現が出る
        - 新人戦なのに通常ライン戦の表現が出る
        - 「穴馬」表現の混入

    Returns:
        検出した警告リスト（空なら問題なし）
    """
    warnings: list[ValidationWarning] = []

    # 1. 本線がすべて market_odds=None
    if prediction.honsen and all(
        b.market_odds is None for b in prediction.honsen
    ):
        warnings.append(ValidationWarning(
            code="HONSEN_ALL_NO_ODDS",
            message="本線がすべてオッズ未取得です。実購入前にオッズ確認が必要です。",
        ))

    # 2. 「一番買いたい買い目」候補に「見送り寄り」/ 高ガミ含む
    #    → final_conclusion 内に該当文言があるかでチェック
    fc = prediction.final_conclusion or ""

    # 3. honsen / final_conclusion の整合性
    #    本線として有力 行に書かれた combo が honsen に存在するか
    judgement_lines = [
        ln for ln in fc.split("\n") if "本線として有力" in ln
    ]
    if judgement_lines:
        judgement_combos = set()
        for ln in judgement_lines:
            for m in re.finditer(r"\b(\d-\d-\d)\b", ln):
                judgement_combos.add(m.group(1))
        honsen_combos = {b.combination for b in prediction.honsen}
        missing = judgement_combos - honsen_combos
        if missing:
            warnings.append(ValidationWarning(
                code="HONSEN_JUDGEMENT_MISMATCH",
                message=(
                    f"実購入判断「本線として有力」({', '.join(sorted(missing))}) "
                    f"が本線セクションに存在しません。"
                ),
            ))

    # 4. ガールズに番手用語混入
    if input_data.race.resolved_is_girls():
        line_terms = ("番手", "別線番手", "本命ライン", "ライン3番手")
        for bucket_name, bucket in (
            ("本線", prediction.honsen), ("押さえ", prediction.osae),
            ("穴", prediction.ana), ("大穴", prediction.ooana),
        ):
            for b in bucket:
                for term in line_terms:
                    if b.reason and term in b.reason:
                        warnings.append(ValidationWarning(
                            code="GIRLS_LINE_TERM",
                            message=(
                                f"ガールズなのに{bucket_name} {b.combination} に "
                                f"「{term}」表現が含まれています: "
                                f"{b.reason[:60]}..."
                            ),
                        ))
                        break

    # 5. 新人戦の line 用語混入は scoring._sanitize_reason_for_rookie で
    #    一次サニタイズされる前提。final_conclusion 側も確認。
    if input_data.race.resolved_is_rookie():
        for term in ("本命ライン", "別線番手", "ライン3番手"):
            if term in fc:
                warnings.append(ValidationWarning(
                    code="ROOKIE_LINE_TERM",
                    message=(
                        f"新人戦なのに最終結論に「{term}」が含まれています"
                    ),
                ))

    # 6. 「穴馬」表現の混入（競輪では「穴目」「穴買い目」と呼ぶ）
    if "穴馬" in fc or "穴馬" in (prediction.gami_memo or ""):
        warnings.append(ValidationWarning(
            code="ANAUMA_TERM",
            message="「穴馬」は競馬用語です。「穴目」「穴買い目」を使ってください。",
        ))
    for bucket in (prediction.honsen, prediction.osae,
                   prediction.ana, prediction.ooana):
        for b in bucket:
            if b.reason and "穴馬" in b.reason:
                warnings.append(ValidationWarning(
                    code="ANAUMA_TERM",
                    message=f"買い目 {b.combination} の reason に「穴馬」が含まれます",
                ))
                break

    # 7. market_odds=None の買い目に gami_risk が高い設定が混じっていた場合は
    # sanitize_prediction で 0 に補正される前提。validate は info レベルで通知。
    for bucket in (prediction.honsen, prediction.osae,
                   prediction.ana, prediction.ooana):
        for b in bucket:
            if b.market_odds is None and b.gami_risk >= 0.6:
                warnings.append(ValidationWarning(
                    code="ODDS_NONE_HIGH_GAMI",
                    severity="info",
                    message=(
                        f"買い目 {b.combination} は market_odds=None でしたが "
                        f"gami_risk={b.gami_risk:.2f} を 0 に補正しました"
                    ),
                ))
                break

    return warnings


# ---------------------------------------------------------------------------
# 要件6: 「穴馬」→「穴目」サニタイズ
# ---------------------------------------------------------------------------


_TERM_REPLACEMENTS = {
    "穴馬": "穴目",
    "穴馬券": "穴買い目",
    "本命馬": "本命",
}

# 反省ポイント等で誤った文言が出た場合の修正辞書（要件5）
_REFLECTION_REPLACEMENTS = {
    "市場人気に基づく無理な展開予想をしない":
        "市場人気が特定頭・特定ラインに集中している場合、"
        "候補昇格が十分だったか確認",
    "市場人気に振り回された無理な展開予想":
        "市場人気が特定頭・特定ラインに集中している場合の候補昇格",
    # 要件5: 反省文言の自然化
    "本線は少額ながら見送る候補を設定する":
        "安い人気筋は厚く買わず、見送りまたは少額確認に留める",
}

# ガールズ専用の用語置換 (要件1,2)
# 「番手」「ライン」「3番手」「4番手」など、ガールズで使用禁止の用語を
# 自然な代替表現に置換する。順序が重要 (長い表現を先に置換)。
_GIRLS_TERM_REPLACEMENTS = {
    # ライン関連
    "本命ライン": "本命候補",
    "別線ライン": "別候補",
    "ライン3番手": "中位",
    # 「N番手」表現 (4番手→4位、3番手→中位、別線番手→追走型)
    "4番手評価": "4位評価",
    "5番手評価": "5位評価",
    "別線番手": "追走型",
    "3番手": "中位",
    "4番手": "4位",
    "5番手": "5位",
    # 「番手」単独 (ただし「2位頭」「対抗頭」等は不変)
    "番手頭": "対抗頭",
    "番手差し": "差し",
    "番手": "追走",
    # ライン単独
    "ライン": "並び",
}


def sanitize_prediction_text(text: str, *, is_girls: bool = False) -> str:
    """LLM出力から競馬用語を競輪用語に置換する（要件6）+ 反省文言補正（要件5）。

    is_girls=True ならガールズ用語サニタイズ (要件1,2) も適用。
    """
    if not text:
        return text
    out = text
    for old, new in _TERM_REPLACEMENTS.items():
        out = out.replace(old, new)
    for old, new in _REFLECTION_REPLACEMENTS.items():
        out = out.replace(old, new)
    if is_girls:
        for old, new in _GIRLS_TERM_REPLACEMENTS.items():
            out = out.replace(old, new)
    return out


def sanitize_prediction(prediction: Prediction) -> None:
    """Prediction オブジェクトの文字列フィールドとフィールド値を破壊的にサニタイズ。

    対応:
        - 文字列フィールドの「穴馬」→「穴目」等を置換
        - market_odds=None の買い目の gami_risk を 0.0 に強制 (要件3)
        - ガールズ時の「番手」「ライン」等を「追走」「並び」等に自動置換 (要件1,2)
    """
    is_girls = bool(prediction.is_girls)
    # 文字列フィールドを総ざらいでサニタイズ
    # (codex review 反映: summary/venue_trend_text/weather_text/lines_text も
    # render_prediction で出力されるため、ガールズ用語が混入してはいけない)
    string_fields = (
        "final_conclusion", "gami_memo",
        "summary", "venue_trend_text", "weather_text", "lines_text",
    )
    for field in string_fields:
        text = getattr(prediction, field, None)
        if text:
            setattr(
                prediction, field,
                sanitize_prediction_text(text, is_girls=is_girls),
            )
    if prediction.reflection_points:
        prediction.reflection_points = [
            sanitize_prediction_text(pt, is_girls=is_girls)
            for pt in prediction.reflection_points
        ]
    for bucket in (prediction.honsen, prediction.osae,
                   prediction.ana, prediction.ooana):
        for b in bucket:
            if b.reason:
                b.reason = sanitize_prediction_text(b.reason, is_girls=is_girls)
            # 要件3: market_odds=None の場合は gami_risk を 0 にする
            if b.market_odds is None and b.gami_risk > 0:
                b.gami_risk = 0.0
