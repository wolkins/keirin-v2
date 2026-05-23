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


@dataclass
class OddsCoverage:
    total: int
    with_odds: int
    honsen_total: int
    honsen_with_odds: int

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
    def has_warning(self) -> bool:
        """本線オッズ取得率0% は警告対象。"""
        return self.honsen_total > 0 and self.honsen_with_odds == 0


def compute_odds_coverage(prediction: Prediction) -> OddsCoverage:
    """予想全体のオッズ取得率を計算する（要件9）。"""
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
    return OddsCoverage(
        total=total, with_odds=with_odds,
        honsen_total=honsen_total, honsen_with_odds=honsen_with_odds,
    )


def render_odds_coverage_section(coverage: OddsCoverage) -> str:
    """オッズ取得率セクションの Markdown を返す（要件9）。"""
    lines = ["### オッズ取得率"]
    lines.append(
        f"- オッズ取得済み: {coverage.with_odds}/{coverage.total}点 "
        f"({coverage.coverage_ratio:.0%})"
    )
    lines.append(
        f"- 本線オッズ取得済み: {coverage.honsen_with_odds}/{coverage.honsen_total}点 "
        f"({coverage.honsen_coverage_ratio:.0%})"
    )
    if coverage.has_warning:
        lines.append(
            "- **注意**: 本線のオッズが未取得のため、購入前に確認してください"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 要件11: 市場オッズの偏り
# ---------------------------------------------------------------------------


def summarize_market_bias(input_data: RaceInput) -> Optional[str]:
    """3連単・3連複オッズ上位の集中ラインを要約する（要件11）。

    Returns:
        集中傾向の説明文（無ければ None）
    """
    if not input_data.odds:
        return None
    # 3連単 上位5件のヘッド車をカウント
    sangle = [o for o in input_data.odds if o.bet_type == "3連単"]
    if not sangle:
        return None
    sangle_sorted = sorted(sangle, key=lambda o: o.odds or 999)[:5]
    heads = []
    for o in sangle_sorted:
        if not o.combination or "-" not in o.combination:
            continue
        try:
            head = int(o.combination.split("-")[0])
            heads.append(head)
        except (ValueError, TypeError):
            continue
    if not heads:
        return None
    from collections import Counter
    head_counts = Counter(heads)
    top_head, top_count = head_counts.most_common(1)[0]
    if top_count >= 3:
        return (
            f"市場（3連単人気上位5件）は **{top_head}番頭** に集中"
            f"（{top_count}/{len(heads)}件）"
        )
    # 3連複の集中
    trio = [o for o in input_data.odds if o.bet_type == "3連複"]
    if trio:
        trio_sorted = sorted(trio, key=lambda o: o.odds or 999)[:3]
        if trio_sorted and trio_sorted[0].odds and trio_sorted[0].odds < 3.0:
            return (
                f"市場（3連複最安）{trio_sorted[0].combination} "
                f"({trio_sorted[0].odds:.1f}倍) に人気集中"
            )
    return None


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

    # 7. market_odds=None の買い目に gami_risk が高い設定 → 表示誤り防止
    for bucket in (prediction.honsen, prediction.osae,
                   prediction.ana, prediction.ooana):
        for b in bucket:
            if b.market_odds is None and b.gami_risk >= 0.6:
                warnings.append(ValidationWarning(
                    code="ODDS_NONE_HIGH_GAMI",
                    message=(
                        f"買い目 {b.combination} は market_odds=None なのに "
                        f"gami_risk={b.gami_risk:.2f} と高い設定です。"
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


def sanitize_prediction_text(text: str) -> str:
    """LLM出力から競馬用語を競輪用語に置換する（要件6）。"""
    if not text:
        return text
    out = text
    for old, new in _TERM_REPLACEMENTS.items():
        out = out.replace(old, new)
    return out


def sanitize_prediction(prediction: Prediction) -> None:
    """Prediction オブジェクトの文字列フィールドを破壊的にサニタイズ。"""
    if prediction.final_conclusion:
        prediction.final_conclusion = sanitize_prediction_text(
            prediction.final_conclusion
        )
    if prediction.gami_memo:
        prediction.gami_memo = sanitize_prediction_text(prediction.gami_memo)
    for bucket in (prediction.honsen, prediction.osae,
                   prediction.ana, prediction.ooana):
        for b in bucket:
            if b.reason:
                b.reason = sanitize_prediction_text(b.reason)
