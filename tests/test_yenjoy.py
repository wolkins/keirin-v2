"""yen-joy 補完取得のテスト。

実ネットワーク通信なし。HTTP は monkeypatch で差し替える。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.fetchers.parsers.yenjoy_race import (
    infer_stats_from_strategy,
    merge_yenjoy_scores_into_riders,
    parse_yenjoy_race_html,
    parse_yenjoy_strategies,
)
from app.fetchers.yenjoy import (
    YenJoyFetcher,
    build_yenjoy_race_url,
)


# ---------------------------------------------------------------------------
# build_yenjoy_race_url
# ---------------------------------------------------------------------------


def test_build_yenjoy_race_url_initial_day():
    """連戦初日（session_no=1）: 初日日付と当日が同じ。"""
    url = build_yenjoy_race_url("武雄", date(2026, 5, 23), 7, session_no=1)
    # 場ID 84 = 武雄
    assert "/84/" in url
    assert "/20260523/20260523/7" in url
    assert "/202605/" in url


def test_build_yenjoy_race_url_third_day():
    """連戦3日目（session_no=3）: 当日=初日+2。"""
    url = build_yenjoy_race_url("武雄", date(2026, 5, 21), 7, session_no=3)
    # 初日=5/21, 当日=5/23
    assert "/20260521/20260523/7" in url


def test_build_yenjoy_race_url_returns_full_url():
    url = build_yenjoy_race_url("武雄", date(2026, 5, 23), 3)
    assert url.startswith("https://www.yen-joy.net/kaisai/race/forecast/detail/")


# ---------------------------------------------------------------------------
# parse_yenjoy_race_html
# ---------------------------------------------------------------------------


_SAMPLE_HTML = """<html><body>
<div>武雄競輪 3R 出走表</div>
<table>
<tr><th>脚力</th><td>87</td><td>84</td><td>84</td><td>82</td>
    <td>86</td><td>84</td><td>85</td><td>86</td><td>86</td></tr>
<tr><th>4ヶ月得点</th>
    <td>109.71</td><td>106.31</td><td>106.36</td>
    <td>104.58</td><td>107.59</td><td>106.33</td>
    <td>109.23</td><td>107.00</td><td>111.50</td>
