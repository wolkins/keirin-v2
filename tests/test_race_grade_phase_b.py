"""フェーズ B: F1/F2/チャレンジ判別と種別ごとの加点差分テスト。

docs/race_type_policy.md フェーズ B1〜B5 の検証。

検証項目:
- B1: resolved_race_grade() の F1/F2 判別
- B2: resolved_race_class() の S級/A級一般/A級チャレンジ判別
- B3: apply_f2_signals() — F2の点数差大/チャレンジ自力/ライン3車加点
- B4: apply_grade_signals() のレース格係数 (F1<G2<G1<GP)
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import Line, RaceInfo, RaceInput, Rider
from app.scoring import (
    GRADE_BOOST_MULTIPLIER,
    apply_f2_signals,
    apply_grade_signals,
    compute_scores,
)


def _race(class_name: str, *, race_grade: str | None = None) -> RaceInfo:
    return RaceInfo(
        race_id="20260523-test-1", date=date(2026, 5, 23),
        venue="テスト", race_no=1, class_name=class_name,
        race_grade=race_grade,
    )


# ---------------------------------------------------------------------------
# B2: race_class 判別
# ---------------------------------------------------------------------------


class TestResolvedRaceClass:
    def test_s_class(self):
        assert _race("F1 S級特選").resolved_race_class() == "S級"

    def test_a_level_general(self):
        assert _race("F2 A級一般").resolved_race_class() == "A級一般"
        assert _race("A1A2 予選").resolved_race_class() == "A級一般"

    def test_challenge(self):
        assert _race("F2 チャレンジ").resolved_race_class() == "A級チャレンジ"
        assert _race("A3 予選").resolved_race_class() == "A級チャレンジ"
        assert _race("Aチャレンジ 決勝").resolved_race_class() == "A級チャレンジ"

    def test_girls(self):
        assert _race("F1 ガールズ予選").resolved_race_class() == "ガールズ"

    def test_rookie(self):
        assert _race("F2 新人戦").resolved_race_class() == "新人"
        assert _race("ルーキーシリーズ").resolved_race_class() == "新人"

    def test_unknown(self):
        assert _race("特殊レース").resolved_race_class() == "不明"


# ---------------------------------------------------------------------------
# B3: F2 加点 (apply_f2_signals)
# ---------------------------------------------------------------------------


def _f2_input(
    *,
    class_name: str = "F2 A級一般",
    top1_score: float = 95.0,
    top2_score: float = 88.0,
    top1_nige: int = 0,
    top1_makuri: int = 0,
    with_three_car_line: bool = True,
) -> RaceInput:
    """F2 テスト入力。本命ライン[1,3,4] + 別線[5,7] + 単騎[6]."""
    riders = [
        Rider(car_no=1, name="L1", score=top1_score,
              nige=top1_nige, makuri=top1_makuri),
        Rider(car_no=2, name="X2", score=78.0),
        Rider(car_no=3, name="L3", score=top2_score, sashi=2),
        Rider(car_no=4, name="L4", score=82.0),
        Rider(car_no=5, name="B5", score=80.0),
        Rider(car_no=6, name="S6", score=85.0),
        Rider(car_no=7, name="B7", score=83.0, nige=1),
    ]
    lines = [
        Line(line_name="本命",
             cars=[1, 3, 4] if with_three_car_line else [1, 3]),
        Line(line_name="別線", cars=[7, 5]),
        Line(line_name="単騎", cars=[6]),
    ]
    return RaceInput(
        race=_race(class_name), riders=riders, lines=lines,
        odds=[], recent_results=[],
    )


class TestApplyF2Signals:
    def test_large_score_diff_boosts_top1_win(self):
        """点数差 >= 5点なら top1 の win_score 加点。"""
        ri = _f2_input(top1_score=95.0, top2_score=85.0)  # 差10点
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 1)
        apply_f2_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 1)
        assert after > before, "点数差大で先頭加点が入っていない"

    def test_small_score_diff_no_boost(self):
        """点数差 < 5点なら加点しない。"""
        ri = _f2_input(top1_score=90.0, top2_score=88.0)  # 差2点
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 1)
        apply_f2_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 1)
        # F2 加点はなし (ただし他signalで動く可能性は別問題)
        # F2 signal自体は加点しないことを確認
        diff = after - before
        # +0.4 (点数差) と +0.3 (チャレンジ自力) のいずれも入らない
        assert diff < 0.4, f"小差なのに加点された: +{diff:.2f}"

    def test_challenge_jiriki_boosts(self):
        """A級チャレンジ + 先頭が自力タイプ → win_score 加点。"""
        ri = _f2_input(
            class_name="F2 チャレンジ",
            top1_nige=3, top1_makuri=1,
            top1_score=85.0, top2_score=84.0,  # 点数差小
        )
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 1)
        apply_f2_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 1)
        assert after > before

    def test_non_challenge_jiriki_no_boost(self):
        """A級一般で先頭自力でも、点数差が小ならチャレンジ加点なし。"""
        ri = _f2_input(
            class_name="F2 A級一般",
            top1_nige=3, top1_makuri=1,
            top1_score=85.0, top2_score=84.0,  # 点数差小
        )
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 1)
        apply_f2_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 1)
        # 点数差 < 5 かつ チャレンジ加点なし
        assert after == before

    def test_three_car_line_boosts_third(self):
        """本命ライン3車以上で3番手 third_score 加点。"""
        ri = _f2_input(with_three_car_line=True,
                       top1_score=88.0, top2_score=87.0)
        scores = compute_scores(ri)
        before = next(s.third_score for s in scores if s.car_no == 4)
        apply_f2_signals(scores, ri)
        after = next(s.third_score for s in scores if s.car_no == 4)
        assert after > before

    def test_two_car_line_no_third_boost(self):
        """ライン2車なら3番手加点なし。"""
        ri = _f2_input(with_three_car_line=False)
        scores = compute_scores(ri)
        # car_no=4 はラインに居ない（line=[1,3] のみ）
        # 4は単騎扱いなので third 加点なし
        before = next(s.third_score for s in scores if s.car_no == 4)
        apply_f2_signals(scores, ri)
        after = next(s.third_score for s in scores if s.car_no == 4)
        assert after == before

    def test_no_boost_for_f1(self):
        """F1 では apply_f2_signals は何もしない。"""
        ri = _f2_input(class_name="F1 A級一般",
                       top1_score=95.0, top2_score=85.0)
        scores = compute_scores(ri)
        before_all = [(s.car_no, s.win_score, s.second_score, s.third_score) for s in scores]
        apply_f2_signals(scores, ri)
        after_all = [(s.car_no, s.win_score, s.second_score, s.third_score) for s in scores]
        assert before_all == after_all

    def test_no_boost_for_grade(self):
        """G3 では F2 加点は入らない。"""
        ri = _f2_input(class_name="G3松山記念",
                       top1_score=110.0, top2_score=100.0)
        scores = compute_scores(ri)
        before_all = [(s.car_no, s.win_score) for s in scores]
        apply_f2_signals(scores, ri)
        after_all = [(s.car_no, s.win_score) for s in scores]
        assert before_all == after_all

    def test_no_boost_for_girls(self):
        ri = _f2_input(class_name="F2 ガールズ予選",
                       top1_score=95.0, top2_score=85.0)
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 1)
        apply_f2_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 1)
        assert after == before


# ---------------------------------------------------------------------------
# B4: グレード係数の差分（F1 < G2 < G1 < GP）
# ---------------------------------------------------------------------------


def _grade_input(class_name: str) -> RaceInput:
    """本命番手(3)が格上=100点のテスト入力。"""
    riders = [
        Rider(car_no=1, name="L1", score=110.0, nige=3),
        Rider(car_no=2, name="X2", score=80.0),
        Rider(car_no=3, name="L3", score=100.0, sashi=3),  # 番手 格上
        Rider(car_no=4, name="L4", score=82.0),
        Rider(car_no=5, name="B5", score=85.0),
        Rider(car_no=6, name="S6", score=85.0),
        Rider(car_no=7, name="B7", score=95.0, nige=2),
    ]
    lines = [
        Line(line_name="本命", cars=[1, 3, 4]),
        Line(line_name="別線", cars=[7, 5, 2]),
        Line(line_name="単騎", cars=[6]),
    ]
    return RaceInput(
        race=_race(class_name), riders=riders, lines=lines,
        odds=[], recent_results=[],
    )


def _bantan_boost(class_name: str) -> float:
    """class_name で apply_grade_signals 実行後の番手(3)の win_score 増分。"""
    ri = _grade_input(class_name)
    scores = compute_scores(ri)
    before = next(s.win_score for s in scores if s.car_no == 3)
    apply_grade_signals(scores, ri)
    after = next(s.win_score for s in scores if s.car_no == 3)
    return after - before


class TestGradeBoostMultiplier:
    def test_threshold_table_matches_policy(self):
        assert GRADE_BOOST_MULTIPLIER["F2"] == 0.0
        assert GRADE_BOOST_MULTIPLIER["F1"] == 1.0
        assert GRADE_BOOST_MULTIPLIER["G3"] == 1.0
        assert GRADE_BOOST_MULTIPLIER["G2"] == 1.1
        assert GRADE_BOOST_MULTIPLIER["G1"] == 1.2
        assert GRADE_BOOST_MULTIPLIER["GP"] == 1.3

    def test_f1_and_g3_same_boost(self):
        """F1 と G3 は同じ係数 (1.0)。"""
        f1 = _bantan_boost("F1 A級特選")
        g3 = _bantan_boost("G3松山記念")
        assert abs(f1 - g3) < 1e-9

    def test_g2_stronger_than_f1(self):
        """G2 は F1 より加点が強い。"""
        f1 = _bantan_boost("F1 A級特選")
        g2 = _bantan_boost("G2共同通信社杯")
        assert g2 > f1

    def test_g1_stronger_than_g2(self):
        g2 = _bantan_boost("G2共同通信社杯")
        g1 = _bantan_boost("G1高松宮記念杯")
        assert g1 > g2

    def test_gp_strongest(self):
        """GP は G1 より係数が強い。GP の格上閾値=105 のため、
        番手得点を 110 にした入力で比較する。"""
        def _bantan_boost_high(class_name: str) -> float:
            ri = _grade_input(class_name)
            # 番手(3)の得点を 110 に引き上げ (GPでも格上判定)
            for r in ri.riders:
                if r.car_no == 3:
                    r.score = 110.0
            scores = compute_scores(ri)
            before = next(s.win_score for s in scores if s.car_no == 3)
            apply_grade_signals(scores, ri)
            after = next(s.win_score for s in scores if s.car_no == 3)
            return after - before

        g1 = _bantan_boost_high("G1高松宮記念杯")
        gp = _bantan_boost_high("KEIRINグランプリ")
        assert gp > g1

    def test_f2_no_boost(self):
        """F2 では grade signal は加点しない（F2 signalで別ロジック）。"""
        diff = _bantan_boost("F2 A級一般")
        assert diff == 0.0

    def test_final_combines_with_grade_mult(self):
        """G1決勝 = G1 × 1.3 で、G1準決勝より強い。"""
        semi = _bantan_boost("G1高松宮記念杯 準決勝")
        final = _bantan_boost("G1高松宮記念杯 決勝")
        assert final > semi
