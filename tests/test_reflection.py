from __future__ import annotations

from pathlib import Path

import pytest

from app.cli import run_prediction
from app.reflection import build_reflection, classify, parse_result
from app.storage import Storage


def test_parse_result():
    assert parse_result("5-1-3") == (5, 1, 3)
    assert parse_result("5=1=3") == (5, 1, 3)
    assert parse_result("invalid") is None
    assert parse_result("") is None


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
