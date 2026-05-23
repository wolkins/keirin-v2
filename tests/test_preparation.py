"""prepare_race_input と prepare-json CLI のテスト。

実ネットワーク通信は一切しない。HTTP は session を MagicMock に差し替える。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.fetchers import FileCache, HttpClient, RateLimiter
from app.models import RaceInput
from app.preparation import PreparationError, prepare_race_input


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RACE_CARD_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")
RACE_CARD_EMPTY_HTML = (FIXTURES / "kdreams_race_card_empty.html").read_text(encoding="utf-8")
RESULTS_HTML = (FIXTURES / "kdreams_results_sample.html").read_text(encoding="utf-8")
RESULTS_EMPTY_HTML = (FIXTURES / "kdreams_results_empty.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTPモックヘルパ
# ---------------------------------------------------------------------------


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def _route_session(*, race_card: str, results: str | None) -> MagicMock:
    """URL に応じて出走表 / 結果 のHTMLを返すモックsessionを作る。

    results=None なら結果取得は 500 を返す（失敗扱い）。
    """
    session = MagicMock()

    def _get(url: str, **kwargs):
        if "racecard" in url:
            return _make_response(200, race_card)
        if "raceresult" in url:
            if results is None:
                return _make_response(500, "")
            return _make_response(200, results)
        return _make_response(404, "")

    session.get.side_effect = _get
    return session


def _make_http_client(tmp_path: Path, session: MagicMock) -> HttpClient:
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    return HttpClient(cache=cache, rate_limiter=rl, session=session)


def _collect_warnings():
    bucket: list[str] = []

    def warn(msg: str) -> None:
        bucket.append(msg)

    return bucket, warn


# ---------------------------------------------------------------------------
# prepare_race_input 単体
# ---------------------------------------------------------------------------


def test_prepare_basic_kdreams_with_results(tmp_path: Path):
    session = _route_session(race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    client = _make_http_client(tmp_path, session)
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=6,
        http_client=client,
        weather="曇り",
        rain=0.0,
        wind_direction="西",
        wind_speed=5.0,
    )
    assert isinstance(ri, RaceInput)
    assert ri.race.venue == "大垣"
    assert ri.race.race_no == 6
    assert ri.weather is not None
    assert ri.weather.condition == "曇り"
    assert ri.weather.wind_direction == "西"
    assert ri.weather.wind_speed_mps == 5.0
    # 出走表
    assert len(ri.riders) == 7
    # 結果（race_no=6 より前のみ: 1, 2, 4）
    nos = sorted(r.race_no for r in ri.recent_results)
    assert nos == [1, 2, 4]
    # HTTP 呼び出し回数: race_card + racedetail(補完) + results = 3 回
    # （racedetail はモックが 404 を返すので補完は黙って失敗するが、呼び出しは発生）
    assert session.get.call_count >= 2


def test_prepare_no_results_flag(tmp_path: Path):
    session = _route_session(race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    client = _make_http_client(tmp_path, session)
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=6,
        http_client=client,
        include_results=False,
    )
    assert ri.recent_results == []
    # 結果ページへの通信は発生しない
    urls = [call.args[0] for call in session.get.call_args_list]
    assert all(any(k in u for k in ("racecard", "racedetail", "yen-joy")) for u in urls)


def test_prepare_results_filtered_by_race_no_strict(tmp_path: Path):
    """race_no=2 のとき、結果は 1Rのみ。"""
    session = _route_session(race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    client = _make_http_client(tmp_path, session)
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=2,
        http_client=client,
    )
    assert sorted(r.race_no for r in ri.recent_results) == [1]


def test_prepare_results_race_no_pinpoint(tmp_path: Path):
    """--results-race-no を指定すると、そのレースだけ取り込む。"""
    session = _route_session(race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    client = _make_http_client(tmp_path, session)
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=8,
        http_client=client,
        results_race_no=4,
    )
    assert len(ri.recent_results) == 1
    assert ri.recent_results[0].race_no == 4
    assert ri.recent_results[0].result == "2-4-6"


def test_prepare_max_results_limits(tmp_path: Path):
    session = _route_session(race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    client = _make_http_client(tmp_path, session)
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=12,
        http_client=client,
        max_results=2,
    )
    assert len(ri.recent_results) == 2


def test_prepare_results_failure_keeps_card(tmp_path: Path):
    """結果取得が失敗しても、出走表側は維持して RaceInput を返す。"""
    session = _route_session(race_card=RACE_CARD_HTML, results=None)
    client = _make_http_client(tmp_path, session)
    warns, warn = _collect_warnings()
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=6,
        http_client=client,
        warn=warn,
    )
    # 出走表は使えている
    assert len(ri.riders) == 7
    # 結果は空
    assert ri.recent_results == []
    # 警告が出ている
    assert any("結果の取得に失敗" in m for m in warns)


def test_prepare_card_failure_uses_fallback(tmp_path: Path):
    """race_card 取得失敗 → fallback-input の手入力JSONを使う。"""
    session = _route_session(race_card=RACE_CARD_EMPTY_HTML, results=RESULTS_HTML)
    client = _make_http_client(tmp_path, session)
    warns, warn = _collect_warnings()
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=1,
        http_client=client,
        fallback_input=SAMPLE,
        warn=warn,
    )
    # ManualFetcher で取得できている（サンプルの race_id）
    assert ri.race.race_id == "20260522-ogaki-1"
    assert any("フォールバック" in m for m in warns)


def test_prepare_both_card_and_fallback_fail(tmp_path: Path):
    """fallback も失敗するなら PreparationError。"""
    session = _route_session(race_card=RACE_CARD_EMPTY_HTML, results=RESULTS_HTML)
    client = _make_http_client(tmp_path, session)
    bad_fb = tmp_path / "nope.json"  # 存在しない
    with pytest.raises(PreparationError) as excinfo:
        prepare_race_input(
            source="kdreams",
            venue="大垣",
            date_str="2026-05-22",
            race_no=1,
            http_client=client,
            fallback_input=bad_fb,
        )
    assert "フォールバック" in str(excinfo.value)


def test_prepare_card_failure_no_fallback_raises(tmp_path: Path):
    session = _route_session(race_card=RACE_CARD_EMPTY_HTML, results=RESULTS_HTML)
    client = _make_http_client(tmp_path, session)
    with pytest.raises(PreparationError) as excinfo:
        prepare_race_input(
            source="kdreams",
            venue="大垣",
            date_str="2026-05-22",
            race_no=1,
            http_client=client,
        )
    assert "出走表" in str(excinfo.value)


def test_prepare_invalid_source():
    with pytest.raises(PreparationError) as excinfo:
        prepare_race_input(
            source="bogus",
            venue="大垣",
            date_str="2026-05-22",
            race_no=1,
        )
    assert "未対応" in str(excinfo.value)


def test_prepare_invalid_date():
    with pytest.raises(PreparationError):
        prepare_race_input(
            source="manual",
            venue="大垣",
            date_str="2026/05/22",
            race_no=1,
            fallback_input=SAMPLE,
        )


def test_prepare_invalid_race_no():
    with pytest.raises(PreparationError):
        prepare_race_input(
            source="manual",
            venue="大垣",
            date_str="2026-05-22",
            race_no=99,
            fallback_input=SAMPLE,
        )


def test_prepare_bank_note_appended(tmp_path: Path):
    """既存 bank_note があってもユーザー指定は別途追記される。"""
    session = _route_session(race_card=RACE_CARD_HTML, results=None)
    client = _make_http_client(tmp_path, session)
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=1,
        http_client=client,
        bank_note="メモ追加",
        include_results=False,
    )
    # race_card 由来の bank_note はない（kdreams parser は None で返す）→ 上書き
    assert ri.race.bank_note == "メモ追加"


def test_prepare_weather_only_partial(tmp_path: Path):
    """wind_speed だけ指定でも weather が構築される。"""
    session = _route_session(race_card=RACE_CARD_HTML, results=None)
    client = _make_http_client(tmp_path, session)
    ri = prepare_race_input(
        source="kdreams",
        venue="大垣",
        date_str="2026-05-22",
        race_no=1,
        http_client=client,
        wind_speed=3.5,
        include_results=False,
    )
    assert ri.weather is not None
    assert ri.weather.wind_speed_mps == 3.5
    # condition 未指定 → "不明"
    assert ri.weather.condition == "不明"


# ---------------------------------------------------------------------------
# CLI prepare-json
# ---------------------------------------------------------------------------


def _patch_route_session(monkeypatch, *, race_card: str, results: str | None) -> MagicMock:
    """HttpClient._get_session() が返すセッションを差し替える。"""
    session = _route_session(race_card=race_card, results=results)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    return session


def test_cli_prepare_json_kdreams_full_flow(tmp_path: Path, monkeypatch):
    session = _patch_route_session(monkeypatch, race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "6",
            "--weather", "曇り",
            "--rain", "0",
            "--wind-direction", "西",
            "--wind-speed", "5.0",
            "--bank-length", "400",
            "--no-odds",
            "--weather-source", "manual",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.venue == "大垣"
    assert ri.race.race_no == 6
    assert ri.weather and ri.weather.wind_speed_mps == 5.0
    assert "周長400m" in (ri.race.bank_note or "")
    assert sorted(r.race_no for r in ri.recent_results) == [1, 2, 4]
    # race_card + racedetail(補完試行) + results 等で >= 2
    assert session.get.call_count >= 2


def test_cli_prepare_json_then_predict(tmp_path: Path, monkeypatch):
    """prepare-json → predict 連携。"""
    _patch_route_session(monkeypatch, race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    runner = CliRunner()
    out = tmp_path / "p.json"
    r1 = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "6",
            "--weather", "曇り",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
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


def test_cli_prepare_json_no_results(tmp_path: Path, monkeypatch):
    session = _patch_route_session(monkeypatch, race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--no-odds",
            "--weather-source", "manual",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "6",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["recent_results"] == []
    # 結果ページへの通信は発生しない
    urls = [call.args[0] for call in session.get.call_args_list]
    assert all(any(k in u for k in ("racecard", "racedetail", "yen-joy")) for u in urls)


def test_cli_prepare_json_max_results(tmp_path: Path, monkeypatch):
    _patch_route_session(monkeypatch, race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "12",
            "--max-results", "2",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert len(raw["recent_results"]) == 2


def test_cli_prepare_json_results_failure_keeps_card(tmp_path: Path, monkeypatch):
    """結果取得が失敗しても race_card は使える。"""
    _patch_route_session(monkeypatch, race_card=RACE_CARD_HTML, results=None)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "6",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert len(ri.riders) == 7
    assert ri.recent_results == []


def test_cli_prepare_json_fallback_input(tmp_path: Path, monkeypatch):
    """出走表取得失敗 → fallback-input の手入力JSONで RaceInput を作る。"""
    _patch_route_session(monkeypatch, race_card=RACE_CARD_EMPTY_HTML, results=RESULTS_HTML)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--fallback-input", str(SAMPLE),
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output


def test_cli_prepare_json_invalid_date(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "manual",
            "--venue", "大垣",
            "--date", "2026/05/22",
            "--race-no", "1",
            "--fallback-input", str(SAMPLE),
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in result.output


def test_cli_prepare_json_invalid_race_no(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "manual",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "99",
            "--fallback-input", str(SAMPLE),
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "1〜12" in result.output


def test_cli_prepare_json_unsupported_source(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "bogus",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "未対応" in result.output


def test_cli_prepare_json_no_html_leak(tmp_path: Path, monkeypatch):
    """生HTMLが出力JSONに混入しないこと。"""
    _patch_route_session(monkeypatch, race_card=RACE_CARD_HTML, results=RESULTS_HTML)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "6",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0
    body = out.read_text(encoding="utf-8")
    # HTMLタグ・href 等が含まれていない
    for tag in ("<table", "<tr", "<td", "<div", "<span", "<html", "href"):
        assert tag not in body


def test_cli_prepare_json_negative_bank_length(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "manual",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--fallback-input", str(SAMPLE),
            "--bank-length", "-1",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "bank-length" in result.output or "正の整数" in result.output
