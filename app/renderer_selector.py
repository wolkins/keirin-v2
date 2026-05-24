"""renderer 切り替え共通モジュール (方針B - 2026-05-24)。

CLI / Web UI から render_prediction (v1) と render_prediction_v2 を
deterministic に切り替えるための共通関数。

切り替えロジック (優先順位):
    1. 明示指定 (`renderer="v1"` または `renderer="v2"`)
    2. 環境変数 KEIRIN_USE_OUTPUT_PLAN ("1" / "true" / "yes" は v2)
    3. デフォルト v1 (互換性)

利用例:
    from app.renderer_selector import render_prediction_auto, select_renderer

    # CLI から: --renderer auto
    md = render_prediction_auto(pred, input_data=ri, renderer="auto")

    # 明示指定: --renderer v2
    md = render_prediction_auto(pred, input_data=ri, renderer="v2")

    # 環境変数だけで判定
    use_v2 = select_renderer("auto") == "v2"
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Literal, Optional

from .models import Prediction, RaceInput

RendererName = Literal["v1", "v2", "auto"]

ENV_VAR_NAME = "KEIRIN_USE_OUTPUT_PLAN"
TRUTHY_VALUES = {"1", "true", "yes", "on"}

_logger = logging.getLogger("keirin.renderer_selector")


def env_says_output_plan_v2(env: Optional[dict] = None) -> bool:
    """環境変数 KEIRIN_USE_OUTPUT_PLAN が truthy なら True (公開 API)。

    UI / 外部モジュールから判定したい場合は本関数を使ってください。
    """
    source = env if env is not None else os.environ
    val = source.get(ENV_VAR_NAME, "")
    return val.strip().lower() in TRUTHY_VALUES


def default_renderer_from_env(env: Optional[dict] = None) -> str:
    """環境変数に基づくデフォルト renderer 名 ("v1" または "v2")。

    UI のチェックボックス初期値などに使う。明示指定をしたい場合は
    select_renderer(explicit, env=...) を呼ぶこと。
    """
    return "v2" if env_says_output_plan_v2(env) else "v1"


# 後方互換 alias (837b8ee 後続レビュー反映)
# 既存の private 名を呼んでいるコードを壊さないため残す。
# 新規コードは env_says_output_plan_v2 を使うこと。
_env_says_v2 = env_says_output_plan_v2


def select_renderer(
    explicit: Optional[str] = None,
    *,
    env: Optional[dict] = None,
) -> Literal["v1", "v2"]:
    """renderer を選択する (deterministic)。

    Args:
        explicit: 明示指定 ("v1" / "v2" / "auto" / None)
                  None または "auto" の場合は環境変数を参照
        env: テスト用に環境変数 dict を注入可能

    Returns:
        "v1" または "v2"
    """
    if explicit and explicit not in ("auto", None):
        if explicit not in ("v1", "v2"):
            raise ValueError(
                f"renderer must be 'v1' / 'v2' / 'auto', got {explicit!r}"
            )
        return explicit  # type: ignore[return-value]
    # auto モード: 環境変数で判定
    return "v2" if _env_says_v2(env) else "v1"


def render_prediction_auto(
    prediction: Prediction,
    *,
    input_data: Optional[RaceInput] = None,
    renderer: Optional[str] = "auto",
    env: Optional[dict] = None,
) -> str:
    """renderer を選択して Markdown 出力を返す。

    Args:
        prediction: Prediction オブジェクト
        input_data: RaceInput (v2 には必須)
        renderer: "v1" / "v2" / "auto" (デフォルト: "auto")
        env: テスト用に環境変数 dict を注入可能

    Returns:
        Markdown 文字列。v2 使用時は末尾コメントに <!-- renderer=output_plan_v2 -->
    """
    # 循環 import 回避: 関数内で import
    from .cli import render_prediction, render_prediction_v2

    chosen = select_renderer(renderer, env=env)
    if chosen == "v2":
        if input_data is None:
            # v2 は input_data 必須。input_data なしなら v1 にフォールバック。
            _logger.warning(
                "v2 が指定されましたが input_data=None のため v1 にフォールバック"
            )
            print(
                "[renderer_selector] v2 -> v1 fallback (input_data=None)",
                file=sys.stderr,
            )
            return render_prediction(prediction, input_data=input_data)
        md = render_prediction_v2(prediction, input_data=input_data)
        # 末尾コメント (Markdown では非表示) + stderr ログ
        md += "\n<!-- renderer=output_plan_v2 -->"
        _logger.info("rendered with output_plan_v2 (race_id=%s)", prediction.race_id)
        print(
            f"[renderer_selector] renderer=output_plan_v2 race_id={prediction.race_id}",
            file=sys.stderr,
        )
        # fallback 検出: warnings に MARKDOWN_FALLBACK_LEAKED / MARKDOWN_COMBO_UNREGISTERED が含まれるか
        if "MARKDOWN_COMBO_UNREGISTERED" in md or "MARKDOWN_FALLBACK_LEAKED" in md:
            _logger.warning(
                "v2 fallback triggered for race_id=%s", prediction.race_id
            )
            print(
                f"[renderer_selector] v2 fallback triggered "
                f"race_id={prediction.race_id}",
                file=sys.stderr,
            )
        return md
    # v1 (デフォルト/互換)
    _logger.debug("rendered with v1 (race_id=%s)", prediction.race_id)
    return render_prediction(prediction, input_data=input_data)
