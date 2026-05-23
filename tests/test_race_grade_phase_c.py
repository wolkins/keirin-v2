"""フェーズ C: 地元・地区連係の加点テスト。

docs/race_type_policy.md フェーズ C1〜C4 の検証。

検証項目:
- C1: Rider.home_area フィールド
- C2: VENUE_TO_AREA テーブル + resolve_venue_area() 部分一致
- C3: apply_home_area_signals() — 地元番手/3番手/別線番手/単騎 加点
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import Line, RaceInfo, RaceInput, Rider
from app.scoring import (
    VENUE_TO_AREA,
    apply_home_area_signals,
    compute_scores,
    resolve_venue_area,
)


# ---------------------------------------------------------------------------
# C1: home_area フィールド
# ---------------------------------------------------------------------------


def test_rider_home_area_field_optional():
    """home_area は未指定でも Rider が作れる。"""
    r = Rider(car_no=1, name="test", score=85.0)
    assert r.home_area is None


def test_rider_home_area_field_settable():
    """home_area を指定して作成できる。"""
    r = Rider(car_no=1, name="test", score=85.0, home_area="九州")
    assert r.home_area == "九州"


# ---------------------------------------------------------------------------
# C2: VENUE_TO_AREA + resolve_venue_area
# ---------------------------------------------------------------------------


class TestResolveVenueArea:
    def test_exact_match(self):
        assert resolve_venue_area("広島") == "中国"
        assert resolve_venue_area("小倉") == "九州"
        assert resolve_venue_area("立川") == "関東"
        assert resolve_venue_area("川崎") == "南関東"
        assert resolve_venue_area("名古屋") == "中部"

    def test_partial_match(self):
        """会場名にバンク名が含まれる場合も解決できる。"""
        # "京都向日町記念" のような表記でも解決
        assert resolve_venue_area("京都向日町記念") == "近畿"
        assert resolve_venue_area("広島競輪場") == "中国"

    def test_none_for_unknown(self):
        assert resolve_venue_area("存在しない場") is None
        assert resolve_venue_area("") is None
        assert resolve_venue_area(None) is None  # type: ignore[arg-type]

    def test_all_8_areas_covered(self):
        """全 8 地区がマッピングに存在する。"""
        areas = set(VENUE_TO_AREA.values())
        expected = {
            "北日本", "関東", "南関東", "中部",
            "近畿", "中国", "四国", "九州",
        }
        assert areas == expected


# ---------------------------------------------------------------------------
# C3: apply_home_area_signals 加点ロジック
# ---------------------------------------------------------------------------


def _g3_hiroshima_input(
    *,
    bantan_home: str | None = "中国",
    third_home: str | None = None,
    bessen_bantan_home: str | None = None,
    tanki_home: str | None = None,
    class_name: str = "G3広島記念",
) -> RaceInput:
    """G3広島記念 (会場=広島, 地区=中国) のテスト入力。

    本命ライン[1,3,4] + 別線[7,5,2] + 単騎[6]。
    """
    riders = [
        Rider(car_no=1, name="L1", score=100.0, nige=3),
        Rider(car_no=2, name="B2", score=80.0),
        Rider(car_no=3, name="L3", score=90.0, sashi=2, home_area=bantan_home),
        Rider(car_no=4, name="L4", score=82.0, home_area=third_home),
        Rider(car_no=5, name="B5", score=85.0, home_area=bessen_bantan_home),
        Rider(car_no=6, name="S6", score=85.0, home_area=tanki_home),
        Rider(car_no=7, name="B7", score=95.0, nige=2),
    ]
    lines = [
        Line(line_name="本命", cars=[1, 3, 4]),
        Line(line_name="別線", cars=[7, 5, 2]),
        Line(line_name="単騎", cars=[6]),
    ]
    return RaceInput(
        race=RaceInfo(
            race_id="20260523-広島-1", date=date(2026, 5, 23),
            venue="広島", race_no=1, class_name=class_name,
        ),
        riders=riders, lines=lines, odds=[], recent_results=[],
    )


class TestApplyHomeAreaSignals:
    def test_local_main_line_bantan_boosts(self):
        """G3広島で地元(中国)番手 → win/second 加点。"""
        ri = _g3_hiroshima_input(bantan_home="中国")
        scores = compute_scores(ri)
        before = next(
            (s.win_score, s.second_score) for s in scores if s.car_no == 3
        )
        apply_home_area_signals(scores, ri)
        after = next(
            (s.win_score, s.second_score) for s in scores if s.car_no == 3
        )
        assert after[0] > before[0], "番手 win_score 加点なし"
        assert after[1] > before[1], "番手 second_score 加点なし"

    def test_non_local_bantan_no_boost(self):
        """地元ではない番手 → 加点なし。"""
        ri = _g3_hiroshima_input(bantan_home="九州")
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_home_area_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after == before

    def test_no_home_area_no_boost(self):
        """home_area 未指定 → 加点なし。"""
        ri = _g3_hiroshima_input(bantan_home=None)
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_home_area_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after == before

    def test_local_third_boosts_second_and_third(self):
        """地元 3番手 → second/third 加点。"""
        ri = _g3_hiroshima_input(third_home="中国")
        scores = compute_scores(ri)
        before = next(
            (s.second_score, s.third_score) for s in scores if s.car_no == 4
        )
        apply_home_area_signals(scores, ri)
        after = next(
            (s.second_score, s.third_score) for s in scores if s.car_no == 4
        )
        assert after[0] > before[0]
        assert after[1] > before[1]

    def test_local_bessen_bantan_boosts(self):
        """地元 別線番手 → second/third 加点（本命番手より控えめ）。"""
        ri = _g3_hiroshima_input(bessen_bantan_home="中国")
        scores = compute_scores(ri)
        before = next(s.second_score for s in scores if s.car_no == 5)
        apply_home_area_signals(scores, ri)
        after = next(s.second_score for s in scores if s.car_no == 5)
        assert after > before

    def test_local_tanki_boosts_third(self):
        """地元 単騎 → third 加点。"""
        ri = _g3_hiroshima_input(tanki_home="中国")
        scores = compute_scores(ri)
        before = next(s.third_score for s in scores if s.car_no == 6)
        apply_home_area_signals(scores, ri)
        after = next(s.third_score for s in scores if s.car_no == 6)
        assert after > before

    def test_unknown_venue_no_boost(self):
        """会場マッピングに無い場合は何もしない。"""
        ri = _g3_hiroshima_input(bantan_home="中国")
        ri.race.venue = "存在しない場"
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_home_area_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after == before

    def test_no_boost_for_girls(self):
        ri = _g3_hiroshima_input(
            bantan_home="中国", class_name="ガールズ"
        )
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_home_area_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after == before

    def test_no_boost_for_rookie(self):
        ri = _g3_hiroshima_input(
            bantan_home="中国", class_name="新人戦"
        )
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_home_area_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        assert after == before

    def test_grade_stronger_than_f2(self):
        """G3 の地元加点 > F2 の地元加点。"""
        def _boost(class_name: str) -> float:
            ri = _g3_hiroshima_input(
                bantan_home="中国", class_name=class_name
            )
            scores = compute_scores(ri)
            before = next(s.win_score for s in scores if s.car_no == 3)
            apply_home_area_signals(scores, ri)
            after = next(s.win_score for s in scores if s.car_no == 3)
            return after - before

        g3 = _boost("G3広島記念")
        f2 = _boost("F2 A級一般")
        assert g3 > f2 > 0

    def test_final_combines_with_grade(self):
        """G1決勝 > G1準決勝 (係数 1.2)。"""
        def _boost(class_name: str) -> float:
            ri = _g3_hiroshima_input(
                bantan_home="中国", class_name=class_name
            )
            scores = compute_scores(ri)
            before = next(s.win_score for s in scores if s.car_no == 3)
            apply_home_area_signals(scores, ri)
            after = next(s.win_score for s in scores if s.car_no == 3)
            return after - before

        semi = _boost("G1高松宮記念杯 準決勝")
        final = _boost("G1高松宮記念杯 決勝")
        assert final > semi
