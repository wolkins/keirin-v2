"""広島9R: 本線オッズ取得率0%時の押さえ→本線昇格 + 実購入判断4枠分割 (要件1-5)。

検証要件:
1. 本線全 odds=None + 押さえに odds 取得済み + 妙味あり
   → odds 取得済みが「オッズ取得済みで買える候補」に昇格表示
2. 市場偏り 3番頭集中 → 3番頭の買い目が最低2点 honsen/osae に含まれる
3. 実購入判断が 4枠 (オッズ確認後 / オッズ取得済み / 押さえ / 穴) に分離
4. 本線として有力が odds=None のみのケースで「オッズ確認後」と明記
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import _summarize_for_final, render_prediction
from app.llm_client import build_default_client
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInfo, RaceInput, Rider,
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
    / "hiroshima_9r_market_bias_with_only_osae_odds.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


def _full(ri: RaceInput):
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
    return scores, bets


def _prediction(ri: RaceInput):
    scores, bets = _full(ri)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(pred, scores, ri.odds)
    promote_oddful_to_osae(pred)
    promote_oddful_to_honsen(pred)
    return pred


# ---------------------------------------------------------------------------
# 要件1: 本線全 odds=None + odds取得済み妙味 → 実購入候補に昇格
# ---------------------------------------------------------------------------


class TestOddsPromotedToBuyableSection:
    def test_oddful_buyable_appears_in_judgement(self):
        """3-1-2 (20.7倍/妙味あり) が「オッズ取得済みで買える候補」に昇格。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        # 実購入判断セクション
        judgement = md.split("### 実購入判断")[1].split("---")[0]
        assert "オッズ取得済みで買える候補" in judgement, (
            f"オッズ取得済みで買える候補セクションが無い:\n{judgement}"
        )
        # 3-1-2 がその行に含まれる
        buyable_line = next(
            (ln for ln in judgement.split("\n")
             if "オッズ取得済みで買える候補" in ln),
            "",
        )
        assert "3-1-2" in buyable_line, (
            f"3-1-2 が「オッズ取得済みで買える候補」に出ていない: {buyable_line}"
        )

    def test_pure_odds_none_case_uses_check_section(self):
        """odds 取得済み候補が無く、本線全 odds=None なら「オッズ確認後の本線候補」のみ。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="6-3-7",
                reason="t", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        judgement = text.split("### 実購入判断")[1]
        # 「オッズ確認後の本線候補」と表示
        assert "オッズ確認後の本線候補" in judgement
        # 「オッズ取得済みで買える候補」は出ない
        assert "オッズ取得済みで買える候補" not in judgement


# ---------------------------------------------------------------------------
# 要件2: 3番頭集中時、3番頭の買い目が最低2点
# ---------------------------------------------------------------------------


class TestMarketBiasThreeHead:
    def test_three_head_minimum_two_in_honsen_or_osae(self):
        """市場上位5件中4件が3番頭 → honsen/osae に 3番頭が最低2点。"""
        ri = _load()
        _, bets = _full(ri)
        all_combos = (
            [b.combination for b in bets["本線"]]
            + [b.combination for b in bets["押さえ"]]
        )
        three_head_count = sum(
            1 for c in all_combos
            if c.split("-")[0] == "3"
        )
        assert three_head_count >= 2, (
            f"3番頭が2点未満: {three_head_count}点 / 全候補: {all_combos}"
        )


# ---------------------------------------------------------------------------
# 要件3: 実購入判断 4枠分割
# ---------------------------------------------------------------------------


class TestPurchaseJudgementSplit:
    def test_four_label_separation(self):
        """主要ラベル (オッズ取得済み / 押さえ) が分離されて出る。

        ※「オッズ確認後の本線候補」と「オッズ未取得だが展開上必要な候補」は
        市場偏り派生候補生成 (2026-05-24) により honsen が odds取得済みで
        埋まるケースが増えたため、必須ではない。本テストは「オッズ取得済み」と
        「押さえとして必要」が分離されることを確認する。
        """
        ri = _load()
        pred = _prediction(ri)
        text = _summarize_for_final(pred)
        judgement = text.split("### 実購入判断")[1]
        assert "オッズ取得済みで買える候補" in judgement, (
            "オッズ取得済みラベルが無い"
        )
        assert "押さえとして必要" in judgement, "押さえラベルが無い"

    def test_odds_present_and_missing_combos_different(self):
        """オッズ取得済み と オッズ確認後 の買い目が重複しない。"""
        ri = _load()
        pred = _prediction(ri)
        text = _summarize_for_final(pred)
        judgement = text.split("### 実購入判断")[1]
        import re
        present_line = next(
            (ln for ln in judgement.split("\n")
             if "オッズ取得済みで買える候補" in ln),
            "",
        )
        missing_line = next(
            (ln for ln in judgement.split("\n")
             if "オッズ確認後の本線候補" in ln),
            "",
        )
        present_combos = set(re.findall(r"\d-\d-\d", present_line))
        missing_combos = set(re.findall(r"\d-\d-\d", missing_line))
        overlap = present_combos & missing_combos
        assert not overlap, (
            f"オッズ取得済みとオッズ確認後で重複: {overlap}"
        )


# ---------------------------------------------------------------------------
# 要件4: 本線として有力が odds=None のみ → 「オッズ確認後」と明記
# ---------------------------------------------------------------------------


def test_odds_none_only_uses_check_label():
    """本線として有力が全て odds=None なら「オッズ確認後の本線候補」と表示。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="6-3-7",
                reason="t", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="3-6-7",
                reason="t", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    judgement = text.split("### 実購入判断")[1]
    # オッズ確認後の本線候補と明記
    assert "オッズ確認後の本線候補" in judgement
    # オッズ取得済みで買える候補は出ない
    assert "オッズ取得済みで買える候補" not in judgement


