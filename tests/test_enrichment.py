"""外部取得結果の RaceInput への取り込みテスト。

実ネットワーク通信は一切行わない。HTTP は session を MagicMock に
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
from app.enrichment import (
    EnrichmentError,
    _auto_memo,
    _format_payout,
    merge_recent_results,
    normalize_results,
)
from app.fetchers import HttpClient
from app.models import RaceInput, RecentResult


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
KDREAMS_HTML = (FIXTURES / "kdreams_results_sample.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _new_envelope(items, *, source="kdreams", venue="大垣", date="2026-05-22"):
    return {
        "source": source,
        "kind": "results",
        "venue": venue,
        "date": date,
        "results": items,
    }


def _make_response(status: int, text: str):
    return SimpleNamespace(status_code=status, text=text, headers={})


# ---------------------------------------------------------------------------
# 内部ヘルパ
# ---------------------------------------------------------------------------


def test_format_payout_int_with_commas():
    assert _format_payout(12340) == "12,340円"


def test_format_payout_none_or_zero():
    assert _format_payout(None) is None
    assert _format_payout(0) is None
    assert _format_payout(False) is None


def test_auto_memo_with_payout():
    assert _auto_memo("5-6-2", 12340, "Kドリームス結果") == "Kドリームス結果: 5-6-2 / 払戻 12,340円"


def test_auto_memo_without_payout():
    assert _auto_memo("5-6-2", None, "Kドリームス結果") == "Kドリームス結果: 5-6-2"


# ---------------------------------------------------------------------------
# normalize_results
# ---------------------------------------------------------------------------


def test_normalize_envelope_with_kdreams_label():
    env = _new_envelope(
        [{"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2", "payout": 12340}]
    )
    out = normalize_results(env)
    assert len(out) == 1
    assert out[0].result == "5-6-2"
    # source=kdreams なので 'Kドリームス結果' ラベル
    assert "Kドリームス結果" in out[0].memo
    assert "12,340円" in out[0].memo


def test_normalize_envelope_fallback_venue_and_date():
    env = _new_envelope(
        [{"race_no": 1, "result": "5-6-2"}],  # venue/dateなし
        venue="大宮",
        date="2026-05-22",
    )
    out = normalize_results(env)
    assert out[0].venue == "大宮"
    assert out[0].date == Date(2026, 5, 22)


def test_normalize_list_input_uses_default_label():
    items = [
        {"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2", "payout": 12340}
    ]
    out = normalize_results(items, source_label="外部取得結果")
    assert out[0].memo.startswith("外部取得結果:")


def test_normalize_keeps_provided_memo():
    items = [{"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2", "memo": "特注メモ"}]
    out = normalize_results(items)
    assert out[0].memo == "特注メモ"


def test_normalize_accepts_recent_result_objects():
    rr = RecentResult(
        date=Date(2026, 5, 22), venue="大垣", race_no=1, result="5-6-2", memo="orig"
    )
    out = normalize_results([rr])
    assert out[0] is rr or out[0] == rr


def test_normalize_recent_result_without_memo_gets_auto():
    rr = RecentResult(
        date=Date(2026, 5, 22), venue="大垣", race_no=1, result="5-6-2"
    )
    out = normalize_results([rr])
    assert out[0].memo.startswith("外部取得結果:")


def test_normalize_missing_result_field_raises():
    with pytest.raises(EnrichmentError):
        normalize_results([{"date": "2026-05-22", "venue": "大垣", "race_no": 1}])


def test_normalize_invalid_envelope_kind():
    with pytest.raises(EnrichmentError) as excinfo:
        normalize_results(
            {"source": "kdreams", "kind": "race_card", "results": []}
        )
    assert "kind" in str(excinfo.value)


def test_normalize_envelope_without_results():
    with pytest.raises(EnrichmentError):
        normalize_results({"source": "kdreams", "kind": "results"})


def test_normalize_invalid_date_format():
    with pytest.raises(EnrichmentError) as excinfo:
        normalize_results([
            {"date": "2026/05/22", "venue": "大垣", "race_no": 1, "result": "5-6-2"}
        ])
    assert "YYYY-MM-DD" in str(excinfo.value)


def test_normalize_invalid_race_no():
    with pytest.raises(EnrichmentError):
        normalize_results([
            {"date": "2026-05-22", "venue": "大垣", "race_no": 99, "result": "5-6-2"}
        ])


def test_normalize_top_level_must_be_dict_or_list():
    with pytest.raises(EnrichmentError):
        normalize_results("not a results object")


# ---------------------------------------------------------------------------
# merge_recent_results
# ---------------------------------------------------------------------------


def test_merge_envelope_into_race_input(sample_input):
    env = _new_envelope(
        [
            {"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2", "payout": 12340},
            {"date": "2026-05-22", "venue": "大垣", "race_no": 2, "result": "1-3-7"},
        ]
    )
    before = len(sample_input.recent_results)
    out = merge_recent_results(sample_input, env)
    assert len(out.recent_results) == before + 2
    # 新規は降順で上に来る (2026-05-22 が先頭付近)
    assert out.recent_results[0].date == Date(2026, 5, 22)


def test_merge_list_input(sample_input):
    items = [
        {"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2"}
    ]
    out = merge_recent_results(sample_input, items)
    assert any(r.race_no == 1 and r.date == Date(2026, 5, 22) for r in out.recent_results)


def test_merge_preserves_existing(sample_input):
    items = [{"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2"}]
    out = merge_recent_results(sample_input, items)
    # 既存3件はすべて含まれている
    existing_keys = {(r.venue, r.date, r.race_no, r.result) for r in sample_input.recent_results}
    out_keys = {(r.venue, r.date, r.race_no, r.result) for r in out.recent_results}
    assert existing_keys.issubset(out_keys)


def test_merge_dedupe_overrides_with_new(sample_input):
    """既存と同じキーの新規がある場合、dedupe=True なら新規で上書きされる。"""
    # 既存に含まれる recent_result: date=2026-05-21, venue=大垣, race_no=10, result=5-4-3
    new_memo_items = [
        {
            "date": "2026-05-21",
            "venue": "大垣",
            "race_no": 10,
            "result": "5-4-3",
            "memo": "新しい memo",
        }
    ]
    out = merge_recent_results(sample_input, new_memo_items, dedupe=True)
    matching = [
        r for r in out.recent_results
        if r.venue == "大垣" and r.date == Date(2026, 5, 21) and r.race_no == 10 and r.result == "5-4-3"
    ]
    assert len(matching) == 1
    assert matching[0].memo == "新しい memo"
    # 全体件数: 既存3件のうち1件が上書きされ、追加1件 → 3件
    assert len(out.recent_results) == 3


def test_merge_no_dedupe_keeps_duplicates(sample_input):
    new_items = [
        {
            "date": "2026-05-21",
            "venue": "大垣",
            "race_no": 10,
            "result": "5-4-3",
            "memo": "別の memo",
        }
    ]
    out = merge_recent_results(sample_input, new_items, dedupe=False)
    matching = [
        r for r in out.recent_results
        if r.venue == "大垣" and r.date == Date(2026, 5, 21) and r.race_no == 10 and r.result == "5-4-3"
    ]
    assert len(matching) == 2


def test_merge_max_results_limits(sample_input):
    items = [
        {"date": "2026-05-22", "venue": "大垣", "race_no": i, "result": "5-6-2"}
        for i in range(1, 6)
    ]
    out = merge_recent_results(sample_input, items, max_results=3)
    assert len(out.recent_results) == 3
    # 新しい日付が上に来る
    assert out.recent_results[0].date == Date(2026, 5, 22)


def test_merge_accepts_dict_input(sample_input):
    raw = json.loads(sample_input.model_dump_json())
    items = [{"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2"}]
    out = merge_recent_results(raw, items)
    assert isinstance(out, RaceInput)


def test_merge_invalid_dict_input_raises():
    with pytest.raises(EnrichmentError):
        merge_recent_results({"not": "a race input"}, [])


def test_merge_invalid_top_level_input_type():
    with pytest.raises(EnrichmentError):
        merge_recent_results("not a thing", [])


def test_merge_validates_output_is_race_input(sample_input):
    env = _new_envelope(
        [{"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2", "payout": 12340}]
    )
    out = merge_recent_results(sample_input, env)
    # RaceInput として再シリアライズできる
    raw = json.loads(out.model_dump_json())
    RaceInput.model_validate(raw)


# ---------------------------------------------------------------------------
# CLI enrich-json
# ---------------------------------------------------------------------------


def _write_envelope(tmp_path: Path, items, *, name="r.json") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            _new_envelope(items),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_cli_enrich_json_with_envelope(tmp_path: Path):
    runner = CliRunner()
    rpath = _write_envelope(
        tmp_path,
        [{"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2", "payout": 12340}],
    )
    out = tmp_path / "enriched.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-json", str(rpath),
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    # 新しいレース1Rが含まれる
    assert any(r.race_no == 1 and r.date == Date(2026, 5, 22) for r in ri.recent_results)


def test_cli_enrich_json_with_results_list(tmp_path: Path):
    runner = CliRunner()
    rpath = tmp_path / "list.json"
    rpath.write_text(
        json.dumps([
            {"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2"}
        ]),
        encoding="utf-8",
    )
    out = tmp_path / "enriched.json"
    result = runner.invoke(
        cli,
        ["enrich-json", "--input", str(SAMPLE), "--results-json", str(rpath), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output


def test_cli_enrich_json_then_predict(tmp_path: Path):
    """enrich-json で出したJSONを predict にそのまま渡せる。"""
    runner = CliRunner()
    rpath = _write_envelope(
        tmp_path,
        [{"date": "2026-05-22", "venue": "大垣", "race_no": 1, "result": "5-6-2"}],
    )
    out = tmp_path / "enriched.json"
    r1 = runner.invoke(
        cli,
        ["enrich-json", "--input", str(SAMPLE), "--results-json", str(rpath), "--out", str(out)],
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
    assert "予想結果" in r2.output


def test_cli_enrich_json_source_kdreams_with_http_mock(tmp_path: Path, monkeypatch):
    session = MagicMock()
    session.get.return_value = _make_response(200, KDREAMS_HTML)
    monkeypatch.setattr(HttpClient, "_get_session", lambda self: session)
    runner = CliRunner()
    out = tmp_path / "enriched.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-source", "kdreams",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--out", str(out),
            "--no-cache",
            "--rate-limit-seconds", "0.0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert session.get.call_count == 1
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    # サンプルHTMLから4レース分の結果が取り込まれる（うち1件は既存と被るかもしれないが、被らない 4 件が新規）
    new_2026_05_22 = [r for r in ri.recent_results if r.date == Date(2026, 5, 22)]
    assert len(new_2026_05_22) == 4
    # memo に Kドリームス由来の表記
    assert all("Kドリームス" in (r.memo or "") for r in new_2026_05_22)


def test_cli_enrich_json_source_manual(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "enriched.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-source", "manual",
            "--results-input", str(SAMPLE),
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output


def test_cli_enrich_json_no_dedupe_keeps_duplicates(tmp_path: Path):
    runner = CliRunner()
    # 既存と同じレース(2026-05-21 大垣10R 5-4-3)を含む結果を渡す
    rpath = _write_envelope(
        tmp_path,
        [
            {"date": "2026-05-21", "venue": "大垣", "race_no": 10, "result": "5-4-3", "memo": "新規"}
        ],
    )
    out = tmp_path / "enriched.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-json", str(rpath),
            "--out", str(out),
            "--no-dedupe",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    matching = [
        r for r in raw["recent_results"]
        if r["venue"] == "大垣" and r["date"] == "2026-05-21" and r["race_no"] == 10 and r["result"] == "5-4-3"
    ]
    assert len(matching) == 2


def test_cli_enrich_json_max_results(tmp_path: Path):
    runner = CliRunner()
    rpath = _write_envelope(
        tmp_path,
        [
            {"date": "2026-05-22", "venue": "大垣", "race_no": i, "result": "1-2-3"}
            for i in range(1, 6)
        ],
    )
    out = tmp_path / "enriched.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-json", str(rpath),
            "--out", str(out),
            "--max-results", "3",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert len(raw["recent_results"]) == 3


# ---------------------------------------------------------------------------
# エラー処理
# ---------------------------------------------------------------------------


def test_cli_enrich_json_input_missing(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "e.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(tmp_path / "nonexistent.json"),
            "--results-json", str(SAMPLE),
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "input" in result.output.lower() or "見つかりません" in result.output


def test_cli_enrich_json_invalid_results_json(tmp_path: Path):
    runner = CliRunner()
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    out = tmp_path / "e.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-json", str(bad),
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "JSON" in result.output


def test_cli_enrich_json_results_missing_required_field(tmp_path: Path):
    runner = CliRunner()
    rpath = tmp_path / "r.json"
    # result フィールドが欠如
    rpath.write_text(
        json.dumps([{"date": "2026-05-22", "venue": "大垣", "race_no": 1}]),
        encoding="utf-8",
    )
    out = tmp_path / "e.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-json", str(rpath),
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "result" in result.output


def test_cli_enrich_json_unimplemented_source(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "e.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-source", "oddspark",
            "--venue", "大垣",
            "--date", "2026-05-22",
            "--out", str(out),
        ],
    )
    # oddspark は --results-source として "未対応" 扱いか NotImplementedSource
    assert result.exit_code != 0
    text = result.output
    assert "未対応" in text or "未実装" in text


def test_cli_enrich_json_source_without_venue(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "e.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-source", "kdreams",
            "--date", "2026-05-22",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "venue" in result.output.lower() or "--venue" in result.output


def test_cli_enrich_json_source_without_date(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "e.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--results-source", "kdreams",
            "--venue", "大垣",
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    assert "date" in result.output.lower() or "--date" in result.output


def test_cli_enrich_json_neither_source_nor_json(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "e.json"
    result = runner.invoke(
        cli,
        [
            "enrich-json",
            "--input", str(SAMPLE),
            "--out", str(out),
        ],
    )
    assert result.exit_code != 0
    text = result.output
    assert "results-json" in text or "results-source" in text
