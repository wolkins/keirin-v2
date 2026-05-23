"""rider_stats パッケージのテスト。

3ケース:
  - actual: 実数値が入っている manual JSON
  - estimated: yenjoy_static (戦法ラベル → 推定値)
  - missing: 取得失敗・ファイルなし・dynamic（未安定）

実ネットワーク通信・実ブラウザ起動なし。すべて monkeypatch / fixture で完結。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.rider_stats import (
    AVAILABLE_SOURCES,
    RiderStat,
    RiderStatsBundle,
    RiderStatsQualitySummary,
    compute_quality_summary,
    fetch_rider_stats,
)
from app.rider_stats.sources.manual import ManualSource


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rider_stats"


# ---------------------------------------------------------------------------
# モデル
# ---------------------------------------------------------------------------


def test_rider_stat_default_missing():
    """quality 既定は missing。"""
    r = RiderStat(car_no=1)
    assert r.quality == "missing"
    assert r.score == 0.0
    assert r.b_count == 0


def test_rider_stat_actual():
    r = RiderStat(
        car_no=1, name="X", score=110.0, b_count=12,
        nige=8, makuri=4, sashi=0, mark=0, quality="actual",
    )
    assert r.quality == "actual"


def test_rider_stat_invalid_quality():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RiderStat(car_no=1, quality="unknown")  # type: ignore[arg-type]


def test_compute_quality_summary_mixed():
    riders = [
        RiderStat(car_no=1, quality="actual"),
        RiderStat(car_no=2, quality="actual"),
        RiderStat(car_no=3, quality="estimated"),
        RiderStat(car_no=4, quality="missing"),
    ]
    qs = compute_quality_summary(riders)
    assert qs.actual_count == 2
    assert qs.estimated_count == 1
    assert qs.missing_count == 1
    assert qs.total == 4


def test_available_sources():
    assert "yenjoy" in AVAILABLE_SOURCES
    assert "yenjoy_dynamic" in AVAILABLE_SOURCES
    assert "manual" in AVAILABLE_SOURCES


# ---------------------------------------------------------------------------
# Case 1: actual - manual JSON から実数値取得
# ---------------------------------------------------------------------------


def test_manual_source_actual():
    """fixture/manual_actual.json から actual の RiderStat が取れる。"""
    src = ManualSource(path=FIXTURES / "manual_actual.json")
    bundle = src.fetch(venue="武雄", date=date(2026, 5, 23), race_no=9)
    assert bundle.source == "manual"
    assert len(bundle.riders) == 9
    assert all(r.quality == "actual" for r in bundle.riders)
    assert bundle.quality_summary.actual_count == 9
    assert bundle.quality_summary.estimated_count == 0
    assert bundle.quality_summary.missing_count == 0
    # 具体的な値
    by_car = {r.car_no: r for r in bundle.riders}
    assert by_car[7].score == pytest.approx(112.36)
    assert by_car[7].nige == 5
    assert by_car[7].name == "菊池岳仁"


# ---------------------------------------------------------------------------
# Case 2: estimated - yenjoy_static (戦法ラベルからの推定)
# ---------------------------------------------------------------------------


_YENJOY_HTML = """<html><body>
<div>4ヶ月得点 109.71 106.31 106.36 104.58 107.59 106.33 109.23 107.00 111.50</div>
<div>戦法 上がりタイム ３年間</div>
<div>追捲 10.8 秒 自在 10.7 秒 逃捲 11.1 秒 追込 10.9 秒
     捲逃 10.6 秒 追捲 11.0 秒 追込 10.6 秒 捲逃 10.5 秒 追捲 10.7 秒</div>
