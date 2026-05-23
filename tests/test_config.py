from __future__ import annotations

import os
from pathlib import Path

from app.config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    load_dotenv,
    load_settings,
)


def test_load_dotenv_parses_kv(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# コメント行\n"
        "LLM_PROVIDER=openai\n"
        'OPENAI_API_KEY="sk-test"\n'
        "OPENAI_MODEL=gpt-test\n"
        "\n",
        encoding="utf-8",
    )
    for key in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    loaded = load_dotenv(env)
    assert loaded["LLM_PROVIDER"] == "openai"
    assert os.environ["OPENAI_API_KEY"] == "sk-test"
    assert loaded["OPENAI_MODEL"] == "gpt-test"


def test_load_settings_defaults(monkeypatch, tmp_path: Path):
    # 既存環境変数をクリア
    for key in (
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    # 存在しないパスを渡す
    s = load_settings(dotenv_path=tmp_path / "nope.env")
    assert s.provider == "mock"
    assert s.openai_api_key is None
    assert s.openai_model == DEFAULT_OPENAI_MODEL
    assert s.anthropic_model == DEFAULT_ANTHROPIC_MODEL


def test_load_settings_override_provider(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    s = load_settings(
        dotenv_path=tmp_path / "nope.env", override_provider="anthropic"
    )
    assert s.provider == "anthropic"


def test_load_settings_uppercases_consistently(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    s = load_settings(dotenv_path=tmp_path / "nope.env")
    assert s.provider == "openai"
