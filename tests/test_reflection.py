from __future__ import annotations

from pathlib import Path

import pytest

from app.cli import run_prediction
from app.reflection import (
    build_reflection,
    classify,
    parse_result,
    parse_results,
)
from app.storage import Storage


def test_parse_result():
    assert parse_result("5-1-3") == (5, 1, 3)
    assert parse_result("5=1=3") == (5, 1, 3)
    assert parse_result("invalid") is None
    assert parse_result("") is None


# ---------------------------------------------------------------------------
# 同着対応 (2026-05-24)
# ---------------------------------------------------------------------------


class TestParseResultsDeadHeat:
    """parse_results() で同着の複数結果をパースできる。"""

    def test_single_result_returns_one_tuple(self):
        assert parse_results("5-1-3") == [(5, 1, 3)]

    def test_slash_separated_two_results(self):
        assert parse_results("3-5-1 / 3-5-9") == [(3, 5, 1), (3, 5, 9)]

    def test_comma_separated_two_results(self):
        assert parse_results("3-5-1, 3-5-9") == [(3, 5, 1), (3, 5, 9)]

    def test_mixed_separator_normalized(self):
        # `/` と `,` の混在も同じ扱い
        assert parse_results("3-5-1 , 3-5-9 / 3-5-7") == [
            (3, 5, 1), (3, 5, 9), (3, 5, 7)
        ]

    def test_whitespace_tolerated(self):
        assert parse_results("  3-5-1  /  3-5-9  ") == [(3, 5, 1), (3, 5, 9)]

    def test_equals_notation_within_chunks(self):
        # 既存 `=` (位置入れ替え) との互換
        assert parse_results("5=1=3 / 4-2-1") == [(5, 1, 3), (4, 2, 1)]

    def test_invalid_chunk_returns_empty(self):
        # 一部不正なら全体を不正扱い (部分採用は誤解を招く)
        assert parse_results("3-5-1 / invalid") == []

    def test_empty_input_returns_empty(self):
        assert parse_results("") == []
        assert parse_results("   ") == []

    def test_parse_result_returns_first_for_dead_heat(self):
        """後方互換: parse_result() は同着でも先頭タプルを返す。"""
        assert parse_result("3-5-1 / 3-5-9") == (3, 5, 1)


