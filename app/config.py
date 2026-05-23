"""環境変数 / .env からのアプリ設定読み込み。

python-dotenv が利用可能なら使い、無ければ最小限の .env パーサで代用する。
APIキーやモデル名はここに集約し、コードに直書きしない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"

# 既知のプロバイダ名。CLIや load_settings で検証に使う。
SUPPORTED_PROVIDERS = ("mock", "openai", "anthropic")


def _parse_dotenv_line(line: str) -> Optional[tuple[str, str]]:
    """単純な KEY=VALUE 形式をパースする。'#' で始まる行と空行は無視。"""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if "=" not in s:
        return None
    key, _, value = s.partition("=")
    key = key.strip()
    value = value.strip()
    # 周りの引用符を外す
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_dotenv(path: Path | str = ".env", *, override: bool = False) -> dict[str, str]:
    """`.env` を読み込んで os.environ に反映する。読み込めた値を辞書で返す。

    python-dotenv があればそちらを優先。無ければ自前パーサ。
    """
    loaded: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return loaded
    try:
        from dotenv import dotenv_values  # type: ignore

        for k, v in dotenv_values(str(p)).items():
            if v is None:
                continue
            if override or k not in os.environ:
                os.environ[k] = v
            loaded[k] = v
        return loaded
    except ImportError:
        pass

    for line in p.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if parsed is None:
            continue
        k, v = parsed
        if override or k not in os.environ:
            os.environ[k] = v
        loaded[k] = v
    return loaded


@dataclass(frozen=True)
class Settings:
    """LLM関連の解決済み設定。"""

    provider: str
    openai_api_key: Optional[str]
    openai_model: str
    anthropic_api_key: Optional[str]
    anthropic_model: str
    bet_budget: Optional[int] = None

    def api_key_for(self, provider: str) -> Optional[str]:
        if provider == "openai":
            return self.openai_api_key
        if provider == "anthropic":
            return self.anthropic_api_key
        return None

    def model_for(self, provider: str) -> str:
        if provider == "openai":
            return self.openai_model
        if provider == "anthropic":
            return self.anthropic_model
        return ""


def load_settings(
    *,
    dotenv_path: Path | str = ".env",
    override_provider: Optional[str] = None,
) -> Settings:
    """`.env` と環境変数から Settings を構築する。

    `override_provider` が与えられた場合はそれを採用する（CLIの --provider 用）。
    """
    load_dotenv(dotenv_path)
    provider = (
        override_provider
        or os.environ.get("LLM_PROVIDER")
        or "mock"
    ).strip().lower()
    # bet_budget: 整数で 7〜40 のみ受け付ける
    bet_budget_raw = _clean(os.environ.get("BET_BUDGET"))
    bet_budget: Optional[int] = None
    if bet_budget_raw:
        try:
            n = int(bet_budget_raw)
            if 7 <= n <= 40:
                bet_budget = n
        except ValueError:
            pass

    return Settings(
        provider=provider,
        openai_api_key=_clean(os.environ.get("OPENAI_API_KEY")),
        openai_model=(_clean(os.environ.get("OPENAI_MODEL")) or DEFAULT_OPENAI_MODEL),
        anthropic_api_key=_clean(os.environ.get("ANTHROPIC_API_KEY")),
        anthropic_model=(
            _clean(os.environ.get("ANTHROPIC_MODEL")) or DEFAULT_ANTHROPIC_MODEL
        ),
        bet_budget=bet_budget,
    )


def _clean(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = v.strip()
    return s or None
