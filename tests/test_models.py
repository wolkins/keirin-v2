from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import Line, RaceInput


SAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def test_sample_json_loads():
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.race_id == "20260522-ogaki-1"
    assert ri.race.venue == "大垣"
    assert len(ri.riders) == 7
    assert any(line.line_name == "九州" for line in ri.lines)


def test_line_unique_cars():
    with pytest.raises(ValidationError):
        Line(line_name="dup", cars=[1, 1, 2])


def test_girls_detection_from_class_name():
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    raw["race"]["class_name"] = "ガールズ"
    raw["race"]["is_girls"] = None
    raw["lines"] = []
    ri = RaceInput.model_validate(raw)
    assert ri.race.resolved_is_girls() is True


def test_rider_by_car(sample_input):
    r = sample_input.rider_by_car(5)
    assert r is not None
    assert r.name.startswith("池部")
