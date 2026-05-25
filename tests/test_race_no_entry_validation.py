"""race_no 根本対応: 入口検証 (RaceRequest + validate_race_no_*).

検証内容:
A. fetcher mismatch: requested vs fetched
B. dataset mismatch: racecard / odds / results 間
C. prediction mismatch: input vs prediction (OutputPlan 最終防衛)
D. normal case: 全部一致なら例外も warning も出ない
E. RaceRequest dataclass
F. quick-json CLI 実行で race_no 不一致時に停止
G. predict CLI で race_id と race.race_no の不一致を検出
"""

from __future__ import annotations

from datetime import date

import pytest

from app.race_request import (
    RACE_NO_DATASET_MISMATCH, RACE_NO_FETCH_MISMATCH, RACE_NO_OUTPUT_MISMATCH,
    RaceNoMismatchError, RaceRequest,
    validate_race_no_dataset_match, validate_race_no_fetch_match,
    validate_race_no_output_match,
)


# ---------------------------------------------------------------------------
# E. RaceRequest dataclass
# ---------------------------------------------------------------------------


class TestRaceRequestDataclass:
    def test_construction(self):
        req = RaceRequest(
            venue="静岡", date=date(2026, 5, 25),
            race_no=5, source="cli_predict",
        )
        assert req.venue == "静岡"
        assert req.race_no == 5
        assert req.source == "cli_predict"

    def test_race_id_prefix(self):
        req = RaceRequest(
            venue="静岡", date=date(2026, 5, 25), race_no=5,
        )
        assert req.race_id_prefix() == "20260525-静岡-5"

    def test_str_format(self):
        req = RaceRequest(
            venue="静岡", date=date(2026, 5, 25), race_no=5,
        )
        s = str(req)
        assert "静岡" in s
        assert "5R" in s


# ---------------------------------------------------------------------------
# A. fetcher mismatch
# ---------------------------------------------------------------------------


class TestFetcherMismatch:
    def _req(self) -> RaceRequest:
        return RaceRequest(
            venue="静岡", date=date(2026, 5, 25), race_no=5,
            source="cli_predict",
        )

    def test_match_no_exception(self):
        req = self._req()
        # 一致なら例外なし
        validate_race_no_fetch_match(req, 5, fetcher_name="fetch_race_card")

    def test_mismatch_raises(self):
        req = self._req()
        with pytest.raises(RaceNoMismatchError) as exc:
            validate_race_no_fetch_match(
                req, 4, fetcher_name="fetch_race_card",
            )
        assert exc.value.code == RACE_NO_FETCH_MISMATCH
        assert exc.value.requested == 5
        assert exc.value.actual == 4
        assert exc.value.source == "fetch_race_card"
        assert exc.value.venue == "静岡"

    def test_none_no_validation(self):
        req = self._req()
        # 取得側に race_no が無いケース (例: 一括取得) は検証スキップ
        validate_race_no_fetch_match(req, None, fetcher_name="x")


# ---------------------------------------------------------------------------
# B. dataset mismatch (racecard / odds / results 間)
# ---------------------------------------------------------------------------


class TestDatasetMismatch:
    def _req(self) -> RaceRequest:
        return RaceRequest(
            venue="静岡", date=date(2026, 5, 25), race_no=5,
        )

    def test_all_match(self):
        req = self._req()
        validate_race_no_dataset_match(
            req, racecard_race_no=5, odds_race_no=5, results_race_no=5,
        )

    def test_racecard_mismatch(self):
        req = self._req()
        with pytest.raises(RaceNoMismatchError) as exc:
            validate_race_no_dataset_match(
                req, racecard_race_no=4, odds_race_no=5,
            )
        assert exc.value.code == RACE_NO_DATASET_MISMATCH
        assert exc.value.source == "racecard"

    def test_odds_mismatch(self):
        req = self._req()
        with pytest.raises(RaceNoMismatchError) as exc:
            validate_race_no_dataset_match(
                req, racecard_race_no=5, odds_race_no=4,
            )
        assert exc.value.code == RACE_NO_DATASET_MISMATCH
        assert exc.value.source == "odds"

    def test_results_mismatch(self):
        req = self._req()
        with pytest.raises(RaceNoMismatchError) as exc:
            validate_race_no_dataset_match(
                req, racecard_race_no=5, results_race_no=4,
            )
        assert exc.value.source == "results"

    def test_none_skipped(self):
        """None の dataset は検証スキップ。"""
        req = self._req()
        validate_race_no_dataset_match(
            req, racecard_race_no=5, odds_race_no=None,
            results_race_no=None,
        )


