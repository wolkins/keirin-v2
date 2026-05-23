"""Kドリームス結果ページ取得・パースのテスト。

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
from app.fetchers.kdreams import (
    build_results_url,
    resolve_jo_code,
)
from app.fetchers.parsers.kdreams_results import (
    parse_results_html,
    _parse_payout,
    _normalize_result,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE_HTML = (FIXTURES / "kdreams_results_sample.html").read_text(encoding="utf-8")
EMPTY_HTML = (FIXTURES / "kdreams_results_empty.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# venue code mapping / URL生成
# ---------------------------------------------------------------------------


def test_resolve_jo_code_known_venues():
    assert resolve_jo_code("大垣") == 44
    assert resolve_jo_code("大宮") == 25
    assert resolve_jo_code("松山") == 75
    assert resolve_jo_code("名古屋") == 42


def test_resolve_jo_code_added_venues():
    """フェーズで追加された主要場の jo_code を確認。"""
    assert resolve_jo_code("平塚") == 35
    assert resolve_jo_code("立川") == 28
    assert resolve_jo_code("川崎") == 34
    assert resolve_jo_code("取手") == 23
    assert resolve_jo_code("別府") == 86
    assert resolve_jo_code("小倉") == 81
    assert resolve_jo_code("函館") == 11


def test_resolve_jo_code_supports_at_least_30_venues():
    """主要競輪場（30場以上）が登録されていること。"""
    from app.fetchers.kdreams import _JO_CODE
    assert len(_JO_CODE) >= 30
    # コード重複なし
    assert len(set(_JO_CODE.values())) == len(_JO_CODE)


def test_resolve_jo_code_unknown_venue_raises():
    with pytest.raises(FetchError) as excinfo:
        resolve_jo_code("存在しない場")
    assert "未対応" in str(excinfo.value)


def test_resolve_jo_code_empty_raises():
    with pytest.raises(FetchError):
        resolve_jo_code("")


def test_build_results_url_with_date_object():
    url = build_results_url("大垣", Date(2026, 5, 22))
    # 実 Kドリームスは パス構造（場名スラッグ + raceresult + kaisaiDateId）
    assert url.startswith("https://keirin.kdreams.jp/")
    assert "/ogaki/raceresult/" in url
    assert "44202605220100" in url  # jo=44 + 20260522 + session=01 + 00


def test_build_results_url_with_string_date():
    url = build_results_url("大宮", "2026-05-22")
    assert "/omiya/raceresult/" in url
    assert "25202605220100" in url


def test_build_results_url_with_session_no():
    url = build_results_url("平塚", "2026-05-22", session_no=3)
    assert "/hiratsuka/raceresult/" in url
    assert "35202605220300" in url


def test_build_results_url_invalid_date():
    with pytest.raises(FetchError) as excinfo:
        build_results_url("大垣", "2026/05/22")
    assert "YYYY-MM-DD" in str(excinfo.value)


def test_build_results_url_unknown_venue():
    with pytest.raises(FetchError):
        build_results_url("不明", "2026-05-22")


# ---------------------------------------------------------------------------
# parse_results_html
# ---------------------------------------------------------------------------


def test_parse_results_full_table():
    rows = parse_results_html(SAMPLE_HTML, venue="大垣", date_str="2026-05-22")
    # 6行中: 1R/2R/4R/6R が確定。3R/5R は未確定でskip → 4件
    race_nos = [r["race_no"] for r in rows]
    assert race_nos == [1, 2, 4, 6]
    by_no = {r["race_no"]: r for r in rows}
    assert by_no[1]["result"] == "5-6-2"
    assert by_no[1]["payout"] == 12340
    assert by_no[1]["venue"] == "大垣"
    assert by_no[1]["date"] == "2026-05-22"
    assert by_no[1]["memo"].startswith("Kドリームス")


def test_parse_results_payout_with_yen_symbol():
    rows = parse_results_html(SAMPLE_HTML, venue="大垣", date_str="2026-05-22")
    by_no = {r["race_no"]: r for r in rows}
    # ¥45,200 → 45200 として読める
    assert by_no[4]["payout"] == 45200


def test_parse_results_full_width_digits_in_result():
    rows = parse_results_html(SAMPLE_HTML, venue="大垣", date_str="2026-05-22")
    by_no = {r["race_no"]: r for r in rows}
    # ７－１－３ が 7-1-3 に正規化される
    assert by_no[6]["result"] == "7-1-3"
    assert by_no[6]["payout"] == 8210


def test_parse_results_filter_by_race_no():
    rows = parse_results_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=2
    )
    assert len(rows) == 1
    assert rows[0]["race_no"] == 2
    assert rows[0]["result"] == "1-3-7"


def test_parse_results_filter_unmatched_race_no_returns_empty():
    rows = parse_results_html(
        SAMPLE_HTML, venue="大垣", date_str="2026-05-22", race_no=99
    )
    assert rows == []


def test_parse_results_skips_pending_rows():
    """3R(- / 未確定) と 5R(発走前 / -) はスキップされる。"""
    rows = parse_results_html(SAMPLE_HTML, venue="大垣", date_str="2026-05-22")
    race_nos = {r["race_no"] for r in rows}
    assert 3 not in race_nos
    assert 5 not in race_nos


def test_parse_results_empty_table_raises():
    """結果テーブル自体が無いHTMLは FetchError。"""
    with pytest.raises(FetchError) as excinfo:
        parse_results_html(EMPTY_HTML, venue="大垣", date_str="2026-05-22")
    assert "結果テーブル" in str(excinfo.value)


def test_parse_results_invalid_html_raises():
    """空文字や明らかに不正なHTMLは FetchError。"""
    with pytest.raises(FetchError):
        parse_results_html("", venue="大垣", date_str="2026-05-22")
    with pytest.raises(FetchError):
        parse_results_html("   ", venue="大垣", date_str="2026-05-22")


def test_parse_payout_helpers():
    assert _parse_payout("12,340円") == 12340
    assert _parse_payout("¥45,200") == 45200
    assert _parse_payout("1234") == 1234
    assert _parse_payout("未確定") is None
    assert _parse_payout("-") is None
    assert _parse_payout("") is None
    assert _parse_payout("円") is None


def test_normalize_result_helpers():
    assert _normalize_result("5-6-2") == "5-6-2"
    assert _normalize_result("５-６-２") == "5-6-2"
    assert _normalize_result("5—6—2") == "5-6-2"  # em-dash → hyphen
    assert _normalize_result("-") is None
    assert _normalize_result("未確定") is None
    assert _normalize_result("abc") is None


# ---------------------------------------------------------------------------
# KDreamsFetcher.fetch_results
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


def test_fetcher_results_calls_http_get_with_correct_url(tmp_path: Path):
    client, session = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    rows = fetcher.fetch_results(venue="大垣", date=Date(2026, 5, 22))
    assert session.get.call_count == 1
    called_url = session.get.call_args.args[0]
    assert "/ogaki/raceresult/" in called_url
    assert "44202605220100" in called_url
    # User-Agent が必ず付与されている
    headers = session.get.call_args.kwargs.get("headers", {})
    assert "User-Agent" in headers
    # 結果が構造化dictリストとして返る
    assert isinstance(rows, list)
    assert all(isinstance(r, dict) for r in rows)
    assert rows[0]["venue"] == "大垣"


def test_fetcher_results_filters_by_race_no(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    rows = fetcher.fetch_results(
        venue="大垣", date=Date(2026, 5, 22), race_no=4
    )
    assert len(rows) == 1
    assert rows[0]["race_no"] == 4
    assert rows[0]["result"] == "2-4-6"


def test_fetcher_results_does_not_leak_raw_html(tmp_path: Path):
    """生HTMLが戻り値（dictやその値）に混入していないこと。"""
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    rows = fetcher.fetch_results(venue="大垣", date=Date(2026, 5, 22))
    serialized = json.dumps(rows, ensure_ascii=False)
    # HTMLタグや<a href...が混入していない
    assert "<table" not in serialized
    assert "<tr" not in serialized
    assert "<td" not in serialized
    assert "href" not in serialized


def test_fetcher_results_without_http_client_raises():
    fetcher = KDreamsFetcher(http_client=None)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_results(venue="大垣", date=Date(2026, 5, 22))
    assert "HttpClient" in str(excinfo.value)


def test_fetcher_results_without_venue_raises(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_results(venue=None, date=Date(2026, 5, 22))
    assert "場名" in str(excinfo.value)


def test_fetcher_results_without_date_raises(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_results(venue="大垣", date=None)
    assert "日付" in str(excinfo.value)


def test_fetcher_results_unsupported_venue(tmp_path: Path):
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_results(venue="存在しない場", date=Date(2026, 5, 22))
    assert "未対応" in str(excinfo.value)


def test_fetcher_results_propagates_http_error(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    session = MagicMock()
    session.get.return_value = _make_response(503, "boom")
    client = HttpClient(cache=cache, rate_limiter=rl, session=session)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_results(venue="大垣", date=Date(2026, 5, 22))
    assert "503" in str(excinfo.value)


def test_fetcher_other_methods_still_unimplemented(tmp_path: Path):
    """fetch_venue_trend のみ未実装。fetch_race_card / fetch_odds は別フェーズで実装済み。"""
    client, _ = _make_http_client(tmp_path, SAMPLE_HTML)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(NotImplementedSource):
        fetcher.fetch_venue_trend(venue="大垣")


# ---------------------------------------------------------------------------
# CLI fetch-json --kind results
# ---------------------------------------------------------------------------


def _patch_http_session(monkeypatch, response_text: str) -> MagicMock:
    """HttpClient._get_session() が返すセッションを差し替える。"""
    session = MagicMock()
    session.get.return_value = _make_response(200, response_text)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    return session


def test_cli_fetch_json_results_kdreams(tmp_path: Path, monkeypatch):
    session = _patch_http_session(monkeypatch, SAMPLE_HTML)
    runner = CliRunner()
    out = tmp_path / "r.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "results",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["source"] == "kdreams"
    assert raw["kind"] == "results"
    assert raw["venue"] == "大垣"
    assert raw["date"] == "2026-05-22"
    assert isinstance(raw["results"], list)
    assert len(raw["results"]) == 4
    # session.get が呼ばれている（通信モック）
    assert session.get.call_count == 1


def test_cli_fetch_json_results_with_race_no(tmp_path: Path, monkeypatch):
    _patch_http_session(monkeypatch, SAMPLE_HTML)
    runner = CliRunner()
    out = tmp_path / "r.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "results",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "4",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert len(raw["results"]) == 1
    assert raw["results"][0]["race_no"] == 4
    assert raw["results"][0]["result"] == "2-4-6"


def test_cli_fetch_json_results_unknown_kind(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "x.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "manual",
            "--input", "examples/race_sample.json",
            "--kind", "bogus",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "未知の取得種別" in result.output


def test_cli_fetch_json_results_manual_source(tmp_path: Path):
    """manual ソースでも --kind results で recent_results を取り出せる。"""
    runner = CliRunner()
    out = tmp_path / "r.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "manual",
            "--kind", "results",
            "--input", "examples/race_sample.json",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["source"] == "manual"
    assert raw["kind"] == "results"
    assert isinstance(raw["results"], list)
    assert raw["results"] and raw["results"][0]["venue"] == "大垣"


def test_cli_fetch_json_results_kdreams_unsupported_venue(tmp_path: Path, monkeypatch):
    _patch_http_session(monkeypatch, SAMPLE_HTML)
    runner = CliRunner()
    out = tmp_path / "x.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "results",
            "--venue", "存在しない場",
            "--date", "2026-05-22",
            "--out", str(out),
            "--no-cache",
        ],
    )
    # 一次取得失敗 → fallback-input 無しなので終了コード非0
    assert result.exit_code != 0
    text = result.output
    assert "未対応" in text


def test_cli_fetch_json_results_empty_html_uses_fallback(tmp_path: Path, monkeypatch):
    """結果テーブルが無いHTML→fallback-input に切り替わる。"""
    _patch_http_session(monkeypatch, EMPTY_HTML)
    runner = CliRunner()
    out = tmp_path / "r.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "results",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--out", str(out),
            "--no-cache",
            "--fallback-input", "examples/race_sample.json",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    # fallback先のmanualから取得されている
    assert raw["source"] == "manual"
    assert raw["kind"] == "results"
