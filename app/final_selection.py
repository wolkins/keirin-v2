"""最終出力前に必ず通す deterministic な final_selection レイヤー。

目的: candidate_bets で出た honsen / osae / ana / ooana を、オッズ取得状況・
value_label・gami_risk・市場偏り・レース種別を見て決定論的に再分類する。

LLM の出力は装飾文として残し、最終的な「買う/買わない」の判断は本モジュールが
持つルールに従う。これにより、レースごとの個別修正ではなく、共通ルールで
最終出力の整合性を保証する。

ルール (2026-05-24, 武雄2R 以降):
  1. best_bets に market_odds=None だけを並べない
  2. best_bets に value_label=見送り寄り を入れない
  3. best_bets に gami_risk>=0.8 を入れない
  4. honsen が全 market_odds=None なら、odds取得済み+value_label良好を
     must_cover_bets に昇格
  5. market_bias がある場合、その偏りに合うオッズ取得済み買い目を最低1点残す
  6. market_odds < 5 は cheap_popular_bets に分離
  7. market_odds >= 20 はガミ注意 (cheap_popular_bets) にしない
  8. market_odds=None はガミ注意 (cheap_popular_bets) にしない
  9. ガールズ/新人戦は買い目を広げすぎない (best_bets 1点上限)
  10. final_conclusion は final_selection の内容だけから生成する (render側で実装)
  11. LLM は final_selection の分類を変更してはいけない (render側で実装)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import BetRecommendation, Prediction, RaceInput


# ---------------------------------------------------------------------------
# 定数 (ルール由来の閾値)
# ---------------------------------------------------------------------------

CHEAP_ODDS_THRESHOLD = 5.0           # ルール6: < 5 は cheap_popular_bets
GAMI_OFF_HIGH_ODDS_THRESHOLD = 20.0  # ルール7: >= 20 はガミ注意にしない
GAMI_RISK_BEST_THRESHOLD = 0.8       # ルール3: >= 0.8 は best_bets 除外
BEST_BETS_MAX_DEFAULT = 2            # 通常戦の best_bets 上限
BEST_BETS_MAX_RESTRICTED = 1         # ガールズ/新人戦の best_bets 上限 (ルール9)
MUST_COVER_BETS_MAX = 2              # must_cover_bets の上限
SMALL_LONGSHOTS_MAX = 1              # small_longshots の上限
WATCH_ONLY_MAX = 2                   # watch_only_bets の上限
DISPLAY_HONSEN_MAX = 3               # 本線セクション最大 (武雄2R 要件3)
DISPLAY_OSAE_MAX = 4
LOW_ODDS_WARNING_THRESHOLD = 10.0    # 警告: 実購入候補4点以上+この値未満
LOW_ODDS_WARNING_MIN_COUNT = 4


# ---------------------------------------------------------------------------
# FinalSelection データ構造
# ---------------------------------------------------------------------------


@dataclass
class FinalSelection:
    """deterministic に決定された最終出力ブロック。

    LLM が触る前/後に関わらず、本データ構造が最終出力の基準となる。
    """

    # 表示ブロック (## 6-9 セクション相当)
    display_honsen: list[BetRecommendation] = field(default_factory=list)
    display_osae: list[BetRecommendation] = field(default_factory=list)
    display_ana: list[BetRecommendation] = field(default_factory=list)
    display_ooana: list[BetRecommendation] = field(default_factory=list)

    # 実購入判断ブロック (### 一番買いたい/押さえとして必要 等)
    best_bets: list[BetRecommendation] = field(default_factory=list)
    must_cover_bets: list[BetRecommendation] = field(default_factory=list)
    small_longshots: list[BetRecommendation] = field(default_factory=list)
    cheap_popular_bets: list[BetRecommendation] = field(default_factory=list)
    watch_only_bets: list[BetRecommendation] = field(default_factory=list)
    # 平塚7R 後続レビュー反映 (2026-05-24, codex P2): display_honsen を
    # 3点契約のまま保つため、見送り寄り honsen は別バケットで管理
    miokuri_bets: list[BetRecommendation] = field(default_factory=list)

    # ⚠️ 表示の警告 (低配当注意 / オッズ未取得 等)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 内部ヘルパ
# ---------------------------------------------------------------------------


def _bet_score(b: BetRecommendation) -> float:
    """best_bets 選定用のスコア。odds取得済み+value_label良好を最優先。"""
    s = 0.0
    if b.market_odds is not None:
        s += 60.0
        if b.value_label in ("妙味あり", "本線向き"):
            s += 40.0
        elif b.value_label == "堅いが安い":
            s -= 5.0
    return s


HIGH_ODDS_NORMALIZE_THRESHOLD = 20.0  # Phase 14 後続: これ以上は gami 扱いしない


def _is_high_odds_normalized(b: BetRecommendation) -> bool:
    """Phase 14 後続 (2026-05-25): market_odds>=20 なら高オッズと判定し、
    上流の gami_warning / low_odds タグを「ガミ扱いの根拠」としては無視する.

    用途:
    - _qualifies_best と _is_cheap_popular で gami タグ判定を skip
    - タグ自体は情報として保持 (warning 出力用)
    """
    return (
        b.market_odds is not None
        and b.market_odds >= HIGH_ODDS_NORMALIZE_THRESHOLD
    )


def _is_cheap_popular(b: BetRecommendation) -> bool:
    """ルール6: market_odds < 5 は cheap_popular_bets に分離.

    Phase 14 (2026-05-25): source_rules ベースの判定にも対応 (除外判定用)。
    source_rules に gami_warning / low_odds があれば cheap 扱い。
    market_odds<5 だが source_rules 未付与のケースもカバーする。

    Phase 14 後続: market_odds>=20 のときは、上流で誤って付いた
    gami_warning/low_odds タグを無視する (高オッズは原則 gami 扱いしない)。

    注意 (codex P1 反映): 本判定は `_qualifies_best` で best_bets から除外
    するために使う。`cheap_pool` への **移動** は `_should_move_to_cheap_pool`
    の別判定 (market_odds<5 明示条件) を使う。
    """
    from .decision import is_gami_source
    # Phase 14 後続: 高オッズなら gami タグを信頼しない
    if _is_high_odds_normalized(b):
        return False
    if is_gami_source(b.source_rules):
        return True
    return b.market_odds is not None and b.market_odds < CHEAP_ODDS_THRESHOLD


def _should_move_to_cheap_pool(b: BetRecommendation) -> bool:
    """cheap_popular_bets に移動する候補の判定 (Phase 14 codex P1 反映).

    `_is_cheap_popular` が source_rules を見るようになったため、
    cheap_pool への移動判定は明示的に `market_odds<5` のみに絞る。
    odds<15+gami>=0.6 等の中オッズ高ガミリスク候補は source_rules で
    `gami_warning` タグが付くだけで、cheap_pool には移動しない
    (OutputPlan の gami filter で plan.gami_warning bucket に移動)。
    """
    return b.market_odds is not None and b.market_odds < CHEAP_ODDS_THRESHOLD


def _ensure_gami_source_rules(b: BetRecommendation) -> None:
    """Phase 14 (2026-05-25): cheap_popular 判定された候補に
    source_rules を補完付与する.

    final_selection 側で cheap 扱いになった候補に source_rules がない場合、
    OutputPlan 側の gami filter (Phase 13) と整合させるためタグを追加する。

    付与ルール:
    - market_odds<5 → low_odds + gami_warning + odds_available
    - market_odds<15 かつ gami_risk>=0.6 → gami_warning
    - market_odds<20 かつ gami_risk>=0.8 → gami_warning
    - source_rules に既にタグがあれば重複追加しない
    """
    if b.market_odds is not None:
        if b.market_odds < 5.0:
            for tag in ("low_odds", "gami_warning", "odds_available"):
                if tag not in b.source_rules:
                    b.source_rules.append(tag)
        elif b.market_odds < 15.0 and b.gami_risk >= 0.6:
            if "gami_warning" not in b.source_rules:
                b.source_rules.append("gami_warning")
            if "odds_available" not in b.source_rules:
                b.source_rules.append("odds_available")
        elif b.market_odds < 20.0 and b.gami_risk >= 0.8:
            if "gami_warning" not in b.source_rules:
                b.source_rules.append("gami_warning")
            if "odds_available" not in b.source_rules:
                b.source_rules.append("odds_available")


def _qualifies_best(b: BetRecommendation) -> bool:
    """best_bets に入る資格判定。ルール2, 3, 6 + 静岡4R-3 をチェック.

    Phase 14: source_rules に gami_warning / low_odds があれば一律除外
    (Phase 13 の OutputPlan filter と同じ判定)。

    Phase 14 後続 (2026-05-25): market_odds>=20 の高オッズ買い目は、
    上流で誤って付いた gami_warning / low_odds タグを無視する (高配当を
    保護する)。
    """
    from .decision import is_gami_source
    if b.value_label == "見送り寄り":  # ルール2
        return False
    if b.value_label == "穴として少額":  # 静岡4R-3 (2026-05-24)
        return False
    if b.gami_risk >= GAMI_RISK_BEST_THRESHOLD:  # ルール3
        return False
    if _is_cheap_popular(b):  # ルール6 (Phase 14 で source_rules も見る)
        return False
    # Phase 14 後続: 高オッズなら gami タグを信頼しない
    if not _is_high_odds_normalized(b):
        if is_gami_source(b.source_rules):  # Phase 14: source_rules ベース
            return False
    return True


def _is_focused_head(b: BetRecommendation, head: int) -> bool:
    """買い目の頭が focused_head に一致するか。"""
    if not b.combination or "-" not in b.combination:
        return False
    parts = b.combination.split("-")
    if len(parts) != 3:
        return False
    try:
        return int(parts[0]) == head
    except (ValueError, TypeError):
        return False


def _is_main_line_direct(b: BetRecommendation, lines) -> bool:
    """買い目が「同ライン直行 (頭+番手+3番手)」か判定 (要件1)。

    展開上必須の本命ラインかどうかを確認するため、line構造を見る。
    odds=None でも must_cover_bets に残すべき買い目を抽出する用途。
    """
    from .scoring import build_line_position_map
    if not b.combination or "-" not in b.combination:
        return False
    parts = b.combination.split("-")
    if len(parts) != 3:
        return False
    try:
        a, b2, c = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, TypeError):
        return False
    line_map = build_line_position_map(lines or [])
    pa = line_map.get(a)
    pb = line_map.get(b2)
    pc = line_map.get(c)
    if not (pa and pb and pc):
        return False
    return (
        pa.line_name == pb.line_name == pc.line_name
        and pa.is_head and pb.is_bantan and pc.is_third
    )


def _dedupe_by_combination(bets: list[BetRecommendation]) -> list[BetRecommendation]:
    """combination ベースで重複除去 (順序保持)。"""
    seen: set[str] = set()
    out: list[BetRecommendation] = []
    for b in bets:
        if b.combination in seen:
            continue
        seen.add(b.combination)
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# メイン関数
# ---------------------------------------------------------------------------


def build_final_selection(
    prediction: Prediction,
    input_data: Optional[RaceInput] = None,
) -> FinalSelection:
    """prediction から FinalSelection を deterministic に構築する。

    Args:
        prediction: candidate_bets を含む Prediction (honsen/osae/ana/ooana)
        input_data: input_data があれば市場偏り検出やレース種別判定に使う

    Returns:
        FinalSelection: 全表示ブロックを再分類した構造
    """
    sel = FinalSelection()

    # Phase 14 codex P2 反映 (2026-05-25): _ensure_gami_source_rules が
    # BetRecommendation.source_rules を破壊的に書き換えるため、元の
    # Prediction を保護する目的で deep copy する。
    # render_prediction_v2 の経路では p_for_plan が既に deep copy 済みで
    # 二重 copy となるが、build_final_selection 単体や旧 render_prediction
    # 経路でも安全になる。
    honsen = [b.model_copy(deep=True) for b in prediction.honsen]
    osae = [b.model_copy(deep=True) for b in prediction.osae]
    ana = [b.model_copy(deep=True) for b in prediction.ana]
    ooana = [b.model_copy(deep=True) for b in prediction.ooana]

    # ---- レース種別判定 (ルール9) ----
    is_girls = False
    is_rookie = False
    if input_data is not None:
        is_girls = input_data.race.resolved_is_girls()
        is_rookie = input_data.race.resolved_is_rookie()
    elif prediction.is_girls:
        is_girls = True
    best_bets_max = (
        BEST_BETS_MAX_RESTRICTED if (is_girls or is_rookie)
        else BEST_BETS_MAX_DEFAULT
    )

    # 武雄12R 対応 (2026-05-24): オッズ取得率が低い (<40%) ときは
    # best_bets を 1点に制限 (購入暫定候補扱い)
    # codex review 反映: 全体 coverage は穴・大穴の未取得で誤発動するため、
    # 「実購入候補ベース」 = honsen+osae の coverage を使う。
    # 平塚4R 対応 (2026-05-24): data_quality=low や honsen_odds_coverage<0.5
    # でも文言弱体化を適用する
    low_coverage = False
    if input_data is not None:
        from .output_validation import (
            assess_data_quality, assess_race_complexity, compute_odds_coverage,
        )
        coverage = compute_odds_coverage(prediction)
        # 実購入候補 (honsen + osae) の coverage で判定
        purchase_total = len(honsen) + len(osae)
        purchase_with_odds = sum(
            1 for b in (honsen + osae) if b.market_odds is not None
        )
        purchase_coverage = (
            purchase_with_odds / purchase_total if purchase_total else 0.0
        )
        if purchase_total > 0 and purchase_coverage < 0.4:
            low_coverage = True
            best_bets_max = min(best_bets_max, 1)
        if purchase_total > 0 and purchase_coverage < 0.25:
            # 超低カバレッジ: 実購入を最小限にする警告
            # (※「見送り寄り」は value_label と衝突するため使用しない)
            sel.warnings.append(
                f"実購入候補のオッズ取得率が極めて低い "
                f"({purchase_coverage:.0%}) — 購入を見送り推奨レベル。"
                f"実購入は最小限に抑えてください。"
            )
        elif purchase_total > 0 and purchase_coverage < 0.4:
            # fee60e4 後続レビュー反映: 「購入対象」を含む警告文を避ける
            # (Markdown 末尾フッタに warning が出るため、本文と矛盾しないように)
            sel.warnings.append(
                f"実購入候補のオッズ取得率が低い ({purchase_coverage:.0%}) — "
                f"「暫定候補」扱い、final_best は1点に制限。"
                f"オッズ再確認後に再判断してください。"
            )

        # 平塚4R 対応: honsen 専用オッズ取得率 < 50% で文言弱体化
        if coverage.honsen_total > 0 and coverage.honsen_coverage_ratio < 0.5:
            low_coverage = True
            sel.warnings.append(
                f"本線オッズ取得率が低い "
                f"({coverage.honsen_coverage_ratio:.0%}) — "
                f"「暫定候補」扱い、オッズ再確認後に再判断してください。"
            )

        # 平塚4R 対応: data_quality=low / very_low でも文言弱体化
        # (coverage が高くてもデータ自体が不足している場合)
        quality = assess_data_quality(input_data, coverage=coverage)
        if quality in ("low", "very_low"):
            low_coverage = True
            sel.warnings.append(
                f"データ品質が {quality} のため、最終出力は「暫定候補」扱い。"
                f"出走表・オッズ・直近結果を再確認してから判断してください。"
            )

        # race_complexity と purchase_coverage の組み合わせで追加警告
        complexity = assess_race_complexity(input_data)
        if (
            complexity == "very_high"
            and purchase_total > 0
            and purchase_coverage < 0.4
        ):
            sel.warnings.append(
                f"レース難度 very_high + 実購入候補オッズ取得率低 "
                f"({purchase_coverage:.0%}) — 購入見送り推奨。"
                f"トップ選手分散・読み筋複数のため、最終結論は様子見が安全。"
            )
        elif complexity in ("high", "very_high"):
            sel.warnings.append(
                f"レース難度 {complexity} — 読み筋分散、購入判断を慎重に。"
            )

    # ---- ルール6: cheap_popular_bets 分離 ----
    # Phase 14 (2026-05-25): すべての honsen/osae 候補に source_rules タグ
    # を補完付与 (odds<5 / odds<15+gami>=0.6 / odds<20+gami>=0.8 のいずれ
    # かなら gami_warning タグ等を追加)。
    # cheap_popular_bets への移動は odds<5 のみ (既存テスト互換)。
    # それ以外の gami_warning タグ付き候補は _qualifies_best で除外され、
    # OutputPlan の gami filter で plan.gami_warning に移される。
    from .decision import is_gami_source
    for b in (honsen + osae):
        _ensure_gami_source_rules(b)

    # Phase 14 後続 (2026-05-25): 高オッズ (odds>=20) なのに上流で
    # gami_warning / low_odds タグが付いている候補を検出して warning を
    # 記録する。タグ自体は情報として残し、_qualifies_best / _is_cheap_popular
    # 側で「タグを信頼しない」処理を入れている。
    high_odds_with_gami = [
        b for b in (honsen + osae)
        if _is_high_odds_normalized(b) and is_gami_source(b.source_rules)
    ]
    if high_odds_with_gami:
        combos = ", ".join(b.combination for b in high_odds_with_gami[:3])
        sel.warnings.append(
            f"高オッズ ({HIGH_ODDS_NORMALIZE_THRESHOLD:.0f}倍以上) なのに "
            f"gami_warning/low_odds タグが付いた候補: {combos} "
            "(タグは情報として残すが、best からは除外しない)"
        )

    # codex P1 反映: cheap_pool への移動は明示的に market_odds<5 のみ
    # (_is_cheap_popular は source_rules を見るので cheap_pool 移動には使わない)
    cheap_pool = _dedupe_by_combination([
        b for b in (honsen + osae) if _should_move_to_cheap_pool(b)
    ])
    sel.cheap_popular_bets = cheap_pool[:5]
    cheap_combos = {b.combination for b in sel.cheap_popular_bets}

    # ---- best_bets 構築 (ルール1, 2, 3) ----
    qualifying = [
        b for b in (honsen + osae)
        if _qualifies_best(b)
    ]
    qualifying.sort(key=lambda b: -_bet_score(b))
    qualifying_dedup = _dedupe_by_combination(qualifying)

    # ルール1: best_bets に market_odds=None を入れない (厳密適用)
    # codex review 反映: odds取得済みが1点も無ければ best_bets は空。
    # must_cover/odds_missing_honsen で代替を提示する設計とする。
    qualifying_with_odds = [b for b in qualifying_dedup if b.market_odds is not None]
    sel.best_bets = qualifying_with_odds[:best_bets_max]

    best_combos = {b.combination for b in sel.best_bets}

    # ---- must_cover_bets 構築 (ルール4, 5) ----
    must_cover_pool: list[BetRecommendation] = []
    must_cover_combos: set[str] = set()

    def _push_must_cover(b: BetRecommendation) -> bool:
        if b.combination in best_combos or b.combination in must_cover_combos:
            return False
        if b.combination in cheap_combos:
            return False
        if not _qualifies_best(b):  # 見送り寄り/高gami は除外
            return False
        must_cover_pool.append(b)
        must_cover_combos.add(b.combination)
        return True

    # ルール4: honsen 全 odds=None なら、odds取得済み+value_label良好を昇格
    honsen_all_no_odds = (
        bool(honsen) and all(b.market_odds is None for b in honsen)
    )
    if honsen_all_no_odds:
        candidates = sorted(
            [b for b in osae if b.market_odds is not None and _qualifies_best(b)],
            key=lambda b: -_bet_score(b),
        )
        for b in candidates:
            _push_must_cover(b)
            if len(must_cover_pool) >= MUST_COVER_BETS_MAX:
                break

    # best_bets で取りこぼした qualifying odds取得済みも must_cover に積む
    # (これにより「best 2点 + osae 妙味 2点 = 計4点」のような実購入候補が
    # final_selection に正しく現れ、低配当注意も発動する)
    if len(must_cover_pool) < MUST_COVER_BETS_MAX:
        leftover = [
            b for b in qualifying_with_odds
            if b.combination not in best_combos
        ]
        for b in leftover:
            _push_must_cover(b)
            if len(must_cover_pool) >= MUST_COVER_BETS_MAX:
                break

    # 要件1 (2026-05-24): 展開上必須の本命ライン (同ライン直行) を
    # must_cover_bets に保持。odds=None でも、line構造的に重要な買い目は
    # 取り逃がさない。
    if input_data is not None and len(must_cover_pool) < MUST_COVER_BETS_MAX:
        lines_for_judge = input_data.lines or []
        main_line_direct_pool = [
            b for b in honsen
            if b.combination not in best_combos
            and b.combination not in must_cover_combos
            and _is_main_line_direct(b, lines_for_judge)
            and _qualifies_best(b)  # 見送り寄り/gami>=0.8/odds<5 は除外
        ]
        for b in main_line_direct_pool:
            _push_must_cover(b)
            if len(must_cover_pool) >= MUST_COVER_BETS_MAX:
                break

    # ルール5: market_bias がある場合、偏りに合う odds取得済み買い目を最低1点
    if input_data is not None:
        from .output_validation import detect_market_bias
        bias = detect_market_bias(input_data)
        if bias.has_head_focus and bias.focused_head is not None:
            head = bias.focused_head
            all_selected = sel.best_bets + must_cover_pool
            has_bias_with_odds = any(
                _is_focused_head(b, head) and b.market_odds is not None
                for b in all_selected
            )
            if not has_bias_with_odds:
                # honsen+osae から head 始まり odds取得済みを探す
                # cheap_popular でも構わない (要件は「最低1点残す」のみ)
                bias_candidates = sorted(
                    [
                        b for b in (honsen + osae)
                        if _is_focused_head(b, head)
                        and b.market_odds is not None
                    ],
                    key=lambda b: (
                        b.market_odds
                        if b.market_odds is not None else 999.0
                    ),
                )
                for b in bias_candidates:
                    if (
                        b.combination not in best_combos
                        and b.combination not in must_cover_combos
                    ):
                        # cheap_popular に既に含まれていれば best/must_cover に
                        # 重複登録しない (cheap_popular_bets が「残った1点」を担う)
                        if b.combination in cheap_combos:
                            break
                        must_cover_pool.append(b)
                        must_cover_combos.add(b.combination)
                        break

    sel.must_cover_bets = must_cover_pool[:MUST_COVER_BETS_MAX]

    # 平塚4R 後続レビュー反映 (2026-05-24): HeadBias のみ (AxisBias 無し) で
    # final_best + final_osae に同一 (head, second) 軸が複数含まれないよう制御。
    # AxisBias がある場合のみ、その軸の複数昇格を許可する。
    if input_data is not None:
        from .output_validation import detect_market_bias
        bias_for_axis = detect_market_bias(input_data)
        if (
            bias_for_axis.has_head_focus
            and not bias_for_axis.has_axis_focus
        ):
            # HeadBias のみ → 同一 (head, second) 軸の重複を除外
            allowed_axes: set[tuple[int, int]] = set()
            def _strip_dup_axis(bets: list) -> list:
                out: list = []
                for b in bets:
                    if not b.combination or "-" not in b.combination:
                        out.append(b)
                        continue
                    parts = b.combination.split("-")
                    if len(parts) < 2:
                        out.append(b)
                        continue
                    try:
                        ax = (int(parts[0]), int(parts[1]))
                    except (ValueError, TypeError):
                        out.append(b)
                        continue
                    if ax in allowed_axes:
                        # この軸は既に 1 点使用済み → 除外
                        continue
                    allowed_axes.add(ax)
                    out.append(b)
                return out
            sel.best_bets = _strip_dup_axis(sel.best_bets)
            sel.must_cover_bets = _strip_dup_axis(sel.must_cover_bets)

    # ---- small_longshots ----
    # value_label=妙味あり/穴として少額 を最大1点
    # codex review 反映: ana/ooana だけでなく honsen/osae の「穴として少額」も
    # 拾う (best_bets から除外されたため、表示から消えるのを防ぐ)
    longshot_source = ana + ooana + [
        b for b in (honsen + osae)
        if b.value_label == "穴として少額"
    ]
    longshot_pool = [
        b for b in longshot_source
        if b.value_label in ("妙味あり", "穴として少額")
    ]
    longshot_pool = _dedupe_by_combination(longshot_pool)
    sel.small_longshots = longshot_pool[:SMALL_LONGSHOTS_MAX]

    # ---- watch_only_bets (参考表示) ----
    # odds取得済み + 5 <= odds < 10 + value_label が悪くない + 上記未選択
    selected_combos = (
        best_combos
        | must_cover_combos
        | cheap_combos
        | {b.combination for b in sel.small_longshots}
    )
    watch_pool = [
        b for b in (honsen + osae)
        if b.combination not in selected_combos
        and b.market_odds is not None
        and 5.0 <= b.market_odds < LOW_ODDS_WARNING_THRESHOLD
        and b.value_label != "見送り寄り"
        and b.gami_risk < GAMI_RISK_BEST_THRESHOLD
    ]
    watch_pool = _dedupe_by_combination(watch_pool)
    sel.watch_only_bets = watch_pool[:WATCH_ONLY_MAX]

    # 平塚7R 後続レビュー反映 (2026-05-24):
    # value_label='見送り寄り' で odds 取得済みの honsen/osae 候補も
    # 参考表示として display_honsen に残す。
    # (本線実購入候補からは _qualifies_best で除外されるが、表示自体は
    # 「見送り寄りの参考候補」サブセクションで行うため display_honsen に積む)
    miokuri_pool: list[BetRecommendation] = []
    for b in (honsen + osae):
        if (
            b.value_label == "見送り寄り"
            and b.market_odds is not None
            and b.combination not in selected_combos
        ):
            miokuri_pool.append(b)
    miokuri_pool = _dedupe_by_combination(miokuri_pool)
    sel.miokuri_bets = miokuri_pool[:2]  # 最大2点

    # ---- display_honsen / display_osae / display_ana / display_ooana ----
    # display_honsen の構築順序 (codex review 反映):
    #   1. best_bets (odds取得済み妙味)
    #   2. 本命ライン直行 odds=None (要件1: 展開上必須)
    #   3. must_cover_bets 残り (leftover odds取得済み)
    #   4. その他の odds=None 本線候補 (オッズ確認後候補)
    # → must_cover_bets に積んでも display_honsen=3点で切られて表示から落ちる
    #   問題を防ぐ。要件1「本命ライン直行を保持」は display 側で優先確保する。
    lines_for_display = (
        input_data.lines if input_data is not None else []
    ) or []
    display_honsen_pool: list[BetRecommendation] = list(sel.best_bets)
    already = {b.combination for b in display_honsen_pool}

    # 2. 本命ライン直行 odds=None を優先補充
    main_line_direct_no_odds = [
        b for b in honsen
        if b.combination not in already
        and b.market_odds is None
        and _qualifies_best(b)
        and b.combination not in cheap_combos
        and _is_main_line_direct(b, lines_for_display)
    ]
    for b in main_line_direct_no_odds:
        if len(display_honsen_pool) >= DISPLAY_HONSEN_MAX:
            break
        display_honsen_pool.append(b)
        already.add(b.combination)

    # 3. must_cover_bets の残り
    for b in sel.must_cover_bets:
        if len(display_honsen_pool) >= DISPLAY_HONSEN_MAX:
            break
        if b.combination not in already:
            display_honsen_pool.append(b)
            already.add(b.combination)

    # 4. その他の odds=None 本線候補
    if len(display_honsen_pool) < DISPLAY_HONSEN_MAX:
        other_no_odds = [
            b for b in honsen
            if b.combination not in already
            and b.market_odds is None
            and _qualifies_best(b)
            and b.combination not in cheap_combos
        ]
        for b in other_no_odds:
            if len(display_honsen_pool) >= DISPLAY_HONSEN_MAX:
                break
            display_honsen_pool.append(b)
            already.add(b.combination)

    sel.display_honsen = display_honsen_pool[:DISPLAY_HONSEN_MAX]

    # 平塚7R 後続レビュー反映 (2026-05-24, codex P2):
    # 見送り寄りは別バケット (OutputPlan.honsen_miokuri) に格納するため、
    # display_honsen には追加しない (DISPLAY_HONSEN_MAX=3 契約維持)。
    # from_final_selection で display_honsen から value_label='見送り寄り' を
    # 分離して OutputPlan.honsen_miokuri に振り分ける。

    # 「見送り寄り」を must_cover_bets から除外 (codex P2 反映: market bias
    # 昇格で「見送り寄り」が must_cover に入ると「押さえとして必要」と
    # 強い文言で表示されてしまう)
    sel.must_cover_bets = [
        b for b in sel.must_cover_bets if b.value_label != "見送り寄り"
    ]

    # display_osae: osae から「best_bets / must_cover_bets / cheap_popular /
    # display_honsen」を全て除外した残り (最大4点)
    osae_selected = (
        best_combos
        | must_cover_combos
        | cheap_combos
        | {b.combination for b in sel.display_honsen}
    )
    sel.display_osae = [
        b for b in osae
        if b.combination not in osae_selected
    ][:DISPLAY_OSAE_MAX]

    # display_ana / display_ooana: そのまま
    sel.display_ana = list(ana)
    sel.display_ooana = list(ooana)

    # ---- warnings ----
    # 警告1: best_bets が空 (要件2): 「オッズ取得済みで買える候補なし。
    # オッズ確認後に判断」を明示。must_cover が odds取得済みなら代替提示があるが、
    # それでも best_bets 空は購入判断の最重要警告として残す。
    if not sel.best_bets:
        sel.warnings.append(
            "オッズ取得済みで買える候補なし — オッズ確認後に判断してください。"
        )

    # 警告2: 低配当注意 (実購入候補 >=4 点 + market_odds<10 含む)
    purchase_bets = sel.best_bets + sel.must_cover_bets
    purchase_dedup = _dedupe_by_combination(purchase_bets)
    if len(purchase_dedup) >= LOW_ODDS_WARNING_MIN_COUNT:
        low_odds = [
            b for b in purchase_dedup
            if b.market_odds is not None
            and b.market_odds < LOW_ODDS_WARNING_THRESHOLD
        ]
        if low_odds:
            combos = ", ".join(
                f"{b.combination}({b.market_odds:.1f}倍)"
                for b in low_odds[:3]
            )
            sel.warnings.append(
                f"低配当注意: 実購入候補 {len(purchase_dedup)}点中、"
                f"{combos} は{LOW_ODDS_WARNING_THRESHOLD:.0f}倍未満 → 点数を絞る"
            )

    # 警告3: 市場偏りあり + best_bets/must_cover に集中頭の odds取得済み無し
    if input_data is not None:
        from .output_validation import detect_market_bias
        bias = detect_market_bias(input_data)
        if bias.has_head_focus and bias.focused_head is not None:
            head = bias.focused_head
            has_bias_with_odds = any(
                _is_focused_head(b, head) and b.market_odds is not None
                for b in (sel.best_bets + sel.must_cover_bets)
            )
            # cheap_popular に偏り頭が含まれていれば「分かっているが厚く買わない」
            has_bias_in_cheap = any(
                _is_focused_head(b, head)
                for b in sel.cheap_popular_bets
            )
            if not has_bias_with_odds and not has_bias_in_cheap:
                sel.warnings.append(
                    f"市場偏り({head}番頭集中) に合うオッズ取得済み買い目が"
                    f"final_selection に無いため、購入前に再確認してください。"
                )

    return sel
