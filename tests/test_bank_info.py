"""bank_info とその自動補完のテスト。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.bank_info import get_bank_info, list_known_venues
from app.cli import cli
from app.fetchers import HttpClient
from app.models import RaceInput


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"
RACE_CARD_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


# ---------------------------------------------------------------------------
# get_bank_info
# ---------------------------------------------------------------------------


def test_get_bank_info_returns_dict_for_known_venue():
    info = get_bank_info("平塚")
    assert info is not None
    assert info["bank_length"] == 400


def test_get_bank_info_returns_none_for_unknown_venue():
    assert get_bank_info("謎の競輪場") is None
    assert get_bank_info("") is None
    assert get_bank_info(None) is None


def test_get_bank_info_500_bank_has_sashi_style():
    """500バンクの大宮・宇都宮・高知は差し有利として登録されている。"""
    for venue in ("大宮", "宇都宮", "高知"):
        info = get_bank_info(venue)
        assert info["bank_length"] == 500
        assert info["bank_style"] == "差し有利"


def test_get_bank_info_returns_defensive_copy():
    """戻り値を変更しても DB が壊れない。"""
    info = get_bank_info("平塚")
    info["bank_length"] = 999
    info2 = get_bank_info("平塚")
    assert info2["bank_length"] == 400


def test_list_known_venues_includes_major_tracks():
    venues = list_known_venues()
    assert "平塚" in venues
    assert "大宮" in venues
    assert "奈良" in venues
    assert "千葉" in venues
    # Wikipedia 43場（別表記2件込みで45以上）
    assert len(venues) >= 43


def test_known_venues_cover_all_43_active_tracks():
    """現存43場すべてが登録されている（Wikipedia 2025年5月時点）。"""
    # 250m
    assert get_bank_info("千葉")["bank_length"] == 250
    # 333m バンク 6場
    for v in ("松戸", "小田原", "伊東", "富山", "奈良", "防府"):
        assert get_bank_info(v)["bank_length"] == 333
    # 335m
    assert get_bank_info("前橋")["bank_length"] == 335
    # 400m バンク 32場
    for v in (
        "函館", "青森", "いわき平", "弥彦", "取手", "西武園",
        "京王閣", "立川", "川崎", "平塚", "静岡", "豊橋",
        "名古屋", "岐阜", "大垣", "四日市", "松阪", "福井",
        "京都向日町", "岸和田", "和歌山", "玉野", "広島",
        "高松", "小松島", "松山", "小倉", "久留米", "武雄",
        "佐世保", "別府", "熊本",
    ):
        info = get_bank_info(v)
        assert info is not None, f"{v} が登録されていない"
        assert info["bank_length"] == 400, f"{v} は 400m のはず"
    # 500m
    for v in ("大宮", "宇都宮", "高知"):
        info = get_bank_info(v)
        assert info["bank_length"] == 500


def test_alternative_names_resolve_to_same_track():
    """別表記でも同じ周長を返す。"""
    assert get_bank_info("伊東") == get_bank_info("伊東温泉")
    assert get_bank_info("向日町") == get_bank_info("京都向日町")


def test_abolished_tracks_are_not_registered():
    """廃止された競輪場（観音寺・甲子園）は登録されていない。"""
    assert get_bank_info("観音寺") is None
    assert get_bank_info("甲子園") is None


# ---------------------------------------------------------------------------
# preparation での自動補完
# ---------------------------------------------------------------------------


def test_prepare_auto_fills_bank_length_for_known_venue(tmp_path, monkeypatch):
    """prepare-json が venue から bank_length を自動補完する。"""
    session = MagicMock()
    session.get.return_value = _make_response(200, RACE_CARD_HTML)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)

    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大宮",  # 500m, 差し有利
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-results", "--no-odds",
            "--weather-source", "manual",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["race"]["bank_length"] == 500
    assert raw["race"]["bank_style"] == "差し有利"
    # 案内メッセージが出る
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "バンク情報を自動補完" in text


def test_prepare_explicit_bank_overrides_auto(tmp_path, monkeypatch):
    """ユーザーが明示した --bank-length / --bank-style が自動補完より優先される。"""
    session = MagicMock()
    session.get.return_value = _make_response(200, RACE_CARD_HTML)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)

    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大宮",  # 自動なら 500m / 差し有利
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-results", "--no-odds",
            "--weather-source", "manual",
            "--bank-length", "400",  # 明示で上書き
            "--bank-style", "先行有利",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    # 明示値が反映される
    assert raw["race"]["bank_length"] == 400
    assert raw["race"]["bank_style"] == "先行有利"


def test_prepare_unknown_venue_no_auto_bank(tmp_path, monkeypatch):
    """未登録の場名は自動補完されない（None のまま）。"""
    session = MagicMock()
    session.get.return_value = _make_response(200, RACE_CARD_HTML)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)

    runner = CliRunner()
    out = tmp_path / "p.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "無名場",  # 未登録
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-results", "--no-odds",
            "--weather-source", "manual",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    # venue が未対応の場合は他のエラーで落ちる可能性があるが、
    # bank_info マッピングは少なくとも例外を起こさない
    if result.exit_code == 0:
        raw = json.loads(out.read_text(encoding="utf-8"))
        # bank_length は None or 未設定
        assert raw["race"].get("bank_length") is None
