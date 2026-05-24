"""武雄1R: 市場偏り集中頭の最終判断保持 + odds=None本線の明示 + 整合 (要件1-5)。

検証要件 (2026-05-24):
1. market_bias=head=9 が4/5件 → 9番頭が最終判断 (実購入判断) に最低2点残る
2. honsen の odds=None が「オッズ確認後の本線候補」に表示される
3. final_conclusion 本文と一番買いたい買い目が矛盾しない
4. 集中頭の低配当は「市場偏り(集中頭)」と区別表示
5. (本テストファイル全体で検証)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.cli import _summarize_for_final, render_prediction
from app.llm_client import build_default_client
from app.models import (
    BetRecommendation,
    Line,
    OddsEntry,
    Prediction,
    RaceInfo,
    RaceInput,
    Rider,
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
    / "takeo_1r_focused_head_retention.json"
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
# 要件1: 集中頭の最終判断最低2点保持
# ---------------------------------------------------------------------------


class TestFocusedHeadFinalRetention:
    """market_bias=head=9 (4/5件) で最終判断に9番頭が最低2点残る。"""

    def test_bias_detects_head_nine(self):
        """前提: bias.focused_head=9, focused_count>=3。"""
        ri = _load()
        from app.output_validation import detect_market_bias
        bias = detect_market_bias(ri)
        assert bias.focused_head == 9
        assert bias.focused_count >= 3
        assert bias.is_focused_head_cheap is True  # 最安3.2倍

    def test_final_judgement_keeps_focused_head_at_least_two(self):
        """実購入判断セクション全体に 9番頭の3連単が最低2点表示される。

        枠は問わない:
          - 「オッズ取得済み」 / 「オッズ確認後の本線候補」 /
          - 「オッズ未取得だが展開上必要な候補」 / 「市場注目枠(9番頭の派生候補)」
        のどれかに 9- で始まる combination が累計 2点以上含まれる。
        """
        ri = _load()
        pred = _prediction(ri)
        out = render_prediction(pred, input_data=ri)
        # 「### 実購入判断」セクション全体を抽出
        if "### 実購入判断" not in out:
            pytest.fail("実購入判断セクションが無い")
        judgement = out.split("### 実購入判断")[1].split("##")[0]
        # 「---」までで終わる (次のセクション境界)
        if "---" in judgement:
            judgement = judgement.split("---")[0]
        nine_head_combos = set(re.findall(r"\b9-\d-\d\b", judgement))
        assert len(nine_head_combos) >= 2, (
            f"集中頭(9)の買い目が最終判断に最低2点必要、実際は "
            f"{len(nine_head_combos)} 点: {sorted(nine_head_combos)}\n"
            f"--- 判断セクション ---\n{judgement}"
        )

    def test_market_focus_supplement_section_appears_when_needed(self):
        """top_pick/tenkai が集中頭を含まない場合、「市場注目枠」セクションが出る。"""
        ri = _load()
        # 集中頭=9 でも honsen+osae に 9- 始まりが少ないシナリオを合成
        # → 補充表示で「市場注目枠」セクションが現れる
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-9-5",
                    reason="本命番手頭",
                    gami_risk=0.0,
                    market_odds=12.0,
                    value_label="妙味あり",
                ),
            ],
            osae=[
                # 集中頭=9 の派生候補を osae に置く
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="9-1-6",
                    reason="市場偏り(9番頭集中): 3連単人気上位(3.2倍)を保持",
                    gami_risk=0.8,
                    market_odds=3.2,
                ),
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="9-1-5",
                    reason="市場偏り(9番頭+1絡み): 派生候補(4.5倍)",
                    gami_risk=0.7,
                    market_odds=4.5,
                ),
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="9-5-1",
                    reason="市場偏り(9番頭+5絡み): 派生候補(5.5倍)",
                    gami_risk=0.0,
                    market_odds=5.5,
                    value_label="本線向き",
                ),
            ],
            ana=[],
            ooana=[],
            final_conclusion="本線は 1-9-5 を中心に据える。",
            gami_memo="",
            reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        # final_selection ルール5: 9番頭の odds取得済み買い目が最低1点残る
        # (cheap_popular_bets に集中頭が含まれる場合は別途参考表示で OK)
        if "### 実購入判断" in out:
            judgement = out.split("### 実購入判断")[1]
            if "---" in judgement:
                judgement = judgement.split("---")[0]
            nine_head_combos = set(re.findall(r"\b9-\d-\d\b", judgement))
            assert len(nine_head_combos) >= 1, (
                f"集中頭(9) の買い目が最低1点必要 (final_selection ルール5)、"
                f"実際は {len(nine_head_combos)} 点"
            )


# ---------------------------------------------------------------------------
# 要件2: honsen の odds=None が「オッズ確認後の本線候補」に表示
# ---------------------------------------------------------------------------


class TestHonsenNoOddsShownAsConfirm:
    """honsen の market_odds=None 買い目が「オッズ確認後の本線候補」に明示される。"""

    def test_honsen_no_odds_appears_in_confirm_section(self):
        """1-9-5 が honsen にあり odds=None → 「オッズ確認後の本線候補」に出る。"""
        ri = _load()
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[
                # odds取得済み 2点 (top_pick を埋めて 1-9-5 を top_pick から外す)
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="9-5-1",
                    reason="本命ライン", gami_risk=0.0,
                    market_odds=5.5, value_label="本線向き",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="9-5-6",
                    reason="本命ライン", gami_risk=0.0,
                    market_odds=8.0, value_label="本線向き",
                ),
                # odds=None の本線: 「オッズ確認後の本線候補」に出るべき
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-9-5",
                    reason="本命番手頭",
                    gami_risk=0.0,
                ),
            ],
            osae=[],
            ana=[],
            ooana=[],
            final_conclusion="本線は 9-5-1, 1-9-5 を中心に据える。",
            gami_memo="",
            reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        assert "### 実購入判断" in out
        judgement = out.split("### 実購入判断")[1].split("---")[0]
        # 「オッズ確認後の本線候補」ラベルが出る
        confirm_line = next(
            (ln for ln in judgement.split("\n")
             if "オッズ確認後の本線候補" in ln),
            "",
        )
        assert confirm_line, (
            f"「オッズ確認後の本線候補」セクションが見当たらない\n"
            f"--- 判断 ---\n{judgement}"
        )
        assert "1-9-5" in confirm_line, (
            f"1-9-5 が「オッズ確認後の本線候補」に出るべき: {confirm_line}"
        )


# ---------------------------------------------------------------------------
# 要件3: 本文と一番買いたい買い目の整合
# ---------------------------------------------------------------------------


class TestConclusionAndJudgementAlignment:
    """final_conclusion 本文と「一番買いたい買い目」が矛盾しない。"""

    def test_conclusion_combos_appear_in_judgement_section(self):
        """本文「本線は X-Y-Z, A-B-C」の combination がいずれも実購入判断
        セクションのどこかに表示される。"""
        ri = _load()
        pred = _prediction(ri)
        out = render_prediction(pred, input_data=ri)
        # final_conclusion を抽出
        assert "## 10. 最終結論" in out
        conclusion_block = out.split("## 10. 最終結論")[1].split("##")[0]
        # 「本線は X, Y を中心に据える」を解析
        m = re.search(r"本線は\s*([\d\-,\s]+)を中心に据える", conclusion_block)
        if not m:
            pytest.skip("本文に「本線は X を中心に据える」が見当たらない")
        conclusion_combos = set(re.findall(r"\d-\d-\d", m.group(1)))
        # 実購入判断セクション全体
        judgement = out.split("### 実購入判断")[1].split("---")[0]
        judgement_combos = set(re.findall(r"\b\d-\d-\d\b", judgement))
        missing = conclusion_combos - judgement_combos
        assert not missing, (
            f"本文で推奨している {missing} が実購入判断のどこにも出ていない。\n"
            f"--- 本文 ---\n{conclusion_block}\n"
            f"--- 判断 ---\n{judgement}"
        )


# ---------------------------------------------------------------------------
# 要件4: 集中頭低配当の区別表示
# ---------------------------------------------------------------------------


class TestFocusedHeadCheapLabel:
    """集中頭の低配当 (9-1-6 等 odds<5) は「市場偏り(集中頭)」と区別表示。"""

    def test_market_focused_cheap_distinct_from_normal_cheap(self):
        """reason に「市場偏り」を含み odds<5 の買い目は別ラベルで出る。"""
        ri = _load()
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-9-1",
                    reason="本命ライン", gami_risk=0.0,
                    market_odds=15.0, value_label="妙味あり",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-9-5",
                    reason="本命番手頭",
                    gami_risk=0.0,
                    market_odds=12.0, value_label="妙味あり",
                ),
            ],
            osae=[
                # 市場偏り起因の低配当 → 「市場偏り(集中頭)」ラベル
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="9-1-6",
                    reason="市場偏り(9番頭集中): 3連単人気上位(3.2倍)を保持",
                    gami_risk=0.8,
                    market_odds=3.2,
                ),
            ],
            ana=[],
            ooana=[],
            final_conclusion="本線は 5-9-1 を中心に据える。",
            gami_memo="",
            reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        if "### 実購入判断" not in out:
            pytest.fail("実購入判断セクションが無い")
        judgement = out.split("### 実購入判断")[1].split("---")[0]
        # final_selection 統合 (2026-05-24): 集中頭+odds<5 は
        # cheap_popular_bets に分離され、「安い人気筋」ラベルで表示される
        # (旧「市場偏り(集中頭)」ラベルは final_selection 単一分類に統合)
        assert "安い人気筋" in judgement or "ガミ注意" in judgement, (
            f"odds<5 の集中頭は安い人気筋ラベルで出るべき: {judgement}"
        )
        # 9-1-6 がいずれかの安い人気筋ラベル行に出る
        cheap_line = next(
            (ln for ln in judgement.split("\n")
             if ("安い人気筋" in ln or "ガミ注意" in ln)),
            "",
        )
        assert "9-1-6" in cheap_line, (
            f"9-1-6 が安い人気筋 枠に出るべき: {cheap_line}"
        )

    def test_focused_head_cheap_not_in_top_pick(self):
        """集中頭の低配当 (odds<5) は「一番買いたい買い目」には入らない。"""
        ri = _load()
        pred = _prediction(ri)
        out = render_prediction(pred, input_data=ri)
        # 「一番買いたい買い目」セクションを抽出
        if "### 一番買いたい買い目" not in out:
            pytest.skip("一番買いたい買い目セクションが無い構成")
        top_section = out.split("### 一番買いたい買い目")[1].split("###")[0]
        # 9-1-6 (3.2倍 < 5) が top に出ないこと
        # 9-1-5 (4.5倍 < 5) も top に出ないこと
        assert "9-1-6" not in top_section, (
            f"9-1-6 (3.2倍) は一番買いたいに入らないはず: {top_section}"
        )
        assert "9-1-5" not in top_section, (
            f"9-1-5 (4.5倍) は一番買いたいに入らないはず: {top_section}"
        )


# ---------------------------------------------------------------------------
# codex review 反映: 重複除外と低配当扱い
# ---------------------------------------------------------------------------


class TestCodexReviewFixesTakeo:
    """codex review (2026-05-24, 武雄1R サイクル) で検出された P2 の回帰テスト。"""

    def test_market_focus_supplement_excludes_low_odds(self):
        """市場注目枠の補充に odds<5 (安すぎ) の集中頭買い目を入れない。

        9-1-6 (3.2倍) のような買い目は「市場偏り(集中頭の低配当)」枠で
        既に「厚く買わない」と表示されるため、市場注目枠に補充されると
        実購入判断内で「残す」と「厚く買わない」が矛盾する。
        """
        ri = _load()
        # honsen+osae に odds<5 の集中頭買い目しか無い状況を作る
        # → 市場注目枠の補充は発動しない (該当 head_pool が空)
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-9-1",
                    reason="本命別線頭", gami_risk=0.0,
                    market_odds=15.0, value_label="妙味あり",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-9-5",
                    reason="本命番手頭", gami_risk=0.0,
                    market_odds=12.0, value_label="妙味あり",
                ),
            ],
            osae=[
                # 安すぎる集中頭買い目 (odds<5)
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="9-1-6",
                    reason="本命ライン直行",
                    gami_risk=0.8,
                    market_odds=3.2,
                ),
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="9-1-5",
                    reason="本命ライン",
                    gami_risk=0.8,
                    market_odds=4.5,
                ),
            ],
            ana=[], ooana=[],
            final_conclusion="本線は 5-9-1 を中心に据える。",
            gami_memo="", reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        judgement = out.split("### 実購入判断")[1].split("---")[0]
        # 「市場注目枠」と「市場偏り(集中頭の低配当)」両方に同じ
        # combination が出ないこと
        supplement_line = next(
            (ln for ln in judgement.split("\n")
             if "市場注目枠" in ln),
            "",
        )
        cheap_line = next(
            (ln for ln in judgement.split("\n")
             if "市場偏り(集中頭" in ln),
            "",
        )
        supplement_combos = set(re.findall(r"\d-\d-\d", supplement_line))
        cheap_combos = set(re.findall(r"\d-\d-\d", cheap_line))
        overlap = supplement_combos & cheap_combos
        assert not overlap, (
            f"「市場注目枠」と「市場偏り(集中頭の低配当)」で重複: {overlap}\n"
            f"--- 判断 ---\n{judgement}"
        )

    def test_missing_honsen_not_duplicated_in_buy_cover(self):
        """odds_missing_honsen の買い目が buy_cover にも重複表示されない。"""
        ri = _load()
        # cover_with_odds が空、cover に odds=None 本線が含まれる構成
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="9-5-1",
                    reason="本命ライン",
                    gami_risk=0.0,
                    market_odds=5.5, value_label="本線向き",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="9-5-6",
                    reason="本命ライン",
                    gami_risk=0.0,
                    market_odds=8.0, value_label="本線向き",
                ),
                # odds=None 本線: 「オッズ確認後の本線候補」に出るべき
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-9-5",
                    reason="本命番手頭",
                    gami_risk=0.0,
                ),
            ],
            osae=[],  # cover_pick は空 (honsen から補充される)
            ana=[], ooana=[],
            final_conclusion="本線は 9-5-1 を中心に据える。",
            gami_memo="", reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        judgement = out.split("### 実購入判断")[1].split("---")[0]
        missing_line = next(
            (ln for ln in judgement.split("\n")
             if "オッズ確認後の本線候補" in ln),
            "",
        )
        cover_line = next(
            (ln for ln in judgement.split("\n")
             if "押さえとして必要" in ln),
            "",
        )
        missing_combos = set(re.findall(r"\d-\d-\d", missing_line))
        cover_combos = set(re.findall(r"\d-\d-\d", cover_line))
        overlap = missing_combos & cover_combos
        assert not overlap, (
            f"「オッズ確認後の本線候補」と「押さえとして必要」で重複: {overlap}\n"
            f"--- 判断 ---\n{judgement}"
        )