</tr>
</table>
</body></html>"""


def test_parse_yenjoy_race_html_basic():
    """4ヶ月得点ラベル直後の9個の小数が抽出される。"""
    scores = parse_yenjoy_race_html(_SAMPLE_HTML)
    assert len(scores) == 9
    assert scores[0] == pytest.approx(109.71)
    assert scores[1] == pytest.approx(106.31)
    assert scores[8] == pytest.approx(111.50)


def test_parse_yenjoy_race_html_empty():
    assert parse_yenjoy_race_html("") == []
    assert parse_yenjoy_race_html("<html></html>") == []


def test_parse_yenjoy_race_html_no_label_fallback():
    """ラベルが無くても、先頭から競走得点っぽい数値を拾えれば返す。"""
    html = "<html>得点 109.71 106.31 106.36 104.58 107.59 106.33 109.23</html>"
    scores = parse_yenjoy_race_html(html)
    # 4ヶ月得点ラベルが無いのでフォールバック → 7件以上で返る
    assert len(scores) >= 7
    assert 109.71 in scores


# ---------------------------------------------------------------------------
# merge_yenjoy_scores_into_riders
# ---------------------------------------------------------------------------


def test_merge_scores_full_match():
    riders = [
        {"car_no": i, "name": f"R{i}", "score": 0.0, "stats_missing": True}
        for i in range(1, 10)
    ]
    scores = [109.71, 106.31, 106.36, 104.58, 107.59, 106.33, 109.23, 107.00, 111.50]
    matched = merge_yenjoy_scores_into_riders(riders, scores)
    assert matched == 9
    # 車番順で対応
    by_car = {r["car_no"]: r for r in riders}
    assert by_car[1]["score"] == pytest.approx(109.71)
    assert by_car[5]["score"] == pytest.approx(107.59)
    # 補完されたら stats_missing=False
    assert all(r["stats_missing"] is False for r in riders)


def test_merge_scores_partial():
    """riders 数 > スコア数のとき、不足分は補完されない。"""
    riders = [
        {"car_no": i, "name": f"R{i}", "score": 0.0, "stats_missing": True}
        for i in range(1, 10)
    ]
    scores = [100.0, 101.0, 102.0]  # 3件しか取れない
    matched = merge_yenjoy_scores_into_riders(riders, scores)
    assert matched == 3


def test_merge_scores_skips_zero_or_none():
    riders = [
        {"car_no": 1, "name": "R1", "score": 0.0, "stats_missing": True},
        {"car_no": 2, "name": "R2", "score": 0.0, "stats_missing": True},
    ]
    matched = merge_yenjoy_scores_into_riders(riders, [None, 0.0])
    assert matched == 0
    assert riders[0]["stats_missing"] is True
    assert riders[1]["stats_missing"] is True


# ---------------------------------------------------------------------------
# YenJoyFetcher (HTTPモック)
# ---------------------------------------------------------------------------


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def test_yenjoy_fetcher_enrich_scores(tmp_path, monkeypatch):
    """YenJoyFetcher.enrich_scores が yen-joy HTML から score を補完する。"""
    from app.fetchers import HttpClient
    from app.fetchers.cache import FileCache
    from app.fetchers.rate_limit import RateLimiter

    session = MagicMock()
    session.get.return_value = _make_response(200, _SAMPLE_HTML)

    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    client = HttpClient(cache=cache, rate_limiter=rl)
    monkeypatch.setattr(client, "_get_session", lambda: session)

    fetcher = YenJoyFetcher(http_client=client)
    payload = {
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 0.0, "stats_missing": True}
            for i in range(1, 10)
        ],
    }
    matched = fetcher.enrich_scores(
        payload, venue="武雄", date=date(2026, 5, 23),
        race_no=3, session_no=1,
    )
    assert matched == 9
    # スコア反映
    assert payload["riders"][0]["score"] == pytest.approx(109.71)
    assert all(r["stats_missing"] is False for r in payload["riders"])


def test_yenjoy_fetcher_silent_on_404(tmp_path, monkeypatch):
    """404 など失敗時は黙って 0 を返す。"""
    from app.fetchers import HttpClient
    from app.fetchers.cache import FileCache
    from app.fetchers.rate_limit import RateLimiter

    session = MagicMock()
    session.get.return_value = _make_response(404, "")
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    client = HttpClient(cache=cache, rate_limiter=rl)
    monkeypatch.setattr(client, "_get_session", lambda: session)

    fetcher = YenJoyFetcher(http_client=client)
    payload = {
        "riders": [
            {"car_no": 1, "name": "R1", "score": 0.0, "stats_missing": True},
        ],
    }
    matched = fetcher.enrich_scores(
        payload, venue="武雄", date=date(2026, 5, 23),
        race_no=3, session_no=1,
    )
    assert matched == 0
    assert payload["riders"][0]["stats_missing"] is True


# ---------------------------------------------------------------------------
# 統合: KDreamsFetcher._enrich_stats_from_racedetail が yen-joy にフォールバック
# ---------------------------------------------------------------------------


def test_kdreams_enrich_falls_back_to_yenjoy(tmp_path, monkeypatch):
    """Kドリームス /racedetail/ がログインエラー HTML を返したら、
    yen-joy にフォールバックして score を補完する。
    """
    from app.fetchers import HttpClient, KDreamsFetcher
    from app.fetchers.cache import FileCache
    from app.fetchers.rate_limit import RateLimiter

    # Kドリームス /racedetail/ はエラーHTML, yen-joy は競走得点を返す
    KDREAMS_ERROR_HTML = "エラーが発生しました。再度ログインからやり直してください。"
    session = MagicMock()

    def _get(url, **kwargs):
        if "yen-joy.net" in url:
            return _make_response(200, _SAMPLE_HTML)
        if "racedetail" in url:
            return _make_response(200, KDREAMS_ERROR_HTML)
        return _make_response(200, KDREAMS_ERROR_HTML)
    session.get.side_effect = _get

    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    client = HttpClient(cache=cache, rate_limiter=rl)
    monkeypatch.setattr(client, "_get_session", lambda: session)

    fetcher = KDreamsFetcher(http_client=client)
    payload = {
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 0.0, "stats_missing": True}
            for i in range(1, 10)
        ],
    }
    matched = fetcher._enrich_stats_from_racedetail(
        payload, venue="武雄", date=date(2026, 5, 23),
        race_no=3, session_no=1,
    )
    assert matched == 9
    # yen-joy が叩かれている
    urls = [c.args[0] for c in session.get.call_args_list]
    assert any("yen-joy.net" in u for u in urls)


# ---------------------------------------------------------------------------
# 戦法ラベル抽出 + 推定
# ---------------------------------------------------------------------------


_STRATEGY_HTML = """<html><body>
<div>戦法</div>
<div>追捲 10.8 秒 自在 10.7 秒 逃捲 11.1 秒 追込 10.9 秒
     捲逃 10.6 秒 追捲 11.0 秒 追込 10.6 秒 捲逃 10.5 秒 追捲 10.7 秒</div>
