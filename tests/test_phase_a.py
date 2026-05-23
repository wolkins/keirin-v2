"""フェーズA: 役割分類 (RiderRole) + バンク補正のテスト。

仕様3章「通常ライン戦の役割分類」と7章「バンク補正」をカバー。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.models import RaceInput
from app.scoring import (
    RIDER_ROLES,
    apply_bank_signals,
    compute_scores,
    resolve_rider_roles,
)


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def _build_input(**race_overrides) -> RaceInput:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"].update(race_overrides)
    return RaceInput.model_validate(raw)


# ---------------------------------------------------------------------------
# resolve_rider_roles
# ---------------------------------------------------------------------------


def test_rider_roles_known_values():
    expected = {
        "line_leader", "second", "third", "fourth",
        "separate_leader", "separate_second", "separate_third",
        "solo", "jizai", "girls",
    }
    assert set(RIDER_ROLES) == expected


def test_resolve_roles_normal_race(sample_input):
    scores = compute_scores(sample_input)
    roles = resolve_rider_roles(sample_input, scores)
    # サンプル: 九州[5,1,3] / 中部中国[2,6,4] / 単騎[7]
    # 5番(池部, 自力,得点85.71) が最上位 → 本命ライン=九州
    assert roles[5] == "line_leader"
    assert roles[1] == "second"
    assert roles[3] == "third"
    # 中部中国は別線
    assert roles[2] == "separate_leader"
    assert roles[6] == "separate_second"
    assert roles[4] == "separate_third"
    # 単騎ライン: 7番は style_tags に "単騎"/"自在" あり → "jizai"
    assert roles[7] in ("jizai", "solo")


def test_resolve_roles_girls():
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"]["class_name"] = "ガールズ"
    raw["race"]["is_girls"] = True
    raw["lines"] = []
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    roles = resolve_rider_roles(ri, scores)
    assert all(r == "girls" for r in roles.values())
    assert len(roles) == len(ri.riders)


def test_resolve_roles_4th_or_later():
    """4車以上のラインで4番手以降が `fourth` 扱いになる。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    # 4車ライン: [5, 1, 3, 7] とする
    raw["lines"] = [
        {"line_name": "拡張ライン", "cars": [5, 1, 3, 7], "description": "5-1-3-7"},
        {"line_name": "中部中国", "cars": [2, 6, 4], "description": "2-6-4"},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    roles = resolve_rider_roles(ri, scores)
    assert roles[5] == "line_leader"
    assert roles[1] == "second"
    assert roles[3] == "third"
    assert roles[7] == "fourth"


def test_resolve_roles_no_lines_uses_tags():
    """ライン情報無しの場合は style_tags から推定。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["lines"] = []
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    roles = resolve_rider_roles(ri, scores)
    # 7番は style_tags=["単騎", "自在"] → jizai 扱い（自在優先）
    assert roles[7] == "jizai"
    # 1番（style_tags=["番手","差し"]）はラインがないので solo
    assert roles[1] == "solo"


# ---------------------------------------------------------------------------
# apply_bank_signals
# ---------------------------------------------------------------------------


def test_bank_signals_500_boosts_bantan_and_third():
    ri = _build_input(bank_length=500)
    scores = compute_scores(ri)
    by_car = {s.car_no: s for s in scores}
    before = {c: (s.win_score, s.second_score, s.third_score) for c, s in by_car.items()}
    apply_bank_signals(scores, ri)
    after = {s.car_no: (s.win_score, s.second_score, s.third_score) for s in scores}

    # 1番(本命番手 = second) は win_score 加点
    assert after[1][0] > before[1][0]
    # 3番(本命3番手 = third) は third_score 加点
    assert after[3][2] > before[3][2]
    # 5番(line_leader) は 500バンクでは加点なし（追込タグも無し）
    assert after[5] == before[5]


def test_bank_signals_333_boosts_leader_reduces_makuri():
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"]["bank_length"] = 333
    # 5番に "捲り" タグを追加（既に在る場合あり）
    for r in raw["riders"]:
        if r["car_no"] == 5 and "捲り" not in r["style_tags"]:
            r["style_tags"].append("捲り")
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    by_car = {s.car_no: s for s in scores}
    before_5 = by_car[5].win_score
    before_1 = by_car[1].win_score
    apply_bank_signals(scores, ri)
    after_5 = next(s.win_score for s in scores if s.car_no == 5)
    after_1 = next(s.win_score for s in scores if s.car_no == 1)
    # 5番(line_leader) は 333バンクで前々加点
    assert after_5 > before_5
    # 1番(second) も加点
    assert after_1 > before_1


def test_bank_signals_sashi_favor_bantan():
    ri = _build_input(bank_style="差し有利")
    scores = compute_scores(ri)
    before_1 = next(s.win_score for s in scores if s.car_no == 1)
    apply_bank_signals(scores, ri)
    after_1 = next(s.win_score for s in scores if s.car_no == 1)
    assert after_1 > before_1


def test_bank_signals_senko_favor_leader():
    ri = _build_input(bank_style="先行有利")
    scores = compute_scores(ri)
    before_5 = next(s.win_score for s in scores if s.car_no == 5)
    apply_bank_signals(scores, ri)
    after_5 = next(s.win_score for s in scores if s.car_no == 5)
    assert after_5 > before_5


def test_bank_signals_no_op_when_absent():
    """bank_length も bank_style も無いとき、何も変わらない。"""
    ri = _build_input()  # 既定: bank_length None
    scores = compute_scores(ri)
    snapshot = [(s.car_no, s.win_score, s.second_score, s.third_score) for s in scores]
    apply_bank_signals(scores, ri)
    after = [(s.car_no, s.win_score, s.second_score, s.third_score) for s in scores]
    assert snapshot == after


def test_bank_signals_falls_back_to_bank_note_keyword():
    """bank_style 未指定でも bank_note に '差し有利' を含めば反応する。"""
    ri = _build_input(bank_note="差し有利バンク")
    scores = compute_scores(ri)
    before_1 = next(s.win_score for s in scores if s.car_no == 1)
    apply_bank_signals(scores, ri)
    after_1 = next(s.win_score for s in scores if s.car_no == 1)
    assert after_1 > before_1


# ---------------------------------------------------------------------------
# RaceInfo model
# ---------------------------------------------------------------------------


def test_raceinfo_bank_length_validation():
    """bank_length は 200〜600 の範囲外で弾く。"""
    from pydantic import ValidationError
    from app.models import RaceInfo
    from datetime import date

    # 範囲内はOK
    info = RaceInfo(
        race_id="t", date=date(2026, 1, 1), venue="X", race_no=1,
        class_name="A級", bank_length=400,
    )
    assert info.bank_length == 400

    # 範囲外はエラー
    with pytest.raises(ValidationError):
        RaceInfo(
            race_id="t", date=date(2026, 1, 1), venue="X", race_no=1,
            class_name="A級", bank_length=100,
        )
    with pytest.raises(ValidationError):
        RaceInfo(
            race_id="t", date=date(2026, 1, 1), venue="X", race_no=1,
            class_name="A級", bank_length=999,
        )


# ---------------------------------------------------------------------------
# CLI prepare-json
# ---------------------------------------------------------------------------


def test_cli_prepare_json_bank_style(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "manual",
            "--fallback-input", str(SAMPLE),
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-results",
            "--bank-length", "500",
            "--bank-style", "差し有利",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["race"]["bank_length"] == 500
    assert raw["race"]["bank_style"] == "差し有利"


def test_cli_prepare_json_unknown_bank_style_warns(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "manual",
            "--fallback-input", str(SAMPLE),
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-results",
            "--bank-style", "謎の特性",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "謎の特性" in text
    assert "自由文" in text or "想定値" in text


def test_cli_predict_uses_bank_signals(tmp_path: Path):
    """500バンクを指定すると番手(1)のスコアが上がり、印に影響することを確認。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"]["bank_length"] = 500
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        [
            "--db", str(db),
            "predict",
            "--input", str(inp),
            "--no-save",
            "--no-reflections",
            "--provider", "mock",
        ],
    )
    assert result.exit_code == 0, result.output
    # 補正により reasons に 500バンク 由来のメッセージが含まれる（保存JSONには出ない場合あり）
    # 出力本文には影響するが、ここでは exit_code 0 と "本線" 含有のみ確認
    assert "本線" in result.output
