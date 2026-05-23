"""外部データ取得モジュールのテスト。

実ネットワーク通信は一切行わない。HTTP は session を差し替えてモックする。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.fetchers import (
    FetchError,
    FileCache,
    HttpClient,
    KDreamsFetcher,
    ManualFetcher,
    NotImplementedSource,
    OddsParkFetcher,
    RateLimiter,
    build_fetcher,
)
from app.fetchers.cache import make_cache_key
from app.models import RaceInput


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


# ---------------------------------------------------------------------------
# FileCache
# ---------------------------------------------------------------------------


def test_cache_set_and_get(tmp_path: Path):
    c = FileCache(cache_dir=tmp_path / "c", ttl_seconds=60)
    key = make_cache_key("GET", "https://example.com/a", {"x": 1})
    c.set(key, url="https://example.com/a", method="GET", params={"x": 1}, body="hello")
    hit = c.get(key)
    assert hit is not None
    assert hit["body"] == "hello"
    assert hit["url"] == "https://example.com/a"


def test_cache_miss_returns_none(tmp_path: Path):
    c = FileCache(cache_dir=tmp_path / "c", ttl_seconds=60)
    key = make_cache_key("GET", "https://example.com/missing", None)
    assert c.get(key) is None


def test_cache_ttl_expired(tmp_path: Path):
    c = FileCache(cache_dir=tmp_path / "c", ttl_seconds=10)
    key = make_cache_key("GET", "https://example.com/a", None)
    c.set(key, url="https://example.com/a", method="GET", params=None, body="x", now=100.0)
    # ttl + epsilon を超えた時刻
    assert c.get(key, now=200.0) is None
    # 範囲内
    assert c.get(key, now=105.0) is not None


def test_cache_disabled(tmp_path: Path):
    c = FileCache(cache_dir=tmp_path / "c", ttl_seconds=60, enabled=False)
    key = make_cache_key("GET", "https://example.com/a", None)
    c.set(key, url="https://example.com/a", method="GET", params=None, body="x")
    assert c.get(key) is None
    # ディスクに書かれていない
    assert not (tmp_path / "c").exists()


def test_cache_key_stable_across_param_order():
    a = make_cache_key("GET", "https://x/y", {"a": 1, "b": 2})
    b = make_cache_key("GET", "https://x/y", {"b": 2, "a": 1})
    assert a == b


def test_cache_key_differs_by_method_and_url():
    a = make_cache_key("GET", "https://x/y", None)
    b = make_cache_key("GET", "https://x/z", None)
    c = make_cache_key("POST", "https://x/y", None)
    assert len({a, b, c}) == 3


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


def _stepped_clock(steps: list[float]):
    """steps の最後を超えても StopIteration せず最終値を返すクロック関数。"""
    state = {"i": 0}

    def now() -> float:
        i = state["i"]
        if i < len(steps):
            state["i"] = i + 1
            return steps[i]
        return steps[-1]

    return now


def test_rate_limiter_first_call_no_wait():
    sleeps: list[float] = []
    rl = RateLimiter(
        min_interval_seconds=1.0,
        sleep_fn=sleeps.append,
        now_fn=_stepped_clock([0.0, 0.0]),
    )
    waited = rl.await_if_needed("https://example.com/a")
    assert waited == 0.0
    assert sleeps == []


def test_rate_limiter_second_call_within_interval_sleeps():
    sleeps: list[float] = []
    # await呼び出しごとに _now() が2回呼ばれる
    # 1回目: now=0.0 / set last_at=0.0
    # 2回目: now=0.3 (elapsed=0.3 < 1.0 → sleep=0.7) / set last_at=0.3
    rl = RateLimiter(
        min_interval_seconds=1.0,
        sleep_fn=sleeps.append,
        now_fn=_stepped_clock([0.0, 0.0, 0.3, 0.3]),
    )
    rl.await_if_needed("https://a.example/x")
    waited = rl.await_if_needed("https://a.example/y")
    assert sleeps and sleeps[0] == pytest.approx(0.7)
    assert waited == pytest.approx(0.7)


def test_rate_limiter_separate_domains():
    sleeps: list[float] = []
    rl = RateLimiter(
        min_interval_seconds=1.0,
        sleep_fn=sleeps.append,
        now_fn=_stepped_clock([0.0, 0.0, 0.1, 0.1]),
    )
    rl.await_if_needed("https://a.example/x")
    rl.await_if_needed("https://b.example/y")  # 別ドメインなので待たない
    assert sleeps == []


def test_rate_limiter_disabled_when_zero():
    sleeps: list[float] = []
    rl = RateLimiter(
        min_interval_seconds=0.0,
        sleep_fn=sleeps.append,
        now_fn=_stepped_clock([0.0, 0.05]),
    )
    rl.await_if_needed("https://a.example/x")
    rl.await_if_needed("https://a.example/y")
    assert sleeps == []


# ---------------------------------------------------------------------------
# HttpClient
# ---------------------------------------------------------------------------


def _make_response(status: int, text: str, headers: dict | None = None):
    return SimpleNamespace(
        status_code=status, text=text, headers=headers or {}
    )


def test_http_client_sets_user_agent(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    session = MagicMock()
    session.get.return_value = _make_response(200, "ok")
    client = HttpClient(
        user_agent="testbot/1.0",
        timeout=5.0,
        cache=cache,
        rate_limiter=rl,
        session=session,
    )
    body = client.get("https://example.com/a", use_cache=False)
    assert body == "ok"
    # User-Agent が確実に設定されている
    call = session.get.call_args
    headers = call.kwargs.get("headers", {})
    assert headers.get("User-Agent") == "testbot/1.0"
    assert call.kwargs.get("timeout") == 5.0


def test_http_client_cache_hit_skips_network(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", ttl_seconds=60)
    rl = RateLimiter(min_interval_seconds=0.0)
    session = MagicMock()
    session.get.return_value = _make_response(200, "first")
    client = HttpClient(cache=cache, rate_limiter=rl, session=session)
    b1 = client.get("https://example.com/a")
    assert b1 == "first"
    assert session.get.call_count == 1
    # キャッシュヒット → 通信無し
    session.get.return_value = _make_response(200, "second")
    b2 = client.get("https://example.com/a")
    assert b2 == "first"
    assert session.get.call_count == 1


def test_http_client_no_cache_bypasses(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", ttl_seconds=60)
    rl = RateLimiter(min_interval_seconds=0.0)
    session = MagicMock()
    session.get.return_value = _make_response(200, "fresh")
    client = HttpClient(cache=cache, rate_limiter=rl, session=session)
    client.get("https://example.com/a", use_cache=True)
    client.get("https://example.com/a", use_cache=False)
    assert session.get.call_count == 2


def test_http_client_non_2xx_raises_fetcherror(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    session = MagicMock()
    session.get.return_value = _make_response(503, "boom")
    client = HttpClient(
        cache=cache,
        rate_limiter=RateLimiter(min_interval_seconds=0.0),
        session=session,
    )
    with pytest.raises(FetchError) as excinfo:
        client.get("https://example.com/a")
    assert "503" in str(excinfo.value)


def test_http_client_network_exception_wrapped(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    session = MagicMock()
    session.get.side_effect = RuntimeError("dns fail")
    client = HttpClient(
        cache=cache,
        rate_limiter=RateLimiter(min_interval_seconds=0.0),
        session=session,
    )
    with pytest.raises(FetchError) as excinfo:
        client.get("https://example.com/a")
    msg = str(excinfo.value)
    assert "通信エラー" in msg
    assert "RuntimeError" in msg


def test_http_client_calls_rate_limiter(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = MagicMock(spec=RateLimiter)
    rl.await_if_needed.return_value = 0.0
    session = MagicMock()
    session.get.return_value = _make_response(200, "ok")
    client = HttpClient(cache=cache, rate_limiter=rl, session=session)
    client.get("https://example.com/a")
    rl.await_if_needed.assert_called_once_with("https://example.com/a")


# ---------------------------------------------------------------------------
# ManualFetcher
# ---------------------------------------------------------------------------


def test_manual_fetcher_loads_sample():
    f = ManualFetcher(input_path=SAMPLE)
    data = f.fetch_race_card()
    assert data["race"]["venue"] == "大垣"
    # RaceInput として通る
    RaceInput.model_validate(data)


def test_manual_fetcher_missing_file_raises():
    f = ManualFetcher(input_path="/nonexistent/path.json")
    with pytest.raises(FetchError) as excinfo:
        f.fetch_race_card()
    assert "見つかりません" in str(excinfo.value)


def test_manual_fetcher_invalid_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    f = ManualFetcher(input_path=bad)
    with pytest.raises(FetchError) as excinfo:
        f.fetch_race_card()
    assert "JSON" in str(excinfo.value)


def test_manual_fetcher_invalid_schema(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    f = ManualFetcher(input_path=bad)
    with pytest.raises(FetchError) as excinfo:
        f.fetch_race_card()
    assert "スキーマ" in str(excinfo.value)


def test_manual_fetcher_odds_results_trend():
    f = ManualFetcher(input_path=SAMPLE)
    odds = f.fetch_odds()
    assert odds and odds[0]["bet_type"] == "3連単"
    results = f.fetch_results()
    assert results and results[0]["venue"] == "大垣"
    trend = f.fetch_venue_trend()
    assert trend and "番手" in (trend.get("favors") or [])


def test_manual_fetcher_mismatch_venue_adds_note():
    f = ManualFetcher(input_path=SAMPLE)
    data = f.fetch_race_card(venue="松山")
    assert "不一致" in (data.get("user_note") or "")


# ---------------------------------------------------------------------------
# 未実装 Fetcher
# ---------------------------------------------------------------------------


def test_kdreams_unimplemented_methods():
    """Kドリームス: 現フェーズでは fetch_venue_trend のみ未実装。

    fetch_race_card / fetch_results / fetch_odds は試験実装済み。
    """
    f = KDreamsFetcher()
    with pytest.raises(NotImplementedSource) as excinfo:
        f.fetch_venue_trend()
    assert "未実装" in str(excinfo.value)
    assert "Kドリームス" in str(excinfo.value)


def test_oddspark_unimplemented_methods():
    """オッズパーク: fetch_odds のみ実装。他は NotImplementedSource。"""
    f = OddsParkFetcher()
    for method in ("fetch_race_card", "fetch_results", "fetch_venue_trend"):
        with pytest.raises(NotImplementedSource) as excinfo:
            getattr(f, method)()
        assert "未実装" in str(excinfo.value)
        assert "オッズパーク" in str(excinfo.value)


# ---------------------------------------------------------------------------
# build_fetcher
# ---------------------------------------------------------------------------


def test_build_fetcher_manual_needs_input():
    with pytest.raises(FetchError):
        build_fetcher("manual")


def test_build_fetcher_manual_with_input():
    f = build_fetcher("manual", manual_input_path=str(SAMPLE))
    assert isinstance(f, ManualFetcher)


def test_build_fetcher_kdreams():
    assert isinstance(build_fetcher("kdreams"), KDreamsFetcher)


def test_build_fetcher_unknown():
    with pytest.raises(FetchError) as excinfo:
        build_fetcher("does-not-exist")
    assert "未知のソース" in str(excinfo.value)
