#!/usr/bin/env python3
"""dry-run: examples/dry_run/*.json を一括 predict して品質確認する手動スクリプト。

使い方:
    python scripts/dry_run_predictions.py

出力:
    outputs/dry_run/{N}.md          各サンプルの予想Markdown
    outputs/dry_run/_SUMMARY.md     観点別チェックの一覧

確認したい品質観点 (仕様レビューに対応):
    1. 本線/押さえ/穴/大穴 の点数分布が自然か
    2. 本線が安すぎる場合にガミ警戒が出るか
    3. 雨/強風時に別線番手・3番手のズレ目候補が出るか
    4. 晴れ/微風時に本線を崩しすぎていないか
    5. ガールズでライン表現が出ていないか
    6. 新人戦で通常ライン戦のロジックが混ざっていないか
    7. オッズ未取得時でも予想が破綻しないか
    8. 反省ログ有無で買い目候補が適度に変わるか（参考: 反省ログなしで実行）
    9. 穴・大穴が過多でないか
    10. 最終結論が4区分（一番買いたい/押さえ/少額穴/ガミ警戒）に分かれているか

このスクリプトは pytest ではなく、手動レビュー用。
LLM は mock 固定。実ネットワーク・実LLM API は使わない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DRY_INPUT_DIR = ROOT / "examples" / "dry_run"
DRY_OUTPUT_DIR = ROOT / "outputs" / "dry_run"


def _setup_path() -> None:
    """app パッケージを import できるよう sys.path に project root を追加。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


_setup_path()

from app.cli import render_prediction  # noqa: E402
from app.llm_client import build_default_client  # noqa: E402
from app.models import RaceInput  # noqa: E402
from app.prompt_builder import build_full_prompt  # noqa: E402
from app.scoring import (  # noqa: E402
    apply_bank_signals,
    apply_market_signals,
    apply_reflection_signals,
    apply_tospo_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    compute_scores,
)
from app.value_analysis import annotate_prediction_with_value  # noqa: E402


# ---------------------------------------------------------------------------
# 単一予想の生成
# ---------------------------------------------------------------------------


def predict_one(ri: RaceInput) -> tuple:
    """1件の予想を作る（mock provider、反省ログなし）。"""
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    bets = build_candidate_bets(ri, scores)
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    client = build_default_client("mock")
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)
    return prediction, render_prediction(prediction)


# ---------------------------------------------------------------------------
# 品質チェック観点
# ---------------------------------------------------------------------------


