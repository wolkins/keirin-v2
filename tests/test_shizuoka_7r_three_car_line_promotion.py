"""静岡7R: 本命2車ライン + 別線3車ライン (5-3-1 型) の昇格回帰テスト。

レース構造:
    本命: 1-7 (2車ライン)
    別線3車: 5-3-4
    別線2車: 2-6
    単騎: 8, 9
    風: 3.0m/s, 静岡は直線長め
    結果: 5-3-1 (別線3車先頭 - 別線3車番手 - 本命自力)

期待:
- best/honsen/osae のいずれかに 5-3-1 が残る
- 5-3-4 / 3-5-1 / 1-5-3 / 1-3-5 のいずれかが押さえに残る
- best が 1-7-* だけに偏らない
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.cli import render_prediction, render_prediction_v2
from app.final_selection import build_final_selection
from app.llm_client import build_default_client
from app.models import Prediction, RaceInput
from app.output_plan import build_output_plan
from app.prompt_builder import build_full_prompt
from app.scoring import (
    apply_bank_signals,
    apply_f2_signals,
    apply_grade_signals,
    apply_home_area_signals,
    apply_market_signals,
    apply_reflection_signals,
    apply_tospo_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    compute_scores,
)
from app.value_analysis import (
    annotate_prediction_with_value,
    promote_oddful_to_honsen,
    promote_oddful_to_osae,
)


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "shizuoka_7r_5_3_1.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


def _prediction(ri: RaceInput) -> Prediction:
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_grade_signals(scores, ri)
    apply_f2_signals(scores, ri)
    apply_home_area_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(pred, scores, ri.odds)
    promote_oddful_to_osae(pred)
    promote_oddful_to_honsen(pred)
    return pred


# ---------------------------------------------------------------------------
# 静岡7R 5-3-1 回帰
# ---------------------------------------------------------------------------


class TestShizuoka7rResult531Regression:
    """5-3-1 型 (別線3車先頭-番手-本命自力) が osae 以上に残る回帰テスト。"""

    def test_531_in_honsen_or_osae(self):
        """5-3-1 が honsen または osae に存在する (raw + OutputPlan 両方)。"""
        ri = _load()
        pred = _prediction(ri)
        # codex review 反映: raw だけでなく OutputPlan も検証
        # (v2 経由でも 5-3-1 が picking 枠に届くことを保証)
        raw_combos = (
            {b.combination for b in pred.honsen}
            | {b.combination for b in pred.osae}
        )
        assert "5-3-1" in raw_combos, (
            f"5-3-1 が raw honsen/osae に無い:\n"
            f"honsen={[b.combination for b in pred.honsen]}\n"
            f"osae={[b.combination for b in pred.osae]}"
        )
        plan = build_output_plan(pred, ri)
        plan_buyable = (
            {b.combination for b in plan.final_best}
            | {b.combination for b in plan.final_osae}
            | {b.combination for b in plan.honsen}
            | {b.combination for b in plan.osae}
        )
        assert "5-3-1" in plan_buyable, (
            f"5-3-1 が OutputPlan の購入候補枠 (final_best/final_osae/"
            f"honsen/osae) に無い:\n"
            f"final_best={[b.combination for b in plan.final_best]}\n"
            f"final_osae={[b.combination for b in plan.final_osae]}\n"
            f"plan.honsen={[b.combination for b in plan.honsen]}\n"
            f"plan.osae={[b.combination for b in plan.osae]}"
        )

    def test_534_remains_as_ana_or_osae(self):
        """5-3-4 (別線3車直行) が ana/ooana/osae のいずれかに残る。"""
        ri = _load()
        pred = _prediction(ri)
        all_combos = (
            {b.combination for b in pred.osae}
            | {b.combination for b in pred.ana}
            | {b.combination for b in pred.ooana}
        )
        # 5-3-4 もしくは関連 (3-5-4 / 5-4-3) のいずれかが残る
        relevant = {"5-3-4", "3-5-4", "5-4-3"}
        kept = relevant & all_combos
        assert kept, (
            f"5-3-4 系 (別線3車直行) が osae/ana/ooana に無い:\n"
            f"osae={[b.combination for b in pred.osae]}\n"
            f"ana={[b.combination for b in pred.ana]}\n"
            f"ooana={[b.combination for b in pred.ooana]}"
        )

    def test_main_jiriki_3rd_pattern_kept(self):
        """1-5-3 または 1-3-5 (本命自力 - 別線3車先頭/番手) も押さえに残る。"""
        ri = _load()
        pred = _prediction(ri)
        all_combos = (
            {b.combination for b in pred.honsen}
            | {b.combination for b in pred.osae}
            | {b.combination for b in pred.ana}
        )
        relevant = {"1-5-3", "1-3-5"}
        kept = relevant & all_combos
        assert kept, (
            f"1-5-3 / 1-3-5 (本命自力頭 + 別線3車3着型) が見当たらない:\n"
            f"honsen={[b.combination for b in pred.honsen]}\n"
            f"osae={[b.combination for b in pred.osae]}\n"
            f"ana={[b.combination for b in pred.ana]}"
        )

    def test_best_or_osae_includes_5x_separate_line(self):
        """別線3車 5-3-* (または 3-5-*) が final_best/final_osae のどこかに残る。

        codex review 反映: 「1-7-* に偏らない」だけでは弱い。本命ラインの
        裏返し (7-1-*) でも通ってしまう。明確に「別線 5- 系」が含まれる
        ことを assert する。
        """
        ri = _load()
        pred = _prediction(ri)
        plan = build_output_plan(pred, ri)
        all_purchase = (
            [b.combination for b in plan.final_best]
            + [b.combination for b in plan.final_osae]
            + [b.combination for b in plan.honsen]
            + [b.combination for b in plan.osae]
        )
        # 別線3車 5-* (1着 5 番) が少なくとも1点含まれる
        has_separate_5_lead = any(c.startswith("5-") for c in all_purchase)
        # または 3-5-* (5 が2着) が含まれる
        has_3_5 = any(c.startswith("3-5-") for c in all_purchase)
        assert has_separate_5_lead or has_3_5, (
            f"別線3車 (5-* または 3-5-*) が購入候補/本線/押さえに無い:\n"
            f"all_purchase={all_purchase}"
        )

    def test_v2_renderer_includes_531_in_purchase_section(self):
        """v2 経由で 5-3-1 が **購入判断セクション** (実購入判断 配下) に出る。

        codex review 反映: 「## 6 以降に出る」だけでは watch_only 等で
        通ってしまう。「### 実購入判断」配下に 5-3-1 が確実に出ることを assert。
        """
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction_v2(pred, input_data=ri)
        # 実購入判断セクションを抽出
        if "### 実購入判断" in md:
            judgement = md.split("### 実購入判断")[1].split("\n##")[0]
            assert "5-3-1" in judgement, (
                f"v2 の実購入判断セクションに 5-3-1 が無い:\n{judgement}"
            )
        else:
            # 実購入判断セクションが無い場合は本線セクションを見る
            honsen_block = md.split("## 6. 本線")[1].split("## 7.")[0]
            assert "5-3-1" in honsen_block, (
                f"v2 の本線セクションに 5-3-1 が無い:\n{honsen_block}"
            )

    def test_final_conclusion_does_not_invent_combos(self):
        """最終結論 (v2) に honsen/osae/ana/ooana 外の combo が出ない。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction_v2(pred, input_data=ri)
        plan = build_output_plan(pred, ri)
        plan_combos = plan.all_combos()
        # 結論部のみを抽出 (## 10. 最終結論 ~ 次セクション)
        conclusion = md.split("## 10. 最終結論")[1].split("##")[0]
        conclusion_combos = set(re.findall(r"\b\d-\d-\d\b", conclusion))
        rogue = conclusion_combos - plan_combos
        assert not rogue, (
            f"結論部に OutputPlan 外の combo: {rogue}\n"
            f"--- 結論部 ---\n{conclusion}"
        )
