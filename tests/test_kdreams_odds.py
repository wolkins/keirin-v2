"""Kドリームスオッズ取得・パースのテスト。

実ネットワーク通信は一切行わない。HttpClient は session を MagicMock に
差し替え、HTMLは tests/fixtures/ から読み込む。
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
    RateLimiter,
)
from app.fetchers.kdreams import build_odds_url
from app.fetchers.parsers.kdreams_odds import (
    _normalize_combination,
    _normalize_odds,
    parse_odds_html,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
TRIFECTA_HTML = (FIXTURES / "kdreams_odds_trifecta_sample.html").read_text(encoding="utf-8")
TRIO_HTML = (FIXTURES / "kdreams_odds_trio_sample.html").read_text(encoding="utf-8")
EXACTA_HTML = (FIXTURES / "kdreams_odds_exacta_sample.html").read_text(encoding="utf-8")
EMPTY_HTML = (FIXTURES / "kdreams_odds_empty.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# URL生成
# ---------------------------------------------------------------------------


def test_build_odds_url_basic():
    url = build_odds_url("大垣", "2026-05-22", 1, "trifecta")
    assert url.startswith("https://keirin.kdreams.jp/")
    assert "/ogaki/racedetail/" in url
    assert "44202605220101" in url  # jo=44 + 20260522 + session=01 + race=01
    assert "pageType=odds" in url
    assert "kakeshikiType=3rentan" in url


def test_build_odds_url_all_bet_types():
    expected_kakeshiki = {
        "trifecta": "3rentan",
        "trio": "3renpuku",
        "exacta": "2tanshou",
    }
    for bt, kakeshiki in expected_kakeshiki.items():
        url = build_odds_url("大宮", Date(2026, 5, 22), 8, bt)
        assert f"kakeshikiType={kakeshiki}" in url
        assert "/omiya/racedetail/" in url
        assert "25202605220108" in url  # jo=25 + 20260522 + session=01 + race=08


def test_build_odds_url_unsupported_bet_type():
    with pytest.raises(FetchError) as excinfo:
        build_odds_url("大垣", "2026-05-22", 1, "quinella")
    assert "未対応" in str(excinfo.value)


def test_build_odds_url_missing_bet_type():
    with pytest.raises(FetchError):
        build_odds_url("大垣", "2026-05-22", 1, None)


def test_build_odds_url_unknown_venue():
    with pytest.raises(FetchError):
        build_odds_url("不明", "2026-05-22", 1, "trifecta")


def test_build_odds_url_invalid_date():
    with pytest.raises(FetchError):
        build_odds_url("大垣", "2026/05/22", 1, "trifecta")


def test_build_odds_url_invalid_race_no():
    with pytest.raises(FetchError):
        build_odds_url("大垣", "2026-05-22", 0, "trifecta")
    with pytest.raises(FetchError):
        build_odds_url("大垣", "2026-05-22", 13, "trifecta")


# ---------------------------------------------------------------------------
# normalize helpers
# ---------------------------------------------------------------------------


def test_normalize_combination_trifecta_variants():
    assert _normalize_combination("5-1-3", bet_type="trifecta") == "5-1-3"
    assert _normalize_combination("５－１－３", bet_type="trifecta") == "5-1-3"
    assert _normalize_combination("5 - 1 - 3", bet_type="trifecta") == "5-1-3"
    assert _normalize_combination("5―1―3", bet_type="trifecta") == "5-1-3"


def test_normalize_combination_trio_variants():
    assert _normalize_combination("1=3=5", bet_type="trio") == "1=3=5"
    assert _normalize_combination("１＝３＝５", bet_type="trio") == "1=3=5"
    # ハイフン区切りで来ても trio なら = に統一
    assert _normalize_combination("1-3-5", bet_type="trio") == "1=3=5"


def test_normalize_combination_exacta():
    assert _normalize_combination("5-1", bet_type="exacta") == "5-1"
    assert _normalize_combination("５－１", bet_type="exacta") == "5-1"


def test_normalize_combination_rejects_invalid():
    assert _normalize_combination("", bet_type="trifecta") is None
    assert _normalize_combination("-", bet_type="trifecta") is None
    # 同じ車番の重複は無効（3連単/3連複/2車単とも）
    assert _normalize_combination("1-1-2", bet_type="trifecta") is None
    assert _normalize_combination("1=1=2", bet_type="trio") is None
    # 範囲外
    assert _normalize_combination("10-1-3", bet_type="trifecta") is None
    # 桁数不一致
    assert _normalize_combination("5-1", bet_type="trifecta") is None


def test_normalize_odds_variants():
    assert _normalize_odds("8.5") == 8.5
    assert _normalize_odds("8.5倍") == 8.5
    assert _normalize_odds("1,234.5") == 1234.5
    assert _normalize_odds("¥1,234") == 1234.0
    assert _normalize_odds("-") is None
    assert _normalize_odds("") is None
    assert _normalize_odds("未確定") is None
    assert _normalize_odds("abc") is None
    assert _normalize_odds("0") is None  # 0以下は無効


# ---------------------------------------------------------------------------
# parse_odds_html
# ---------------------------------------------------------------------------


def test_parse_trifecta_full():
    rows = parse_odds_html(TRIFECTA_HTML, bet_type="trifecta")
    # rank=5 (odds="-") はスキップ → 5件
    assert len(rows) == 5
    assert rows[0] == {"rank": 1, "combination": "5-1-3", "odds": 8.5}
    # "12.4倍" → 12.4
    assert rows[1]["odds"] == 12.4
    # 全角 → 半角
    assert rows[2]["combination"] == "1-5-3"
    # カンマ除去
    assert rows[3]["odds"] == 1234.5
    # rank=5 はスキップされ rank=6 が含まれる
    assert rows[-1]["rank"] == 6


def test_parse_trio_full():
    rows = parse_odds_html(TRIO_HTML, bet_type="trio")
    assert len(rows) == 4
    assert rows[0] == {"rank": 1, "combination": "1=3=5", "odds": 4.0}
    # 全角 ＝ → =
    assert rows[1]["combination"] == "1=5=6"
    # スペース除去
    assert rows[2]["combination"] == "2=5=6"
    # 1-2-5 → 1=2=5（trioならハイフンも = に統一）
    assert rows[3]["combination"] == "1=2=5"


def test_parse_exacta_full():
    rows = parse_odds_html(EXACTA_HTML, bet_type="exacta")
    # rank=4 ("未確定") はスキップ → 3件
    assert len(rows) == 3
    assert rows[0] == {"rank": 1, "combination": "5-1", "odds": 3.6}
    assert rows[2]["combination"] == "5-6"


def test_parse_odds_limit_applied():
    rows = parse_odds_html(TRIFECTA_HTML, bet_type="trifecta", limit=2)
    assert len(rows) == 2
    assert [r["rank"] for r in rows] == [1, 2]


def test_parse_odds_unsupported_bet_type():
    with pytest.raises(FetchError):
        parse_odds_html(TRIFECTA_HTML, bet_type="quinella")


def test_parse_odds_empty_html_raises():
    with pytest.raises(FetchError):
        parse_odds_html("", bet_type="trifecta")


def test_parse_odds_no_table_raises():
    with pytest.raises(FetchError) as excinfo:
        parse_odds_html(EMPTY_HTML, bet_type="trifecta")
    assert "オッズテーブル" in str(excinfo.value)


# ---------------------------------------------------------------------------
# KDreamsFetcher.fetch_odds
# ---------------------------------------------------------------------------


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def _route_odds_session() -> MagicMock:
    """URL の kakeshikiType から bet_type を判別して HTML を返すモック。"""
    session = MagicMock()

    def _get(url: str, **kwargs):
        if "kakeshikiType=3rentan" in url:
            return _make_response(200, TRIFECTA_HTML)
        if "kakeshikiType=3renpuku" in url:
            return _make_response(200, TRIO_HTML)
        if "kakeshikiType=2tanshou" in url:
            return _make_response(200, EXACTA_HTML)
        return _make_response(404, "")

    session.get.side_effect = _get
    return session


def _make_http_client(tmp_path: Path, session: MagicMock) -> HttpClient:
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    return HttpClient(cache=cache, rate_limiter=rl, session=session)


def test_fetcher_odds_all_bet_types(tmp_path: Path):
    session = _route_odds_session()
    client = _make_http_client(tmp_path, session)
    fetcher = KDreamsFetcher(http_client=client)
    out = fetcher.fetch_odds(
        venue="大垣", date=Date(2026, 5, 22), race_no=1, limit=10
    )
    assert set(out.keys()) == {"trifecta_popular", "trio_popular", "exacta_popular"}
    assert len(out["trifecta_popular"]) == 5
    assert len(out["trio_popular"]) == 4
    assert len(out["exacta_popular"]) == 3
    # 3回 HTTP
    assert session.get.call_count == 3


def test_fetcher_odds_specific_bet_type(tmp_path: Path):
    session = _route_odds_session()
    client = _make_http_client(tmp_path, session)
    fetcher = KDreamsFetcher(http_client=client)
    out = fetcher.fetch_odds(
        venue="大垣",
        date=Date(2026, 5, 22),
        race_no=1,
        bet_type="trifecta",
        limit=3,
    )
    assert set(out.keys()) == {"trifecta_popular"}
    assert len(out["trifecta_popular"]) == 3
    assert session.get.call_count == 1


def test_fetcher_odds_does_not_leak_raw_html(tmp_path: Path):
    session = _route_odds_session()
    client = _make_http_client(tmp_path, session)
    fetcher = KDreamsFetcher(http_client=client)
    out = fetcher.fetch_odds(
        venue="大垣", date=Date(2026, 5, 22), race_no=1, bet_type="trifecta"
    )
    serialized = json.dumps(out, ensure_ascii=False)
    for tag in ("<table", "<tr", "<td", "<div", "href"):
        assert tag not in serialized


def test_fetcher_odds_user_agent(tmp_path: Path):
    session = _route_odds_session()
    client = _make_http_client(tmp_path, session)
    fetcher = KDreamsFetcher(http_client=client)
    fetcher.fetch_odds(
        venue="大垣", date=Date(2026, 5, 22), race_no=1, bet_type="trio"
    )
    headers = session.get.call_args.kwargs.get("headers", {})
    assert "User-Agent" in headers


def test_fetcher_odds_without_http_client_raises():
    fetcher = KDreamsFetcher(http_client=None)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_odds(venue="大垣", date=Date(2026, 5, 22), race_no=1)
    assert "HttpClient" in str(excinfo.value)


def test_fetcher_odds_without_venue_raises(tmp_path: Path):
    session = _route_odds_session()
    client = _make_http_client(tmp_path, session)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError):
        fetcher.fetch_odds(venue=None, date=Date(2026, 5, 22), race_no=1)


def test_fetcher_odds_invalid_race_no(tmp_path: Path):
    session = _route_odds_session()
    client = _make_http_client(tmp_path, session)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError):
        fetcher.fetch_odds(venue="大垣", date=Date(2026, 5, 22), race_no=99)


def test_fetcher_odds_propagates_http_error(tmp_path: Path):
    session = MagicMock()
    session.get.return_value = _make_response(503, "")
    client = _make_http_client(tmp_path, session)
    fetcher = KDreamsFetcher(http_client=client)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch_odds(
            venue="大垣", date=Date(2026, 5, 22), race_no=1, bet_type="trifecta"
        )
    assert "503" in str(excinfo.value)


# ---------------------------------------------------------------------------
# CLI fetch-json --kind odds
# ---------------------------------------------------------------------------


def _patch_route_odds(monkeypatch) -> MagicMock:
    session = _route_odds_session()
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    return session


def test_cli_fetch_json_odds_all_bet_types(tmp_path: Path, monkeypatch):
    session = _patch_route_odds(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "odds.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "odds",
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
    assert raw["source"] == "kdreams"
    assert raw["kind"] == "odds"
    assert raw["venue"] == "大垣"
    assert raw["race_no"] == 1
    assert set(raw["odds"].keys()) == {
        "trifecta_popular", "trio_popular", "exacta_popular"
    }
    assert session.get.call_count == 3


def test_cli_fetch_json_odds_specific_bet_type(tmp_path: Path, monkeypatch):
    session = _patch_route_odds(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "odds.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "odds",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--bet-type", "trifecta",
            "--limit", "3",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert set(raw["odds"].keys()) == {"trifecta_popular"}
    assert len(raw["odds"]["trifecta_popular"]) == 3
    assert session.get.call_count == 1


def test_cli_fetch_json_odds_unsupported_kind(tmp_path: Path):
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


def test_cli_fetch_json_odds_no_html_leak(tmp_path: Path, monkeypatch):
    _patch_route_odds(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "odds.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "kdreams",
            "--kind", "odds",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0
    body = out.read_text(encoding="utf-8")
    for tag in ("<table", "<tr", "<td", "<div", "<html", "href"):
        assert tag not in body
