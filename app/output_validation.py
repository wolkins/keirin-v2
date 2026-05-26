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


def assess_data_quality(
    input_data: RaceInput,
    coverage: Optional["OddsCoverage"] = None,
) -> DataQuality:
    """RaceInput のデータ品質を 4段階で評価する（要件10）。

    判定基準:
        - high: score / 決まり手 / odds / recent_results が揃っている
                + (武雄12R 対応 2026-05-24) odds_overall_coverage >= 0.4
        - medium: score と odds はあるが、決まり手が欠損 or
                  odds_overall_coverage が 0.4 未満
        - low: score または odds が欠損
        - very_low: score も odds も不足

    Args:
        input_data: 評価対象
        coverage: あれば odds_overall_coverage (= coverage_ratio) を判定に使う。
                  武雄12R: coverage_ratio < 0.4 のときは high を許容しない。

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
    # 武雄12R 対応: overall coverage が 40% 未満なら high にしない
    if coverage is not None and coverage.coverage_ratio < 0.4:
        return "medium"
    return "high"


# ---------------------------------------------------------------------------
# 静岡4R #377: data_quality を 5項目に分解 (2026-05-24)
# ---------------------------------------------------------------------------


@dataclass
class DataQualityBreakdown:
    """data_quality の内訳。総合判定 (assess_data_quality) の根拠を
    5項目で表示するための補助構造。

    Fields:
        score: 競走得点が 80% 以上揃っているか
        odds: オッズが 1件以上あるか
        kimarite: 決まり手 (nige/makuri/sashi/mark) が 50% 以上揃っているか
        recent: recent_results が 1件以上あるか
        weather: 天候情報 (condition + wind_speed_mps の少なくとも片方) があるか
        overall: 総合判定 (assess_data_quality の結果)
    """

    score: bool
    odds: bool
    kimarite: bool
    recent: bool
    weather: bool
    overall: DataQuality

    def to_markdown_lines(self) -> list[str]:
        """5項目を行頭マーカー付きで返す (○=有り、×=欠損)。"""
        def _mk(label: str, ok: bool) -> str:
            return f"- {'○' if ok else '×'} {label}"
        return [
            _mk("競走得点 (80%+揃い)", self.score),
            _mk("オッズソース (市場上位オッズ取得あり)", self.odds),
            _mk("決まり手 (50%+揃い)", self.kimarite),
            _mk("直近結果 (1件以上)", self.recent),
            _mk("天候情報 (風速/雨量/天気)", self.weather),
        ]


def assess_data_quality_breakdown(
    input_data: RaceInput,
    coverage: Optional["OddsCoverage"] = None,
) -> DataQualityBreakdown:
    """data_quality の内訳と総合判定を返す。`assess_data_quality` と整合。

    既存の総合判定ロジック (high/medium/low/very_low) は変えない。
    本関数は追加で内訳 (5項目) を返すための補助 API。
    """
    riders = input_data.riders or []
    if not riders:
        return DataQualityBreakdown(
            score=False, odds=False, kimarite=False,
            recent=False, weather=False, overall="very_low",
        )

    valid_riders = [r for r in riders if not r.stats_missing]
    score_ratio = len(valid_riders) / len(riders)
    kimarite_ratio = sum(
        1 for r in valid_riders
        if (r.nige + r.makuri + r.sashi + r.mark) > 0
    ) / len(riders)

    has_odds = bool(input_data.odds)
    has_recent = bool(input_data.recent_results)
    weather_ok = (
        input_data.weather is not None
        and (
            bool(input_data.weather.condition)
            or input_data.weather.wind_speed_mps is not None
            or input_data.weather.rain_mm_per_hour is not None
        )
    )

    return DataQualityBreakdown(
        score=score_ratio >= 0.8,
        odds=has_odds,
        kimarite=kimarite_ratio >= 0.5,
        recent=has_recent,
        weather=weather_ok,
        overall=assess_data_quality(input_data, coverage=coverage),
    )


# ---------------------------------------------------------------------------
# 武雄12R 対応: race_complexity 判定 (2026-05-24)
# ---------------------------------------------------------------------------


RaceComplexity = Literal["low", "medium", "high", "very_high"]


def assess_race_complexity(input_data: RaceInput) -> RaceComplexity:
    """レースの読みづらさ (難度) を 4段階で評価する。

    判定要素 (武雄12R 仕様):
        - 競走得点 115 以上の選手数 (S級+ 相当)
        - 2車ラインの数
        - 単騎の格上 (高 score) 数
        - グレード / 特選 / 優秀系 (race_grade)
        - 出走選手の競走得点散らばり

    Returns:
        "low" / "medium" / "high" / "very_high"

    使い方:
        - high / very_high: 読み筋分散、購入判断を慎重に
        - very_high + coverage<0.4: 「購入見送り推奨レベル」と final_selection
          で警告
    """
    riders = input_data.riders or []
    if not riders:
        return "low"

    score = 0  # 加点式 (合計から複雑度を判定)

    # 1. 競走得点 115 以上の選手数 (S級+)
    top_score_riders = sum(
        1 for r in riders if r.score and r.score >= 115.0
    )
    if top_score_riders >= 4:
        score += 3
    elif top_score_riders >= 2:
        score += 2
    elif top_score_riders >= 1:
        score += 1

    # 2. 2車ラインの数 (3車以上のラインが少ない → 読みづらい)
    lines = input_data.lines or []
    two_car_lines = sum(
        1 for ln in lines if ln.cars and len(ln.cars) == 2
    )
    if two_car_lines >= 3:
        score += 2
    elif two_car_lines >= 2:
        score += 1

    # 3. 単騎の格上 (score >= 100) 数
    tanki_cars: set[int] = set()
    for ln in lines:
        if ln.cars and len(ln.cars) == 1:
            tanki_cars.add(ln.cars[0])
    tanki_top = sum(
        1 for r in riders
        if r.car_no in tanki_cars and r.score and r.score >= 100.0
    )
    if tanki_top >= 2:
        score += 2
    elif tanki_top >= 1:
        score += 1

    # 4. グレード / 特選 / 優秀系 (race_grade)
    race_grade = (input_data.race.resolved_race_grade() or "").upper()
    if race_grade in ("GP", "G1"):
        score += 3
    elif race_grade in ("G2", "G3"):
        score += 2
    elif race_grade == "F1":
        score += 1
    class_name = (input_data.race.class_name or "").lower()
    if (
        "特選" in input_data.race.class_name
        or "優秀" in input_data.race.class_name
        or "spr" in class_name
    ):
        score += 1

    # 5. 競走得点散らばり (上位3名と中位の差)
    scores = sorted(
        (r.score for r in riders if r.score), reverse=True
    )
    if len(scores) >= 5:
        top3_avg = sum(scores[:3]) / 3
        mid_avg = sum(scores[2:5]) / 3
        spread = top3_avg - mid_avg
        # 上位と中位の差が小さい (拮抗) → 読みづらい
        if spread < 2.0:
            score += 2
        elif spread < 5.0:
            score += 1

    # 合計 score から complexity を判定
    if score >= 8:
        return "very_high"
    if score >= 5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


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


def compute_odds_coverage(
    prediction: Prediction,
    plan=None,
) -> OddsCoverage:
    """予想全体のオッズ取得率を計算する（要件9 + 要件1で実購入/安い人気筋分離）。

    平塚6R 対応 (2026-05-24, codex review 反映):
    - plan (OutputPlan) を渡すと、本線母集団を **plan.honsen** に切り替える
      (= 実際に表示される本線で集計、表示と footer がズレない)
    - `plan.gami_warning` の combo を honsen_real から除外
    - 全体集計 (total / with_odds) は plan があれば
      plan の表示セクション (honsen+osae+ana+ooana+gami_warning) で集計
    """
    if plan is not None:
        # 表示母集団 = plan のセクション + gami_warning
        all_bets = (
            list(plan.honsen) + list(plan.osae)
            + list(plan.ana) + list(plan.ooana)
            + list(plan.gami_warning)
        )
        honsen_source = list(plan.honsen)
    else:
        all_bets = (
            list(prediction.honsen) + list(prediction.osae)
            + list(prediction.ana) + list(prediction.ooana)
        )
        honsen_source = list(prediction.honsen)

    # 重複排除 (gami_warning が他カテゴリと重複する場合)
    seen: set[str] = set()
    deduped_all: list = []
    for b in all_bets:
        key = b.combination or id(b)
        if key in seen:
            continue
        seen.add(key)
        deduped_all.append(b)
    all_bets = deduped_all
    total = len(all_bets)
    with_odds = sum(1 for b in all_bets if b.market_odds is not None)

    honsen_total = len(honsen_source)
    honsen_with_odds = sum(
        1 for b in honsen_source if b.market_odds is not None
    )
    # gami_warning に該当する combo は honsen_real から除外
    gami_combos: set[str] = set()
    if plan is not None:
        gami_combos = {
            b.combination for b in plan.gami_warning if b.combination
        }
    # 実購入本線 (安い人気筋 + gami_warning を除く)
    honsen_real = [
        b for b in honsen_source
        if not _is_cheap_pop(b) and b.combination not in gami_combos
    ]
    honsen_cheap = [
        b for b in honsen_source
        if _is_cheap_pop(b) or b.combination in gami_combos
    ]
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


def render_coverage_metrics_section(metrics) -> str:
    """Phase 16 Step 5A (2026-05-26): CoverageMetrics から候補買い目オッズ
    取得率セクションを「目的別」に分けた layout で生成.

    旧 layout は「取得済み: 5/16 / 本線オッズ取得済み: 3/3」のように
    1 つの集計しか出さず、静岡6R の `本文 6.1倍 / 末尾 0/8 (0%)` という
    矛盾が解消できなかった。新 layout では:

    - 表示候補オッズ: 全表示候補の取得済み件数
    - 実購入候補オッズ: 購入候補のみの取得済み件数 (0/0 なら「購入候補なし」)
    - 本線表示候補オッズ: 安い人気筋を除いた本線
    - 参考候補オッズ: decision_state=WATCH_ONLY のみ集計
    - ガミ注意候補オッズ: decision_state=GAMI_WARNING のみ集計
    - 安い人気筋オッズ (honsen_cheap > 0 のときのみ): 参考表示・厚く買わない

    各カテゴリで total=0 のときはその行を省略する (混乱を避ける)。
    実購入候補だけは特別扱い: 0/0 → 「購入候補なし」と明示する。

    Args:
        metrics: CoverageMetrics

    Returns:
        Markdown 文字列
    """
    lines = ["### 候補買い目オッズ取得率"]

    # 1. 表示候補オッズ (基本指標、必ず表示)
    display = metrics.display
    if display.total > 0:
        lines.append(
            f"- 表示候補オッズ: {display.with_odds}/{display.total}点 "
            f"({display.ratio:.0%})"
        )
    else:
        lines.append("- 表示候補オッズ: 表示候補なし")

    # 2. 購入候補オッズ
    # Phase 16 Step 5A (2026-05-26): ラベルは「実購入候補オッズ」ではなく
    # 「購入候補オッズ」とする。Phase 15 の禁止語チェック (basic_forbidden)
    # に「実購入候補」が含まれるため、本文ラベルでも誤検出されない
    # 表現に統一する。
    purchase = metrics.purchase
    if purchase.total == 0:
        lines.append("- 購入候補オッズ: 購入候補なし")
    else:
        lines.append(
            f"- 購入候補オッズ: {purchase.with_odds}/{purchase.total}点 "
            f"({purchase.ratio:.0%})"
        )

    # 3. 本線表示候補オッズ (honsen_real)
    honsen_real = metrics.honsen_real
    if honsen_real.total > 0:
        lines.append(
            f"- 本線表示候補オッズ: "
            f"{honsen_real.with_odds}/{honsen_real.total}点 "
            f"({honsen_real.ratio:.0%})"
        )

    # 4. 参考候補オッズ (state=WATCH_ONLY)
    watch_only = metrics.watch_only
    if watch_only.total > 0:
        lines.append(
            f"- 参考候補オッズ: {watch_only.with_odds}/{watch_only.total}点 "
            f"({watch_only.ratio:.0%}・厚く買わない)"
        )

    # 5. ガミ注意候補オッズ (state=GAMI_WARNING)
    gami = metrics.gami_warning
    if gami.total > 0:
        lines.append(
            f"- ガミ注意候補オッズ: {gami.with_odds}/{gami.total}点 "
            f"({gami.ratio:.0%}・売れすぎ警戒)"
        )

    # 6. 安い人気筋オッズ (旧 honsen_cheap、互換) — 旧 OddsCoverage との整合
    honsen_cheap = metrics.honsen_cheap
    if honsen_cheap.total > 0:
        lines.append(
            f"- 安い人気筋オッズ: "
            f"{honsen_cheap.with_odds}/{honsen_cheap.total}点 "
            f"(参考表示・厚く買わない)"
        )

    # 7. 警告 (購入候補が空 OR 実購入本線オッズ 0%)
    if purchase.total == 0:
        lines.append(
            "- **⚠️ 注意**: 購入候補なし — オッズ再取得後に再判断"
            "してください"
        )
    elif honsen_real.total > 0 and honsen_real.with_odds == 0:
        lines.append(
            "- **⚠️ 注意**: 本線表示候補のオッズが未取得のため、"
            "再取得後に再確認してください"
        )
    return "\n".join(lines)


def render_odds_coverage_section(coverage: OddsCoverage) -> str:
    """**v1 legacy 経路用** の候補買い目オッズ取得率セクション.

    Phase 16 Step 5B (2026-05-26): 本関数は **v1 renderer 専用** に位置づけ
    変更。v2 経路では `render_coverage_metrics_section(metrics)` (lifecycle
    ベース、state 別 layout) を使う。

    v2 で本関数を呼ぶケース:
    - decision_engine populate 失敗時の safe fallback のみ
    - その場合は markdown_renderer.py が DECISION_ENGINE_NOT_POPULATED
      warning を併出する

    Phase 15 (2026-05-25): 見出しを「オッズ取得率」→「候補買い目オッズ
    取得率」に変更。市場人気オッズの取得状況は
    `render_market_odds_status_section` で別セクションとして表示するように
    分離 (候補買い目側 0/8 でも市場人気オッズが取れていれば矛盾に
    見えない表示にする)。
    """
    lines = ["### 候補買い目オッズ取得率"]
    lines.append(
        f"- 取得済み: {coverage.with_odds}/{coverage.total}点 "
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
            "再取得後に再確認してください"
        )
    return "\n".join(lines)


def render_market_odds_status_section(input_data) -> str:
    """市場人気オッズ取得状況セクションの Markdown を返す。

    Phase 15 (2026-05-25): 候補買い目オッズ取得率と分離する。候補側が
    0/8 でも、市場人気オッズ (input_data.odds) が取得済みなら
    「市場人気オッズは取得済み — 候補側のオッズ突き合わせ未完了」と表示
    して矛盾に見えない形にする。

    Args:
        input_data: RaceInput (odds: list[OddsEntry] を持つ)

    Returns:
        Markdown 文字列 (空白行含む)
    """
    if input_data is None:
        return ""
    odds_list = getattr(input_data, "odds", []) or []
    total = len(odds_list)
    # bet_type 別の内訳 (主要 4 種)
    by_type: dict[str, int] = {}
    for o in odds_list:
        bt = getattr(o, "bet_type", None)
        if bt:
            by_type[bt] = by_type.get(bt, 0) + 1

    lines = ["### 市場人気オッズ取得状況"]
    if total == 0:
        lines.append("- 未取得 — 市場人気オッズが取得できていません")
    else:
        lines.append(f"- 取得済み: {total}点")
        # 主要 bet_type の取得件数を補足表示
        priority = ("3連単", "3連複", "2車単", "2車複", "ワイド")
        breakdown_parts: list[str] = []
        for bt in priority:
            if bt in by_type:
                breakdown_parts.append(f"{bt} {by_type[bt]}点")
        if breakdown_parts:
            lines.append(f"- 内訳: {' / '.join(breakdown_parts)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 要件11: 市場オッズの偏り
# ---------------------------------------------------------------------------


@dataclass
class MarketBias:
    """市場の偏り検出結果（構造化）。

    武雄12R 対応 (2026-05-24): HeadBias と AxisBias を分離。
    - HeadBias: 1着車番が市場上位5件中3件以上の集中
    - AxisBias: 1-2着軸 (head + second の組み合わせ) が3件以上
    HeadBias だけなら 1-?-X を分散候補に。AxisBias があれば 1-7-X 集中許可。
    """
    focused_head: Optional[int] = None     # 集中する頭車番 (HeadBias)
    focused_count: int = 0                 # HeadBias 件数
    total_top: int = 5                     # 観察件数 (デフォルト 3連単上位5)
    description: Optional[str] = None      # 人間可読な説明
    top_sangle_combos: list[str] = None    # 観察した3連単 上位combo
    cheapest_focused_odds: Optional[float] = None  # 集中頭の最安オッズ (要件2)
    # 武雄12R: AxisBias (1-2着軸固定)
    focused_axis: Optional[tuple[int, int]] = None   # (head, second)
    focused_axis_count: int = 0                      # AxisBias 件数

    def __post_init__(self):
        if self.top_sangle_combos is None:
            self.top_sangle_combos = []

    @property
    def has_head_focus(self) -> bool:
        """1頭集中 (>= 3/5件) があるか。"""
        return self.focused_count >= 3

    @property
    def has_axis_focus(self) -> bool:
        """1-2着軸集中 (>= 3/5件) があるか (武雄12R 対応)。"""
        return self.focused_axis_count >= 3

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

    # 武雄12R 対応 (2026-05-24): AxisBias (1-2着固定軸) 検出
    # parsed: [(odds_entry, head)] から second を抽出して (head, second) を集計
    axis_counts: Counter = Counter()
    for o, head in parsed:
        parts = o.combination.split("-")
        if len(parts) >= 2:
            try:
                second = int(parts[1])
                axis_counts[(head, second)] += 1
            except (ValueError, TypeError):
                continue
    focused_axis: Optional[tuple[int, int]] = None
    focused_axis_count = 0
    if axis_counts:
        top_axis, top_axis_count = axis_counts.most_common(1)[0]
        if top_axis_count >= 3:
            focused_axis = top_axis
            focused_axis_count = top_axis_count

    return MarketBias(
        focused_head=top_head if top_count >= 3 else None,
        focused_count=top_count if top_count >= 3 else 0,
        total_top=len(heads),
        description=description,
        top_sangle_combos=combos,
        cheapest_focused_odds=cheapest_focused_odds,
        focused_axis=focused_axis,
        focused_axis_count=focused_axis_count,
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

    # 8. 静岡4R 修正方針1 (2026-05-24):
    # final_conclusion 内の3連単買い目が honsen/osae/ana/ooana のいずれにも
    # 登録されていない場合は ERROR レベル警告
    registered_combos: set[str] = set()
    for bucket in (prediction.honsen, prediction.osae,
                   prediction.ana, prediction.ooana):
        for b in bucket:
            if b.combination:
                registered_combos.add(b.combination)
    if fc:
        fc_combos = set(re.findall(r"\b(\d-\d-\d)\b", fc))
        unregistered = fc_combos - registered_combos
        if unregistered:
            warnings.append(ValidationWarning(
                code="CONCLUSION_COMBO_UNREGISTERED",
                severity="error",
                message=(
                    f"最終結論に honsen/osae/ana/ooana に存在しない買い目が"
                    f"含まれます: {', '.join(sorted(unregistered))} → "
                    f"テンプレート生成にフォールバックすべき"
                ),
            ))

    # 9. 静岡4R 修正方針3 (2026-05-24):
    # ◎ の選手が honsen の1着または2着候補に一度も出ない場合は警告
    honmei = prediction.marks.get("◎") if prediction.marks else None
    if honmei is not None and prediction.honsen:
        honmei_str = str(honmei)
        in_top12 = False
        for b in prediction.honsen:
            if not b.combination or "-" not in b.combination:
                continue
            parts = b.combination.split("-")
            if len(parts) >= 2 and (parts[0] == honmei_str or parts[1] == honmei_str):
                in_top12 = True
                break
        if not in_top12:
            warnings.append(ValidationWarning(
                code="HONMEI_NOT_IN_HONSEN_TOP2",
                severity="warning",
                message=(
                    f"◎{honmei} 番が本線の1着候補にも2着候補にも"
                    # 2026-05-24: 「ライン」を「位置取り」に置換 (新人戦/ガールズ
                    # でも誤検出されない汎用文言にする)
                    f"含まれません。印と位置取り評価の整合を再確認してください。"
                ),
            ))

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


# 新人戦専用の用語置換 (2026-05-24, d0e5fea 後続対応)
# 新人戦も固定ライン戦の前提を持たないため、ガールズと同じ方針で置換する。
# 「ライン」→「位置取り」をベースに、要件で指定された語を網羅する。
# ガールズと辞書を独立に持つことで、将来の差分対応 (新人戦のみ別表現にする等)
# にも対応できる。順序が重要 (長い表現を先に置換)。
# 平塚7R 後続レビュー反映 (2026-05-24): 「本命頭」「本命自力」を追加、
# 「番手頭」を「追走型の浮上」に変更
_ROOKIE_TERM_REPLACEMENTS = {
    # 長い表現を先に置換 (順序重要)
    "本命ライン": "上位評価",
    "別線ライン": "別候補",
    "ライン3番手": "中位",
    "4番手評価": "4位評価",
    "5番手評価": "5位評価",
    "本命自力": "上位評価選手",
    "本命頭": "上位評価の頭",
    "別線番手": "追走型",
    "3番手": "中位",
    "4番手": "4位評価",
    "5番手": "5位評価",
    "番手頭": "追走型の浮上",
    "番手差し": "差し",
    "番手": "追走",
    # 新人戦は「位置取り」表現を許容するため「ライン」→「位置取り」
    "ライン": "位置取り",
}


# 平塚10R 後続レビュー反映 (2026-05-24): low coverage 状況での文言弱体化
# value_label 表示や「(本線)」表記を「暫定候補」「参考候補」に置換する。
# 注意:
# - 「本線」は ## 6. 本線 のような大見出しでは使ってよい
# - 強い購入推奨を意味する「(本線)」「**本線向き**」 等を弱める
# - render_output_plan / _build_gami_memo の後段で適用
# 平塚10R 後続レビュー反映 (2026-05-24): 「オッズ確認後の本線候補」サブ
# セクション見出しも置換対象。レンダラ側で is_girls/is_rookie/low_coverage
# で書き換える条件があるが、いずれにも該当しない low coverage ケース
# (例: 通常戦 + data_quality=low) では本見出しが残るため、sanitize で確実
# に置換する。
_LOW_QUALITY_TEXT_REPLACEMENTS = {
    # 長い表現を先に (順序重要: 「本線候補」を含む文字列を「本線」より前に)
    "オッズ確認後の本線候補": "オッズ確認後の上位候補",
    "オッズ確認後の本線": "オッズ確認後の上位",
    # category 表記 (gami_memo 内の「(本線)」「(押さえ)」)
    "(本線)": "(暫定候補)",
    "(押さえ)": "(押さえ暫定)",
    # value_label の弱体化 (表示時のみ、Bet オブジェクトは触らない)
    # _format_bet は odds あり時 "(N.N倍 / value_label)"、odds なし時
    # "(value_label)" を出すため、両方のパターンをカバー
    "/ 本線向き": "/ 暫定候補",
    "/ 妙味あり": "/ 妙味あり (再確認)",
    "/ 穴として少額": "/ 参考穴候補",
    # odds なし + value_label の (本線向き) 等 (codex review 反映)
    "(本線向き)": "(暫定候補)",
    "(妙味あり)": "(妙味あり (再確認))",
    "(穴として少額)": "(参考穴候補)",
    # 「本線」単独 (gami_memo の自然文中など) は「暫定候補」へ
    # ※ 大見出し「## 6. 本線」「### 出力整合性」等はパス
}


# 静岡4R #378 後続レビュー反映 (2026-05-24, a122ae1 後続):
# venue_trend.long_term / venue_trend.today 用のサニタイズマップ。
# これらは LLM 出力ではなく RaceInput から直接 markdown_renderer へ渡るため、
# sanitize_prediction_text / sanitize_prediction の経路をすり抜ける。
# ガールズ・新人戦のとき、ライン前提の用語を非ライン語に置換する。
#
# 順序重要 (codex review 反映): 長い/具体的な表現を先に置換する。
# 単独 `番手` を `追走` に置き換える行を最後に置く。これより前に
# `4番手` 等の数字付き表現を消費しないと、`4番手` → `4追走` のような
# 誤置換が発生する。
_VENUE_TREND_NON_LINE_REPLACEMENTS = {
    # 1) 最長一致のライン表現を先に消費
    "ライン3番手": "中位",
    "4番手評価": "4位評価",
    "5番手評価": "5位評価",
    "本命自力": "上位評価選手",
    "本命ライン": "上位評価",
    "本命頭": "上位評価の頭",
    "別線番手": "追走型",
    "番手頭": "追走型の浮上",
    "番手差し": "差し",
    # 2) 数字付き「N番手」を単独「番手」より前に
    "3番手": "中位",
    "4番手": "4位評価",
    "5番手": "5位評価",
    # 3) 単独の番手 / ライン を最後に
    "番手": "追走",
    "ライン": "位置取り",
}


def sanitize_venue_trend_text(
    text: str,
    *,
    is_girls: bool = False,
    is_rookie: bool = False,
) -> str:
    """venue_trend.long_term / today 用のサニタイズ。

    ガールズ/新人戦のとき、ライン前提の用語を非ライン語に置換する。
    通常ライン戦 (is_girls=False かつ is_rookie=False) では原文を維持。

    既存の `sanitize_prediction_text` とマップが微妙に異なるのは:
    - venue_trend は事実記述 (「番手差しが連発」「ライン3番手の伸び」等)
      なので、ガールズ/新人戦の表現に統一する必要がある
    - ガールズ用の "対抗頭" / "並び" は LLM 出力向けで、事実記述には
      合わないため、ROOKIE 寄りの「追走型の浮上」「位置取り」を選択
    """
    if not text:
        return text
    if not (is_girls or is_rookie):
        return text
    out = text
    for old, new in _VENUE_TREND_NON_LINE_REPLACEMENTS.items():
        out = out.replace(old, new)
    return out


def sanitize_low_quality_text(text: str) -> str:
    """low coverage / low data_quality 状況で文言を弱体化する。

    平塚10R 後続レビュー反映 (2026-05-24):
    - gami_memo の「(本線)」を「(暫定候補)」に
    - value_label 表示「/ 本線向き」を「/ 暫定候補」に
    - 「## 6. 本線」のような大見出しはそのまま (置換対象は前後文脈が
      明確な表現に限定)
    """
    if not text:
        return text
    out = text
    for old, new in _LOW_QUALITY_TEXT_REPLACEMENTS.items():
        out = out.replace(old, new)
    return out


def sanitize_prediction_text(
    text: str,
    *,
    is_girls: bool = False,
    is_rookie: bool = False,
) -> str:
    """LLM出力から競馬用語を競輪用語に置換する（要件6）+ 反省文言補正（要件5）。

    is_girls=True ならガールズ用語サニタイズ (要件1,2) も適用。
    is_rookie=True なら新人戦用語サニタイズ (2026-05-24) も適用。
    is_girls と is_rookie が両方 True の場合はガールズを優先 (排他的な状況は
    実装上想定しないが、ガールズの方が既存実装で安定しているため)。
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
    elif is_rookie:
        for old, new in _ROOKIE_TERM_REPLACEMENTS.items():
            out = out.replace(old, new)
    return out