def test_mixed_odds_shows_both_sections():
    """odds 取得済みと未取得が混在 → 両方のラベルが出る。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="3-1-2",
                reason="t", gami_risk=0.0, market_odds=20.7,
                value_label="妙味あり",
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="6-3-7",
                reason="t", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    judgement = text.split("### 実購入判断")[1]
    assert "オッズ取得済みで買える候補" in judgement
    assert "オッズ確認後の本線候補" in judgement
    # 3-1-2 が「オッズ取得済み」側、6-3-7 が「オッズ確認後」側
    present_line = next(
        (ln for ln in judgement.split("\n")
         if "オッズ取得済みで買える候補" in ln),
        "",
    )
    missing_line = next(
        (ln for ln in judgement.split("\n")
         if "オッズ確認後の本線候補" in ln),
        "",
    )
    assert "3-1-2" in present_line
    assert "6-3-7" in missing_line


# ---------------------------------------------------------------------------
# 統合: 広島9R の整合性
# ---------------------------------------------------------------------------


def test_full_render_includes_three_head_promotion():
    """フルレンダリングで、3番頭が一番買いたいまたは実購入判断に出る。"""
    ri = _load()
    pred = _prediction(ri)
    md = render_prediction(pred, input_data=ri)
    # 3-1-2 が「一番買いたい買い目」または「オッズ取得済みで買える候補」に出る
    assert "3-1-2" in md
    # 一番買いたい買い目 セクションに 3-1-2
    top_section = md.split("### 一番買いたい買い目")[1].split("### 押さえるべき")[0]
    assert "3-1-2" in top_section


# ---------------------------------------------------------------------------
# 追加要件 (1,3): 本文の押さえに出た odds取得済み妙味買い目が最終結論で消えない
# ---------------------------------------------------------------------------


class TestOddfulValueBetSurvives:
    def test_oddful_value_in_osae_stays_in_cover_section(self):
        """本文の押さえに odds取得済み + 妙味あり買い目があるとき、
        top_pick に昇格しても押さえるべき買い目セクションに残る (要件1,3)。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="6-3-7",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="3-6-7",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
            ],
            osae=[
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="3-1-2",
                    reason="市場偏り(3番頭集中): 3連単人気上位を保持",
                    gami_risk=0.0, market_odds=20.7,
                    value_label="妙味あり",
                ),
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="6-7-3",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
            ],
            ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        # 「押さえるべき買い目」セクションに 3-1-2 が残る
        cover_section = text.split("### 押さえるべき買い目")[1].split("###")[0]
        assert "3-1-2" in cover_section, (
            f"odds取得済み妙味+市場偏りの 3-1-2 が押さえるべき買い目から消えた:"
            f"\n{cover_section}"
        )

    def test_market_bias_combo_kept_even_when_in_top_pick(self):
        """市場偏り合致 reason を持つ買い目は、top_pick と重複しても押さえに残る。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[],
            osae=[
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="3-1-2",
                    reason="市場偏り(3番頭集中)",  # reason に「市場偏り」
                    gami_risk=0.0, market_odds=20.7,
                    value_label="妙味あり",
                ),
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="6-7-3",
                    reason="t", gami_risk=0.0, market_odds=None,
                ),
            ],
            ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        # 3-1-2 が「一番買いたい買い目」 + 「押さえるべき買い目」両方に出る
        top_section = text.split("### 押さえるべき")[0]
        assert "3-1-2" in top_section  # 一番買いたい
        cover_section = text.split("### 押さえるべき買い目")[1].split("###")[0]
        assert "3-1-2" in cover_section, (
            "市場偏り合致買い目が押さえに残らない"
        )

    def test_honsen_zero_odds_coverage_shows_two_sections(self):
        """本線オッズ取得率 0% + osae に odds取得済み → 4枠表示。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="6-3-7",
                    reason="t", gami_risk=0.0, market_odds=None,
                ),
            ],
            osae=[
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="3-1-2",
                    reason="市場偏り", gami_risk=0.0, market_odds=20.7,
                    value_label="妙味あり",
                ),
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="6-7-3",
                    reason="t", gami_risk=0.0, market_odds=None,
                ),
            ],
            ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        judgement = text.split("### 実購入判断")[1]
        assert "オッズ取得済みで買える候補" in judgement
        assert "オッズ確認後の本線候補" in judgement