class TestClassifyDeadHeat:
    """classify() で同着のいずれかにマッチすれば的中扱い。"""

    def test_hit_atari_when_second_actual_matches(self, sample_input):
        """予想に 3-5-9 だけがあり、結果が `3-5-1 / 3-5-9` でも的中扱い。"""
        from app.models import BetRecommendation, Prediction
        pred = Prediction(
            race_id=sample_input.race.race_id,
            venue=sample_input.race.venue,
            race_no=sample_input.race.race_no,
            is_girls=False,
            summary="テスト", venue_trend_text="テスト",
            weather_text="テスト", lines_text="テスト",
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単",
                    combination="3-5-9",
                    reason="同着シナリオ用",
                    gami_risk=0.0,
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        cats = classify(
            prediction=pred,
            actual_result="3-5-1 / 3-5-9",
            input_data=sample_input,
        )
        # 同着のうち 3-5-9 が予想にあるので的中
        assert "的中(同着)" in cats, (
            f"同着の1つが予想にマッチすれば的中扱い。実際: {cats}"
        )

    def test_dead_heat_label_distinguishes_from_normal_hit(self, sample_input):
        """単一結果での的中は「的中」、同着での的中は「的中(同着)」。"""
        from app.models import BetRecommendation, Prediction
        pred = Prediction(
            race_id=sample_input.race.race_id,
            venue=sample_input.race.venue,
            race_no=sample_input.race.race_no,
            is_girls=False,
            summary="テスト", venue_trend_text="テスト",
            weather_text="テスト", lines_text="テスト",
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単",
                    combination="3-5-1",
                    reason="単一結果用",
                    gami_risk=0.0,
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        cats_single = classify(
            prediction=pred,
            actual_result="3-5-1",
            input_data=sample_input,
        )
        cats_dead = classify(
            prediction=pred,
            actual_result="3-5-1 / 3-5-9",
            input_data=sample_input,
        )
        assert "的中" in cats_single and "的中(同着)" not in cats_single
        assert "的中(同着)" in cats_dead

    def test_no_hit_when_neither_actual_matches(self, sample_input):
        """同着両方とも予想に無い場合は的中扱いにならない。"""
        from app.models import BetRecommendation, Prediction
        pred = Prediction(
            race_id=sample_input.race.race_id,
            venue=sample_input.race.venue,
            race_no=sample_input.race.race_no,
            is_girls=False,
            summary="テスト", venue_trend_text="テスト",
            weather_text="テスト", lines_text="テスト",
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単",
                    combination="1-2-3",
                    reason="無関係", gami_risk=0.0,
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        cats = classify(
            prediction=pred,
            actual_result="3-5-1 / 3-5-9",
            input_data=sample_input,
        )
        assert "的中" not in cats
        assert "的中(同着)" not in cats

    def test_invalid_dead_heat_returns_format_error(self, sample_input):
        """同着のうち1つでもフォーマット不正なら「結果フォーマット不正」。"""
        from app.models import BetRecommendation, Prediction
        pred = Prediction(
            race_id=sample_input.race.race_id,
            venue=sample_input.race.venue,
            race_no=sample_input.race.race_no,
            is_girls=False,
            summary="テスト", venue_trend_text="テスト",
            weather_text="テスト", lines_text="テスト",
            honsen=[], osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        cats = classify(
            prediction=pred,
            actual_result="3-5-1 / invalid",
            input_data=sample_input,
        )
        assert cats == ["結果フォーマット不正"]


class TestSaveResultDeadHeat:
    """save_result() で同着文字列がそのまま保存・取得できる (後方互換)。"""

    def test_save_and_get_dead_heat_string(self, tmp_path, sample_input):
        from app.cli import run_prediction
        pred = run_prediction(sample_input)
        storage = Storage(tmp_path / "test.db")
        storage.save_prediction(pred)
        storage.save_result(pred.race_id, "3-5-1 / 3-5-9")
        got = storage.get_result(pred.race_id)
        assert got == "3-5-1 / 3-5-9"


# ---------------------------------------------------------------------------
# codex review 反映: 入力順依存解消 + reporting 同着対応
# ---------------------------------------------------------------------------


class TestDeadHeatCodexFixes:
    """codex review (2026-05-24) P2 修正の回帰テスト。"""

    def test_classify_order_independent_for_dead_heat(self, sample_input):
        """3着同着の `3-5-1 / 3-5-9` と `3-5-9 / 3-5-1` で同じカテゴリ集合。"""
        from app.cli import run_prediction
        pred = run_prediction(sample_input)
        cats_a = set(classify(
            prediction=pred,
            actual_result="3-5-1 / 3-5-9",
            input_data=sample_input,
        ))
        cats_b = set(classify(
            prediction=pred,
            actual_result="3-5-9 / 3-5-1",
            input_data=sample_input,
        ))
        assert cats_a == cats_b, (
            f"3着同着の順序で反省カテゴリが変わってはいけない: "
            f"{cats_a} vs {cats_b}"
        )

    def test_reporting_classify_hit_with_dead_heat(self):
        """reporting.classify_hit が同着の2つ目だけマッチでも的中扱い。"""
        from app.models import BetRecommendation, Prediction
        from app.reporting import classify_hit
        pred = Prediction(
            race_id="test-dh", venue="武雄", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="", lines_text="",
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="3-5-9",
                    reason="", gami_risk=0.0,
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        # 3-5-9 が予想にあるので main_hit
        assert classify_hit(pred, "3-5-1 / 3-5-9") == "main_hit"
        # 順序入れ替えでも同じ
        assert classify_hit(pred, "3-5-9 / 3-5-1") == "main_hit"
        # どちらも予想に無ければ miss
        assert classify_hit(pred, "1-2-4 / 1-2-7") == "miss"


def test_hit_honsen_classified_as_atari(sample_input):
    pred = run_prediction(sample_input)
    # 予想本線の1点目を実結果とする
    actual = pred.honsen[0].combination.replace("=", "-")
    cats = classify(prediction=pred, actual_result=actual, input_data=sample_input)
    assert "的中" in cats


def test_partial_hit_classified(sample_input):
    pred = run_prediction(sample_input)
    # 押さえの最初を結果に
    if pred.osae:
        actual = pred.osae[0].combination.replace("=", "-")
        cats = classify(prediction=pred, actual_result=actual, input_data=sample_input)
        assert "買い目にはあったが本線ではなかった" in cats or "的中" in cats


def test_third_position_negligence_when_not_in_bets(sample_input):
    """3番手車が3着に来たが買い目に含まれていない場合の分類。"""
    pred = run_prediction(sample_input)
    # 全買い目の3着位置に3番手が入っているかチェックなしに、
    # ここでは結果として九州3番手(3)が来た場合の挙動を見る
    cats = classify(
        prediction=pred,
        actual_result="2-6-3",  # 別線頭、別線番手2着、本命3番手3着
        input_data=sample_input,
    )
    # いずれかの反省カテゴリが付くこと
    assert len(cats) >= 1


def test_wind_correction_warning(sample_input):
    """強風で番手/3番手が絡んでも買い目に拾えていないケース。"""
    pred = run_prediction(sample_input)
    # 全く合っていない結果
    cats = classify(
        prediction=pred,
        actual_result="4-7-2",
        input_data=sample_input,
    )
    assert len(cats) >= 1


def test_storage_roundtrip(tmp_path: Path, sample_input):
    db = tmp_path / "test.db"
    s = Storage(db)
    pred = run_prediction(sample_input)
    s.save_prediction(pred)
    loaded = s.get_prediction(pred.race_id)
    assert loaded is not None
    assert loaded.race_id == pred.race_id
    assert loaded.marks == pred.marks


def test_reflection_save_and_list(tmp_path: Path, sample_input):
    db = tmp_path / "test.db"
    s = Storage(db)
    pred = run_prediction(sample_input)
    s.save_prediction(pred)
    s.save_result(pred.race_id, "5-1-3")
    reflection = build_reflection(
        prediction=pred, actual_result="5-1-3", input_data=sample_input
    )
    s.save_reflection(reflection)
    items = s.list_reflections(venue="大垣")
    assert len(items) == 1
    assert items[0].race_id == pred.race_id
    assert items[0].categories
