"""result コマンドの簡略化テスト。

簡略化された呼び出しパターンを検証:
- positional 引数で結果を渡す: `result 5-1-3`
- --input から race_id を自動抽出
- 何も指定しなければ直近の予想を自動使用
- 既存の --race-id / --result 形式は引き続き動く（後方互換）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.models import RaceInput
from app.storage import Storage


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def _setup_db_with_prediction(tmp_path: Path) -> Path:
    """サンプルJSONから予想を1件保存した DB を作る。"""
    db = tmp_path / "t.db"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--db", str(db), "predict", "--input", str(SAMPLE), "--no-reflections", "--provider", "mock"],
    )
    assert result.exit_code == 0, result.output
    return db


def test_result_positional(tmp_path: Path):
    """`result 5-1-3` の positional 引数で動くこと（race_idは直近予想から自動）。"""
    db = _setup_db_with_prediction(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--db", str(db), "result", "5-1-3"]
    )
    assert result.exit_code == 0, result.output
    assert "結果を保存しました" in result.output
    assert "20260522-ogaki-1 → 5-1-3" in result.output
    # 直近予想を使った案内
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "直近の予想" in text


def test_result_with_input_extracts_race_id(tmp_path: Path):
    """--input から race_id が自動抽出されること。"""
    db = _setup_db_with_prediction(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--db", str(db),
            "result", "5-1-3",
            "--input", str(SAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "race_id を抽出" in text
    assert "20260522-ogaki-1" in result.output


def test_result_backward_compat_old_args(tmp_path: Path):
    """旧来の --race-id / --result 指定が引き続き動くこと（後方互換）。"""
    db = _setup_db_with_prediction(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--db", str(db),
            "result",
            "--race-id", "20260522-ogaki-1",
            "--result", "5-1-3",
            "--input", str(SAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "20260522-ogaki-1 → 5-1-3" in result.output


def test_result_no_args_no_prediction_fails(tmp_path: Path):
    """予想が無い状態で result を呼ぶと日本語エラー。"""
    db = tmp_path / "t.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db), "result", "5-1-3"])
    assert result.exit_code != 0
    assert "race_id" in result.output and "predict" in result.output


def test_result_missing_result_fails(tmp_path: Path):
    """結果が指定されていないとき日本語エラー。"""
    db = _setup_db_with_prediction(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db), "result"])
    assert result.exit_code != 0
    assert "結果が指定されていません" in result.output


def test_result_positional_takes_precedence_over_flag(tmp_path: Path):
    """positional 引数が --result フラグより優先される。"""
    db = _setup_db_with_prediction(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--db", str(db),
            "result",
            "5-1-3",
            "--result", "9-9-9",
        ],
    )
    assert result.exit_code == 0, result.output
    # positional の 5-1-3 が採用される
    assert "→ 5-1-3" in result.output
    assert "→ 9-9-9" not in result.output


def test_result_input_with_missing_race_id_fails(tmp_path: Path):
    """--input のJSONに race.race_id が無いとエラー。"""
    db = _setup_db_with_prediction(tmp_path)
    bad = tmp_path / "no_race_id.json"
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"].pop("race_id", None)
    bad.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--db", str(db), "result", "5-1-3", "--input", str(bad)],
    )
    # race_id が抽出できない → スキーマ違反でエラー（または直近にフォールバックしない仕様）
    # 実装上は read で race_id が欠けると ClickException
    assert result.exit_code != 0


def test_result_latest_uses_most_recent(tmp_path: Path):
    """複数の予想がある場合、result は最も新しい予想を使う。"""
    db = tmp_path / "t.db"
    runner = CliRunner()

    # 1件目: race_sample.json
    runner.invoke(cli, ["--db", str(db), "predict", "--input", str(SAMPLE), "--no-reflections", "--provider", "mock"])

    # 2件目: 別の race_id で予想を保存
    other = tmp_path / "other.json"
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"]["race_id"] = "20260523-松山-3"
    raw["race"]["venue"] = "松山"
    raw["race"]["date"] = "2026-05-23"
    raw["race"]["race_no"] = 3
    other.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    runner.invoke(cli, ["--db", str(db), "predict", "--input", str(other), "--no-reflections", "--provider", "mock"])

    # 直近は other (松山-3)
    result = runner.invoke(cli, ["--db", str(db), "result", "1-2-3"])
    assert result.exit_code == 0, result.output
    assert "20260523-松山-3 → 1-2-3" in result.output
