"""Kドリームス出走表ページ取得・パースのテスト。

実ネットワーク通信は一切行わない。HttpClient は session を MagicMock に
差し替え、HTMLは tests/fixtures/ の固定ファイルから読み込む。
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.fetchers import (
    FetchError,
    FileCache,
    HttpClient,
    KDreamsFetcher,
    NotImplementedSource,
    RateLimiter,
)
from app.fetchers.kdreams import build_race_card_url
from app.fetchers.parsers.kdreams_race_card import (
    _extract_line_cars,
    parse_race_card_html,
)
from app.models import RaceInput


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")
EMPTY_HTML = (FIXTURES / "kdreams_race_card_empty.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# URL生成
# ---------------------------------------------------------------------------


def test_build_race_card_url_basic():
    url = build_race_card_url("大垣", "2026-05-22", 1)
    # 実 Kドリームスはパス構造。出走表は開催日単位のページなので race_no はパスに含まれない
    assert url.startswith("https://keirin.kdreams.jp/")
    assert "/ogaki/racecard/" in url
    assert "44202605220100" in url


def test_build_race_card_url_with_date_object():
    url = build_race_card_url("大宮", Date(2026, 5, 22), 8)
    assert "/omiya/racecard/" in url
    assert "25202605220100" in url


def test_build_race_card_url_with_session_no():
    url = build_race_card_url("平塚", "2026-05-22", 4, session_no=2)
    assert "/hiratsuka/racecard/" in url
    assert "35202605220200" in url


def test_build_race_card_url_unknown_venue():
    with pytest.raises(FetchError) as excinfo:
        build_race_card_url("不明", "2026-05-22", 1)
    assert "未対応" in str(excinfo.value)


def test_build_race_card_url_invalid_date():
    with pytest.raises(FetchError) as excinfo:
        build_race_card_url("大垣", "2026/05/22", 1)
    assert "YYYY-MM-DD" in str(excinfo.value)


def test_build_race_card_url_invalid_race_no():
    with pytest.raises(FetchError) as excinfo:
        build_race_card_url("大垣", "2026-05-22", 0)
    assert "1〜12" in str(excinfo.value)
    with pytest.raises(FetchError):
        build_race_card_url("大垣", "2026-05-22", 13)


def test_build_race_card_url_non_int_race_no():
    with pytest.raises(FetchError):
        build_race_card_url("大垣", "2026-05-22", "abc")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# パーサ
# ---------------------------------------------------------------------------


def test_parse_race_card_basic_structure():
    d = parse_race_card_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=1
    )
    # race ブロック
    assert d["race"]["race_id"] == "20260522-大垣-1"
    assert d["race"]["venue"] == "大垣"
    assert d["race"]["race_no"] == 1
    assert d["race"]["date"] == "2026-05-22"
    assert d["race"]["class_name"] == "A級一般"
    assert d["race"]["start_time"] == "10:53"
    # riders
    assert len(d["riders"]) == 7
    cars = [r["car_no"] for r in d["riders"]]
    assert cars == [1, 2, 3, 4, 5, 6, 7]


def test_parse_race_card_returns_race_input_validatable():
    d = parse_race_card_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=1
    )
    ri = RaceInput.model_validate(d)
    assert ri.race.race_id == "20260522-大垣-1"
    assert len(ri.riders) == 7


def test_parse_race_card_rider_fields():
    d = parse_race_card_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=1
    )
    by_car = {r["car_no"]: r for r in d["riders"]}
    # 1番 楢原
    assert by_car[1]["name"] == "楢原悠斗"
    assert by_car[1]["score"] == 83.20
    assert by_car[1]["b_count"] == 1
    assert by_car[1]["sashi"] == 4
    assert by_car[1]["mark"] == 5
    assert by_car[1]["comment"] == "番手"
    assert by_car[1]["recent_summary"] == "前節2-3-2着"


def test_parse_race_card_full_width_digits_normalized():
    """6番 永井: 全角数字 ７９．８０ などが半角に正規化される。"""
    d = parse_race_card_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=1
    )
    by_car = {r["car_no"]: r for r in d["riders"]}
    assert by_car[6]["score"] == 79.8
    assert by_car[6]["b_count"] == 1
    assert by_car[6]["sashi"] == 3


def test_parse_race_card_missing_score_becomes_zero():
    """4番 山根: score が '-' なら 0.0、整数欄も 0。"""
    d = parse_race_card_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=1
    )
    by_car = {r["car_no"]: r for r in d["riders"]}
    assert by_car[4]["score"] == 0.0
    assert by_car[4]["b_count"] == 0
    assert by_car[4]["nige"] == 0


def test_parse_race_card_empty_comment_is_none():
    """7番 白井: comment 空文字 → None。"""
    d = parse_race_card_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=1
    )
    by_car = {r["car_no"]: r for r in d["riders"]}
    assert by_car[7]["comment"] is None


def test_parse_race_card_lines():
    d = parse_race_card_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=1
    )
    line_specs = [(l["line_name"], l["cars"]) for l in d["lines"]]
    assert line_specs == [
        ("九州", [5, 1, 3]),
        ("中部中国", [2, 6, 4]),
        ("単騎", [7]),
    ]


def test_parse_race_card_empty_html_raises():
    with pytest.raises(FetchError):
        parse_race_card_html("", venue="大垣", date_str="2026-05-22", race_no=1)
    with pytest.raises(FetchError):
        parse_race_card_html("  ", venue="大垣", date_str="2026-05-22", race_no=1)


def test_parse_race_card_no_riders_raises():
    with pytest.raises(FetchError) as excinfo:
        parse_race_card_html(
            EMPTY_HTML, venue="大垣", date_str="2026-05-22", race_no=1
        )
    assert "選手" in str(excinfo.value)


def test_extract_line_cars_helper():
    assert _extract_line_cars("⑤池部－①楢原－③平") == [5, 1, 3]
    assert _extract_line_cars("⑦単騎") == [7]
    assert _extract_line_cars("②夏目—⑥永井—④山根") == [2, 6, 4]
    assert _extract_line_cars("") == []
    # 文字列内の重複は dedupe
    assert _extract_line_cars("①①②") == [1, 2]


# ---------------------------------------------------------------------------
# KDreamsFetcher.fetch_race_card
# ---------------------------------------------------------------------------


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def _make_http_client(tmp_path: Path, response_text: str) -> tuple[HttpClient, MagicMock]:
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    session = MagicMock()
    session.get.return_value = _make_response(200, response_text)
    client = HttpClient(cache=cache, rate_limiter=rl, session=session)
    return client, session


def test_fetcher_race_card_calls_http_get_with_correct_url(tmp_path: Path):
    client, session = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    data = fetcher.fetch_race_card(
        venue="大垣", date=Date(2026, 5, 22), race_no=1
    )
    assert session.get.call_count == 1
    called_url = session.get.call_args.args[0]
    assert "/ogaki/racecard/" in called_url
    assert "44202605220100" in called_url
    # User-Agent ヘッダ付与
    headers = session.get.call_args.kwargs.get("headers", {})
    assert "User-Agent" in headers
    # RaceInput に通る
    RaceInput.model_validate(data)


def test_fetcher_race_card_does_not_leak_raw_html(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    data = fetcher.fetch_race_card(
        venue="大垣", date=Date(2026, 5, 22), race_no=1
    )
    serialized = json.dumps(data, ensure_ascii=False, default=str)
    # HTMLタグ / href / table 等が混入していない
    assert "<table" not in serialized
    assert "<tr" not in serialized
    assert "<td" not in serialized
    assert "<div" not in serialized
    assert "href" not in serialized


def test_fetcher_race_card_without_http_client_raises():
    fetcher = KDreamsFetcher(http_client=None)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_race_card(venue="大垣", date=Date(2026, 5, 22), race_no=1)
    assert "HttpClient" in str(excinfo.value)


def test_fetcher_race_card_without_venue_raises(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_race_card(venue=None, date=Date(2026, 5, 22), race_no=1)
    assert "場名" in str(excinfo.value)


def test_fetcher_race_card_without_date_raises(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_race_card(venue="大垣", date=None, race_no=1)
    assert "日付" in str(excinfo.value)


def test_fetcher_race_card_without_race_no_raises(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_race_card(venue="大垣", date=Date(2026, 5, 22), race_no=None)
    assert "レース番号" in str(excinfo.value)


def test_fetcher_race_card_invalid_race_no(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_race_card(venue="大垣", date=Date(2026, 5, 22), race_no=99)
    assert "1〜12" in str(excinfo.value)


def test_fetcher_race_card_unsupported_venue(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_race_card(
            venue="存在しない場", date=Date(2026, 5, 22), race_no=1
        )
    assert "未対応" in str(excinfo.value)


def test_fetcher_race_card_propagates_http_error(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    session = MagicMock()
    session.get.return_value = _make_response(503, "boom")
    client = HttpClient(cache=cache, rate_limiter=rl, session=session)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_race_card(venue="大垣", date=Date(2026, 5, 22), race_no=1)
    assert "503" in str(excinfo.value)


def test_fetcher_race_card_propagates_empty_table(tmp_path: Path):
    """選手が0件のHTMLを返すサイト変更を想定。"""
    client, _ = _make_http_client(tmp_path, EMPTY_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_race_card(venue="大垣", date=Date(2026, 5, 22), race_no=1)
    assert "選手" in str(excinfo.value)


def test_fetcher_other_methods_still_unimplemented(tmp_path: Path):
    """fetch_venue_trend のみ未実装。fetch_odds は別フェーズで実装済み。"""
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(NotImplementedSource):
        fetcher.fetch_venue_trend(venue="大垣")


# ---------------------------------------------------------------------------
# CLI fetch-json --kind race_card
# ---------------------------------------------------------------------------


def _patch_http_session(monkeypatch, response_text: str) -> MagicMock:
    session = MagicMock()
    session.get.return_value = _make_response(200, response_text)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    return session


def test_cli_fetch_json_race_card_kdreams(tmp_path: Path, monkeypatch):
    session = _patch_http_session(monkeypatch, SAMPLE_HTML)
    runner = CliRunner()
    out = tmp_path / "card.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "race_card",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.venue == "大垣"
    assert ri.race.race_no == 1
    assert len(ri.riders) == 7
    assert len(ri.lines) == 3
    assert session.get.call_count == 1


def test_cli_fetch_json_race_card_then_predict(tmp_path: Path, monkeypatch):
    """fetch-json で取った出走表JSONを predict に渡して動くこと。"""
    _patch_http_session(monkeypatch, SAMPLE_HTML)
    runner = CliRunner()
    card_out = tmp_path / "card.json"
    r1 = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "race_card",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--out", str(card_out),
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
            "--input", str(card_out),
            "--no-save",
            "--no-reflections",
            "--provider", "mock",
        ],
    )
    assert r2.exit_code == 0, r2.output
    assert "予想結果" in r2.output


def test_cli_fetch_json_race_card_invalid_race_no(tmp_path: Path, monkeypatch):
    _patch_http_session(monkeypatch, SAMPLE_HTML)
    runner = CliRunner()
    out = tmp_path / "card.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "race_card",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "99",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "1〜12" in result.output


def test_cli_fetch_json_race_card_empty_html_falls_back(tmp_path: Path, monkeypatch):
    """選手が取れないHTML → fallback-input に切替できる。"""
    _patch_http_session(monkeypatch, EMPTY_HTML)
    runner = CliRunner()
    out = tmp_path / "card.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "race_card",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--out", str(out),
            "--no-cache",
            "--fallback-input", "examples/race_sample.json",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    # fallback先のmanualから取得されている
    assert ri.race.venue == "大垣"
