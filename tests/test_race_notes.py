"""共通 RaceNotes パイプラインのテスト。

- signals 抽出 (共通辞書)
- manual_text パーサ
- dict ↔ RaceNotes 変換
- merge_race_notes が Pydantic / dict 両方を受ける
- 既存テストを壊さない
- ガールズで番手signalがライン扱いされない（既存 test_review_hardening でカバー済み）
- 実ネットワーク通信なし
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.enrichment import merge_race_notes
from app.models import RaceInput, RaceNotes, RiderNote
from app.prompt_builder import build_race_notes_section
from app.race_notes import (
    ManualTextParseError,
    KNOWN_SIGNALS,
    SIGNAL_KEYWORDS,
    dict_to_race_notes,
    extract_signals,
    parse_race_notes_text,
)


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def _load_sample() -> RaceInput:
    return RaceInput.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# signals 辞書
# ---------------------------------------------------------------------------


def test_known_signals_includes_spec_19():
    """仕様の19種のsignals が KNOWN_SIGNALS に含まれる。"""
    must_have = {
        "自力", "前々", "単騎", "自在", "番手", "3番手",
        "地元", "状態良い", "疲れ", "不安", "落車明け",
        "穴評価", "本命評価",
        "差し有力", "先行有力", "位置取り良い",
        "コメント強気", "コメント弱気",
    }
    assert must_have.issubset(set(KNOWN_SIGNALS))


def test_extract_signals_from_japanese_text():
    s = extract_signals("自力。状態は良い。前々に踏める。")
    assert "自力" in s
    assert "前々" in s
    assert "状態良い" in s


def test_extract_signals_dedup():
    s = extract_signals("自力で前々。自力ライン先頭")
    assert s.count("自力") == 1


def test_extract_signals_empty():
    assert extract_signals("") == []
    assert extract_signals("関係ない文章。") == []


def test_extract_signals_negatives():
    s = extract_signals("不安あり。重い印象。疲れ気味。")
    assert "不安" in s
    assert "重い" in s
    assert "疲れ" in s


def test_extract_signals_positive_phrases():
    s = extract_signals("コメント強気。状態◎。位置取りが良い。差し脚良好。")
    assert "コメント強気" in s
    assert "状態良い" in s
    assert "位置取り良い" in s
    assert "差し有力" in s


# ---------------------------------------------------------------------------
# manual_text パーサ
# ---------------------------------------------------------------------------


SAMPLE_TEXT = """場名: 松山
日付: 2026-05-22
R: 10

並び: 5-1-3 / 6-4 / 7
記者見解: 本線は5-1。穴は6-4

