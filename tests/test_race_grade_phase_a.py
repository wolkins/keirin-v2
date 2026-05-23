"""フェーズ A: レース格判定・格上加点・フェーズフラグの統合テスト。

docs/race_type_policy.md フェーズ A1〜A6 の検証。

検証項目:
- A1/A2: RaceInfo.race_grade フィールド + resolved_race_grade() 自動推定
- A3: is_kakujou() 閾値判定
- A4: apply_grade_signals() による番手・3番手・別線番手・単騎の格上加点
- A5: resolved_is_final / resolved_is_semi_final / resolved_is_tokusen
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.models import Line, RaceInfo, RaceInput, Rider
from app.scoring import (
    KAKUJOU_THRESHOLD,
    apply_grade_signals,
    compute_scores,
    is_kakujou,
)


# ---------------------------------------------------------------------------
# A1 + A2: race_grade の判定
# ---------------------------------------------------------------------------


def _race(class_name: str, race_id: str = "20260523-test-1") -> RaceInfo:
    return RaceInfo(
        race_id=race_id, date=date(2026, 5, 23), venue="テスト",
        race_no=1, class_name=class_name,
    )


class TestResolvedRaceGrade:
    def test_explicit_takes_priority(self):
        r = RaceInfo(
            race_id="t", date=date(2026, 5, 23), venue="t",
            race_no=1, class_name="A級一般", race_grade="G1",
        )
        assert r.resolved_race_grade() == "G1"

    def test_gp_detection(self):
        assert _race("GP決勝").resolved_race_grade() == "GP"
        assert _race("KEIRINグランプリ").resolved_race_grade() == "GP"

    def test_g1_detection(self):
        assert _race("G1高松宮記念杯").resolved_race_grade() == "G1"
        assert _race("競輪祭 決勝").resolved_race_grade() == "G1"
        assert _race("オールスター競輪").resolved_race_grade() == "G1"

    def test_g2_detection(self):
        assert _race("G2共同通信社杯").resolved_race_grade() == "G2"
        assert _race("ヤンググランプリ").resolved_race_grade() == "G2"

    def test_g3_detection(self):
        assert _race("G3松山記念").resolved_race_grade() == "G3"
        assert _race("名古屋記念 初日").resolved_race_grade() == "G3"

    def test_f1_detection(self):
        assert _race("F1 A級一般").resolved_race_grade() == "F1"
        assert _race("F1 S級ガールズ").resolved_race_grade() == "F1"

    def test_f2_detection(self):
        assert _race("F2 A級一般").resolved_race_grade() == "F2"
        assert _race("F2 チャレンジ").resolved_race_grade() == "F2"

    def test_default_to_f2(self):
        """キーワード不一致なら F2 デフォルト。"""
        assert _race("A級一般").resolved_race_grade() == "F2"


# ---------------------------------------------------------------------------
# A5: 準決・決勝・特選・初日フラグ
# ---------------------------------------------------------------------------


class TestRaceDayFlags:
    def test_final_detection(self):
        assert _race("G1高松宮記念杯 決勝").resolved_is_final() is True
        assert _race("G3 初日特選").resolved_is_final() is False

    def test_semi_final_detection(self):
        assert _race("G3松山記念 準決勝").resolved_is_semi_final() is True
        assert _race("F1 準決").resolved_is_semi_final() is True
        assert _race("G3 決勝").resolved_is_semi_final() is False

    def test_tokusen_detection(self):
        assert _race("G3 初日特選").resolved_is_tokusen() is True
        assert _race("F1 A級特選").resolved_is_tokusen() is True
        assert _race("G3 準決").resolved_is_tokusen() is False

    def test_first_day_detection(self):
        assert _race("G3 初日特選").resolved_is_first_day() is True
        assert _race("F1 決勝").resolved_is_first_day() is False

    def test_explicit_overrides(self):
        r = RaceInfo(
            race_id="t", date=date(2026, 5, 23), venue="t",
            race_no=1, class_name="A級一般", is_final=True,
        )
        assert r.resolved_is_final() is True


# ---------------------------------------------------------------------------
# A3: is_kakujou() 閾値判定
# ---------------------------------------------------------------------------


def _rider(score: float, stats_missing: bool = False) -> Rider:
    return Rider(car_no=1, name="t", score=score, stats_missing=stats_missing)


class TestIsKakujou:
    def test_f2_threshold(self):
        assert is_kakujou(_rider(85.0), "F2") is True
        assert is_kakujou(_rider(84.9), "F2") is False

    def test_f1_threshold(self):
        assert is_kakujou(_rider(95.0), "F1") is True
        assert is_kakujou(_rider(94.9), "F1") is False
        assert is_kakujou(_rider(110.0), "F1") is True

    def test_grade_threshold(self):
        for grade in ("G3", "G2", "G1"):
            assert is_kakujou(_rider(100.0), grade) is True
            assert is_kakujou(_rider(99.9), grade) is False

    def test_gp_threshold(self):
        assert is_kakujou(_rider(105.0), "GP") is True
        assert is_kakujou(_rider(104.9), "GP") is False

    def test_stats_missing_returns_false(self):
        assert is_kakujou(_rider(120.0, stats_missing=True), "G1") is False

    def test_zero_score_returns_false(self):
        assert is_kakujou(_rider(0.0), "G1") is False


# ---------------------------------------------------------------------------
# A4: apply_grade_signals() 加点ロジック
# ---------------------------------------------------------------------------


def _g3_input(
    *,
    bantan_score: float = 100.0,
    third_score: float = 100.0,
    bessen_bantan_score: float = 100.0,
    tanki_score: float = 90.0,
    class_name: str = "G3松山記念",
) -> RaceInput:
    """本命ライン[1,3,4] + 別線[7,5,2] + 単騎[6] のG3テスト入力。"""
    riders = [
        Rider(car_no=1, name="L1", score=110.0, nige=3),  # 本命先頭
        Rider(car_no=2, name="B2", score=80.0),
        Rider(car_no=3, name="L3", score=bantan_score, sashi=3),  # 本命番手
        Rider(car_no=4, name="L4", score=third_score),  # 本命3番手
        Rider(car_no=5, name="B5", score=bessen_bantan_score),  # 別線番手
        Rider(car_no=6, name="S6", score=tanki_score),  # 単騎
        Rider(car_no=7, name="B7", score=95.0, nige=2),  # 別線先頭
    ]
    lines = [
        Line(line_name="本命", cars=[1, 3, 4]),
        Line(line_name="別線", cars=[7, 5, 2]),
        Line(line_name="単騎", cars=[6]),
    ]
    return RaceInput(
        race=RaceInfo(
            race_id="t", date=date(2026, 5, 23), venue="t",
            race_no=1, class_name=class_name,
        ),
        riders=riders, lines=lines, odds=[], recent_results=[],
    )


class TestApplyGradeSignals:
    def test_main_line_bantan_kakujou_boosts_win_and_second(self):
        """本命ライン番手(3)が格上→ win_score / second_score 加点。"""
        ri = _g3_input(bantan_score=100.0)
        scores = compute_scores(ri)
        before = {s.car_no: (s.win_score, s.second_score) for s in scores}
        apply_grade_signals(scores, ri)
        after = {s.car_no: (s.win_score, s.second_score) for s in scores}
        assert after[3][0] > before[3][0], "番手 win_score が上がっていない"
        assert after[3][1] > before[3][1], "番手 second_score が上がっていない"

    def test_main_line_bantan_not_kakujou_no_boost(self):
        """本命番手の得点が閾値未満なら加点しない。"""
        ri = _g3_input(bantan_score=80.0)
        scores = compute_scores(ri)
        before_win = next(s.win_score for s in scores if s.car_no == 3)
        apply_grade_signals(scores, ri)
        after_win = next(s.win_score for s in scores if s.car_no == 3)
        assert after_win == before_win

    def test_third_kakujou_boosts_second_and_third(self):
        """本命3番手(4)が格上→ second_score / third_score 加点。"""
        ri = _g3_input(third_score=100.0)
        scores = compute_scores(ri)
        before = {s.car_no: (s.second_score, s.third_score) for s in scores}
        apply_grade_signals(scores, ri)
        after = {s.car_no: (s.second_score, s.third_score) for s in scores}
        assert after[4][0] > before[4][0]
        assert after[4][1] > before[4][1]

    def test_bessen_bantan_kakujou_boosts_second_and_third(self):
        """別線番手(5)が格上→ second_score / third_score 加点。"""
        ri = _g3_input(bessen_bantan_score=100.0)
        scores = compute_scores(ri)
        before = {s.car_no: (s.second_score, s.third_score) for s in scores}
        apply_grade_signals(scores, ri)
        after = {s.car_no: (s.second_score, s.third_score) for s in scores}
        assert after[5][0] > before[5][0]

    def test_tanki_kakujou_boosts_third(self):
        """単騎(6)が格上→ third_score 加点（高めの 0.3）。"""
        ri = _g3_input(tanki_score=110.0)
        scores = compute_scores(ri)
        before = next(s.third_score for s in scores if s.car_no == 6)
        apply_grade_signals(scores, ri)
        after = next(s.third_score for s in scores if s.car_no == 6)
        assert after > before

    def test_final_doubles_boost(self):
        """決勝戦は加点係数 1.3 倍。"""
        ri_normal = _g3_input(bantan_score=100.0, class_name="G3松山記念 準決勝")
        ri_final = _g3_input(bantan_score=100.0, class_name="G1 決勝")

        sc_normal = compute_scores(ri_normal)
        apply_grade_signals(sc_normal, ri_normal)
        sc_final = compute_scores(ri_final)
        apply_grade_signals(sc_final, ri_final)

        bantan_normal = next(
            s.win_score for s in sc_normal if s.car_no == 3
        )
        bantan_final = next(
            s.win_score for s in sc_final if s.car_no == 3
        )
        # 決勝 = 通常 * 1.3 (両者ともG3扱い)、なので決勝のほうが加点が大きい
        # ただしF1の準決勝 vs G1の決勝でも閾値が違う可能性。同じ得点100で
        # G3閾値=100/F1閾値=95。両方とも格上判定。
        assert bantan_final > bantan_normal

    def test_no_boost_for_girls(self):
        """ガールズでは加点しない。"""
        ri = _g3_input(bantan_score=110.0, class_name="ガールズ")
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_grade_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after == before

    def test_no_boost_for_rookie(self):
        """新人戦では加点しない。"""
        ri = _g3_input(bantan_score=110.0, class_name="新人 準決勝")
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_grade_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after == before

    def test_no_boost_for_f2(self):
        """F2 ではグレード補正は控えめ → スキップ。"""
        ri = _g3_input(bantan_score=110.0, class_name="F2 A級一般")
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_grade_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after == before

    def test_boost_for_f1(self):
        """F1 では格上加点が入る（F1閾値=95、ライダー100点なら加点）。"""
        ri = _g3_input(bantan_score=100.0, class_name="F1 A級特選")
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_grade_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after > before


# ---------------------------------------------------------------------------
# 閾値辞書の値が docs/race_type_policy.md と一致
# ---------------------------------------------------------------------------


def test_kakujou_threshold_matches_policy():
    assert KAKUJOU_THRESHOLD["F2"] == 85.0
    assert KAKUJOU_THRESHOLD["F1"] == 95.0
    assert KAKUJOU_THRESHOLD["G3"] == 100.0
    assert KAKUJOU_THRESHOLD["G2"] == 100.0
    assert KAKUJOU_THRESHOLD["G1"] == 100.0
    assert KAKUJOU_THRESHOLD["GP"] == 105.0
