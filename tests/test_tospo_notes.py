"""東スポ補助情報 (fetcher / parser / enrichment / scoring / prompt / CLI) のテスト。

全テストは fixture HTML / mock session ベース。実ネットワーク通信は一切しない。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.enrichment import EnrichmentError, merge_race_notes
from app.fetchers import FetchError, FileCache, HttpClient, RateLimiter, TospoFetcher
from app.fetchers.parsers.tospo_notes import (
    MAX_RAW_EXCERPT_LEN,
    parse_tospo_race_notes_html,
)
from app.models import RaceInput
from app.prompt_builder import build_tospo_section
from app.scoring import apply_tospo_signals, compute_scores


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE_INPUT = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"
TOSPO_HTML = (FIXTURES / "tospo_race_notes_sample.html").read_text(encoding="utf-8")


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


def _make_http_client(tmp_path: Path, session: MagicMock) -> HttpClient:
    cache = FileCache(cache_dir=tmp_path / "c", enabled=False)
    rl = RateLimiter(min_interval_seconds=0.0)
    return HttpClient(cache=cache, rate_limiter=rl, session=session)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def test_parse_tospo_basic():
    notes = parse_tospo_race_notes_html(
        TOSPO_HTML, venue="松山", date="2026-05-22", race_no=10
    )
    assert notes["source"] == "tospo"
    assert notes["venue"] == "松山"
    assert notes["race_no"] == 10
    assert notes["race_summary"]
    assert notes["line_hint"]
    assert notes["prediction_hint"]
    assert len(notes["rider_notes"]) == 7


def test_parse_tospo_signals_for_car_5_includes_jiriki_and_state_good():
    notes = parse_tospo_race_notes_html(TOSPO_HTML)
    r5 = next(n for n in notes["rider_notes"] if n["car_no"] == 5)
    assert "自力" in r5["signals"]
    assert "状態良い" in r5["signals"]
    assert r5["name"] == "長野魅切"


def test_parse_tospo_signals_for_car_4_tanki_jizai():
    notes = parse_tospo_race_notes_html(TOSPO_HTML)
    r4 = next(n for n in notes["rider_notes"] if n["car_no"] == 4)
    assert "単騎" in r4["signals"]
    assert "自在" in r4["signals"]
    assert "不安" in r4["signals"]


def test_parse_tospo_signals_for_car_2_oikomi_heavy():
    notes = parse_tospo_race_notes_html(TOSPO_HTML)
    r2 = next(n for n in notes["rider_notes"] if n["car_no"] == 2)
    assert "追込" in r2["signals"]
    assert "重い" in r2["signals"]


def test_parse_tospo_no_raw_excerpt_by_default():
    """既定では raw_excerpt は含めない（著作権配慮）。"""
    notes = parse_tospo_race_notes_html(TOSPO_HTML)
    assert all("raw_excerpt" not in n for n in notes["rider_notes"])


def test_parse_tospo_raw_excerpt_when_requested_is_truncated():
    """include_raw_excerpt=True にしても 50 文字以内に切られる。"""
    notes = parse_tospo_race_notes_html(TOSPO_HTML, include_raw_excerpt=True)
    for n in notes["rider_notes"]:
        # raw_excerpt は最大 50 文字 + 末尾「…」
        assert len(n["raw_excerpt"]) <= MAX_RAW_EXCERPT_LEN + 1


def test_parse_tospo_comment_summary_is_short():
    """comment_summary は短い要約（最大40文字+「…」）。"""
    notes = parse_tospo_race_notes_html(TOSPO_HTML)
    for n in notes["rider_notes"]:
        assert len(n["comment_summary"]) <= 41


def test_parse_tospo_empty_html_raises():
    with pytest.raises(FetchError):
        parse_tospo_race_notes_html("")


def test_parse_tospo_no_riders_raises():
    """選手コメント表が無いHTMLは FetchError。"""
    with pytest.raises(FetchError) as e:
        parse_tospo_race_notes_html("<html><body><p>no riders</p></body></html>")
    assert "選手コメント" in str(e.value) or "サイト構造" in str(e.value)


def test_parse_tospo_result_does_not_contain_html_tags():
    """戻り値 dict に HTML タグが漏れていないこと。"""
    notes = parse_tospo_race_notes_html(TOSPO_HTML, include_raw_excerpt=True)
    s = json.dumps(notes, ensure_ascii=False)
    for tag in ("<table", "<tr", "<td", "<html", "<section", "<p ", "<article"):
        assert tag not in s


# ---------------------------------------------------------------------------
# TospoFetcher
# ---------------------------------------------------------------------------


def test_tospo_fetcher_uses_http_client(tmp_path):
    session = MagicMock()
    session.get.return_value = _make_response(200, TOSPO_HTML)
    client = _make_http_client(tmp_path, session)
    f = TospoFetcher(http_client=client)
    notes = f.fetch_race_notes(
        venue="松山", race_no=10, url="https://example.com/race"
    )
    assert session.get.call_count == 1
    called = session.get.call_args.args[0]
    assert called == "https://example.com/race"
    headers = session.get.call_args.kwargs.get("headers", {})
    assert "User-Agent" in headers
    assert notes["source"] == "tospo"


def test_tospo_fetcher_requires_url():
    f = TospoFetcher(http_client=MagicMock())
    with pytest.raises(FetchError) as e:
        f.fetch_race_notes(venue="松山")
    assert "URL" in str(e.value)


def test_tospo_fetcher_requires_http_client():
    f = TospoFetcher(http_client=None)
    with pytest.raises(FetchError):
        f.fetch_race_notes(url="https://example.com/race")


def test_tospo_fetcher_other_methods_not_implemented():
    f = TospoFetcher()
    from app.fetchers import NotImplementedSource
    for method in ("fetch_race_card", "fetch_odds", "fetch_results", "fetch_venue_trend"):
        with pytest.raises(NotImplementedSource):
            getattr(f, method)()


# ---------------------------------------------------------------------------
# merge_race_notes (enrichment)
# ---------------------------------------------------------------------------


def _load_sample_input() -> RaceInput:
    raw = json.loads(SAMPLE_INPUT.read_text(encoding="utf-8"))
    return RaceInput.model_validate(raw)


def test_merge_race_notes_appends_to_existing_comment():
    """既存 comment は上書きせず、'/' で追記される。"""
    ri = _load_sample_input()
    # car_no=5 (池部) の comment を確認
    before_5 = next(r for r in ri.riders if r.car_no == 5).comment
    notes = {
        "source": "tospo",
        "rider_notes": [
            {"car_no": 5, "name": "池部", "comment_summary": "自力勢の地脚良い",
             "signals": ["自力", "状態良い"]},
        ],
    }
    merged = merge_race_notes(ri, notes)
    after_5 = next(r for r in merged.riders if r.car_no == 5).comment
    assert before_5 in after_5  # 既存 comment は保持
    assert "[東スポ]" in after_5
    assert "自力勢の地脚良い" in after_5


def test_merge_race_notes_adds_signals_to_style_tags():
    ri = _load_sample_input()
    notes = {
        "rider_notes": [
            {"car_no": 5, "name": "池部", "comment_summary": "自力",
             "signals": ["自力", "状態良い", "前々"]},
        ],
    }
    merged = merge_race_notes(ri, notes)
    r5 = next(r for r in merged.riders if r.car_no == 5)
    assert "状態良い" in r5.style_tags
    assert "前々" in r5.style_tags


def test_merge_race_notes_no_duplicate_tags():
    """既に同じ tag があれば重複追加しない。"""
    ri = _load_sample_input()
    original_tags = list(next(r for r in ri.riders if r.car_no == 5).style_tags)
    notes = {
        "rider_notes": [
            {"car_no": 5, "name": "池部", "comment_summary": "",
             "signals": original_tags + ["新タグ"]},
        ],
    }
    merged = merge_race_notes(ri, notes)
    r5 = next(r for r in merged.riders if r.car_no == 5)
    # 既存タグの重複が無い
    assert r5.style_tags.count(original_tags[0]) == 1
    assert "新タグ" in r5.style_tags


def test_merge_race_notes_appends_to_user_note():
    ri = _load_sample_input()
    notes = {
        "rider_notes": [],
        "race_summary": "混戦",
        "prediction_hint": "本線は5-1",
        "line_hint": "5-1-3",
    }
    merged = merge_race_notes(ri, notes)
    assert merged.user_note
    assert "[東スポ]" in merged.user_note
    assert "混戦" in merged.user_note
    assert "本線は5-1" in merged.user_note


def test_merge_race_notes_with_none_returns_base():
    ri = _load_sample_input()
    merged = merge_race_notes(ri, None)
    assert merged.model_dump() == ri.model_dump()


def test_merge_race_notes_invalid_type_raises():
    ri = _load_sample_input()
    with pytest.raises(EnrichmentError):
        merge_race_notes(ri, "not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# scoring.apply_tospo_signals
# ---------------------------------------------------------------------------


def test_apply_tospo_signals_boosts_jiriki():
    ri = _load_sample_input()
    # car_no=5 の style_tags に「自力」「状態良い」を追加
    new_riders = []
    for r in ri.riders:
        if r.car_no == 5:
            new_riders.append(r.model_copy(update={
                "style_tags": r.style_tags + ["自力", "状態良い"]
            }))
        else:
            new_riders.append(r)
    ri2 = ri.model_copy(update={"riders": new_riders})

    scores = compute_scores(ri2)
    before = next(s.win_score for s in scores if s.car_no == 5)
    apply_tospo_signals(scores, ri2)
    after = next(s.win_score for s in scores if s.car_no == 5)
    assert after > before
    # 加点は最大 ±0.5 程度（自力 0.3 + 状態良い 0.2 = +0.5）
    assert after - before <= 0.7


def test_apply_tospo_signals_penalizes_unstable():
    ri = _load_sample_input()
    new_riders = []
    for r in ri.riders:
        if r.car_no == 4:
            new_riders.append(r.model_copy(update={
                "style_tags": r.style_tags + ["不安"]
            }))
        else:
            new_riders.append(r)
    ri2 = ri.model_copy(update={"riders": new_riders})
    scores = compute_scores(ri2)
    before = next(s.win_score for s in scores if s.car_no == 4)
    apply_tospo_signals(scores, ri2)
    after = next(s.win_score for s in scores if s.car_no == 4)
    assert after < before


# ---------------------------------------------------------------------------
# prompt_builder.build_tospo_section
# ---------------------------------------------------------------------------


def test_build_tospo_section_includes_summaries():
    ri = _load_sample_input()
    notes = {
        "rider_notes": [
            {"car_no": 5, "name": "池部", "comment_summary": "自力。状態は良い。",
             "signals": ["自力", "状態良い"]},
        ],
        "race_summary": "混戦",
        "prediction_hint": "本線は5-1",
    }
    merged = merge_race_notes(ri, notes)
    section = build_tospo_section(merged)
    # セクション名は汎用化されたので「コメント・記者補助情報」になる。
    # ただし内容に「東スポ」プレフィックスが入っていればOK
    assert "コメント・記者補助情報" in section
    assert "[東スポ]" in section
    assert "自力。状態は良い。" in section
    assert "混戦" in section
    # HTML タグが含まれていない
    assert "<" not in section.split("\n", 1)[0]


def test_build_tospo_section_empty_when_no_notes():
    ri = _load_sample_input()
    section = build_tospo_section(ri)
    assert section == ""


# ---------------------------------------------------------------------------
# CLI fetch-json --kind race_notes
# ---------------------------------------------------------------------------


def test_cli_fetch_json_race_notes(tmp_path, monkeypatch):
    session = MagicMock()
    session.get.return_value = _make_response(200, TOSPO_HTML)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    runner = CliRunner()
    out = tmp_path / "notes.json"
    result = runner.invoke(
        cli,
        [
            "fetch-json",
            "--source", "tospo",
            "--kind", "race_notes",
            "--url", "https://example.com/race",
            "--venue", "松山",
            "--date", "2026-05-22",
            "--race-no", "10",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source"] == "tospo"
    assert payload["venue"] == "松山"
    assert payload["race_no"] == 10
    assert payload["rider_notes"]


# ---------------------------------------------------------------------------
# CLI prepare-json --tospo-url
# ---------------------------------------------------------------------------


def test_cli_prepare_json_with_tospo_url(tmp_path, monkeypatch):
    """prepare-json --tospo-url で notes が RaceInput に取り込まれる。"""
    KDREAMS_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")

    session = MagicMock()

    def _get(url: str, **kwargs):
        if "tokyo-sports" in url or "tospo" in url or url == "https://example.com/tospo":
            return _make_response(200, TOSPO_HTML)
        if "racecard" in url:
            return _make_response(200, KDREAMS_HTML)
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
            "--no-results", "--no-odds",
            "--weather-source", "manual",
            "--tospo-notes",
            "--tospo-url", "https://example.com/tospo",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    # user_note に「[東スポ]」プレフィックスが入っている
    assert raw.get("user_note") is not None
    assert "[東スポ]" in raw["user_note"]


def test_cli_prepare_json_tospo_failure_continues(tmp_path, monkeypatch):
    """東スポ取得失敗でも警告のみで処理続行（出走表は維持）。"""
    KDREAMS_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")

    session = MagicMock()

    def _get(url: str, **kwargs):
        if "racecard" in url:
            return _make_response(200, KDREAMS_HTML)
        if url == "https://example.com/tospo":
            return _make_response(500, "server error")
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
            "--no-results", "--no-odds",
            "--weather-source", "manual",
            "--tospo-notes",
            "--tospo-url", "https://example.com/tospo",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    # 出走表は取れているので exit_code は 0、東スポは警告
    assert result.exit_code == 0, result.output
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "東スポ" in text and "失敗" in text


def test_cli_prepare_json_tospo_url_missing_warns(tmp_path, monkeypatch):
    """--tospo-notes 有効だが --tospo-url 未指定なら警告のみで続行。"""
    KDREAMS_HTML = (FIXTURES / "kdreams_race_card_sample.html").read_text(encoding="utf-8")
    session = MagicMock()
    session.get.return_value = _make_response(200, KDREAMS_HTML)
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
            "--no-results", "--no-odds",
            "--weather-source", "manual",
            "--tospo-notes",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "tospo-url" in text or "東スポ" in text
