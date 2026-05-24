"""武雄2R: 結論文整合 + 重複除外 + 本線3点制限 + 低配当注意 (要件1-5)。

検証要件 (2026-05-24):
1. 最終結論文の本線推奨 == 「### 一番買いたい買い目」 の順序
2. 一番買いたい買い目が「### 押さえるべき買い目」に重複表示されない
3. 本線セクション「**実購入候補**」は最大3点
4. odds=None の本線候補は「**オッズ確認後の本線候補**」セクションに分離
5. 実購入候補4点以上 + market_odds<10 → 「⚠️ 低配当注意」を表示
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.cli import (
    _build_purchase_judgement,
    _compute_top_pick,
    _summarize_for_final,
    render_prediction,
)
from app.llm_client import build_default_client
from app.models import (
    BetRecommendation,
    Prediction,
    RaceInput,
)
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
    Path(__file__).resolve().parent
    / "fixtures"
    / "takeo_2r_alignment.json"
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
# 要件1: 最終結論文と一番買いたいの順序整合
# ---------------------------------------------------------------------------


class TestConclusionTopPickAlignment:
    """final_conclusion 本文の本線推奨が、top_pick 順序と一致する。"""

    def test_conclusion_rewritten_when_honsen_empty(self):
        """codex review 反映: honsen が空でも osae 由来 top_pick で書き換え。"""
        ri = _load()
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="t", venue_trend_text="t",
            weather_text="t", lines_text="t",
            honsen=[],  # 空
            osae=[
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="9-3-4",
                    reason="t", gami_risk=0.0,
                    market_odds=7.2, value_label="妙味あり",
                ),
            ],
            ana=[], ooana=[],
            # 古い本線推奨が結論文に書かれている
            final_conclusion="本線は 5-3-8, 3-5-8 を中心に据える。",
            gami_memo="", reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        conclusion_block = out.split("## 10. 最終結論")[1].split("##")[0]
        # 結論文が osae 由来 top_pick (9-3-4) で書き換わる
        assert "9-3-4" in conclusion_block, (
            f"honsen 空でも osae 由来の top_pick で結論文を書き換えるべき:\n"
            f"{conclusion_block}"
        )
        # 古い記載は消える
        assert "5-3-8" not in conclusion_block.split("本線は")[1].split("。")[0], (
            f"古い本線推奨は書き換わるべき:\n{conclusion_block}"
        )

    def test_conclusion_first_combo_matches_top_pick_first(self):
        """本文「本線は X, Y を中心に据える」の X が top_pick[0] と一致。"""
        ri = _load()
        pred = _prediction(ri)
        out = render_prediction(pred, input_data=ri)
        # 最終結論ブロック抽出
        assert "## 10. 最終結論" in out
        conclusion_block = out.split("## 10. 最終結論")[1].split("##")[0]
        m = re.search(r"本線は\s*([\d\-, ]+)を中心に据える", conclusion_block)
        assert m, "最終結論文に「本線は X を中心に据える」が無い"
        conclusion_combos = [c.strip() for c in m.group(1).split(",")]
        # top_pick を計算
        top_pick = _compute_top_pick(pred, max_picks=2)
        top_combos = [b.combination for b in top_pick]
        # 結論文の順序が top_pick の順序と一致
        assert conclusion_combos == top_combos, (
            f"結論文の本線推奨 {conclusion_combos} と top_pick {top_combos} "
            f"の順序が一致しません。"
        )

    def test_conclusion_combo_appears_in_top_section(self):
        """結論文の最初の combo が「### 一番買いたい買い目」の先頭と一致。"""
        ri = _load()
        pred = _prediction(ri)
        out = render_prediction(pred, input_data=ri)
        # 結論文の最初の combo
        conclusion_block = out.split("## 10. 最終結論")[1].split("##")[0]
        m = re.search(r"本線は\s*([\d\-, ]+)を中心に据える", conclusion_block)
        assert m
        first_conclusion = m.group(1).split(",")[0].strip()
        # 一番買いたい買い目セクションの最初の combo
        top_section = out.split("### 一番買いたい買い目")[1].split("###")[0]
        top_lines = [
            ln for ln in top_section.split("\n") if ln.strip().startswith("- ")
        ]
        assert top_lines, "一番買いたい買い目が空"
        first_top = re.search(r"\d-\d-\d", top_lines[0]).group(0)
        assert first_conclusion == first_top, (
            f"結論文先頭 {first_conclusion} と一番買いたい先頭 {first_top} "
            f"が一致しません"
        )