def run_checks(ri: RaceInput, prediction, md: str) -> dict[str, Any]:
    """観点別チェックを dict で返す。"""
    checks: dict[str, Any] = {}

    # 観点1: 本線/押さえ/穴/大穴 の点数
    checks["本線点数"] = len(prediction.honsen)
    checks["押さえ点数"] = len(prediction.osae)
    checks["穴点数"] = len(prediction.ana)
    checks["大穴点数"] = len(prediction.ooana)
    checks["合計買い目点数"] = (
        len(prediction.honsen) + len(prediction.osae)
        + len(prediction.ana) + len(prediction.ooana)
    )

    # 観点2: 本線が安すぎるとガミ警戒
    cheapest_main = min(
        (b.market_odds for b in prediction.honsen if b.market_odds is not None),
        default=None,
    )
    checks["本線最安オッズ"] = cheapest_main
    if cheapest_main is not None and cheapest_main < 5.0:
        checks["本線安_ガミ警戒あり"] = "ガミになりやすい買い目" in md and any(
            "gami_risk" in b.reason or b.gami_risk >= 0.6 for b in prediction.honsen
        )

    # 観点3: 雨/強風時の必須形
    weather = ri.weather
    if weather and weather.rain_mm_per_hour > 0:
        checks["雨補正含む"] = "雨補正" in md
    if weather and weather.wind_speed_mps >= 4.0:
        checks["強風補正含む"] = "強風補正" in md

    # 観点4: 晴れ/微風で本線を崩しすぎていないか
    if (
        weather and weather.rain_mm_per_hour == 0
        and weather.wind_speed_mps < 3.0
        and not ri.race.resolved_is_girls()
    ):
        # 本線が「本命ライン: 先頭-番手-3番手」または「スコア上位3名の素直」
        # など、自然な軸を含むことを確認
        checks["晴れ微風_本線素直あり"] = any(
            ("素直" in b.reason)
            or ("上位3" in b.reason)
            or ("本命ライン" in b.reason)
            or ("先頭-番手" in b.reason)
            for b in prediction.honsen
        )

    # 観点5: ガールズでライン表現が出ていない
    if ri.race.resolved_is_girls():
        checks["ガールズ_番手差し表現なし"] = "番手差し" not in md
        checks["ガールズ_別線番手表現なし"] = "別線番手" not in md
        # 仕様文「ラインに依存しない」は許容（明示的に「使わない」と書くため）
        # build_candidate_bets が「ガールズ:」reason のみを使うことを確認
        checks["ガールズ_本線にライン根拠なし"] = not any(
            ("別線" in b.reason or "強風補正" in b.reason or "雨補正" in b.reason)
            for b in prediction.honsen
        )

    # 観点6: 新人戦で通常ライン戦のロジックが混ざっていないか
    if "新人" in (ri.race.class_name or "") and not ri.race.resolved_is_girls():
        # 新人戦でもラインがあれば通常戦と同じロジックでOK。
        # ここでは「ラインがあるなら通常戦扱い」を確認するだけ
        checks["新人戦_ライン情報あり"] = len(ri.lines) > 0

    # 観点7: オッズ未取得時に予想が破綻しないか
    if not ri.odds:
        checks["オッズ無_本線生成あり"] = len(prediction.honsen) > 0
        checks["オッズ無_オッズ未取得ラベル"] = "オッズ未取得" in md

    # 観点9: 穴・大穴が過多でないか
    # 仕様の目標は穴4・大穴3。雨/強風/荒れが同時発動するとHARD上限(10)まで膨らむ。
    # 実装の HARD_ANA=10, HARD_OOANA=5 と整合させる。
    checks["穴_過多でない"] = len(prediction.ana) <= 10
    checks["大穴_過多でない"] = len(prediction.ooana) <= 5
    # 合計買い目が 25 点超だと「広げすぎ」（HARD合計の上限）
    checks["買い目_合計25点以下"] = checks["合計買い目点数"] <= 25

    # 観点10: 最終結論4区分の存在
    checks["最終結論_一番買いたい"] = "### 一番買いたい買い目" in md
    checks["最終結論_押さえるべき"] = "### 押さえるべき買い目" in md
    checks["最終結論_少額で足す穴"] = "### 少額で足す穴" in md
    checks["最終結論_ガミになりやすい"] = "### ガミになりやすい買い目" in md

    return checks


# ---------------------------------------------------------------------------
# 反省ログあり vs なしの比較（観点8 用の参考確認）
# ---------------------------------------------------------------------------


def run_with_synthetic_reflections(ri: RaceInput) -> tuple:
    """合成反省ログを注入した場合の予想を作る。

    観点8 のスポットチェック用。実 DB を汚さないようインメモリで実施。
    """
    from app.models import Reflection
    from datetime import datetime

    # 合成: 「別線番手を軽視」「3番手の伸びを軽視」を5件分注入
    synthetic_reflections = [
        Reflection(
            race_id=f"dummy-{i}",
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            actual_result="1-2-3",
            categories=["別線番手を軽視", "3番手の伸びを軽視"],
            weather_condition=(ri.weather.condition if ri.weather else None),
            wind_speed_mps=(ri.weather.wind_speed_mps if ri.weather else None),
            rain_mm_per_hour=(ri.weather.rain_mm_per_hour if ri.weather else None),
            is_girls=ri.race.resolved_is_girls(),
            note="dry-run合成データ",
        )
        for i in range(3)
    ]

    scores = compute_scores(ri)
    apply_reflection_signals(scores, synthetic_reflections, ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    bets = build_candidate_bets(ri, scores)
    prompt = build_full_prompt(ri, scores, bets, reflections=synthetic_reflections)
    client = build_default_client("mock")
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)
    return prediction, render_prediction(prediction)


