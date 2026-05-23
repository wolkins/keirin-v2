"""オッズ妙味分析。

各買い目について以下を評価する:
- predicted_strength: 予想スコアから組み合わせの強さを算出
- market_odds: 取得済みオッズから市場オッズを引く
- market_rank: 同一 bet_type 内の人気順位
- value_score / value_label: strength × odds マトリクスでラベル決定

ルールベースで説明可能なロジックに留め、LLMには丸投げしない。
これは予想支援であり、的中保証ではない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    BetRecommendation,
    OddsEntry,
    Prediction,
    RaceInput,
    RiderScore,
)


# 価値ラベルと value_score のマッピング（ルールベース、説明可能）
VALUE_LABEL_SCORES: dict[str, float] = {
    "堅いが安い": -0.3,
    "本線向き": 0.3,
    "妙味あり": 0.7,
    "穴として少額": 0.2,
    "見送り寄り": -0.5,
    "オッズ未取得・要確認": 0.0,
}


# オッズ層: <5 / 5-15 / 15-50 / 50+ を 0-3 の整数で表現
def _odds_tier(odds: Optional[float]) -> Optional[int]:
    if odds is None:
        return None
    if odds < 5.0:
        return 0
    if odds < 15.0:
        return 1
    if odds < 50.0:
        return 2
    return 3


# strength tier: 0(低) / 1(中) / 2(高) は全bet内の percentile で判定する。
# 同様にラベル決定マトリクス[strength_tier][odds_tier] = label
_LABEL_MATRIX: dict[tuple[int, int], str] = {
    (2, 0): "堅いが安い",
    (2, 1): "本線向き",
    (2, 2): "妙味あり",
    (2, 3): "妙味あり",
    (1, 0): "堅いが安い",
    (1, 1): "本線向き",
    (1, 2): "妙味あり",
    (1, 3): "穴として少額",
    (0, 0): "見送り寄り",
    (0, 1): "見送り寄り",
    (0, 2): "穴として少額",
    (0, 3): "穴として少額",
}


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------


@dataclass
class BetValueAnalysis:
    """1つの買い目に対する妙味評価。"""

    category: str
    bet_type: str
    combination: str
    predicted_strength: Optional[float] = None
    market_odds: Optional[float] = None
    market_rank: Optional[int] = None
    gami_risk: float = 0.0
    value_score: float = 0.0
    value_label: str = "オッズ未取得・要確認"


# ---------------------------------------------------------------------------
# strength 計算
# ---------------------------------------------------------------------------


_CARS_PART_RE = re.compile(r"[=]")


def _first_cars(combination: str, *, sep: str) -> list[int]:
    """combination 文字列から先頭車番を順に取り出す。

    BetRecommendation.combination は `5-1-3` や `5-1=6`（or 表記）が混在する
    可能性があるが、ここでは「先頭の整数」を順に取って評価する。
    """
    parts = combination.split(sep)
    cars: list[int] = []
    for p in parts:
        first = _CARS_PART_RE.split(p, 1)[0].strip()
        if first.isdigit():
            cars.append(int(first))
        else:
            return []
    return cars


def _scores_map(scores: list[RiderScore]) -> dict[int, RiderScore]:
    return {s.car_no: s for s in scores}


def compute_predicted_strength(
    bet: BetRecommendation, scores: list[RiderScore]
) -> Optional[float]:
    """買い目の予想強度を計算する。bet_type ごとに重み付けが異なる。"""
    sm = _scores_map(scores)
    if bet.bet_type == "3連単":
        cars = _first_cars(bet.combination, sep="-")
        if len(cars) != 3:
            return None
        s = [sm.get(c) for c in cars]
        if any(x is None for x in s):
            return None
        return s[0].win_score + s[1].second_score * 0.8 + s[2].third_score * 0.6  # type: ignore[union-attr]
    if bet.bet_type == "2車単":
        cars = _first_cars(bet.combination, sep="-")
        if len(cars) != 2:
            return None
        s = [sm.get(c) for c in cars]
        if any(x is None for x in s):
            return None
        return s[0].win_score + s[1].second_score * 0.8  # type: ignore[union-attr]
    if bet.bet_type == "3連複":
        cars = _first_cars(bet.combination, sep="=")
        if len(cars) != 3:
            return None
        s = [sm.get(c) for c in cars]
        if any(x is None for x in s):
            return None
        wins = [x.win_score for x in s]  # type: ignore[union-attr]
        seconds = [x.second_score for x in s]  # type: ignore[union-attr]
        return max(wins) + sum(seconds) / len(seconds) * 0.7
    return None


# ---------------------------------------------------------------------------
# 市場オッズ / 人気順位
# ---------------------------------------------------------------------------


def build_market_rank_map(
    odds_entries: list[OddsEntry] | list[dict],
) -> dict[tuple[str, str], tuple[float, int]]:
    """(bet_type, combination) → (odds, rank) を構築する。

    rank は **同一 bet_type 内** のオッズ昇順順位（人気順位）。
    """
    grouped: dict[str, list[tuple[str, float]]] = {}
    for entry in odds_entries:
        if isinstance(entry, dict):
            bt = entry.get("bet_type")
            combo = entry.get("combination")
            odds = entry.get("odds")
        else:
            bt = entry.bet_type
            combo = entry.combination
            odds = entry.odds
        if not bt or not combo or odds is None:
            continue
        try:
            o = float(odds)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(bt, []).append((combo, o))

    out: dict[tuple[str, str], tuple[float, int]] = {}
    for bt, items in grouped.items():
        items_sorted = sorted(items, key=lambda x: x[1])
        for rank, (combo, o) in enumerate(items_sorted, start=1):
            # 同一 combo が複数回ある場合は最良（安い）を採用
            key = (bt, combo)
            if key not in out:
                out[key] = (o, rank)
    return out


def _lookup_market(
    bet: BetRecommendation,
    rank_map: dict[tuple[str, str], tuple[float, int]],
) -> tuple[Optional[float], Optional[int]]:
    info = rank_map.get((bet.bet_type, bet.combination))
    if info is None:
        return None, None
    return info[0], info[1]


# ---------------------------------------------------------------------------
# strength tier 判定（全bet内 percentile）
# ---------------------------------------------------------------------------


def _strength_tier(
    strength: Optional[float], sorted_strengths: list[float]
) -> int:
    """0=低 / 1=中 / 2=高。sorted_strengths は昇順。"""
    if strength is None or not sorted_strengths:
        return 1  # 中央扱い
    n = len(sorted_strengths)
    # strength の順位（同値は最初の位置）
    rank = 0
    for i, v in enumerate(sorted_strengths):
        if strength <= v:
            rank = i
            break
        rank = i + 1
    # percentile
    pct = rank / max(n - 1, 1)
    if pct < 1 / 3:
        return 0
    if pct < 2 / 3:
        return 1
    return 2


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def _all_bets(prediction: Prediction) -> list[BetRecommendation]:
    return list(prediction.honsen) + list(prediction.osae) + list(prediction.ana) + list(prediction.ooana)


def annotate_prediction_with_value(
    prediction: Prediction,
    scores: list[RiderScore],
    odds: list[OddsEntry] | list[dict],
) -> None:
    """予想の各買い目に妙味分析結果を破壊的に書き込む。

    Args:
        prediction: 対象 Prediction（フィールドを直接更新）
        scores: 各車のスコア（強度計算用）
        odds: RaceInput.odds や fetch_odds の結果リスト
    """
    bets = _all_bets(prediction)
    if not bets:
        return

    # 強度を全bet分先に計算
    strengths: dict[int, Optional[float]] = {}
    for i, b in enumerate(bets):
        strengths[i] = compute_predicted_strength(b, scores)

    valid_strengths = sorted([v for v in strengths.values() if v is not None])

    rank_map = build_market_rank_map(odds)

    for i, b in enumerate(bets):
        strength = strengths[i]
        market_odds, market_rank = _lookup_market(b, rank_map)
        b.predicted_strength = strength
        b.market_odds = market_odds
        b.market_rank = market_rank

        if market_odds is None:
            b.value_label = "オッズ未取得・要確認"
            b.value_score = VALUE_LABEL_SCORES["オッズ未取得・要確認"]
            # 既存 gami_risk はそのまま温存
            continue

        s_tier = _strength_tier(strength, valid_strengths)
        o_tier = _odds_tier(market_odds)
        if o_tier is None:
            b.value_label = "オッズ未取得・要確認"
            b.value_score = 0.0
            continue
        label = _LABEL_MATRIX.get((s_tier, o_tier), "本線向き")
        b.value_label = label
        b.value_score = VALUE_LABEL_SCORES.get(label, 0.0)

        # ガミリスクの追加調整: 「堅いが安い」「見送り寄り」はガミリスクを底上げ
        if label == "堅いが安い":
            b.gami_risk = max(b.gami_risk, 0.6)
        elif label == "見送り寄り":
            b.gami_risk = max(b.gami_risk, 0.7)


def analyze_value(
    input_data: RaceInput,
    prediction: Prediction,
    scores: list[RiderScore],
) -> list[BetValueAnalysis]:
    """評価結果を BetValueAnalysis のリストとして返す（非破壊）。

    呼び出し前後で prediction は変更しない（テスト用途で便利）。
    """
    odds = list(input_data.odds)
    # 一旦コピー対象は作らず、prediction の現状から読み取り→計算結果のみ返す
    bets = _all_bets(prediction)
    if not bets:
        return []

    strengths = [compute_predicted_strength(b, scores) for b in bets]
    valid_strengths = sorted([v for v in strengths if v is not None])
    rank_map = build_market_rank_map(odds)

    results: list[BetValueAnalysis] = []
    for b, strength in zip(bets, strengths):
        market_odds, market_rank = _lookup_market(b, rank_map)
        if market_odds is None:
            label = "オッズ未取得・要確認"
            score = VALUE_LABEL_SCORES[label]
        else:
            s_tier = _strength_tier(strength, valid_strengths)
            o_tier = _odds_tier(market_odds) or 0
            label = _LABEL_MATRIX.get((s_tier, o_tier), "本線向き")
            score = VALUE_LABEL_SCORES.get(label, 0.0)
        results.append(
            BetValueAnalysis(
                category=b.category,
                bet_type=b.bet_type,
                combination=b.combination,
                predicted_strength=strength,
                market_odds=market_odds,
                market_rank=market_rank,
                gami_risk=b.gami_risk,
                value_score=score,
                value_label=label,
            )
        )
    return results


def promote_oddful_to_honsen(
    prediction: Prediction, *, max_promotions: int = 3
) -> int:
    """本線が全件オッズ未取得で、押さえに妙味あり買い目があれば本線に昇格する。

    本線セクションと「一番買いたい買い目」（最終結論）の整合性を保つための後処理。

    呼び出し順:
        annotate_prediction_with_value(...)
        promote_oddful_to_osae(...)   # 穴→押さえ
        promote_oddful_to_honsen(...) # 押さえ→本線

    昇格条件:
        - 本線が全件 market_odds=None
        - 押さえに market_odds 取得済み + value_label が「妙味あり」「本線向き」

    Returns:
        実際に昇格した点数
    """
    if not prediction.honsen:
        return 0
    honsen_with_odds = sum(
        1 for b in prediction.honsen if b.market_odds is not None
    )
    if honsen_with_odds > 0:
        return 0  # 本線にすでにオッズ取得済みがある → 昇格不要

    # 押さえから「妙味あり」「本線向き」+ オッズ取得済みを抽出
    candidates: list = []
    for b in prediction.osae:
        if b.market_odds is None:
            continue
        if b.value_label not in ("本線向き", "妙味あり"):
            continue
        candidates.append(b)

    # 「本線向き」優先 → 「妙味あり」 → オッズ昇順
    def _sort_key(b):
        label_pref = 0 if b.value_label == "本線向き" else 1
        return (label_pref, b.market_odds or 999.0)

    candidates.sort(key=_sort_key)

    promoted = 0
    promoted_combos: set[str] = set()
    existing_honsen = {b.combination for b in prediction.honsen}
    for b in candidates:
        if b.combination in existing_honsen:
            continue
        b.category = "本線"
        b.reason = (
            f"{b.reason} ＋ 本線へ昇格(オッズ取得済み+妙味)"
            if b.reason else "本線へ昇格(オッズ取得済み+妙味)"
        )
        prediction.honsen.append(b)
        promoted_combos.add(b.combination)
        promoted += 1
        if promoted >= max_promotions:
            break
    # 押さえから昇格分を削除（二重表示を避ける）
    if promoted_combos:
        prediction.osae = [
            b for b in prediction.osae if b.combination not in promoted_combos
        ]
    return promoted


def promote_oddful_to_osae(prediction: Prediction, *, max_promotions: int = 2) -> int:
    """本線の半数以上がオッズ未取得なら、穴のオッズ取得済み中穴を押さえ末尾に昇格する。

    新人戦・個人戦などで本線がオッズ未取得ばかりになるとき、市場が支持する
    中穴（10〜30倍）を実購入候補として押さえに上げるための後処理。

    既存の押さえはそのまま残し、新規追加のみ。

    Args:
        prediction: Prediction（破壊的更新）
        max_promotions: 1レースで昇格させる最大点数（デフォルト 2）

    Returns:
        実際に昇格した点数
    """
    honsen_with_odds = sum(
        1 for b in prediction.honsen if b.market_odds is not None
    )
    # 本線3点中、オッズ取得済みが1点以下なら昇格対象
    if not prediction.honsen:
        return 0
    if honsen_with_odds >= max(2, len(prediction.honsen) - 1):
        return 0  # 既に十分

    # 穴の中で odds 10〜30 + value_label が「妙味あり」「本線向き」を優先
    candidates: list = []
    for b in prediction.ana:
        if b.market_odds is None:
            continue
        if not (8.0 <= b.market_odds <= 30.0):
            continue
        if b.value_label in ("本線向き", "妙味あり"):
            candidates.append(b)
    # オッズ昇順で上位を採用
    candidates.sort(key=lambda b: b.market_odds or 999)

    promoted = 0
    existing_osae_combos = {b.combination for b in prediction.osae}
    for b in candidates:
        if b.combination in existing_osae_combos:
            continue
        # 押さえに追加（穴からは削除しない: 二重表示で意図を明示）
        b_copy = b.model_copy(deep=True)
        b_copy.category = "押さえ"
        b_copy.reason = (
            f"{b.reason} ＋ オッズ取得済み中穴を押さえに昇格"
            if b.reason else "オッズ取得済み中穴を押さえに昇格"
        )
        prediction.osae.append(b_copy)
        promoted += 1
        if promoted >= max_promotions:
            break
    return promoted
