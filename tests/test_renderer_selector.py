"""renderer_selector: 環境変数 / --renderer / 静岡4R 回帰テスト (方針B)。

検証要件 (2026-05-24):
1. select_renderer: 環境変数 ON/OFF + 明示指定の優先順位
2. render_prediction_auto: v1/v2 切り替えで正しい renderer が呼ばれる
3. v2 使用時のメタ情報 (末尾コメント) と fallback ログ
4. CLI --renderer フラグの動作 (CliRunner 経由)
5. v2 経由で静岡4R シナリオの未登録 combo が排除される
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models import BetRecommendation, Prediction, RaceInput
from app.renderer_selector import (
    ENV_VAR_NAME,
    _env_says_v2,
    render_prediction_auto,
    select_renderer,
)


def _bet(combo, *, market_odds=None, value_label="", gami_risk=0.0,
         category="本線", reason="test"):
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=reason, gami_risk=gami_risk,
        market_odds=market_odds, value_label=value_label,
    )


def _pred(*, honsen=None, osae=None, ana=None, ooana=None,
          final_conclusion="", is_girls=False, summary=""):
    return Prediction(
        race_id="test-renderer", venue="テスト", race_no=1, is_girls=is_girls,
        summary=summary, venue_trend_text="t",
        weather_text="w", lines_text="l",
        marks={},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion=final_conclusion,
        gami_memo="", reflection_points=[],
    )


def _input(*, class_name="A級一般", lines=None):
    return RaceInput.model_validate({
        "race": {
            "race_id": "test-renderer", "date": "2026-05-24",
            "venue": "テスト", "race_no": 1,
            "class_name": class_name, "start_time": "10:00",
        },
        "weather": {
            "condition": "晴れ", "rain_mm_per_hour": 0.0,
            "wind_speed_mps": 2.0,
        },
        "lines": lines or [
            {"line_name": "本命", "cars": [1, 2, 3]},
            {"line_name": "別線", "cars": [4, 5, 6]},
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 85.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "近畿"}
            for i in range(1, 8)
        ],
        "odds": [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# select_renderer: 優先順位
# ---------------------------------------------------------------------------


class TestSelectRenderer:
    def test_default_is_v1_when_no_env(self):
        assert select_renderer("auto", env={}) == "v1"
        assert select_renderer(None, env={}) == "v1"

    def test_env_1_true_yes_on_returns_v2(self):
        for val in ("1", "true", "yes", "on", "True", "YES", "ON"):
            assert select_renderer("auto", env={ENV_VAR_NAME: val}) == "v2", (
                f"env={val!r} should select v2"
            )

    def test_env_falsy_returns_v1(self):
        for val in ("0", "false", "no", "off", "", "anything_else"):
            assert select_renderer("auto", env={ENV_VAR_NAME: val}) == "v1", (
                f"env={val!r} should select v1"
            )

    def test_explicit_overrides_env(self):
        assert select_renderer("v1", env={ENV_VAR_NAME: "1"}) == "v1"
        assert select_renderer("v2", env={ENV_VAR_NAME: "0"}) == "v2"

    def test_invalid_explicit_raises(self):
        with pytest.raises(ValueError):
            select_renderer("v3", env={})


# ---------------------------------------------------------------------------
# render_prediction_auto: v1/v2 切り替え
# ---------------------------------------------------------------------------


class TestRenderPredictionAuto:
    def test_v1_no_meta_comment(self):
        """v1 では末尾コメントが付かない (既存出力との互換性)。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
        )
        md = render_prediction_auto(
            pred, input_data=_input(), renderer="v1",
        )
        assert "renderer=output_plan_v2" not in md

    def test_v2_adds_meta_comment(self):
        """v2 では Markdown 末尾に <!-- renderer=output_plan_v2 --> が付く。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
        )
        md = render_prediction_auto(
            pred, input_data=_input(), renderer="v2",
        )
        assert "renderer=output_plan_v2" in md, (
            f"v2 メタ情報が末尾に無い:\n{md[-200:]}"
        )

    def test_auto_with_env_v2_uses_v2(self):
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
        )
        md = render_prediction_auto(
            pred, input_data=_input(),
            renderer="auto", env={ENV_VAR_NAME: "1"},
        )
        assert "renderer=output_plan_v2" in md

    def test_auto_without_env_uses_v1(self):
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
        )
        md = render_prediction_auto(
            pred, input_data=_input(),
            renderer="auto", env={},
        )
        assert "renderer=output_plan_v2" not in md

    def test_v2_with_no_input_data_falls_back_to_v1(self):
        """v2 指定でも input_data=None なら v1 にフォールバック。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
        )
        md = render_prediction_auto(
            pred, input_data=None, renderer="v2",
        )
        # v1 にフォールバックしたので v2 メタコメントは無い
        assert "renderer=output_plan_v2" not in md


