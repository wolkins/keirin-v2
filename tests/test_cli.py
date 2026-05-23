"""CLIの最低限の挙動テスト。実APIは呼ばない。"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from app.cli import cli


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def test_predict_mock_runs(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        ["--db", str(db), "predict", "--input", str(SAMPLE), "--no-save", "--provider", "mock"],
    )
    assert result.exit_code == 0, result.output
    assert "予想結果" in result.output
    assert "本線" in result.output


def test_predict_unknown_provider_returns_japanese_error(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db),
            "predict",
            "--input",
            str(SAMPLE),
            "--no-save",
            "--provider",
            "nonexistent",
        ],
    )
    assert result.exit_code != 0
    assert "未知のLLMプロバイダ" in result.output


def _stderr_or_output(result) -> str:
    """Clickのバージョン差を吸収して stderr 相当のテキストを取り出す。

    click 8.2+ では runner が stderr を分離して保持する。古い版では output に混ざる。
    """
    err = ""
    try:
        err = result.stderr
    except (AttributeError, ValueError):
        err = ""
    return err or result.output


def test_predict_openai_without_key_falls_back(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    # .env から OPENAI_API_KEY が再注入されないように dotenv 読込を無効化
    # （実 .env に APIキーがある開発環境でもテストが安定するように）
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **kw: {})
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        ["--db", str(db), "predict", "--input", str(SAMPLE), "--no-save", "--provider", "openai"],
    )
    assert result.exit_code == 0, result.output
    text = _stderr_or_output(result)
    # 警告とフォールバックが出ていること
    assert "OPENAI_API_KEY" in text
    assert "Mock" in text
    # 予想本体は標準出力に出る
    assert "予想結果" in result.output


def test_predict_default_provider_uses_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli, ["--db", str(db), "predict", "--input", str(SAMPLE), "--no-save"]
    )
    assert result.exit_code == 0, result.output
    text = _stderr_or_output(result)
    assert "使用プロバイダ: mock" in text


def test_create_json_outputs_template(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "new.json"
    result = runner.invoke(cli, ["create-json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    # ガールズ用
    out_g = tmp_path / "g.json"
    result = runner.invoke(cli, ["create-json", "--out", str(out_g), "--girls"])
    assert result.exit_code == 0, result.output
    text = out_g.read_text(encoding="utf-8")
    assert "ガールズ" in text