# ---------------------------------------------------------------------------
# C. prediction (output) mismatch — 最終防衛、例外でなく warning
# ---------------------------------------------------------------------------


class TestOutputMismatch:
    def test_match_returns_none(self):
        assert validate_race_no_output_match(5, 5) is None

    def test_mismatch_returns_message(self):
        msg = validate_race_no_output_match(5, 4)
        assert msg is not None
        assert RACE_NO_OUTPUT_MISMATCH in msg
        assert "5" in msg and "4" in msg

    def test_none_returns_none(self):
        assert validate_race_no_output_match(None, 5) is None
        assert validate_race_no_output_match(5, None) is None


# ---------------------------------------------------------------------------
# D. normal case: full pipeline
# ---------------------------------------------------------------------------


class TestNormalCase:
    def test_full_pipeline_no_error(self):
        """fetcher / dataset / output すべて 5 → 例外も warning も出ない。"""
        req = RaceRequest(
            venue="静岡", date=date(2026, 5, 25), race_no=5,
        )
        validate_race_no_fetch_match(req, 5, fetcher_name="fetcher")
        validate_race_no_dataset_match(
            req, racecard_race_no=5, odds_race_no=5, results_race_no=5,
        )
        assert validate_race_no_output_match(5, 5) is None


# ---------------------------------------------------------------------------
# F. CLI quick-json で不一致を検出 (race_no 内部整合)
# ---------------------------------------------------------------------------


class TestCliQuickJsonRaceNo:
    def test_quick_json_race_no_consistent(self, tmp_path):
        """quick-json で --race-no と build_quick_input の race_no が一致
        するなら正常終了。"""
        from click.testing import CliRunner
        from app.cli import cli
        runner = CliRunner()
        out = tmp_path / "out.json"
        result = runner.invoke(cli, [
            "quick-json", "--out", str(out),
            "--venue", "静岡", "--race-no", "5",
            "--date", "2026-05-25",
        ])
        assert result.exit_code == 0, result.output
        assert "requested" in result.output
        assert "race_no" in result.output


# ---------------------------------------------------------------------------
# G. predict CLI で race_id と race.race_no の不一致検出
# ---------------------------------------------------------------------------


class TestCliPredictRaceIdMismatch:
    def test_predict_detects_race_id_mismatch(self, tmp_path):
        """race_id='20260525-静岡-5' なのに race.race_no=4 の JSON を
        渡すと予想生成へ進まず ClickException。"""
        import json
        from click.testing import CliRunner
        from app.cli import cli
        bad_json = {
            "race": {
                "race_id": "20260525-shizuoka-5",
                "date": "2026-05-25",
                "venue": "静岡",
                "race_no": 4,  # ← race_id と不一致
                "class_name": "A級一般",
                "start_time": "10:53",
            },
            "lines": [{"line_name": "L", "cars": [1, 2, 3]}],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 80.0,
                 "b_count": 0, "nige": 0, "makuri": 0, "sashi": 0,
                 "mark": 0, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [],
            "recent_results": [],
        }
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(bad_json), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "predict", "--input", str(p),
            "--provider", "mock",
        ])
        # 不一致で停止 (exit_code != 0)
        assert result.exit_code != 0
        assert (
            "RACE_NO_DATASET_MISMATCH" in result.output
            or "race_no 不一致" in result.output
        )

    def test_predict_passes_with_matching_race_id(self, tmp_path):
        """race_id と race.race_no が一致するなら正常進行。"""
        import json
        from click.testing import CliRunner
        from app.cli import cli
        good_json = {
            "race": {
                "race_id": "20260525-shizuoka-5",
                "date": "2026-05-25",
                "venue": "静岡",
                "race_no": 5,  # ← race_id と一致
                "class_name": "A級一般",
                "start_time": "10:53",
            },
            "lines": [{"line_name": "L", "cars": [1, 2, 3]}],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 80.0,
                 "b_count": 0, "nige": 0, "makuri": 0, "sashi": 0,
                 "mark": 0, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [],
            "recent_results": [],
        }
        p = tmp_path / "good.json"
        p.write_text(json.dumps(good_json), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "predict", "--input", str(p),
            "--provider", "mock",
        ])
        # race_no エラーで止まらない (LLM等の他要因で失敗してもよいが、
        # 「RACE_NO_DATASET_MISMATCH」 は出ない)
        assert "RACE_NO_DATASET_MISMATCH" not in result.output
