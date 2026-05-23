"""prepare-json の最小フラグモードのテスト。

仕様: `--venue --date` (とラウンド) だけで動くシンプル化。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.cli import _auto_out_path, cli
from app.fetchers import HttpClient
from app.models import RaceInput


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"
RACE_CARD_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")
RESULTS_HTML = (FIXTURES / "kdreams_results_sample.html").read_text(encoding="utf-8")
TRIFECTA_HTML = (FIXTURES / "oddspark_trifecta_sample.html").read_text(encoding="utf-8")
TRIO_HTML = (FIXTURES / "oddspark_trio_sample.html").read_text(encoding="utf-8")
EXACTA_HTML = (FIXTURES / "oddspark_exacta_sample.html").read_text(encoding="utf-8")


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def _open_meteo_payload() -> dict:
    times = [f"2026-05-22T{h:02d}:00" for h in range(24)]
    return {
        "hourly": {
            "time": times,
            "weather_code": [0] * 24,
            "precipitation": [0.0] * 24,
            "wind_speed_10m": [2.5] * 24,
            "wind_direction_10m": [225.0] * 24,
            "temperature_2m": [12.0] * 24,
        }
    }


def _patch_full_session(monkeypatch) -> MagicMock:
    """racecard / raceresult / oddspark / open-meteo を出し分ける mock。"""
    session = MagicMock()

    def _get(url: str, **kwargs):
        if "racecard" in url:
            return _make_response(200, RACE_CARD_HTML)
        if "raceresult" in url:
            return _make_response(200, RESULTS_HTML)
        if "oddspark.com" in url:
            if "betType=9" in url:
                return _make_response(200, TRIFECTA_HTML)
            if "betType=8" in url:
                return _make_response(200, TRIO_HTML)
            if "betType=6" in url:
                return _make_response(200, EXACTA_HTML)
        if "open-meteo.com" in url:
            import json as _json
            return _make_response(200, _json.dumps(_open_meteo_payload()))
        return _make_response(404, "")

    session.get.side_effect = _get
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    return session


# ---------------------------------------------------------------------------
# _auto_out_path
# ---------------------------------------------------------------------------


def test_auto_out_path_japanese_venue():
    p = _auto_out_path("大垣", "2026-05-22", 1)
    assert str(p) == "tmp/大垣_2026-05-22_01r.json"


def test_auto_out_path_zero_padded_race_no():
    p = _auto_out_path("平塚", "2026-05-22", 12)
    assert str(p) == "tmp/平塚_2026-05-22_12r.json"


def test_auto_out_path_strips_invalid_chars():
    p = _auto_out_path("Slash/Venue", "2026-05-22", 5)
    # / は除去される
    assert "/" not in str(p.name)


# ---------------------------------------------------------------------------
# 単一レース・最小フラグ
# ---------------------------------------------------------------------------


def test_prepare_json_minimal_single_race(tmp_path: Path, monkeypatch):
    """--venue --date --race-no だけで（既定 source=kdreams, weather=open-meteo,
    odds=on, odds-source=oddspark）動くこと。"""
    _patch_full_session(monkeypatch)
    runner = CliRunner(env={})
    # 出力ディレクトリを tmp_path に向けるため、cwd を tmp_path にする
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    # 自動パスにファイルが書かれている
    expected = tmp_path / "tmp" / "大垣_2026-05-22_01r.json"
    assert expected.exists()
    raw = json.loads(expected.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.venue == "大垣"
    assert ri.race.race_no == 1
    # オッズが入っている（既定 odds=on, source=oddspark）
    assert len(ri.odds) > 0
    # 天候が入っている（既定 weather-source=open-meteo）
    assert ri.weather is not None


# ---------------------------------------------------------------------------
# 全レース一括モード
# ---------------------------------------------------------------------------


def test_prepare_json_all_races_loop(tmp_path: Path, monkeypatch):
    """--race-no 未指定で 1〜12R 一括生成。"""
    _patch_full_session(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    # 1〜12R 分のファイルが生成されている
    out_dir = tmp_path / "tmp"
    files = sorted(out_dir.glob("大垣_2026-05-22_*r.json"))
    # サンプルHTMLは 7車・ある程度のレースまでパースできる前提なので、複数件は確定で出る
    assert len(files) >= 1
    # 案内メッセージ
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "1〜12R を一括生成" in text


def test_prepare_json_all_races_out_path_conflict(tmp_path: Path):
    """--race-no 省略時に --out を指定したらエラー。"""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--out", str(tmp_path / "x.json"),
        ],
    )
    assert result.exit_code != 0
    assert "--out は指定できません" in result.output


def test_prepare_json_default_odds_source_is_oddspark(tmp_path: Path, monkeypatch):
    """既定では --odds-source=oddspark で取得が走る。"""
    session = _patch_full_session(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    runner.invoke(
        cli,
        [
            "prepare-json",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-results",
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    urls = [c.args[0] for c in session.get.call_args_list]
    # オッズパーク URL が叩かれている（既定 odds-source=oddspark）
    assert any("oddspark.com" in u for u in urls)


def test_prepare_json_default_weather_source_is_open_meteo(tmp_path: Path, monkeypatch):
    """既定では --weather-source=open-meteo で天候取得が走る。"""
    session = _patch_full_session(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    runner.invoke(
        cli,
        [
            "prepare-json",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-odds",
            "--no-results",
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    urls = [c.args[0] for c in session.get.call_args_list]
    assert any("open-meteo.com" in u for u in urls)
