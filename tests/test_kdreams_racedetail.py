"""Kドリームス /racedetail/ パーサ + 補完取得テスト。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.fetchers.parsers.kdreams_race_detail import (
    merge_stats_into_riders,
    normalize_name,
    parse_race_detail_html,
)


# ---------------------------------------------------------------------------
# parse_race_detail_html
# ---------------------------------------------------------------------------


_RACEDETAIL_SAMPLE_HTML = """<html><body>
<table>
<tr>
  <td>4</td><td>1</td>
  <td>高津 晃治<br>東京/46/87</td>
  <td>S2</td><td>追</td>
  <td>3.92</td><td>100.54</td>
  <td>1</td><td>0</td><td>0</td><td>0</td>
  <td>5</td><td>2</td><td>4</td><td>3</td>
  <td>8</td><td>18</td>
  <td>12.1</td><td>21.2</td><td>45.4</td>
</tr>
<tr>
  <td>2</td><td>2</td>
  <td>望月 湧世<br>栃木/30/108</td>
  <td>S1</td><td>逃</td>
  <td>4.21</td><td>98.44</td>
  <td>14</td><td>5</td><td>2</td><td>0</td>
  <td>6</td><td>3</td><td>5</td><td>2</td>
</tr>
<tr>
  <td>3</td><td>3</td>
  <td>山崎 泰己<br>大阪/40/95</td>
  <td>S1</td><td>追</td>
  <td>4.00</td><td>96.92</td>
  <td>0</td><td>0</td><td>1</td><td>1</td>
  <td>3</td><td>5</td><td>4</td><td>6</td>