</body></html>"""


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def test_yenjoy_static_source_estimated(tmp_path, monkeypatch):
    """yenjoy_static は戦法ラベルから推定値で埋め、quality=estimated を立てる。"""
    from app.fetchers import HttpClient
    from app.fetchers.cache import FileCache
    from app.fetchers.rate_limit import RateLimiter
    from app.rider_stats.sources.yenjoy_static import YenJoyStaticSource

    session = MagicMock()
    session.get.return_value = _make_response(200, _YENJOY_HTML)
    client = HttpClient(
        cache=FileCache(cache_dir=tmp_path / "c", enabled=False),
        rate_limiter=RateLimiter(min_interval_seconds=0.0),
    )
    monkeypatch.setattr(client, "_get_session", lambda: session)

    src = YenJoyStaticSource(http_client=client)
    bundle = src.fetch(venue="武雄", date=date(2026, 5, 23), race_no=9)
    assert bundle.source == "yenjoy_static"
    # 9車分、全員 estimated
    assert len(bundle.riders) >= 7
    estimated_count = sum(1 for r in bundle.riders if r.quality == "estimated")
    assert estimated_count >= 7
    assert bundle.quality_summary.actual_count == 0
    # score は実数で入っている（4ヶ月得点）
    by_car = {r.car_no: r for r in bundle.riders}
    assert by_car[1].score == pytest.approx(109.71)
    # 戦法 → 推定の決まり手
    # 車番1: 「追捲」(逆順なので最後の「追捲」が車1)
    assert by_car[1].nige == 1  # 追捲 推定値: nige=1, makuri=4
    assert by_car[1].makuri == 4
    # 車番7: 「逃捲」
    assert by_car[7].nige == 5
    assert by_car[7].makuri == 5
    assert by_car[7].b_count == 3
    # notes に戦法ラベルが入る
    assert "逃捲" in (by_car[7].notes or "")


# ---------------------------------------------------------------------------
# Case 3: missing - 取得失敗
# ---------------------------------------------------------------------------


def test_manual_source_missing_file(tmp_path):
    """存在しないファイル → 全員 missing、警告付き。"""
    src = ManualSource(path=tmp_path / "nope.json")
    bundle = src.fetch(venue="武雄", date=date(2026, 5, 23), race_no=9)
    assert all(r.quality == "missing" for r in bundle.riders)
    assert bundle.quality_summary.missing_count == len(bundle.riders)
    assert bundle.quality_summary.actual_count == 0
    assert any("見つかりません" in w for w in bundle.warnings)


def test_yenjoy_static_source_missing_on_404(tmp_path, monkeypatch):
    """HTTP 404 → 全員 missing、予想全体を止めない（例外を投げない）。"""
    from app.fetchers import HttpClient
    from app.fetchers.cache import FileCache
    from app.fetchers.rate_limit import RateLimiter
    from app.rider_stats.sources.yenjoy_static import YenJoyStaticSource

    session = MagicMock()
    session.get.return_value = _make_response(404, "")
    client = HttpClient(
        cache=FileCache(cache_dir=tmp_path / "c", enabled=False),
        rate_limiter=RateLimiter(min_interval_seconds=0.0),
    )
    monkeypatch.setattr(client, "_get_session", lambda: session)

    src = YenJoyStaticSource(http_client=client)
    bundle = src.fetch(venue="武雄", date=date(2026, 5, 23), race_no=9)
    # 例外なく完了、riders 全員 missing
    assert all(r.quality == "missing" for r in bundle.riders)
    assert bundle.quality_summary.missing_count >= 7
    # 警告が出ている
    assert any("失敗" in w or "404" in w for w in bundle.warnings)


def test_yenjoy_dynamic_source_returns_missing_for_now():
    """yenjoy_dynamic は現状未安定なので missing を返し、警告を出す。

    （安定化したら quality=actual に変わる予定）
    """
    from app.rider_stats.sources.yenjoy_dynamic import YenJoyDynamicSource
    src = YenJoyDynamicSource()
    bundle = src.fetch(venue="武雄", date=date(2026, 5, 23), race_no=9)
    assert all(r.quality == "missing" for r in bundle.riders)
    assert any("未安定" in w or "安定化" in w for w in bundle.warnings)


# ---------------------------------------------------------------------------
# 0 と未取得を混同しないことの検証
# ---------------------------------------------------------------------------


def test_zero_is_not_treated_as_missing():
    """quality=actual で nige=0 等は『実際に0回』を意味する。missing と混同しない。"""
    r = RiderStat(
        car_no=1, name="番手専門", score=85.0,
        b_count=0, nige=0, makuri=0, sashi=8, mark=4,
        quality="actual",
    )
    assert r.quality == "actual"
    assert r.nige == 0  # 実際に逃げ 0回
    # quality_summary では actual カウント
    qs = compute_quality_summary([r])
    assert qs.actual_count == 1
    assert qs.missing_count == 0


def test_missing_with_zero_values():
    """quality=missing は数値が 0 でも『未取得』を意味する。"""
    r = RiderStat(car_no=1, quality="missing")
    assert r.score == 0.0
    assert r.quality == "missing"


# ---------------------------------------------------------------------------
# CLI fetch-rider-stats
# ---------------------------------------------------------------------------


def test_cli_fetch_rider_stats_manual(tmp_path):
    """CLI 経由で manual ソースから取得して JSON 出力。"""
    runner = CliRunner()
    out_path = tmp_path / "stats.json"
    result = runner.invoke(
        cli,
        [
            "fetch-rider-stats",
            "--source", "manual",
            "--venue", "武雄",
            "--date", "2026-05-23",
            "--race-no", "9",
            "--manual-path", str(FIXTURES / "manual_actual.json"),
            "--out", str(out_path),
            "--no-auto-session-search",
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["source"] == "manual"
    assert raw["venue"] == "武雄"
    assert raw["race_no"] == 9
    assert raw["quality_summary"]["actual_count"] == 9
    assert raw["quality_summary"]["missing_count"] == 0
    # riders 9件
    assert len(raw["riders"]) == 9
    # quality タグ
    assert all(r["quality"] == "actual" for r in raw["riders"])


def test_cli_fetch_rider_stats_yenjoy(tmp_path, monkeypatch):
    """CLI 経由で yenjoy ソースから取得。HTTP は monkeypatch でモック。"""
    from app.fetchers import HttpClient
    session = MagicMock()
    session.get.return_value = _make_response(200, _YENJOY_HTML)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)

    runner = CliRunner()
    out_path = tmp_path / "stats.json"
    result = runner.invoke(
        cli,
        [
            "fetch-rider-stats",
            "--source", "yenjoy",
            "--venue", "武雄",
            "--date", "2026-05-23",
            "--race-no", "9",
            "--no-auto-session-search",
            "--out", str(out_path),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["source"] == "yenjoy_static"
    # 推定値（estimated）
    assert raw["quality_summary"]["estimated_count"] >= 7
    assert raw["quality_summary"]["actual_count"] == 0


def test_cli_fetch_rider_stats_dynamic_falls_back_to_missing(tmp_path):
    """yenjoy_dynamic は未安定なので missing を返す。"""
    runner = CliRunner()
    out_path = tmp_path / "stats.json"
    result = runner.invoke(
        cli,
        [
            "fetch-rider-stats",
            "--source", "yenjoy_dynamic",
            "--venue", "武雄",
            "--date", "2026-05-23",
            "--race-no", "9",
            "--no-auto-session-search",
            "--out", str(out_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["quality_summary"]["missing_count"] >= 7
    assert raw["quality_summary"]["actual_count"] == 0


def test_cli_fetch_rider_stats_invalid_source():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "fetch-rider-stats",
            "--source", "winticket",  # 未対応
            "--venue", "武雄",
            "--date", "2026-05-23",
            "--race-no", "9",
            "--out", "/tmp/nope.json",
        ],
    )
    assert result.exit_code != 0
    # click が --source の choice エラーを返す
    assert "Invalid value" in result.output or "winticket" in result.output


# ---------------------------------------------------------------------------
# fetch_rider_stats 公開関数
# ---------------------------------------------------------------------------


def test_fetch_rider_stats_manual_via_public_api():
    bundle = fetch_rider_stats(
        source="manual",
        venue="武雄",
        date=date(2026, 5, 23),
        race_no=9,
        manual_path=FIXTURES / "manual_actual.json",
    )
    assert isinstance(bundle, RiderStatsBundle)
    assert bundle.quality_summary.actual_count == 9


def test_fetch_rider_stats_unknown_source_raises():
    with pytest.raises(ValueError, match="未対応のソース"):
        fetch_rider_stats(
            source="foobar",
            venue="武雄",
            date=date(2026, 5, 23),
            race_no=9,
        )