5 長野魅切 自力。状態は良い。前々に踏める。
1 久樹 長野マーク。番手。差し脚良好。
3 山本 3番手。位置取り良い。
6 永井 別線番手。差し脚良好で穴狙い妙味。
4 山根 単騎・自在。状態不安あり。
7 高橋 単騎。
2 夏目 追込。重い。
"""


def test_parse_manual_text_basic():
    notes = parse_race_notes_text(SAMPLE_TEXT)
    assert notes.source == "manual_text"
    assert notes.venue == "松山"
    assert notes.date == Date(2026, 5, 22)
    assert notes.race_no == 10
    assert notes.line_hint == "5-1-3 / 6-4 / 7"
    assert notes.prediction_hint == "本線は5-1。穴は6-4"
    assert len(notes.rider_notes) == 7


def test_parse_manual_text_signals_per_rider():
    notes = parse_race_notes_text(SAMPLE_TEXT)
    by_car = {n.car_no: n for n in notes.rider_notes}
    assert "自力" in by_car[5].signals
    assert "状態良い" in by_car[5].signals
    assert "前々" in by_car[5].signals
    assert "番手" in by_car[1].signals
    assert "差し有力" in by_car[1].signals
    assert "単騎" in by_car[4].signals
    assert "自在" in by_car[4].signals
    assert "不安" in by_car[4].signals
    assert "追込" in by_car[2].signals


def test_parse_manual_text_comment_summary_under_120_chars():
    """comment_summary は120文字以内（Pydantic で強制）。"""
    notes = parse_race_notes_text(SAMPLE_TEXT)
    for n in notes.rider_notes:
        assert len(n.comment_summary) <= 120


def test_parse_manual_text_no_raw_excerpt_saved():
    """raw 全文は保存されない（raw_excerpt は None または50文字以内）。"""
    notes = parse_race_notes_text(SAMPLE_TEXT)
    for n in notes.rider_notes:
        if n.raw_excerpt is not None:
            assert len(n.raw_excerpt) <= 50


def test_parse_manual_text_source_override():
    notes = parse_race_notes_text(SAMPLE_TEXT, source="winticket")
    assert notes.source == "winticket"


def test_parse_manual_text_explicit_args_override_header():
    """関数引数が本文ヘッダより優先される。"""
    notes = parse_race_notes_text(
        SAMPLE_TEXT, venue="大垣", date="2026-06-01", race_no=5,
    )
    assert notes.venue == "大垣"
    assert notes.date == Date(2026, 6, 1)
    assert notes.race_no == 5


def test_parse_manual_text_empty_raises():
    with pytest.raises(ManualTextParseError):
        parse_race_notes_text("")


def test_parse_manual_text_no_riders_raises():
    """選手行も見解も無いテキストは例外。"""
    with pytest.raises(ManualTextParseError):
        parse_race_notes_text("ただの文章\n何も無い")


def test_parse_manual_text_only_hint_succeeds():
    """選手行は無くても、並び/見解だけあれば成功。"""
    notes = parse_race_notes_text(
        "並び: 1-2-3 / 4-5\n記者見解: 本線は1-2"
    )
    assert notes.line_hint == "1-2-3 / 4-5"
    assert notes.prediction_hint == "本線は1-2"
    assert notes.rider_notes == []


def test_parse_manual_text_circle_digit_car_no():
    """丸数字での車番指定もサポート。"""
    notes = parse_race_notes_text("①長野 自力\n②夏目 番手")
    assert {n.car_no for n in notes.rider_notes} == {1, 2}


# ---------------------------------------------------------------------------
# dict ↔ RaceNotes 変換
# ---------------------------------------------------------------------------


def test_dict_to_race_notes_basic():
    payload = {
        "source": "tospo",
        "venue": "松山",
        "race_no": 10,
        "rider_notes": [
            {"car_no": 5, "name": "長野", "comment_summary": "自力",
             "signals": ["自力"]},
        ],
        "line_hint": "5-1-3",
    }
    notes = dict_to_race_notes(payload)
    assert notes.source == "tospo"
    assert notes.rider_notes[0].car_no == 5


def test_dict_to_race_notes_unknown_source_defaults_generic():
    notes = dict_to_race_notes({"source": "unknown", "rider_notes": []})
    assert notes.source == "generic"


def test_dict_to_race_notes_skips_invalid_riders():
    payload = {
        "source": "manual_text",
        "rider_notes": [
            {"car_no": 5, "name": "OK"},
            {"car_no": 15, "name": "車番範囲外"},
            {"name": "no car"},
            "not a dict",
        ],
    }
    notes = dict_to_race_notes(payload)
    assert len(notes.rider_notes) == 1
    assert notes.rider_notes[0].car_no == 5


def test_dict_to_race_notes_truncates_raw_excerpt():
    """raw_excerpt が50文字超なら 50文字に切られる。"""
    long_text = "あ" * 100
    notes = dict_to_race_notes({
        "source": "manual_text",
        "rider_notes": [
            {"car_no": 1, "raw_excerpt": long_text}
        ],
    })
    assert len(notes.rider_notes[0].raw_excerpt) <= 50


# ---------------------------------------------------------------------------
# Pydantic モデル制約
# ---------------------------------------------------------------------------


def test_race_notes_max_length_enforced():
    """Pydantic で max_length が強制される。"""
    from pydantic import ValidationError
    long_text = "あ" * 200  # 120 超
    with pytest.raises(ValidationError):
        RiderNote(car_no=1, comment_summary=long_text)


def test_race_notes_raw_excerpt_max_50():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RiderNote(car_no=1, raw_excerpt="あ" * 100)


def test_race_notes_invalid_source_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RaceNotes(source="invalid_source", rider_notes=[])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# merge_race_notes が Pydantic / dict 両方を受ける
# ---------------------------------------------------------------------------


def test_merge_race_notes_accepts_pydantic():
    ri = _load_sample()
    notes = parse_race_notes_text(SAMPLE_TEXT, source="winticket")
    merged = merge_race_notes(ri, notes)
    # 5番池部 → 長野の名前ではないが、car_no=5 で comment 追記される
    r5 = next(r for r in merged.riders if r.car_no == 5)
    assert "[WINTICKET]" in r5.comment
    # user_note に WINTICKET セクション
    assert "[WINTICKET]" in (merged.user_note or "")


def test_merge_race_notes_accepts_dict_backward_compat():
    """source 未指定 dict は東スポとして扱われる（後方互換）。"""
    ri = _load_sample()
    notes_dict = {
        "rider_notes": [
            {"car_no": 5, "name": "池部", "comment_summary": "自力",
             "signals": ["自力"]},
        ],
    }
    merged = merge_race_notes(ri, notes_dict)
    r5 = next(r for r in merged.riders if r.car_no == 5)
    assert "[東スポ]" in r5.comment


def test_merge_race_notes_label_by_source():
    """各 source ごとに正しい日本語ラベルが付く。"""
    ri = _load_sample()
    for src, label in [
        ("tospo", "東スポ"),
        ("winticket", "WINTICKET"),
        ("netkeirin", "netkeirin"),
        ("oddspark", "オッズパーク"),
        ("yenjoy", "yenjoy"),
        ("manual_text", "手入力"),
        ("generic", "補助情報"),
    ]:
        notes = RaceNotes(
            source=src,  # type: ignore[arg-type]
            rider_notes=[RiderNote(car_no=1, comment_summary="テスト")],
        )
        merged = merge_race_notes(ri, notes)
        r1 = next(r for r in merged.riders if r.car_no == 1)
        assert f"[{label}]" in r1.comment, f"source={src} のラベル {label} が出ていない"


def test_merge_race_notes_existing_comment_preserved():
    """既存 comment は上書きされず、追記される。"""
    ri = _load_sample()
    before = next(r for r in ri.riders if r.car_no == 5).comment
    notes = RaceNotes(
        source="winticket",
        rider_notes=[RiderNote(car_no=5, comment_summary="新規コメント")],
    )
    merged = merge_race_notes(ri, notes)
    after = next(r for r in merged.riders if r.car_no == 5).comment
    assert before in after  # 既存保持
    assert "新規コメント" in after  # 新規追記


def test_merge_race_notes_full_text_not_saved():
    """元のテキスト本文は RaceInput JSON に保存されない（要約のみ）。"""
    ri = _load_sample()
    long_comment = "完全な記事本文を全部入れる" * 50  # 1000文字超
    # comment_summary は120文字制限なので、validate 時に弾かれる
    # ここではテストで 120文字以内に切ってから渡す
    notes = RaceNotes(
        source="manual_text",
        rider_notes=[RiderNote(car_no=5, comment_summary=long_comment[:120])],
    )
    merged = merge_race_notes(ri, notes)
    r5 = next(r for r in merged.riders if r.car_no == 5)
    # 最終的に rider.comment に 全文が入ることはない
    assert len(r5.comment) < 500  # ベース comment + 120文字以下の追記


# ---------------------------------------------------------------------------
# prompt_builder
# ---------------------------------------------------------------------------


def test_build_race_notes_section_multi_source():
    """複数ソースが混在しても section に並ぶ。"""
    ri = _load_sample()
    n1 = RaceNotes(
        source="tospo",
        rider_notes=[RiderNote(car_no=5, name="池部", comment_summary="東スポ評")],
    )
    n2 = RaceNotes(
        source="winticket",
        rider_notes=[RiderNote(car_no=1, name="楢原", comment_summary="WIN評")],
    )
    merged = merge_race_notes(ri, n1)
    merged = merge_race_notes(merged, n2)
    section = build_race_notes_section(merged)
    assert "[東スポ]" in section
    assert "[WINTICKET]" in section
    assert "東スポ評" in section
    assert "WIN評" in section


def test_build_race_notes_section_empty():
    ri = _load_sample()
    section = build_race_notes_section(ri)
    # 元の race_sample.json は user_note があるが、[ソース] プレフィックスは無い
    assert section == ""


# ---------------------------------------------------------------------------
# CLI parse-race-notes / merge-notes
# ---------------------------------------------------------------------------


def test_cli_parse_race_notes(tmp_path: Path):
    runner = CliRunner()
    input_path = tmp_path / "notes.txt"
    input_path.write_text(SAMPLE_TEXT, encoding="utf-8")
    out_path = tmp_path / "notes.json"
    result = runner.invoke(
        cli,
        [
            "parse-race-notes",
            "--source", "winticket",
            "--input", str(input_path),
            "--out", str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["source"] == "winticket"
    assert len(raw["rider_notes"]) == 7
    # raw_excerpt が含まれない（既定）
    for n in raw["rider_notes"]:
        assert n.get("raw_excerpt") is None


def test_cli_merge_notes(tmp_path: Path):
    runner = CliRunner()
    # 1. parse-race-notes
    text_path = tmp_path / "notes.txt"
    text_path.write_text(SAMPLE_TEXT, encoding="utf-8")
    notes_path = tmp_path / "notes.json"
    r1 = runner.invoke(
        cli,
        ["parse-race-notes",
         "--source", "manual_text",
         "--input", str(text_path),
         "--out", str(notes_path)],
    )
    assert r1.exit_code == 0, r1.output

    # 2. merge-notes
    out_path = tmp_path / "merged.json"
    r2 = runner.invoke(
        cli,
        ["merge-notes",
         "--input", str(SAMPLE),
         "--notes", str(notes_path),
         "--out", str(out_path)],
    )
    assert r2.exit_code == 0, r2.output

    merged = json.loads(out_path.read_text(encoding="utf-8"))
    assert "[手入力]" in merged.get("user_note", "")


def test_cli_prepare_json_race_notes_text(tmp_path: Path, monkeypatch):
    """prepare-json --race-notes-text でテキストファイルから取り込み。"""
    from app.fetchers import HttpClient
    from unittest.mock import MagicMock
    from types import SimpleNamespace

    # サンプル race_card HTML 用 mock
    fixtures = Path(__file__).resolve().parent / "fixtures"
    KDREAMS_HTML = (fixtures / "kdreams_race_card_sample.html").read_text(encoding="utf-8")
    session = MagicMock()
    session.get.return_value = SimpleNamespace(
        status_code=200, text=KDREAMS_HTML, headers={}
    )
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)

    notes_text = tmp_path / "notes.txt"
    notes_text.write_text(
        "5 長野 自力。状態良い。\n1 久樹 番手。差し脚良好。",
        encoding="utf-8",
    )

    runner = CliRunner()
    out_path = tmp_path / "race.json"
    result = runner.invoke(
        cli,
        [
            "prepare-json",
            "--source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--race-no", "1",
            "--no-results", "--no-odds",
            "--weather-source", "manual",
            "--race-notes-text", str(notes_text),
            "--race-notes-source", "winticket",
            "--out", str(out_path),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    # 1番か5番のいずれかに WINTICKET タグが入る
    has_winticket = any(
        "[WINTICKET]" in (r.get("comment") or "") for r in raw["riders"]
    )
    assert has_winticket
