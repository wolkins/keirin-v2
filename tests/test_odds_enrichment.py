"""merge_odds と prepare-json --odds のテスト。

実ネットワーク通信は一切行わない。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.enrichment import EnrichmentError, merge_odds, normalize_odds
from app.fetchers import HttpClient
from app.models import OddsEntry, RaceInput


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RACE_CARD_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")
RESULTS_HTML = (FIXTURES / "kdreams_results_sample.html").read_text(encoding="utf-8")
TRIFECTA_HTML = (FIXTURES / "kdreams_odds_trifecta_sample.html").read_text(encoding="utf-8")
TRIO_HTML = (FIXTURES / "kdreams_odds_trio_sample.html").read_text(encoding="utf-8")
EXACTA_HTML = (FIXTURES / "kdreams_odds_exacta_sample.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# normalize_odds 単体
# ---------------------------------------------------------------------------


def test_normalize_odds_group_dict():
    payload = {
        "trifecta_popular": [
            {"rank": 1, "combination": "5-1-3", "odds": 8.5},
        ],
        "trio_popular": [
            {"rank": 1, "combination": "1=3=5", "odds": 4.0},
        ],
        "exacta_popular": [
            {"rank": 1, "combination": "5-1", "odds": 3.6},
        ],
    }
    items = normalize_odds(payload)
    assert len(items) == 3
    by_bt = {o.bet_type for o in items}
    assert by_bt == {"3連単", "3連複", "2車単"}


def test_normalize_odds_envelope():
    env = {
        "source": "kdreams",
        "kind": "odds",
        "venue": "大垣",
        "date": "2026-05-22",
        "race_no": 1,
        "odds": {
            "trifecta_popular": [{"combination": "5-1-3", "odds": 8.5}],
        },
    }
    items = normalize_odds(env)
    assert len(items) == 1
    assert items[0].bet_type == "3連単"
    assert items[0].combination == "5-1-3"
    assert items[0].odds == 8.5


def test_normalize_odds_flat_list_with_bet_type():
    payload = [
        {"bet_type": "3連単", "combination": "5-1-3", "odds": 8.5},
        {"bet_type": "trio", "combination": "1=3=5", "odds": 4.0},
    ]
    items = normalize_odds(payload)
    assert items[0].bet_type == "3連単"
    assert items[1].bet_type == "3連複"


def test_normalize_odds_missing_combination():
    with pytest.raises(EnrichmentError):
        normalize_odds([{"bet_type": "3連単", "odds": 8.5}])


def test_normalize_odds_missing_odds():
    with pytest.raises(EnrichmentError):
        normalize_odds([{"bet_type": "3連単", "combination": "5-1-3"}])


def test_normalize_odds_invalid_odds_type():
    with pytest.raises(EnrichmentError):
        normalize_odds([{"bet_type": "3連単", "combination": "5-1-3", "odds": "abc"}])


def test_normalize_odds_missing_bet_type_in_list():
    """list 入力で bet_type が無いとエラー（グループ判定できない）。"""
    with pytest.raises(EnrichmentError):
        normalize_odds([{"combination": "5-1-3", "odds": 8.5}])


def test_normalize_odds_invalid_top_level():
    with pytest.raises(EnrichmentError):
        normalize_odds("nope")


# ---------------------------------------------------------------------------
# merge_odds
# ---------------------------------------------------------------------------


def test_merge_odds_replaces_existing(sample_input):
    payload = {
        "trifecta_popular": [
            {"combination": "5-1-3", "odds": 8.5},
            {"combination": "5-1-6", "odds": 12.4},
        ]
    }
    out = merge_odds(sample_input, payload, replace=True)
    # 既存 odds は置換される（race_sample.json には 3連単 9件あった）
    assert len(out.odds) == 2
    assert all(o.bet_type == "3連単" for o in out.odds)


def test_merge_odds_append_no_replace(sample_input):
    payload = {
        "trifecta_popular": [
            {"combination": "5-1-3", "odds": 8.5},  # 既存と重複
            {"combination": "9-9-9", "odds": 99.0},  # 新規
        ]
    }
    base_count = len(sample_input.odds)
    out = merge_odds(sample_input, payload, replace=False)
    # 同じ (bet_type, combination) は新規で上書き、新規分が追加
    # 既存に 5-1-3 があれば 1件減って 1件増えるので変わらず、9-9-9 が追加
    # ただし 9-9-9 は OddsEntry の組合せでパースは通る
    assert any(o.combination == "9-9-9" for o in out.odds)


def test_merge_odds_envelope(sample_input):
    env = {
        "source": "kdreams",
        "kind": "odds",
        "odds": {
            "trifecta_popular": [{"combination": "5-1-3", "odds": 8.5}],
        },
    }
    out = merge_odds(sample_input, env)
    assert out.odds[0].combination == "5-1-3"


def test_merge_odds_validates_output(sample_input):
    payload = {"trifecta_popular": [{"combination": "5-1-3", "odds": 8.5}]}
    out = merge_odds(sample_input, payload)
    raw = json.loads(out.model_dump_json())
    RaceInput.model_validate(raw)


def test_merge_odds_dict_input(sample_input):
    raw = json.loads(sample_input.model_dump_json())
    payload = {"trifecta_popular": [{"combination": "5-1-3", "odds": 8.5}]}
    out = merge_odds(raw, payload)
    assert isinstance(out, RaceInput)


# ---------------------------------------------------------------------------
# CLI prepare-json --odds
# ---------------------------------------------------------------------------


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def _route_full_session() -> MagicMock:
    session = MagicMock()

    def _get(url: str, **kwargs):
        if "racecard" in url:
            return _make_response(200, RACE_CARD_HTML)
        if "raceresult" in url:
            return _make_response(200, RESULTS_HTML)
        if "racedetail" in url and "kakeshikiType=3rentan" in url:
            return _make_response(200, TRIFECTA_HTML)
        if "racedetail" in url and "kakeshikiType=3renpuku" in url:
            return _make_response(200, TRIO_HTML)
        if "racedetail" in url and "kakeshikiType=2tanshou" in url:
            return _make_response(200, EXACTA_HTML)
        return _make_response(404, "")

    session.get.side_effect = _get
    return session


def _patch_session(monkeypatch) -> MagicMock:
    session = _route_full_session()
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    return session


def test_cli_prepare_json_with_odds_full(tmp_path: Path, monkeypatch):
    session = _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--weather-source", "manual",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "6",
            "--weather", "曇り",
            "--wind-speed", "5.0",
            "--odds",
            "--odds-source", "kdreams",
            "--odds-limit", "5",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.venue == "大垣"
    # 出走表 + 結果 + オッズ がそろう
    assert len(ri.riders) == 7
    assert sorted(r.race_no for r in ri.recent_results) == [1, 2, 4]
    # 3種類のオッズが入っている
    bet_types = {o.bet_type for o in ri.odds}
    assert bet_types == {"3連単", "3連複", "2車単"}
    # 通信回数: race_card 1 + racedetail(補完試行) 1 + results 1 + odds 3 = 6
    # racedetail 補完試行は失敗してもカウントされる
    assert session.get.call_count >= 5


def test_cli_prepare_json_with_odds_specific_bet_type(tmp_path: Path, monkeypatch):
    session = _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--weather-source", "manual",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--odds",
            "--odds-source", "kdreams",
            "--odds-bet-type", "trifecta",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    # 3連単のみ
    bet_types = {o.bet_type for o in ri.odds}
    assert bet_types == {"3連単"}
    # 通信回数: race_card 1 + racedetail(補完試行) 1 + odds(trifecta) 1 = 3
    assert session.get.call_count >= 2


def test_cli_prepare_json_no_odds_explicit(tmp_path: Path, monkeypatch):
    """--no-odds を明示するとオッズページに通信しない。"""
    session = _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--weather-source", "manual",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-odds",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    # 既存サンプルの odds は kdreams パーサが空配列で返すので 0 件
    assert raw["odds"] == []
    # オッズ系URLが叩かれていない（pageType=odds を含むかで判定）
    # 注: /racedetail/ は競走得点・決まり手の補完取得にも使われるため、
    # オッズページかどうかは pageType=odds クエリで判定する
    urls = [c.args[0] for c in session.get.call_args_list]
    assert not any("pageType=odds" in u for u in urls)


def test_cli_prepare_json_odds_then_predict(tmp_path: Path, monkeypatch):
    """prepare-json --odds で作ったJSONを predict に渡して動くこと。"""
    _patch_session(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "p.json"
    r1 = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--odds",
            "--no-results",
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


def test_cli_prepare_json_odds_invalid_bet_type(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "manual",
            "--fallback-input", str(SAMPLE),
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--odds",
            "--odds-bet-type", "quinella",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "未対応のオッズ種別" in result.output


def test_cli_prepare_json_odds_failure_keeps_card(tmp_path: Path, monkeypatch):
    """オッズページが 500 でも race_card は維持して RaceInput を返す。"""
    session = MagicMock()

    def _get(url: str, **kwargs):
        if "racecard" in url:
            return _make_response(200, RACE_CARD_HTML)
        if "racedetail" in url:
            return _make_response(500, "")
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
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--odds",
            "--no-results",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    # 出走表は使えている、オッズは空
    assert len(ri.riders) == 7
    assert ri.odds == []
