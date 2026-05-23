"""広島4R - 押さえ→本線昇格テスト（カテゴリ整合性）。

仕様要件:
1. honsen が全件 market_odds=None
2. osae に market_odds 取得済み + value_label="妙味あり"|"本線向き" がある
3. → osae の該当買い目が honsen に昇格する
4. 最終結論の「本線として有力」と本線セクションが矛盾しない
"""

from __future__ import annotations

import pytest

from app.cli import _summarize_for_final, render_prediction
from app.models import BetRecommendation, Prediction
from app.value_analysis import promote_oddful_to_honsen


def _bet(
    category: str,
    combo: str,
    *,
    odds: float | None = None,
    label: str | None = None,
    gami: float = 0.0,
) -> BetRecommendation:
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=f"{category}: {combo}", gami_risk=gami,
        market_odds=odds, value_label=label,
    )


def _hiroshima_4r_pred() -> Prediction:
    """広島4Rの問題ケースを再現。

    - honsen: 6-5-7 / 5-6-7 / 6-7-5  すべて market_odds=None
    - osae:   2-1-5 (12.0倍/妙味あり) / 1-2-5 (8.0倍/本線向き) / 1-2-6 (15.0倍/妙味あり)
    """
    return Prediction(
        race_id="20260523-広島-4", venue="広島", race_no=4,
        is_girls=False, marks={"◎": 6, "◯": 5, "△": 7},
        honsen=[
            _bet("本線", "6-5-7"),
            _bet("本線", "5-6-7"),
            _bet("本線", "6-7-5"),
        ],
        osae=[
            _bet("押さえ", "2-1-5", odds=12.0, label="妙味あり"),
            _bet("押さえ", "1-2-5", odds=8.0, label="本線向き"),
            _bet("押さえ", "1-2-6", odds=15.0, label="妙味あり"),
        ],
        ana=[], ooana=[],
        final_conclusion="",
    )


# ---------------------------------------------------------------------------
# 要件1〜4: 押さえ→本線昇格
# ---------------------------------------------------------------------------


def test_promote_oddful_to_honsen_basic():
    """honsen 全件 odds=None + osae に妙味あり → 押さえ→本線昇格。"""
    p = _hiroshima_4r_pred()
    promoted = promote_oddful_to_honsen(p)
    assert promoted >= 1, "本線昇格が発生していない"
    # 本線にオッズ取得済みが入る
    assert any(b.market_odds is not None for b in p.honsen)


def test_promoted_bets_have_value_label():
    """昇格した買い目は value_label が「妙味あり」「本線向き」のいずれか。"""
    p = _hiroshima_4r_pred()
    promote_oddful_to_honsen(p)
    promoted = [b for b in p.honsen if b.market_odds is not None]
    for b in promoted:
        assert b.value_label in ("本線向き", "妙味あり"), (
            f"昇格 {b.combination} の value_label が不適: {b.value_label}"
        )


def test_promoted_removed_from_osae():
    """昇格した買い目は押さえからは削除される（二重表示を避ける）。"""
    p = _hiroshima_4r_pred()
    promote_oddful_to_honsen(p)
    honsen_combos = {b.combination for b in p.honsen}
    osae_combos = {b.combination for b in p.osae}
    overlap = honsen_combos & osae_combos
    assert not overlap, f"本線と押さえに重複: {overlap}"


def test_honsen_with_odds_no_promotion():
    """本線にすでにオッズ取得済みがある場合は昇格しない。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            _bet("本線", "1-2-3", odds=20.0, label="妙味あり"),
            _bet("本線", "1-2-4"),
        ],
        osae=[
            _bet("押さえ", "2-1-3", odds=10.0, label="本線向き"),
        ],
        ana=[], ooana=[],
        final_conclusion="",
    )
    promoted = promote_oddful_to_honsen(p)
    assert promoted == 0
    # 押さえはそのまま
    assert any(b.combination == "2-1-3" for b in p.osae)


def test_no_osae_with_odds_no_promotion():
    """押さえに market_odds 取得済み + 妙味買い目が無い場合は昇格しない。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[_bet("本線", "1-2-3")],
        osae=[
            _bet("押さえ", "2-1-3"),  # odds=None
            _bet("押さえ", "3-1-2", odds=10.0, label=None),  # ラベル無し
        ],
        ana=[], ooana=[],
        final_conclusion="",
    )
    promoted = promote_oddful_to_honsen(p)
    assert promoted == 0