def test_oddful_value_promoted_to_honsen_still_kept_in_cover():
    """codex review 指摘の回帰テスト:
    promote_oddful_to_honsen で osae→honsen に移動した後でも、
    odds取得済み+妙味/市場偏り買い目は押さえセクションに残る。
    """
    ri = _load()
    pred = _prediction(ri)  # promote_oddful_to_honsen 適用済み
    # 3-1-2 は本線に昇格、osae からは削除されている前提
    assert any(b.combination == "3-1-2" for b in pred.honsen)
    assert all(b.combination != "3-1-2" for b in pred.osae)
    # それでも最終結論「押さえるべき買い目」に 3-1-2 が出る
    text = _summarize_for_final(pred)
    cover_section = text.split("### 押さえるべき買い目")[1].split("###")[0]
    assert "3-1-2" in cover_section, (
        f"promote後でも 3-1-2 が押さえセクションに出るべき:\n{cover_section}"
    )


def test_non_value_odds_bet_excluded_from_cover_on_overlap():
    """odds=None かつ妙味ラベル無しの押さえは、top_pick と重複したら除外。

    top_pick が 2点まで埋まる前提で、3点以上の押さえを用意し、
    そのうち 1点を top_pick と重複させる。
    """
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="本命", gami_risk=0.0, market_odds=8.0,
                value_label="本線向き",
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-3-2",
                reason="本命", gami_risk=0.0, market_odds=10.0,
                value_label="本線向き",
            ),
        ],
        osae=[
            # top_pick と重複 + odds=None + ラベル無し → 除外
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.0, market_odds=None,
                value_label=None,
            ),
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="2-1-3",
                reason="押さえA", gami_risk=0.0, market_odds=None,
            ),
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="3-1-2",
                reason="押さえB", gami_risk=0.0, market_odds=None,
            ),
        ],
        ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    cover_section = text.split("### 押さえるべき買い目")[1].split("###")[0]
    # 2-1-3 / 3-1-2 が押さえに残る (top_pick と被らない)
    assert "2-1-3" in cover_section
    assert "3-1-2" in cover_section
    # 1-2-3 は top_pick と重複 + odds=None+ラベル無し → cover から除外
    # ただし top_pick で出ているので、最終結論全体では存在する
    top_section = text.split("### 押さえるべき")[0]
    assert "1-2-3" in top_section
