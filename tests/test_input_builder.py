"""手入力JSON作成UXのテスト。

- parse_lines のパース仕様
- quick-json コマンドが Pydantic を通る JSON を生成すること
- create-json --interactive が CliRunner の擬似入力で動くこと
- girls / 不正並び / 生成JSON→RaceInput の往復
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.models import RaceInput
from app.race_input_builder import (
    LinesParseError,
    build_placeholder_rider,
    build_quick_input,
    parse_lines,
)


# ---------------------------------------------------------------------------
# parse_lines
# ---------------------------------------------------------------------------


def test_parse_lines_basic():
    lines = parse_lines("3-7-2 / 1-5 / 4-6")
    assert [l.cars for l in lines] == [[3, 7, 2], [1, 5], [4, 6]]
    assert lines[0].line_name == "ライン1"
    assert lines[1].line_name == "ライン2"
    assert lines[2].line_name == "ライン3"


def test_parse_lines_tanki():
    lines = parse_lines("3-7-2 / 1-5 / 4")
    assert lines[-1].cars == [4]
    assert lines[-1].line_name == "単騎"


def test_parse_lines_full_width_digits():
    """全角数字とフルワイドハイフン/スラッシュをNFKCで正規化する。"""
    lines = parse_lines("３－７－２ ／ １－５ ／ ４－６")
    assert [l.cars for l in lines] == [[3, 7, 2], [1, 5], [4, 6]]


def test_parse_lines_alternate_separators():
    lines = parse_lines("3 7 2 | 1,5 | 4-6")
    assert [l.cars for l in lines] == [[3, 7, 2], [1, 5], [4, 6]]


def test_parse_lines_dash_variants():
    lines = parse_lines("5—1—3 / 2—6—4")
    assert [l.cars for l in lines] == [[5, 1, 3], [2, 6, 4]]


def test_parse_lines_duplicate_across_lines():
    with pytest.raises(LinesParseError) as excinfo:
        parse_lines("5-1-3 / 1-2-4")
    assert "複数ライン" in str(excinfo.value)


def test_parse_lines_duplicate_within_line():
    with pytest.raises(LinesParseError) as excinfo:
        parse_lines("5-5-3 / 1-2-4")
    assert "ライン内" in str(excinfo.value)


def test_parse_lines_invalid_token():
    with pytest.raises(LinesParseError) as excinfo:
        parse_lines("3-a-2 / 1-5")
    assert "車番として解釈" in str(excinfo.value)


def test_parse_lines_out_of_range():
    with pytest.raises(LinesParseError) as excinfo:
        parse_lines("3-10-2 / 1-5")
    assert "1〜9" in str(excinfo.value)


def test_parse_lines_empty():
    with pytest.raises(LinesParseError):
        parse_lines("")
    with pytest.raises(LinesParseError):
        parse_lines("   ")


# ---------------------------------------------------------------------------
# build_quick_input
# ---------------------------------------------------------------------------


def test_build_quick_input_from_lines_includes_all_cars():
    ri = build_quick_input(
        venue="大宮",
        race_no=8,
        class_name="A級特選",
        lines_text="3-7-2 / 1-5 / 4-6",
        weather="曇り",
        wind_direction="北",
        wind_speed=4.0,
    )
    assert {r.car_no for r in ri.riders} == {1, 2, 3, 4, 5, 6, 7}
    assert ri.race.race_id.endswith("大宮-8")
    assert ri.weather.condition == "曇り"
    assert ri.weather.wind_speed_mps == 4.0
    assert len(ri.lines) == 3


def test_build_quick_input_girls_has_empty_lines():
    ri = build_quick_input(
        venue="松山",
        race_no=5,
        class_name="ガールズ",
        girls=True,
        car_count=7,
        weather="晴れ",
    )
    assert ri.lines == []
    assert ri.race.resolved_is_girls() is True
    assert len(ri.riders) == 7


def test_build_quick_input_girls_ignores_lines_text():
    ri = build_quick_input(
        venue="松山",
        race_no=5,
        class_name="ガールズ",
        girls=True,
        lines_text="1-2-3 / 4-5-6",
    )
    assert ri.lines == []


def test_build_quick_input_invalid_race_no():
    with pytest.raises(LinesParseError):
        build_quick_input(venue="X", race_no=13, class_name="A級")


def test_build_quick_input_invalid_date():
    with pytest.raises(LinesParseError):
        build_quick_input(
            venue="X", race_no=1, class_name="A級", date_str="2026/01/01"
        )


def test_build_placeholder_rider_minimal():
    r = build_placeholder_rider(3)
    assert r.car_no == 3
    assert r.name.endswith("3")
    assert r.score == 0.0


# ---------------------------------------------------------------------------
# quick-json CLI
# ---------------------------------------------------------------------------


def test_cli_quick_json_basic(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "r.json"
    result = runner.invoke(
        cli,
        [
            "quick-json",
            "--out", str(out),
            "--venue", "大宮",
            "--race-no", "8",
            "--class-name", "A級特選",
            "--weather", "曇り",
            "--wind-direction", "北",
            "--wind-speed", "4.0",
            "--lines", "3-7-2 / 1-5 / 4-6",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.venue == "大宮"
    assert ri.race.race_no == 8
    assert len(ri.lines) == 3


def test_cli_quick_json_girls(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "g.json"
    result = runner.invoke(
        cli,
        [
            "quick-json",
            "--out", str(out),
            "--venue", "松山",
            "--race-no", "5",
            "--class-name", "ガールズ",
            "--girls",
            "--cars", "7",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.resolved_is_girls() is True
    assert ri.lines == []
    assert len(ri.riders) == 7


def test_cli_quick_json_invalid_lines_japanese_error(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "x.json"
    result = runner.invoke(
        cli,
        [
            "quick-json",
            "--out", str(out),
            "--venue", "X",
            "--race-no", "1",
            "--class-name", "A級",
            "--lines", "3-10-2",
        ],
    )
    assert result.exit_code != 0
    assert "1〜9" in result.output
    assert not out.exists()


def test_cli_quick_json_then_predict_runs(tmp_path: Path):
    """quick-json で作ったJSONを predict に食わせて通ること。"""
    runner = CliRunner()
    out = tmp_path / "p.json"
    r1 = runner.invoke(
        cli,
        [
            "quick-json",
            "--out", str(out),
            "--venue", "大宮",
            "--race-no", "8",
            "--class-name", "A級特選",
            "--lines", "3-7-2 / 1-5 / 4-6",
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
    assert "予想結果" in r2.output


def test_cli_quick_json_girls_with_lines_warns(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "g.json"
    result = runner.invoke(
        cli,
        [
            "quick-json",
            "--out", str(out),
            "--venue", "松山",
            "--race-no", "5",
            "--class-name", "ガールズ",
            "--girls",
            "--lines", "1-2-3",
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output + (getattr(result, "stderr", "") or "")
    assert "無視" in text


# ---------------------------------------------------------------------------
# create-json --interactive
# ---------------------------------------------------------------------------


def _interactive_inputs() -> str:
    """対話モードへの擬似入力（通常戦・選手詳細を全員入力）。

    プロンプト順:
    1. 場名
    2. レース番号
    3. クラス
    4. 日付
    5. 発走時刻
    6. バンクメモ
    7. ガールズ? (y/n)
    8. 天候を入力? (y/n)
    9. 天候
    10. 風向
    11. 風速
    12. 雨量
    13. 風メモ
    14. 並び
    15. 各車情報入力する? (y/n)
    16+ 各車について (名前, 得点, B, 逃げ, 捲り, 差し, マーク, コメント, 直近, タグ)
    """
    lines = [
        "大垣",           # 場名
        "1",              # レース番号
        "A級一般",         # クラス
        "2026-05-22",     # 日付
        "10:53",          # 発走時刻
        "",               # バンクメモ
        "n",              # ガールズ?
        "y",              # 天候入力?
        "曇り",            # 天候
        "西",              # 風向
        "5.0",            # 風速
        "0.0",            # 雨量
        "",               # 風メモ
        "5-1-3 / 2-6 / 7",  # 並び
        "n",              # 各車情報入力する? → No で placeholder
    ]
    return "\n".join(lines) + "\n"


def test_cli_create_json_interactive(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "i.json"
    result = runner.invoke(
        cli,
        ["create-json", "--out", str(out), "--interactive"],
        input=_interactive_inputs(),
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.venue == "大垣"
    assert ri.race.race_no == 1
    assert ri.weather.wind_speed_mps == 5.0
    assert len(ri.lines) == 3
    # placeholder で 6車（5,1,3,2,6,7）
    assert len(ri.riders) == 6


def test_cli_create_json_interactive_invalid_lines_then_valid(tmp_path: Path):
    """並びを最初は不正に入れ、再入力で受理されること。"""
    inputs = [
        "松山",
        "5",
        "ガールズ",
        "",  # 日付
        "",  # 発走時刻
        "",  # バンク
        "y",  # ガールズ確認
        "n",  # 天候入力しない
        "7",  # 出走頭数
        "n",  # 各車情報入力しない
    ]
    runner = CliRunner()
    out = tmp_path / "g.json"
    result = runner.invoke(
        cli,
        ["create-json", "--out", str(out), "--interactive"],
        input="\n".join(inputs) + "\n",
    )
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert ri.race.resolved_is_girls() is True
    assert ri.lines == []
    assert len(ri.riders) == 7


def test_cli_create_json_template_still_works(tmp_path: Path):
    """既存の create-json (テンプレート出力) が壊れていないこと。"""
    runner = CliRunner()
    out = tmp_path / "t.json"
    result = runner.invoke(cli, ["create-json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    raw = json.loads(out.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    assert len(ri.riders) == 7