# ---------------------------------------------------------------------------
# 要件2: 一番買いたいと押さえの重複除外
# ---------------------------------------------------------------------------


class TestNoDuplicationAcrossSections:
    """top_pick の combo が cover_pick (押さえるべき) に重複表示されない。"""

    def test_top_pick_combo_not_in_cover_section(self):
        """「一番買いたい買い目」の combo が「押さえるべき買い目」に出ない。"""
        ri = _load()
        pred = _prediction(ri)
        out = render_prediction(pred, input_data=ri)
        # 一番買いたいセクションの全 combo
        top_section = out.split("### 一番買いたい買い目")[1].split("###")[0]
        top_combos = set(re.findall(r"\b\d-\d-\d\b", top_section))
        # 押さえるべき買い目セクションの全 combo
        cover_section = out.split("### 押さえるべき買い目")[1].split("###")[0]
        cover_combos = set(re.findall(r"\b\d-\d-\d\b", cover_section))
        overlap = top_combos & cover_combos
        assert not overlap, (
            f"「一番買いたい」と「押さえるべき」で重複: {overlap}\n"
            f"top: {top_combos}\ncover: {cover_combos}"
        )


# ---------------------------------------------------------------------------
# 要件3: 本線最大3点制限
# ---------------------------------------------------------------------------


class TestHonsenMaxThree:
    """## 6. 本線 セクションの「実購入候補」は最大3点。"""

    def test_real_buys_capped_at_three_with_synthetic_pred(self):
        """honsen に odds取得済み妙味 5点を入れても表示は3点に制限される。"""
        ri = _load()
        # 強制的に odds取得済み妙味あり 5点を honsen に置く
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="t", venue_trend_text="t",
            weather_text="t", lines_text="t",
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination=combo,
                    reason="t", gami_risk=0.0,
                    market_odds=odds, value_label="妙味あり",
                )
                for combo, odds in [
                    ("9-3-4", 7.2),
                    ("9-4-3", 9.5),
                    ("9-3-5", 11.0),
                    ("9-5-3", 13.5),
                    ("5-3-8", 28.0),
                ]
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="本線は 9-3-4 を中心に据える。",
            gami_memo="", reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        honsen_block = out.split("## 6. 本線")[1].split("## 7.")[0]
        assert "**実購入候補**" in honsen_block, (
            f"odds取得済み妙味買い目があるので実購入候補セクションが出るべき:\n"
            f"{honsen_block}"
        )
        # 「**実購入候補**」から次の `**` までの `-` 行を数える
        after = honsen_block.split("**実購入候補**")[1]
        section = after.split("**")[0] if "**" in after else after
        bet_lines = [
            ln for ln in section.split("\n") if ln.strip().startswith("-")
        ]
        assert len(bet_lines) <= 3, (
            f"本線実購入候補は最大3点。実際: {len(bet_lines)} 点\n"
            f"{bet_lines}"
        )


# ---------------------------------------------------------------------------
# 要件4: odds=None の本線候補が「オッズ確認後の本線候補」に分離
# ---------------------------------------------------------------------------


class TestOddsConfirmHonsenSeparation:
    """odds=None の本線候補は「オッズ確認後の本線候補」サブセクションに分離。"""

    def test_no_odds_honsen_appears_in_confirm_subsection(self):
        """odds=None の本線が「オッズ確認後の本線候補」に出る。"""
        ri = _load()
        # 強制的に odds=None の本線を作る合成 Prediction
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="t", venue_trend_text="t",
            weather_text="t", lines_text="t",
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="9-3-4",
                    reason="本命ライン直行",
                    gami_risk=0.0, market_odds=7.2,
                    value_label="妙味あり",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-3-8",
                    reason="別線頭の展開",
                    gami_risk=0.0,  # market_odds=None
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="本線は 9-3-4, 5-3-8 を中心に据える。",
            gami_memo="", reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        honsen_block = out.split("## 6. 本線")[1].split("## 7.")[0]
        # 「オッズ確認後の本線候補」サブセクションが出る
        assert "オッズ確認後の本線候補" in honsen_block, (
            f"odds=None 本線が「オッズ確認後」セクションに分離されていない:\n"
            f"{honsen_block}"
        )
        # 5-3-8 が「オッズ確認後」セクションに含まれる
        confirm_section = honsen_block.split("オッズ確認後の本線候補")[1]
        assert "5-3-8" in confirm_section, (
            f"odds=None の 5-3-8 が「オッズ確認後」に出るべき:\n{confirm_section}"
        )


# ---------------------------------------------------------------------------
# 要件5: 低配当注意
# ---------------------------------------------------------------------------