</body></html>"""


def test_parse_yenjoy_strategies_basic():
    """yen-joy の戦法ラベル9車分（逆順）を車番順に並べ替える。"""
    strategies = parse_yenjoy_strategies(_STRATEGY_HTML)
    # raw は逆順、 result は car_no 1〜9 の順
    assert len(strategies) == 9
    assert strategies[0] == "追捲"   # 車1（HTMLでは最後）
    assert strategies[6] == "逃捲"   # 車7
    assert strategies[8] == "追捲"   # 車9（HTMLでは最初）


def test_parse_yenjoy_strategies_empty():
    assert parse_yenjoy_strategies("") == []
    assert parse_yenjoy_strategies("<html></html>") == []


def test_infer_stats_from_strategy_known():
    """既知の戦法ラベルから (nige, makuri, sashi, mark, b_count) が返る。"""
    nige = infer_stats_from_strategy("逃")
    assert nige is not None
    assert nige[0] == 8  # nige が大きい
    assert nige[4] >= 3  # b_count あり

    oikomi = infer_stats_from_strategy("追込")
    assert oikomi is not None
    assert oikomi[3] == 7  # mark が大きい
    assert oikomi[4] == 0  # b_count ゼロ

    oimakuri = infer_stats_from_strategy("追捲")
    assert oimakuri is not None
    assert oimakuri[1] == 4  # makuri 成分あり
    assert oimakuri[3] == 4  # mark 成分あり


def test_infer_stats_from_strategy_unknown():
    """未知ラベルは None。"""
    assert infer_stats_from_strategy("不明") is None
    assert infer_stats_from_strategy("") is None


def test_merge_yenjoy_with_strategies():
    """score + 戦法ラベルから決まり手・B数を補完する。"""
    riders = [
        {"car_no": i, "name": f"R{i}", "score": 0.0, "b_count": 0,
         "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
         "style_tags": [], "stats_missing": True}
        for i in range(1, 10)
    ]
    scores = [109.71, 106.31, 106.36, 104.58, 107.59, 106.33, 109.23, 107.00, 111.50]
    strategies = ["逃", "追", "差", "自在", "追捲", "追込", "逃捲", "両", "捲逃"]
    matched = merge_yenjoy_scores_into_riders(
        riders, scores, strategies_by_car_index=strategies,
    )
    assert matched == 9
    # 車1: 「逃」 → nige=8, b_count=4
    assert riders[0]["nige"] == 8
    assert riders[0]["b_count"] == 4
    # 車3: 「差」 → sashi=7
    assert riders[2]["sashi"] == 7
    # 車7: 「逃捲」 → nige=5, makuri=5
    assert riders[6]["nige"] == 5
    assert riders[6]["makuri"] == 5
    # style_tags に戦法が追加されている
    assert "逃" in riders[0]["style_tags"]
    assert "逃捲" in riders[6]["style_tags"]
    # stats_missing は全員 false
    assert all(r["stats_missing"] is False for r in riders)


def test_merge_yenjoy_preserves_existing_nonzero():
    """既存値が 0 以外の場合は推定値で上書きしない。"""
    riders = [
        {"car_no": 1, "name": "R1", "score": 0.0, "b_count": 5,
         "nige": 10, "makuri": 0, "sashi": 0, "mark": 0,
         "style_tags": [], "stats_missing": True},
    ]
    scores = [100.0]
    strategies = ["逃"]  # 推定値 (nige=8, b_count=4)
    merge_yenjoy_scores_into_riders(riders, scores, strategies_by_car_index=strategies)
    # 既存の 10 / 5 がそのまま（推定値の 8 / 4 で上書きされない）
    assert riders[0]["nige"] == 10
    assert riders[0]["b_count"] == 5