# ---------------------------------------------------------------------------
# CLI --renderer フラグ
# ---------------------------------------------------------------------------


class TestCliRendererFlag:
    def test_predict_command_accepts_renderer_option(self):
        """predict コマンドが --renderer v1|v2|auto オプションを受け付ける。"""
        from click.testing import CliRunner
        from app.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["predict", "--help"])
        assert result.exit_code == 0
        assert "--renderer" in result.output
        assert "v1" in result.output
        assert "v2" in result.output
        assert "auto" in result.output


# ---------------------------------------------------------------------------
# 837b8ee 後続レビュー反映: CLI 実行経路の renderer 切り替えテスト
# ---------------------------------------------------------------------------


class TestCliRendererSwitching:
    """CliRunner で実際に predict コマンドを呼び、renderer 切り替えを確認。"""

    def _invoke(self, args, env=None):
        """predict コマンドを CliRunner で実行する共通ヘルパ。

        codex review 反映: CliRunner.invoke(env=...) は **指定キーだけの
        上書き** で、親プロセスの環境変数は残る。`KEIRIN_USE_OUTPUT_PLAN`
        を明示的に削除するために、env に含まれない場合は `None` を渡す。
        """
        from click.testing import CliRunner
        from app.cli import cli
        runner = CliRunner()
        # examples/race_sample.json を使い、--provider mock + --no-save で
        # 外部依存と DB 書き込みを避ける
        full_args = [
            "predict",
            "--input", "examples/race_sample.json",
            "--provider", "mock",
            "--no-save",
            "--no-reflections",
        ] + args
        # CliRunner の env は os.environ への上書き。
        # 明示的に削除したい場合は None を渡す。
        invoke_env = dict(env) if env else {}
        if "KEIRIN_USE_OUTPUT_PLAN" not in invoke_env:
            invoke_env["KEIRIN_USE_OUTPUT_PLAN"] = None
        return runner.invoke(cli, full_args, env=invoke_env)

    def test_renderer_v2_outputs_meta_comment(self):
        """--renderer v2 で Markdown 末尾に renderer=output_plan_v2 コメント。"""
        result = self._invoke(["--renderer", "v2"], env={})
        assert result.exit_code == 0, result.output
        assert "renderer=output_plan_v2" in result.output, (
            f"v2 メタコメントが出ない:\n{result.output[-500:]}"
        )

    def test_renderer_v1_does_not_output_meta_comment(self):
        """--renderer v1 では v2 メタコメントが出ない。"""
        result = self._invoke(["--renderer", "v1"], env={})
        assert result.exit_code == 0, result.output
        assert "renderer=output_plan_v2" not in result.output, (
            f"v1 で v2 メタコメントが出ている:\n{result.output[-500:]}"
        )

    def test_renderer_auto_with_env_uses_v2(self):
        """KEIRIN_USE_OUTPUT_PLAN=1 + --renderer auto で v2 になる。"""
        result = self._invoke(
            ["--renderer", "auto"],
            env={"KEIRIN_USE_OUTPUT_PLAN": "1"},
        )
        assert result.exit_code == 0, result.output
        assert "renderer=output_plan_v2" in result.output, (
            f"env=1 + auto で v2 にならない:\n{result.output[-500:]}"
        )

    def test_renderer_auto_without_env_uses_v1(self):
        """環境変数なし + --renderer auto で v1。"""
        result = self._invoke(["--renderer", "auto"], env={})
        assert result.exit_code == 0, result.output
        assert "renderer=output_plan_v2" not in result.output, (
            f"env なし + auto で v2 になっている:\n{result.output[-500:]}"
        )

    def test_explicit_v1_overrides_env_v2(self):
        """明示 --renderer v1 は環境変数 v2 を上書きする。"""
        result = self._invoke(
            ["--renderer", "v1"],
            env={"KEIRIN_USE_OUTPUT_PLAN": "1"},
        )
        assert result.exit_code == 0, result.output
        assert "renderer=output_plan_v2" not in result.output, (
            f"明示 v1 が env v2 を上書きしない:\n{result.output[-500:]}"
        )


