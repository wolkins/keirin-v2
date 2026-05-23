"""反省ログの自動注入機能のテスト。

storage.get_relevant_reflections の関連度順序、
scoring の reflection_bonus 反映、
prompt_builder の反省セクション、CLIフラグの動作を検証する。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli, run_prediction
from app.models import RaceInput, Reflection
from app.prompt_builder import build_full_prompt, build_reflections_section
from app.scoring import (
    apply_reflection_signals,
    build_candidate_bets,
    build_line_position_map,
    compute_scores,
    gami_inflation_from_reflections,
)
from app.storage import Storage


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _make_reflection(
    *,
    race_id: str = "20260520-ogaki-9",
    venue: str = "大垣",
    race_no: int = 9,
    is_girls: bool = False,
    class_name: str = "A級一般",
    weather: str = "曇り",
    wind: float = 5.0,
    rain: float = 0.0,
    actual: str = "5-2-3",
    honsen: list[str] | None = None,
    categories: list[str] | None = None,
    note: str = "",
) -> Reflection:
    return Reflection(
        race_id=race_id,
        venue=venue,
        race_no=race_no,
        is_girls=is_girls,
        weather_condition=weather,
        wind_speed_mps=wind,
        rain_mm_per_hour=rain,
        class_name=class_name,
        predicted_honsen=honsen or ["5-1-3"],
        actual_result=actual,
        categories=categories or ["別線番手を軽視"],
        note=note,
    )


def _insert_with_created_at(
    storage: Storage, reflection: Reflection, created_at: str
) -> None:
    """テスト用に created_at を直接指定して挿入する。"""
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute(
            """
            INSERT INTO reflections (
                race_id, venue, race_no, is_girls,
                weather_condition, wind_speed_mps, rain_mm_per_hour,
                actual_result, categories_json, note, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reflection.race_id,
                reflection.venue,
                reflection.race_no,
                1 if reflection.is_girls else 0,
                reflection.weather_condition,
                reflection.wind_speed_mps,
                reflection.rain_mm_per_hour,
                reflection.actual_result,
                "[]",
                reflection.note,
                reflection.model_dump_json(),
                created_at,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# storage.get_relevant_reflections
# ---------------------------------------------------------------------------


def test_get_relevant_reflections_filters_by_girls(
    tmp_path: Path, sample_input: RaceInput
):
    db = tmp_path / "t.db"
    s = Storage(db)
    # ガールズの反省は通常戦予想には含めない
    girls_ref = _make_reflection(
        race_id="g1", is_girls=True, class_name="ガールズ"
    )
    normal_ref = _make_reflection(race_id="n1")
    s.save_reflection(girls_ref)
    s.save_reflection(normal_ref)
    result = s.get_relevant_reflections(sample_input, limit=5)
    ids = [r.race_id for r in result]
    assert "g1" not in ids
    assert "n1" in ids


def test_get_relevant_reflections_ranks_by_venue_and_weather(
    tmp_path: Path, sample_input: RaceInput
):
    """サンプルは 大垣 / 曇り / 西5.0m/s。一致条件が多いほど上位に。"""
    db = tmp_path / "t.db"
    s = Storage(db)
    # 完全一致に近いもの
    s.save_reflection(_make_reflection(race_id="match", venue="大垣", weather="曇り", wind=5.0))
    # 場違い
    s.save_reflection(_make_reflection(race_id="other_venue", venue="松山", weather="曇り", wind=5.0))
    # 天候違い
    s.save_reflection(_make_reflection(race_id="other_weather", venue="大垣", weather="晴れ", wind=0.0))
    result = s.get_relevant_reflections(sample_input, limit=3)
    assert result[0].race_id == "match"
    # other_venue より other_weather が上（場が一致するため）
    ids = [r.race_id for r in result]
    assert ids.index("other_weather") < ids.index("other_venue")


def test_get_relevant_reflections_recency_boost(
    tmp_path: Path, sample_input: RaceInput
):
    db = tmp_path / "t.db"
    s = Storage(db)
    now = datetime(2026, 5, 22, 10, 0, 0)
    recent = _make_reflection(race_id="recent")
    old = _make_reflection(race_id="old")
    _insert_with_created_at(s, recent, "2026-05-22 09:00:00")
    _insert_with_created_at(s, old, "2024-01-01 00:00:00")
    result = s.get_relevant_reflections(sample_input, limit=2, now=now)
    assert result[0].race_id == "recent"


def test_get_relevant_reflections_limit(
    tmp_path: Path, sample_input: RaceInput
):
    db = tmp_path / "t.db"
    s = Storage(db)
    for i in range(8):
        s.save_reflection(_make_reflection(race_id=f"r{i}"))
    result = s.get_relevant_reflections(sample_input, limit=3)
    assert len(result) <= 3


def test_get_relevant_reflections_empty(
    tmp_path: Path, sample_input: RaceInput
):
    db = tmp_path / "t.db"
    s = Storage(db)
    result = s.get_relevant_reflections(sample_input, limit=5)
    assert result == []


# ---------------------------------------------------------------------------
# scoring.apply_reflection_signals
# ---------------------------------------------------------------------------


def test_apply_reflection_bessen_bantan_keizen(sample_input: RaceInput):
    scores = compute_scores(sample_input)
    pos = build_line_position_map(sample_input.lines)
    # 別線番手 = 6番 (中部中国の番手) は同ライン番手とは別
    car6_before = next(s for s in scores if s.car_no == 6)
    base_total = car6_before.total()

    refs = [
        _make_reflection(categories=["別線番手を軽視"]) for _ in range(2)
    ]
    apply_reflection_signals(scores, refs, sample_input)
    car6_after = next(s for s in scores if s.car_no == 6)
    assert car6_after.reflection_bonus > 0
    assert car6_after.total() > base_total
    assert pos[6].is_bantan
    # 理由欄に反省由来の文言が入る
    assert any("過去の反省" in reason for reason in car6_after.reasons)


def test_apply_reflection_third_negligence(sample_input: RaceInput):
    scores = compute_scores(sample_input)
    refs = [
        _make_reflection(categories=["3番手の伸びを軽視"]),
        _make_reflection(categories=["3番手の2着上がりを軽視した"]),
    ]
    apply_reflection_signals(scores, refs, sample_input)
    # 3番(九州3番手), 4番(中部中国3番手) のいずれも third_score が伸びる
    by = {s.car_no: s for s in scores}
    assert by[3].reflection_bonus > 0
    assert by[4].reflection_bonus > 0


def test_apply_reflection_head_overconfidence_reduces_win(sample_input: RaceInput):
    scores = compute_scores(sample_input)
    before5 = next(s for s in scores if s.car_no == 5).win_score
    refs = [_make_reflection(categories=["本命自力の過信"]) for _ in range(2)]
    apply_reflection_signals(scores, refs, sample_input)
    after5 = next(s for s in scores if s.car_no == 5).win_score
    assert after5 < before5


def test_apply_reflection_empty_is_noop(sample_input: RaceInput):
    scores = compute_scores(sample_input)
    snapshot = [(s.car_no, s.reflection_bonus, s.total()) for s in scores]
    apply_reflection_signals(scores, [], sample_input)
    for s, (car_no, ref_bonus, total) in zip(scores, snapshot):
        assert s.reflection_bonus == ref_bonus
        assert s.total() == pytest.approx(total)


def test_gami_inflation_increases_ana_risk(sample_input: RaceInput):
    scores = compute_scores(sample_input)
    bets_base = build_candidate_bets(sample_input, scores)
    base_ana_risks = [b.gami_risk for b in bets_base["穴"]]
    refs = [
        _make_reflection(categories=["穴を広げすぎてガミリスク増加"]) for _ in range(2)
    ]
    inflation = gami_inflation_from_reflections(refs)
    assert inflation > 0
    bets_inflated = build_candidate_bets(
        sample_input, scores, gami_inflation=inflation
    )
    inflated_ana_risks = [b.gami_risk for b in bets_inflated["穴"]]
    assert sum(inflated_ana_risks) > sum(base_ana_risks)


# ---------------------------------------------------------------------------
# prompt_builder
# ---------------------------------------------------------------------------


def test_reflections_section_with_items():
    refs = [
        _make_reflection(
            race_id="20260520-ogaki-5",
            venue="大宮",
            race_no=5,
            categories=["別線番手を軽視"],
            note="北風4m/sでは別線番手の頭・3着を残す",
        )
    ]
    refs[0].created_at = "2026-05-20 10:00:00"
    section = build_reflections_section(refs)
    assert "過去の反省からの補正" in section
    assert "大宮5R" in section
    assert "別線番手を軽視" in section
    assert "メモ" in section


def test_reflections_section_empty():
    section = build_reflections_section([])
    assert "過去の反省からの補正" in section
    assert "ありません" in section


def test_build_full_prompt_includes_reflections_section(sample_input: RaceInput):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    refs = [_make_reflection(categories=["別線番手を軽視"])]
    prompt = build_full_prompt(sample_input, scores, bets, reflections=refs)
    assert "過去の反省からの補正" in prompt
    assert "別線番手を軽視" in prompt
    # 末尾にJSON応答指示も入る
    assert "JSON" in prompt


def test_build_full_prompt_without_reflections(sample_input: RaceInput):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    prompt = build_full_prompt(sample_input, scores, bets, reflections=[])
    assert "過去の反省からの補正" in prompt
    assert "ありません" in prompt


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_predict_with_no_reflections_flag(tmp_path: Path):
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
            "--no-reflections",
            "--provider",
            "mock",
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "無効化" in text


def test_cli_predict_use_reflections_with_db(tmp_path: Path):
    runner = CliRunner()
    db = tmp_path / "t.db"
    s = Storage(db)
    # 大垣・曇り・5m/sの近い反省を入れる
    s.save_reflection(
        _make_reflection(categories=["別線番手を軽視", "3番手の伸びを軽視"])
    )
    result = runner.invoke(
        cli,
        [
            "--db",
            str(db),
            "predict",
            "--input",
            str(SAMPLE),
            "--no-save",
            "--use-reflections",
            "--reflection-limit",
            "5",
            "--provider",
            "mock",
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "過去の反省を" in text


def test_cli_predict_prompt_out_contains_reflections_when_using_real_provider(
    tmp_path: Path, monkeypatch
):
    """--provider openai (キー未設定→Mockへフォールバック) でも、
    プロンプト書き出しは実LLMフォーマットで作られ、過去反省セクションが含まれる。
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    runner = CliRunner()
    db = tmp_path / "t.db"
    s = Storage(db)
    s.save_reflection(_make_reflection(categories=["別線番手を軽視"]))
    prompt_file = tmp_path / "p.txt"
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
            "openai",
            "--prompt-out",
            str(prompt_file),
        ],
    )
    # フォールバックされるが exit 0
    assert result.exit_code == 0, result.output
    assert prompt_file.exists()
    body = prompt_file.read_text(encoding="utf-8")
    # 実LLM用フォーマットなので JSON 指示と反省セクションが含まれる
    assert "過去の反省からの補正" in body
    assert "別線番手を軽視" in body


def test_run_prediction_works_without_any_reflections(sample_input: RaceInput):
    """反省ログが一切なくても、predict は正常に動く。"""
    pred = run_prediction(sample_input)
    assert pred.race_id == sample_input.race.race_id
    assert pred.honsen  # 本線が出る