def test_max_promotions_limit():
    """max_promotions の上限を超えない。"""
    p = _hiroshima_4r_pred()
    promoted = promote_oddful_to_honsen(p, max_promotions=2)
    assert promoted <= 2
    promoted_count = sum(1 for b in p.honsen if b.market_odds is not None)
    assert promoted_count <= 2


def test_prefers_honsen_label_over_myomi():
    """「本線向き」が「妙味あり」より優先して昇格される。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[_bet("本線", "9-9-9")],  # odds=None
        osae=[
            _bet("押さえ", "5-4-6", odds=35.0, label="妙味あり"),
            _bet("押さえ", "1-2-3", odds=10.0, label="本線向き"),
        ],
        ana=[], ooana=[],
        final_conclusion="",
    )
    promote_oddful_to_honsen(p, max_promotions=1)
    promoted = [b for b in p.honsen if b.market_odds is not None]
    assert len(promoted) == 1
    assert promoted[0].combination == "1-2-3"
    assert promoted[0].value_label == "本線向き"


# ---------------------------------------------------------------------------
# 要件5: 最終結論と本線セクションの整合性
# ---------------------------------------------------------------------------


def test_final_conclusion_matches_honsen_after_promotion():
    """昇格後、最終結論の『本線として有力』と本線セクションが矛盾しない。"""
    p = _hiroshima_4r_pred()
    promote_oddful_to_honsen(p)
    md = render_prediction(p)
    text = _summarize_for_final(p)

    # 最終結論の「本線として有力」買い目を取得
    judgement = text.split("### 実購入判断")[1]
    import re
    main_line = next(
        (line for line in judgement.split("\n") if "本線として有力" in line), ""
    )
    main_combos = set(re.findall(r"\d-\d-\d", main_line))

    # 本線セクションの「実購入候補」を取得
    honsen_section = md.split("## 6. 本線")[1].split("## 7. 押さえ")[0]
    real_part, _, _ = honsen_section.partition("安い人気筋")
    honsen_combos = set(re.findall(r"\d-\d-\d", real_part))

    # 最終結論の本線買い目は honsen セクションに含まれる
    missing = main_combos - honsen_combos
    assert not missing, (
        f"最終結論の本線買い目 {main_combos} が本線セクション {honsen_combos} に無い:\n"
        f"  欠落: {missing}\n  最終結論行: {main_line}\n  本線部: {real_part}"
    )


def test_top_pick_combos_are_in_honsen_section():
    """昇格後、「一番買いたい買い目」が本線セクションに含まれる。"""
    p = _hiroshima_4r_pred()
    promote_oddful_to_honsen(p)
    md = render_prediction(p)
    text = _summarize_for_final(p)

    top_section = text.split("### 一番買いたい買い目")[1].split("### 押さえるべき")[0]
    import re
    top_combos = set(re.findall(r"\d-\d-\d", top_section))
    # 確認メモ行は除外（オッズ取得済みのケースなのでメモは出ないはず）
    honsen_section = md.split("## 6. 本線")[1].split("## 7. 押さえ")[0]
    real_part, _, _ = honsen_section.partition("安い人気筋")
    honsen_combos = set(re.findall(r"\d-\d-\d", real_part))

    # 一番買いたい買い目は honsen に居る
    missing = top_combos - honsen_combos
    assert not missing, (
        f"一番買いたい買い目 {top_combos} が本線セクション {honsen_combos} に無い"
    )