class TestLowOddsWarning:
    """実購入候補4点以上 + market_odds<10 → 「⚠️ 低配当注意」表示。"""

    def test_low_odds_warning_appears(self):
        """odds<10 を含む4点以上の実購入候補で警告が出る。"""
        # 実購入判断レイヤー (_build_purchase_judgement) の単体テスト
        # top_pick 2点 + buy_cover 2点 = 計4点で odds<10 を1点以上含む
        top_pick = [
            BetRecommendation(
                category="本線", bet_type="3連単", combination="9-3-4",
                reason="t", gami_risk=0.0, market_odds=7.2,
                value_label="妙味あり",
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="9-4-3",
                reason="t", gami_risk=0.0, market_odds=9.5,
                value_label="妙味あり",
            ),
        ]
        cover_pick = [
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="9-3-5",
                reason="t", gami_risk=0.0, market_odds=11.0,
                value_label="本線向き",
            ),
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="9-5-3",
                reason="t", gami_risk=0.0, market_odds=13.5,
                value_label="本線向き",
            ),
        ]
        out = "\n".join(_build_purchase_judgement(
            top_pick, cover_pick, [], [],
            honsen=top_pick, osae=cover_pick,
            input_data=None, lines=[],
        ))
        assert "低配当注意" in out, (
            f"odds<10 を含む 4点以上の実購入候補で低配当注意が出るべき:\n{out}"
        )
        # 9-3-4 (7.2倍) と 9-4-3 (9.5倍) が警告対象
        assert "9-3-4" in out and "9-4-3" in out

    def test_no_warning_when_under_threshold(self):
        """実購入候補が3点以下なら低配当注意は出ない。"""
        top_pick = [
            BetRecommendation(
                category="本線", bet_type="3連単", combination="9-3-4",
                reason="t", gami_risk=0.0, market_odds=7.2,
                value_label="妙味あり",
            ),
        ]
        cover_pick = [
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="9-4-3",
                reason="t", gami_risk=0.0, market_odds=9.5,
                value_label="本線向き",
            ),
        ]
        out = "\n".join(_build_purchase_judgement(
            top_pick, cover_pick, [], [],
            honsen=top_pick, osae=cover_pick,
            input_data=None, lines=[],
        ))
        assert "低配当注意" not in out, (
            f"実購入候補2点では低配当注意は出ないはず:\n{out}"
        )

    def test_warning_counts_missing_honsen_too(self):
        """codex review 反映: odds_missing_honsen も実購入候補にカウント。"""
        # top_pick 1点 + missing_honsen 3点 = 4点で odds<10 を含む
        top_pick = [
            BetRecommendation(
                category="本線", bet_type="3連単", combination="9-3-4",
                reason="t", gami_risk=0.0, market_odds=7.2,
                value_label="妙味あり",
            ),
        ]
        # honsen に odds=None を 3点
        honsen_with_missing = top_pick + [
            BetRecommendation(
                category="本線", bet_type="3連単", combination=combo,
                reason="t", gami_risk=0.0,  # odds=None
            )
            for combo in ["5-3-8", "3-5-8", "5-8-3"]
        ]
        out = "\n".join(_build_purchase_judgement(
            top_pick, [], [], [],
            honsen=honsen_with_missing, osae=[],
            input_data=None, lines=[],
        ))
        assert "低配当注意" in out, (
            f"top_pick 1点 + odds_missing_honsen 3点 = 計4点 + odds<10 を含む "
            f"→ 低配当注意が出るべき:\n{out}"
        )

    def test_no_warning_when_all_odds_high(self):
        """全て 10倍以上なら警告は出ない。"""
        top_pick = [
            BetRecommendation(
                category="本線", bet_type="3連単", combination="5-3-8",
                reason="t", gami_risk=0.0, market_odds=28.0,
                value_label="妙味あり",
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="3-5-8",
                reason="t", gami_risk=0.0, market_odds=32.0,
                value_label="妙味あり",
            ),
        ]
        cover_pick = [
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="9-3-5",
                reason="t", gami_risk=0.0, market_odds=11.0,
                value_label="本線向き",
            ),
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="9-5-3",
                reason="t", gami_risk=0.0, market_odds=13.5,
                value_label="本線向き",
            ),
        ]
        out = "\n".join(_build_purchase_judgement(
            top_pick, cover_pick, [], [],
            honsen=top_pick, osae=cover_pick,
            input_data=None, lines=[],
        ))
        assert "低配当注意" not in out, (
            f"全て10倍以上なら低配当注意は出ないはず:\n{out}"
        )
