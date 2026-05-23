"""pytest 共通フィクスチャ。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput


SAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


@pytest.fixture
def sample_input() -> RaceInput:
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    return RaceInput.model_validate(raw)


@pytest.fixture
def girls_input(sample_input) -> RaceInput:
    """サンプルをガールズに改造したフィクスチャ。"""
    data = sample_input.model_dump()
    data["race"]["class_name"] = "ガールズ"
    data["race"]["is_girls"] = True
    data["lines"] = []
    for r in data["riders"]:
        # 適当にスタイルタグを付与
        if r["car_no"] in (1, 5):
            r["style_tags"] = ["自力"]
        elif r["car_no"] in (2, 6):
            r["style_tags"] = ["追走"]
    return RaceInput.model_validate(data)