# ---------------------------------------------------------------------------
# 単体ランナー
# ---------------------------------------------------------------------------


def run_one(input_path: Path) -> tuple[Path, dict[str, Any]]:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    ri = RaceInput.model_validate(raw)
    prediction, md = predict_one(ri)

    # 反省ログあり版（観点8）
    pred_with_refs, md_with_refs = run_with_synthetic_reflections(ri)
    combos_no_ref = {b.combination for buc in (
        prediction.honsen, prediction.osae, prediction.ana, prediction.ooana
    ) for b in buc}
    combos_with_ref = {b.combination for buc in (
        pred_with_refs.honsen, pred_with_refs.osae,
        pred_with_refs.ana, pred_with_refs.ooana,
    ) for b in buc}
    diff = combos_no_ref ^ combos_with_ref

    # ベース観点チェック
    checks = run_checks(ri, prediction, md)
    checks["反省ログあり_買い目変化件数"] = len(diff)

    # Markdown 出力
    DRY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DRY_OUTPUT_DIR / f"{input_path.stem}.md"
    header_lines = [
        f"# dry-run: {input_path.stem}",
        "",
        f"- 場名: {ri.race.venue}",
        f"- レース番号: {ri.race.race_no}R",
        f"- レース種別: {ri.race.class_name}",
        f"- ガールズ: {ri.race.resolved_is_girls()}",
        f"- 天候: {ri.weather.condition if ri.weather else '不明'}, "
        f"雨{ri.weather.rain_mm_per_hour if ri.weather else 0}mm/h, "
        f"風{ri.weather.wind_speed_mps if ri.weather else 0}m/s",
        f"- ライン数: {len(ri.lines)}",
        f"- オッズ件数: {len(ri.odds)}",
        f"- recent_results: {len(ri.recent_results)} 件",
        "",
        "---",
        "",
    ]
    out_path.write_text("\n".join(header_lines) + md, encoding="utf-8")

    return out_path, checks


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _format_summary(all_results: list[tuple[str, Path, dict]]) -> str:
    lines = [
        "# dry-run サマリー",
        "",
        f"検証件数: **{len(all_results)}**",
        "",
        "各サンプルの観点別チェック結果。`True` は仕様通り、`False` は仕様逸脱の疑い。",
        "",
    ]
    for name, out_path, checks in all_results:
        lines.append(f"## {name}")
        lines.append(f"- 詳細出力: `{out_path.relative_to(ROOT)}`")
        for k, v in checks.items():
            if isinstance(v, bool):
                mark = "✅" if v else "⚠️"
                lines.append(f"  - {mark} {k}: {v}")
            else:
                lines.append(f"  - {k}: {v}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if not DRY_INPUT_DIR.exists():
        print(f"[ERROR] 入力ディレクトリが存在しません: {DRY_INPUT_DIR}")
        return 1

    json_files = sorted(DRY_INPUT_DIR.glob("*.json"))
    if not json_files:
        print(f"[ERROR] JSON ファイルが見つかりません: {DRY_INPUT_DIR}/*.json")
        return 1

    print(f"=== dry-run: {len(json_files)} レースを mock provider で予想 ===\n")

    all_results: list[tuple[str, Path, dict[str, Any]]] = []
    errors: list[tuple[str, Exception]] = []
    for jf in json_files:
        try:
            out_path, checks = run_one(jf)
            all_results.append((jf.name, out_path, checks))
            print(f"✅ {jf.name}")
            for k, v in checks.items():
                if isinstance(v, bool) and not v:
                    print(f"   ⚠️ {k}: {v}")
                else:
                    print(f"     {k}: {v}")
            print()
        except Exception as e:
            errors.append((jf.name, e))
            print(f"❌ {jf.name} 失敗: {type(e).__name__}: {e}")
            print()

    # サマリー Markdown を保存
    DRY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = DRY_OUTPUT_DIR / "_SUMMARY.md"
    summary_path.write_text(_format_summary(all_results), encoding="utf-8")

    print(f"=== サマリー: {summary_path.relative_to(ROOT)} ===")
    print(f"成功: {len(all_results)} / 失敗: {len(errors)}")

    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