def sanitize_prediction(
    prediction: Prediction,
    *,
    is_rookie: bool = False,
) -> None:
    """Prediction オブジェクトの文字列フィールドとフィールド値を破壊的にサニタイズ。

    対応:
        - 文字列フィールドの「穴馬」→「穴目」等を置換
        - market_odds=None の買い目の gami_risk を 0.0 に強制 (要件3)
        - ガールズ時の「番手」「ライン」等を「追走」「並び」等に自動置換 (要件1,2)
        - 新人戦時 (is_rookie=True) も同様の置換を適用 (2026-05-24)

    Args:
        prediction: サニタイズ対象 (破壊的に書き換える)
        is_rookie: 新人戦時 True。Prediction には is_rookie 属性が無いため
                   外部から RaceInput.race.resolved_is_rookie() を渡す必要がある。
                   既存呼び出し (引数なし) は False で互換性維持。
    """
    is_girls = bool(prediction.is_girls)
    # 文字列フィールドを総ざらいでサニタイズ
    # (codex review 反映: summary/venue_trend_text/weather_text/lines_text も
    # render_prediction で出力されるため、ガールズ/新人戦用語が混入してはいけない)
    string_fields = (
        "final_conclusion", "gami_memo",
        "summary", "venue_trend_text", "weather_text", "lines_text",
    )
    for field in string_fields:
        text = getattr(prediction, field, None)
        if text:
            setattr(
                prediction, field,
                sanitize_prediction_text(
                    text, is_girls=is_girls, is_rookie=is_rookie,
                ),
            )
    if prediction.reflection_points:
        prediction.reflection_points = [
            sanitize_prediction_text(
                pt, is_girls=is_girls, is_rookie=is_rookie,
            )
            for pt in prediction.reflection_points
        ]
    for bucket in (prediction.honsen, prediction.osae,
                   prediction.ana, prediction.ooana):
        for b in bucket:
            if b.reason:
                b.reason = sanitize_prediction_text(
                    b.reason, is_girls=is_girls, is_rookie=is_rookie,
                )
            # 要件3: market_odds=None の場合は gami_risk を 0 にする
            if b.market_odds is None and b.gami_risk > 0:
                b.gami_risk = 0.0
