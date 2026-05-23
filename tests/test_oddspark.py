"""オッズパーク Fetcher / parser のテスト。実通信は行わない。"""

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
    OddsParkFetcher,
    RateLimiter,
)
from app.fetchers.oddspark import build_oddspark_odds_url
from app.fetchers.parsers.oddspark_odds import (
    BET_TYPE_TO_ODDSPARK,
    parse_oddspark_odds_html,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
TRIFECTA_HTML = (FIXTURES / "oddspark_trifecta_sample.html").read_text(encoding="utf-8")
TRIO_HTML = (FIXTURES / "oddspark_trio_sample.html").read_text(encoding="utf-8")
EXACTA_HTML = (FIXTURES / "oddspark_exacta_sample.html").read_text(encoding="utf-8")


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def _make_http_client(tmp_path: Path, session: MagicMock) -> HttpClient:
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    return HttpClient(cache=cache, rate_limiter=rl, session=session)


# ---------------------------------------------------------------------------
# URL生成
# ---------------------------------------------------------------------------


def test_build_oddspark_url_trifecta():
    url = build_oddspark_odds_url("平塚", "2026-05-22", 4, "trifecta")
    assert url.startswith("https://www.oddspark.com/keirin/Odds.do")
    assert "joCode=35" in url
    assert "kaisaiBi=20260522" in url
    assert "raceNo=4" in url
    assert "betType=9" in url
    assert "viewType=1" in url


def test_build_oddspark_url_all_bet_types():
    expected = {"trifecta": 9, "trio": 8, "exacta": 6}
    for bt, code in expected.items():
        url = build_oddspark_odds_url("大宮", Date(2026, 5, 22), 8, bt)
        assert f"betType={code}" in url
        assert "joCode=25" in url


def test_build_oddspark_url_unknown_venue():
    with pytest.raises(FetchError):
        build_oddspark_odds_url("不明", "2026-05-22", 1, "trifecta")


def test_build_oddspark_url_invalid_bet_type():
    with pytest.raises(FetchError):
        build_oddspark_odds_url("平塚", "2026-05-22", 1, "quinella")


def test_bet_type_to_oddspark_map():
    assert BET_TYPE_TO_ODDSPARK == {"trifecta": 9, "trio": 8, "exacta": 6}


# ---------------------------------------------------------------------------
# parse_oddspark_odds_html
# ---------------------------------------------------------------------------


def test_parse_oddspark_trifecta_basic():
    rows = parse_oddspark_odds_html(TRIFECTA_HTML, bet_type="trifecta")
    # rank=5 (odds="-") はスキップされて5件
    assert len(rows) == 5
    assert rows[0]["combination"] == "4-7-2"
    assert rows[0]["odds"] == 8.5
    assert rows[0]["rank"] == 1
    assert rows[3]["odds"] == 1234.5  # カンマ除去


def test_parse_oddspark_limit():
    rows = parse_oddspark_odds_html(TRIFECTA_HTML, bet_type="trifecta", limit=2)
    assert len(rows) == 2


def test_parse_oddspark_empty():
    with pytest.raises(FetchError):
        parse_oddspark_odds_html("", bet_type="trifecta")


def test_parse_oddspark_unsupported_bet_type():
    with pytest.raises(FetchError):
        parse_oddspark_odds_html(TRIFECTA_HTML, bet_type="quinella")


def test_parse_oddspark_no_rows():
    with pytest.raises(FetchError) as excinfo:
        parse_oddspark_odds_html(
            "<html><body><p>データなし</p></body></html>",
            bet_type="trifecta",
        )
    assert "検出できませんでした" in str(excinfo.value)


# ---------------------------------------------------------------------------
# OddsParkFetcher.fetch_odds
# ---------------------------------------------------------------------------


def test_oddspark_fetcher_uses_http_client(tmp_path: Path):
    session = MagicMock()
    session.get.return_value = _make_response(200, TRIFECTA_HTML)
    client = _make_http_client(tmp_path, session)
    fetcher = OddsParkFetcher(http_client=client)
    out = fetcher.fetch_odds(
        venue="平塚", date=Date(2026, 5, 22), race_no=4, bet_type="trifecta", limit=5
    )
    assert "trifecta_popular" in out
    assert len(out["trifecta_popular"]) == 5
    called = session.get.call_args.args[0]
    assert "www.oddspark.com" in called
    assert "betType=9" in called
    # User-Agent
    headers = session.get.call_args.kwargs.get("headers", {})
    assert "User-Agent" in headers


def test_oddspark_fetcher_all_bet_types_three_requests(tmp_path: Path):
    session = MagicMock()

    def _get(url: str, **kwargs):
        if "betType=9" in url:
            return _make_response(200, TRIFECTA_HTML)
        if "betType=8" in url:
            return _make_response(200, TRIO_HTML)
        if "betType=6" in url:
            return _make_response(200, EXACTA_HTML)
        return _make_response(404, "")

    session.get.side_effect = _get
    client = _make_http_client(tmp_path, session)
    fetcher = OddsParkFetcher(http_client=client)
    out = fetcher.fetch_odds(
        venue="平塚", date=Date(2026, 5, 22), race_no=4, limit=5
    )
    # 3種別 → 3回HTTP
    assert session.get.call_count == 3
    assert set(out.keys()) == {"trifecta_popular", "trio_popular", "exacta_popular"}
    assert len(out["trifecta_popular"]) == 5
    assert len(out["trio_popular"]) == 3
    assert len(out["exacta_popular"]) == 3
    assert out["trio_popular"][0]["combination"] == "1=3=5"
    assert out["exacta_popular"][0]["combination"] == "4-7"


def test_oddspark_fetcher_no_http_client_raises():
    f = OddsParkFetcher(http_client=None)
    with pytest.raises(FetchError):
        f.fetch_odds(venue="平塚", date=Date(2026, 5, 22), race_no=4)


def test_oddspark_fetcher_does_not_leak_raw_html(tmp_path: Path):
    session = MagicMock()
    session.get.return_value = _make_response(200, TRIFECTA_HTML)
    client = _make_http_client(tmp_path, session)
    fetcher = OddsParkFetcher(http_client=client)
    out = fetcher.fetch_odds(
        venue="平塚", date=Date(2026, 5, 22), race_no=4, bet_type="trifecta"
    )
    s = json.dumps(out, ensure_ascii=False)
    for tag in ("<table", "<tr", "<td", "<html", "href"):
        assert tag not in s


# ---------------------------------------------------------------------------
# CLI prepare-json --odds-source oddspark
# ---------------------------------------------------------------------------


def test_cli_prepare_json_odds_source_oddspark(tmp_path: Path, monkeypatch):
    """--odds-source oddspark で OddsParkFetcher が使われることを確認。

    出走表は kdreams 側、オッズはオッズパーク側、を区別するため URL ベースで
    HTML を出し分ける session を仕込む。
    """
    SAMPLE_FIX = Path(__file__).resolve().parent / "fixtures"
    KDREAMS_RACE_CARD = (SAMPLE_FIX / "kdreams_race_card_sample.html").read_text(encoding="utf-8")

    session = MagicMock()

    def _get(url: str, **kwargs):
        if "oddspark.com" in url:
            return _make_response(200, TRIFECTA_HTML)
        if "racecard" in url:
            return _make_response(200, KDREAMS_RACE_CARD)
        return _make_response(404, "")

    session.get.side_effect = _get
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)

    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "平塚",
            "--date", "2026-05-22",
            "--race-no", "4",
            "--odds",
            "--odds-source", "oddspark",
            "--odds-bet-type", "trifecta",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    bet_types = {o["bet_type"] for o in raw["odds"]}
    assert "3連単" in bet_types
    # オッズパーク URL が叩かれていること
    urls = [c.args[0] for c in session.get.call_args_list]
    assert any("oddspark.com" in u for u in urls)