# ---------------------------------------------------------------------------
# 公開 API: env_says_output_plan_v2 / default_renderer_from_env
# ---------------------------------------------------------------------------


class TestPublicEnvHelpers:
    def test_env_says_output_plan_v2_is_public(self):
        from app.renderer_selector import env_says_output_plan_v2
        assert callable(env_says_output_plan_v2)
        assert env_says_output_plan_v2(env={}) is False
        assert env_says_output_plan_v2(env={ENV_VAR_NAME: "1"}) is True

    def test_default_renderer_from_env_is_public(self):
        from app.renderer_selector import default_renderer_from_env
        assert default_renderer_from_env(env={}) == "v1"
        assert default_renderer_from_env(env={ENV_VAR_NAME: "true"}) == "v2"

    def test_legacy_private_alias_still_works(self):
        """後方互換: _env_says_v2 は env_says_output_plan_v2 の alias。"""
        from app.renderer_selector import (
            _env_says_v2, env_says_output_plan_v2,
        )
        assert _env_says_v2 is env_says_output_plan_v2


# ---------------------------------------------------------------------------
# 静岡4R シナリオ: v2 経由で未登録 combo が排除される
# ---------------------------------------------------------------------------


class TestShizuoka4rViaAuto:
    def test_v2_excludes_llm_unregistered_combos(self):
        """v2 経由で render すると、LLM 捏造 combo (4-3-6 等) が出ない。"""
        pred = _pred(
            honsen=[
                _bet("2-5-3", market_odds=12.0, value_label="妙味あり"),
                _bet("2-5-4", market_odds=18.0, value_label="妙味あり"),
            ],
            osae=[
                _bet("5-2-4", market_odds=126.0, value_label="穴として少額",
                     category="押さえ"),
            ],
            final_conclusion="本線では 4-3-6, 3-4-6, 4-6-3 を中心に据える。",
        )
        md = render_prediction_auto(
            pred, input_data=_input(), renderer="v2",
        )
        body = md.split("## 10. 最終結論")[1].split("\n---\n")[0]
        for bad in ("4-3-6", "3-4-6", "4-6-3"):
            assert bad not in body, (
                f"v2 経由でも捏造 combo {bad} が結論部に出ている:\n{body}"
            )

    def test_v1_still_works_without_changes(self):
        """v1 (デフォルト) は既存挙動を変えない。"""
        pred = _pred(
            honsen=[
                _bet("2-5-3", market_odds=12.0, value_label="妙味あり"),
            ],
        )
        md_v1 = render_prediction_auto(
            pred, input_data=_input(), renderer="v1",
        )
        # v1 では既存通り 2-5-3 が出る
        assert "2-5-3" in md_v1
        # v1 メタコメントは付かない
        assert "renderer=output_plan_v2" not in md_v1


# ---------------------------------------------------------------------------
# codex review 反映: MARKDOWN_FALLBACK_LEAKED が Markdown にもマーカーで残る
# ---------------------------------------------------------------------------


class TestFallbackMarker:
    def test_normal_v2_no_fallback_marker(self):
        """通常 v2 では MARKDOWN_FALLBACK_LEAKED マーカーが付かない。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
        )
        md = render_prediction_auto(
            pred, input_data=_input(), renderer="v2",
        )
        assert "MARKDOWN_FALLBACK_LEAKED" not in md
