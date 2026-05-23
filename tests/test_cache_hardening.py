"""キャッシュ堅牢化のテスト。

1. HttpClient のエラーレスポンス非キャッシュ (validate_body)
2. --refresh-cache フラグ
3. 開催なしインデックス
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.fetchers import FileCache, HttpClient, RateLimiter
from app.fetchers.cache import make_cache_key
from app.no_meet_index import NoMeetIndex


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


# ---------------------------------------------------------------------------
# 1. エラーレスポンス非キャッシュ
# ---------------------------------------------------------------------------


def test_validate_body_false_skips_cache(tmp_path: Path):
    """validate_body が False を返すとキャッシュに保存されない。"""
    cache = FileCache(cache_dir=tmp_path / "c", enabled=True)
    session = MagicMock()
    session.get.return_value = _make_response(200, "SYSTEM_ERROR page")
    client = HttpClient(
        cache=cache, rate_limiter=RateLimiter(min_interval_seconds=0.0),
        session=session,
    )

    def is_ok(body: str) -> bool:
        return "SYSTEM_ERROR" not in body

    body = client.get("https://example.com/race", validate_body=is_ok)
    assert "SYSTEM_ERROR" in body
    # キャッシュに保存されていない
    key = make_cache_key("GET", "https://example.com/race", None)
    assert cache.get(key) is None


def test_validate_body_true_caches_normally(tmp_path: Path):
    """validate_body が True なら通常通りキャッシュ。"""
    cache = FileCache(cache_dir=tmp_path / "c", enabled=True)
    session = MagicMock()
    session.get.return_value = _make_response(200, "正常な出走表")
    client = HttpClient(
        cache=cache, rate_limiter=RateLimiter(min_interval_seconds=0.0),
        session=session,
    )

    body = client.get(
        "https://example.com/race",
        validate_body=lambda b: "SYSTEM_ERROR" not in b,
    )
    assert body == "正常な出走表"
    key = make_cache_key("GET", "https://example.com/race", None)
    hit = cache.get(key)
    assert hit is not None
    assert hit["body"] == "正常な出走表"


# ---------------------------------------------------------------------------
# 2. --refresh-cache / force_refresh
# ---------------------------------------------------------------------------


def test_refresh_kwarg_invalidates_cache(tmp_path: Path):
    """get(refresh=True) は既存キャッシュを削除して再取得。"""
    cache = FileCache(cache_dir=tmp_path / "c", enabled=True)
    key = make_cache_key("GET", "https://example.com/race", None)
    cache.set(
        key, url="https://example.com/race", method="GET", params=None,
        body="old", headers={},
    )
    assert cache.get(key) is not None

    session = MagicMock()
    session.get.return_value = _make_response(200, "new")
    client = HttpClient(
        cache=cache, rate_limiter=RateLimiter(min_interval_seconds=0.0),
        session=session,
    )
    body = client.get("https://example.com/race", refresh=True)
    assert body == "new"
    # 新しい値がキャッシュにある
    hit = cache.get(key)
    assert hit["body"] == "new"


def test_force_refresh_init_applies_to_all_get(tmp_path: Path):
    """HttpClient(force_refresh=True) はすべての get() で refresh 動作。"""
    cache = FileCache(cache_dir=tmp_path / "c", enabled=True)
    key = make_cache_key("GET", "https://example.com/x", None)
    cache.set(
        key, url="https://example.com/x", method="GET", params=None,
        body="old", headers={},
    )

    session = MagicMock()
    session.get.return_value = _make_response(200, "fresh")
    client = HttpClient(
        cache=cache, rate_limiter=RateLimiter(min_interval_seconds=0.0),
        session=session, force_refresh=True,
    )
    body = client.get("https://example.com/x")
    assert body == "fresh"


def test_filecache_invalidate(tmp_path: Path):
    cache = FileCache(cache_dir=tmp_path / "c", enabled=True)
    key = make_cache_key("GET", "https://example.com/a", None)
    cache.set(
        key, url="https://example.com/a", method="GET", params=None,
        body="hello", headers={},
    )
    assert cache.invalidate(key) is True
    assert cache.get(key) is None
    # もう一度 invalidate は False（存在しない）
    assert cache.invalidate(key) is False


# ---------------------------------------------------------------------------
# 3. 開催なしインデックス
# ---------------------------------------------------------------------------


def test_no_meet_index_record_and_read(tmp_path: Path):
    idx = NoMeetIndex(tmp_path / "cache")
    assert idx.is_known_no_meet("広島", "2026-05-22") is False
    idx.record_no_meet("広島", "2026-05-22")
    assert idx.is_known_no_meet("広島", "2026-05-22") is True
    # 別の場・別の日付は false
    assert idx.is_known_no_meet("平塚", "2026-05-22") is False
    assert idx.is_known_no_meet("広島", "2026-05-23") is False


def test_no_meet_index_ttl_expiry(tmp_path: Path):
    """TTL を超えた記録は無視される。"""
    idx = NoMeetIndex(tmp_path / "cache", ttl_seconds=60)
    idx.record_no_meet("広島", "2026-05-22")
    # 1時間後（TTL=60秒を大きく超過）
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert idx.is_known_no_meet("広島", "2026-05-22", now=future) is False


def test_no_meet_index_session_no_dimension(tmp_path: Path):
    """session_no が違えば別レコード。"""
    idx = NoMeetIndex(tmp_path / "cache")
    idx.record_no_meet("広島", "2026-05-22", session_no=1)
    assert idx.is_known_no_meet("広島", "2026-05-22", session_no=1) is True
    assert idx.is_known_no_meet("広島", "2026-05-22", session_no=2) is False


def test_no_meet_index_clear(tmp_path: Path):
    idx = NoMeetIndex(tmp_path / "cache")
    idx.record_no_meet("広島", "2026-05-22")
    idx.record_no_meet("平塚", "2026-05-22")
    idx.clear(venue="広島")
    assert idx.is_known_no_meet("広島", "2026-05-22") is False
    assert idx.is_known_no_meet("平塚", "2026-05-22") is True
    # 全削除
    idx.clear()
    assert idx.is_known_no_meet("平塚", "2026-05-22") is False


# ---------------------------------------------------------------------------
# CLI 統合
# ---------------------------------------------------------------------------


SYSTEM_ERROR_HTML = """<html><body>
<div id="SYSTEM_ERROR"><p class="message">エラーが発生しました。</p></div>
</body></html>"""


def test_cli_prepare_json_records_no_meet_and_skips_next_time(tmp_path: Path, monkeypatch):
    """1回目: SYSTEM_ERROR → 記録、2回目: 通信なしで即スキップ。"""
    # キャッシュ場所を tmp_path 配下に向ける
    monkeypatch.setattr(
        "app.cli.DEFAULT_CACHE_DIR", tmp_path / "cache"
    )

    session = MagicMock()
    session.get.return_value = _make_response(200, SYSTEM_ERROR_HTML)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    # 1回目
    result1 = runner.invoke(
        cli,
        [
            "prepare-json",
            "--venue", "広島",
            "--date", "2026-05-22",
            "--no-odds", "--no-results",
            "--weather-source", "manual",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result1.exit_code == 0, result1.output
    text1 = result1.output + (getattr(result1, "stderr", "") or "")
    assert "開催なし" in text1
    first_call_count = session.get.call_count
    assert first_call_count >= 1  # 1回は通信した

    # 2回目: 通信なしで即スキップする
    result2 = runner.invoke(
        cli,
        [
            "prepare-json",
            "--venue", "広島",
            "--date", "2026-05-22",
            "--no-odds", "--no-results",
            "--weather-source", "manual",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result2.exit_code == 0, result2.output
    text2 = result2.output + (getattr(result2, "stderr", "") or "")
    assert "記録済み" in text2
    # 2回目は新規通信なし
    assert session.get.call_count == first_call_count


def test_cli_prepare_json_refresh_cache_bypasses_no_meet_index(
    tmp_path: Path, monkeypatch,
):
    """--refresh-cache 指定時は開催なしインデックスを無視して再取得。"""
    monkeypatch.setattr("app.cli.DEFAULT_CACHE_DIR", tmp_path / "cache")
    # 事前に開催なしインデックスを作成
    idx = NoMeetIndex(tmp_path / "cache")
    idx.record_no_meet("広島", "2026-05-22", session_no=1)
    assert idx.is_known_no_meet("広島", "2026-05-22") is True

    # 今度は正常な出走表を返す（開催が復活したシナリオ）
    HTML = (Path(__file__).resolve().parent / "fixtures"
            / "kdreams_race_card_sample.html").read_text(encoding="utf-8")
    session = MagicMock()
    session.get.return_value = _make_response(200, HTML)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--venue", "広島",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-odds", "--no-results",
            "--weather-source", "manual",
            "--refresh-cache",   # 強制再取得
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    # 通信されている（インデックスを無視）
    assert session.get.call_count >= 1
