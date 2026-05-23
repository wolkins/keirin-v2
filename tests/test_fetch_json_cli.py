"""CLI fetch-json コマンドのテスト。

実ネットワーク通信は一切行わない。
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from app.cli import cli
from app.models import RaceInput


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def _stderr_or_output(result) -> str:
    err = ""
    try:
        err = result.stderr
    except (AttributeError, ValueError):
        err = ""
    return err or result.output


def test_fetch_json_manual_runs(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "f.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "manual",
            "--input", str(SAMPLE),
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.venue == "大垣"


def test_fetch_json_manual_then_predict(tmp_path: Path):
    """fetch-json manual で出したJSONを predict にそのまま渡せる。"""
    runner = CliRunner()
    out = tmp_path / "f.json"
    r1 = runner.invoke(
        cli,
        ["fetch-json", "--source", "manual", "--input", str(SAMPLE), "--out", str(out)],
    )
    assert r1.exit_code == 0, r1.output
    db = tmp_path / "t.db"
    r2 = runner.invoke(
        cli,
        [
            "--db", str(db),
            "predict",
            "--input", str(out),
            "--no-save",
            "--no-reflections",
            "--provider", "mock",
        ],
    )
    assert r2.exit_code == 0, r2.output
    assert "予想結果" in r2.output


def test_fetch_json_unknown_source(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "x.json"
    result = runner.invoke(
        cli, ["fetch-json", "--source", "nowhere", "--out", str(out)]
    )
    assert result.exit_code != 0
    assert "未知のソース" in result.output


def test_fetch_json_kdreams_race_card_requires_date(tmp_path: Path):
    """race_card 取得には --date が必須。未指定ならフォールバック案内付きで失敗する。"""
    runner = CliRunner()
    out = tmp_path / "k.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--race-no", "1",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    text = result.output + _stderr_or_output(result)
    assert "日付" in text
    # 出力ファイルは作られない
    assert not out.exists()


def test_fetch_json_kdreams_falls_back_to_manual(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "k.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--race-no", "1",
            "--out", str(out),
            "--fallback-input", str(SAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output + _stderr_or_output(result)
    assert "フォールバック" in text
    # 出力は valid RaceInput
    raw = json.loads(out.read_text(encoding="utf-8"))
    RaceInput.model_validate(raw)


def test_fetch_json_oddspark_unimplemented(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "o.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "oddspark",
            "--venue", "大垣",
            "--race-no", "1",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    text = result.output + _stderr_or_output(result)
    assert "未実装" in text


def test_fetch_json_invalid_date(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "x.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "manual",
            "--input", str(SAMPLE),
            "--out", str(out),
            "--date", "2026/05/22",
        ],
    )
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in result.output


def test_fetch_json_no_cache_flag_accepted(tmp_path: Path):
    """--no-cache を指定しても manual はネットワーク使わないので正常終了する。"""
    runner = CliRunner()
    out = tmp_path / "f.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "manual",
            "--input", str(SAMPLE),
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output


def test_fetch_json_manual_without_input(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "f.json"
    result = runner.invoke(
        cli, ["fetch-json", "--source", "manual", "--out", str(out)]
    )
    assert result.exit_code != 0
    text = result.output
    assert "manual" in text.lower() or "input" in text.lower() or "必要" in text