</tr>
</table>
</body></html>
"""


def test_parse_race_detail_html_basic():
    """3選手分の score / 決まり手が取れる。"""
    stats = parse_race_detail_html(_RACEDETAIL_SAMPLE_HTML)
    assert "高津 晃治" in stats
    high = stats["高津 晃治"]
    assert high["score"] == pytest.approx(100.54)
    assert high["nige"] == 1
    assert high["makuri"] == 0
    assert high["sashi"] == 0
    assert high["mark"] == 0

    mochizuki = stats["望月 湧世"]
    assert mochizuki["score"] == pytest.approx(98.44)
    assert mochizuki["nige"] == 14
    assert mochizuki["makuri"] == 5
    assert mochizuki["sashi"] == 2
    assert mochizuki["mark"] == 0


def test_parse_race_detail_html_empty():
    assert parse_race_detail_html("") == {}
    assert parse_race_detail_html("<html><body></body></html>") == {}


def test_parse_race_detail_html_no_score_skip():
    """選手名はあるが score が無い行はスキップ。"""
    html = """<html><body><table>
    <tr>
      <td>1</td><td>選手 名前</td><td>S1</td><td>追</td>
    </tr></table></body></html>"""
    stats = parse_race_detail_html(html)
    # スコアが無いのでマッチしない
    assert stats == {}


# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------


def test_normalize_name_strips_spaces():
    assert normalize_name("高津 晃治") == "高津晃治"
    assert normalize_name("高津　晃治") == "高津晃治"  # 全角スペース
    assert normalize_name(" 高津  晃治 ") == "高津晃治"


# ---------------------------------------------------------------------------
# merge_stats_into_riders
# ---------------------------------------------------------------------------


def test_merge_stats_into_riders_full():
    """riders の各選手にスコア・決まり手が反映される。"""
    riders = [
        {"car_no": 1, "name": "望月湧世", "score": 0.0, "b_count": 0,
         "nige": 0, "makuri": 0, "sashi": 0, "mark": 0, "stats_missing": True},
        {"car_no": 2, "name": "高津 晃治", "score": 0.0, "b_count": 0,
         "nige": 0, "makuri": 0, "sashi": 0, "mark": 0, "stats_missing": True},
    ]
    stats = parse_race_detail_html(_RACEDETAIL_SAMPLE_HTML)
    matched = merge_stats_into_riders(riders, stats)
    assert matched == 2

    # 望月（半角スペース無しでもマッチ）
    r1 = next(r for r in riders if r["car_no"] == 1)
    assert r1["score"] == pytest.approx(98.44)
    assert r1["nige"] == 14
    assert r1["stats_missing"] is False

    # 高津（スペース有りでもマッチ）
    r2 = next(r for r in riders if r["car_no"] == 2)
    assert r2["score"] == pytest.approx(100.54)
    assert r2["nige"] == 1
    assert r2["stats_missing"] is False


def test_merge_stats_into_riders_partial():
    """一部だけマッチ。マッチしない選手は stats_missing のまま。"""
    riders = [
        {"car_no": 1, "name": "山崎 泰己", "score": 0.0, "b_count": 0,
         "nige": 0, "makuri": 0, "sashi": 0, "mark": 0, "stats_missing": True},
        {"car_no": 2, "name": "存在しない選手", "score": 0.0, "b_count": 0,
         "nige": 0, "makuri": 0, "sashi": 0, "mark": 0, "stats_missing": True},
    ]
    stats = parse_race_detail_html(_RACEDETAIL_SAMPLE_HTML)
    matched = merge_stats_into_riders(riders, stats)
    assert matched == 1
    # マッチしない選手は元のまま
    r2 = next(r for r in riders if r["car_no"] == 2)
    assert r2["stats_missing"] is True
    assert r2["score"] == 0.0


# ---------------------------------------------------------------------------
# KDreamsFetcher 補完統合テスト
# ---------------------------------------------------------------------------


def _make_response(status: int, text: str):
    resp = SimpleNamespace(status_code=status, text=text, headers={})
    return resp


def test_enrich_stats_from_racedetail_integrates_with_payload():
    """KDreamsFetcher._enrich_stats_from_racedetail が payload を更新する。

    既存 _build_rider_real で stats_missing=True の選手に対して、
    racedetail の HTML を渡すと score / 決まり手が埋まる。
    """
    from app.fetchers import HttpClient, KDreamsFetcher
    from app.fetchers.cache import FileCache
    from app.fetchers.rate_limit import RateLimiter
    from unittest.mock import MagicMock

    payload = {
        "riders": [
            {"car_no": 1, "name": "高津 晃治", "score": 0.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "stats_missing": True},
            {"car_no": 2, "name": "望月湧世", "score": 0.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "stats_missing": True},
        ],
        "race": {"venue": "宇都宮", "race_no": 1},
    }

    # http_client が racedetail HTML を返す
    session = MagicMock()
    session.get.return_value = _make_response(200, _RACEDETAIL_SAMPLE_HTML)
    cache = FileCache(cache_dir="/tmp/keirin_test_cache", ttl_seconds=60, enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    client = HttpClient(cache=cache, rate_limiter=rl)
    client._get_session = lambda: session  # type: ignore

    fetcher = KDreamsFetcher(http_client=client)
    matched = fetcher._enrich_stats_from_racedetail(
        payload, venue="宇都宮", date=date(2026, 5, 21),
        race_no=1, session_no=3,
    )
    assert matched == 2
    # 補完済み
    r1 = next(r for r in payload["riders"] if r["car_no"] == 1)
    assert r1["score"] == pytest.approx(100.54)
    assert r1["nige"] == 1
    assert r1["stats_missing"] is False
    r2 = next(r for r in payload["riders"] if r["car_no"] == 2)
    assert r2["score"] == pytest.approx(98.44)
    assert r2["nige"] == 14
    assert r2["stats_missing"] is False


def test_enrich_stats_silent_on_http_failure():
    """racedetail 取得が 例外を投げても黙って 0 を返す。"""
    from app.fetchers import HttpClient, KDreamsFetcher
    from app.fetchers.cache import FileCache
    from app.fetchers.rate_limit import RateLimiter
    from unittest.mock import MagicMock

    payload = {
        "riders": [
            {"car_no": 1, "name": "X1", "score": 0.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "stats_missing": True},
        ],
    }

    session = MagicMock()
    session.get.side_effect = RuntimeError("Network failed")
    cache = FileCache(cache_dir="/tmp/keirin_test_cache", ttl_seconds=60, enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    client = HttpClient(cache=cache, rate_limiter=rl)
    client._get_session = lambda: session  # type: ignore

    fetcher = KDreamsFetcher(http_client=client)
    # 例外を投げず 0 を返す
    matched = fetcher._enrich_stats_from_racedetail(
        payload, venue="宇都宮", date=date(2026, 5, 21),
        race_no=1, session_no=3,
    )
    assert matched == 0
    # 補完されていない
    assert payload["riders"][0]["stats_missing"] is True


def test_fetcher_default_enrich_stats_is_false():
    """fetch_race_card のシグネチャで enrich_stats のデフォルトは False。"""
    import inspect
    from app.fetchers import KDreamsFetcher
    sig = inspect.signature(KDreamsFetcher.fetch_race_card)
    assert sig.parameters["enrich_stats"].default is False
