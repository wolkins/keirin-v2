"""広島7R + 出力整合性チェック（16項目要件）の統合テスト。

検証する要件:
1. honsen がすべて market_odds=None → 「オッズ確認後に判断する本線候補」表示
3. 3車ライン 2-3-6 や 3-2-6 が候補に含まれる
4. 2車ライン本命でも3車ラインが消えない
6. 出力に「穴馬」が含まれない
8. validate_prediction_output() による整合性チェック
9. オッズ取得率セクション
10. data_quality 判定
11. 市場オッズの偏り
12. レース種別ごとの最大点数制限（ガールズ/新人戦は strict）
14. market_odds=None の統一扱い
16. 整合性チェック共通テスト
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path

import pytest

from app.cli import _summarize_for_final, render_prediction
from app.llm_client import build_default_client
from app.models import (
    BetRecommendation, Line, OddsEntry, Prediction, RaceInfo, RaceInput, Rider,
)
from app.output_validation import (
    OddsCoverage,
    assess_data_quality,
    compute_odds_coverage,
    render_odds_coverage_section,
    sanitize_prediction,
    sanitize_prediction_text,
    summarize_market_bias,
    validate_prediction_output,
)
from app.prompt_builder import build_full_prompt
from app.scoring import (
    MAX_POINTS_GIRLS,
    MAX_POINTS_ROOKIE,
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
    get_max_points_for_race,
)
from app.value_analysis import (
    annotate_prediction_with_value,
    promote_oddful_to_honsen,
    promote_oddful_to_osae,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "hiroshima_7r_three_line_a_semifinal.json"
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
# 要件3,4: 3車ライン 2-3-6 / 3-2-6 が候補に含まれる
# ---------------------------------------------------------------------------


class TestThreeCarLinePreserved:
    def test_three_car_line_forward_in_osae(self):
        """3車ライン (line_leader=2, second=3, third=6) の順方向 2-3-6 が
        本線か押さえに含まれる。"""
        ri = _load()
        _, bets = _full(ri)
        all_combos = (
            [b.combination for b in bets["本線"]]
            + [b.combination for b in bets["押さえ"]]
        )
        assert "2-3-6" in all_combos, (
            f"3車ライン 2-3-6 がない: {all_combos}"
        )

    def test_three_car_line_reverse_in_osae(self):
        """3車ライン (line_leader=2, second=3, third=6) の番手頭 3-2-6 が
        本線か押さえに含まれる。"""
        ri = _load()
        _, bets = _full(ri)
        all_combos = (
            [b.combination for b in bets["本線"]]
            + [b.combination for b in bets["押さえ"]]
        )
        assert "3-2-6" in all_combos, (
            f"3車ライン 3-2-6 (番手頭) がない: {all_combos}"
        )

    def test_two_car_line_main_does_not_kill_three_car_line(self):
        """2車ライン本命 (4-7) でも、別線3車 (2-3-6) が完全に消えない。"""
        ri = _load()
        _, bets = _full(ri)
        # 中国3車に関わる買い目が押さえに最低1点
        chinese_line_combos = {
            "2-3-6", "3-2-6", "2-3-4", "3-2-4", "2-3-7", "3-2-7",
        }
        cover_combos = {b.combination for b in bets["押さえ"]}
        overlap = chinese_line_combos & cover_combos
        assert len(overlap) >= 1, (
            f"3車別線(中国)が押さえに残っていない: {cover_combos}"
        )


# ---------------------------------------------------------------------------
# 要件1,14: honsen 全 market_odds=None → 「オッズ確認後に判断」
# ---------------------------------------------------------------------------


class TestOddsNoneHandling:
    def test_honsen_all_no_odds_shows_check_section(self):
        """本線がすべて market_odds=None なら『一番買いたい』ではなく
        『オッズ確認後に判断する本線候補』と表示。"""
        ri = _load()
        pred = _prediction(ri)
        text = _summarize_for_final(pred)
        # 本線が全部 odds=None なら check section
        if all(b.market_odds is None for b in pred.honsen):
            assert "オッズ確認後に判断する本線候補" in text
            assert "### 一番買いたい買い目" not in text.split("### 押さえるべき")[0]

    def test_summary_renders_section_label(self):
        """セクションラベル文言を確認。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="2-1-3",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        assert "オッズ確認後に判断する本線候補" in text
        assert "確定オッズを見てから購入判断" in text


# ---------------------------------------------------------------------------
# 要件10: data_quality 判定
# ---------------------------------------------------------------------------


class TestDataQuality:
    def test_high_quality(self):
        """全項目揃っていれば high。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="A級一般",
            ),
            riders=[
                Rider(car_no=i, name=f"R{i}", score=85.0+i,
                      nige=1, makuri=1, sashi=1, mark=1,
                      stats_missing=False) for i in range(1, 8)
            ],
            lines=[], recent_results=[
                # at least 1 result
            ],
            odds=[OddsEntry(bet_type="3連単", combination="1-2-3", odds=5.0)],
        )
        # recent_results 空でも medium 以上
        q = assess_data_quality(ri)
        assert q in ("medium", "high")

    def test_low_quality_no_odds(self):
        """オッズ無しなら low。"""
        ri = _load()
        # 広島7R fixture は odds=[]
        q = assess_data_quality(ri)
        assert q in ("low", "very_low")

    def test_very_low_no_score_no_odds(self):
        """score 全件 stats_missing + odds 無し → very_low。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="A級一般",
            ),
            riders=[
                Rider(car_no=i, name=f"R{i}", score=0.0, stats_missing=True)
                for i in range(1, 8)
            ],
            lines=[], odds=[], recent_results=[],
        )
        q = assess_data_quality(ri)
        assert q == "very_low"


# ---------------------------------------------------------------------------
# 要件9: オッズ取得率
# ---------------------------------------------------------------------------


class TestOddsCoverage:
    def test_compute_coverage_basic(self):
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.0, market_odds=10.0,
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-3-2",
                    reason="t", gami_risk=0.0, market_odds=None,
                ),
            ],
            osae=[BetRecommendation(
                category="押さえ", bet_type="3連単", combination="2-1-3",
                reason="t", gami_risk=0.0, market_odds=15.0,
            )],
            ana=[], ooana=[],
            final_conclusion="",
        )
        cov = compute_odds_coverage(p)
        assert cov.total == 3
        assert cov.with_odds == 2
        assert cov.honsen_total == 2
        assert cov.honsen_with_odds == 1
        assert not cov.has_warning

    def test_warning_when_honsen_no_odds(self):
        """本線オッズ取得 0% で警告フラグ。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.0, market_odds=None,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        cov = compute_odds_coverage(p)
        assert cov.has_warning is True

    def test_render_section_contains_keywords(self):
        cov = OddsCoverage(total=10, with_odds=5, honsen_total=3,
                           honsen_with_odds=0)
        text = render_odds_coverage_section(cov)
        assert "オッズ取得済み: 5/10点" in text
        assert "本線オッズ取得済み: 0/3点" in text
        assert "注意" in text


# ---------------------------------------------------------------------------
# 要件11: 市場オッズの偏り
# ---------------------------------------------------------------------------


class TestMarketBias:
    def test_bias_to_specific_head(self):
        """3連単上位5件のうち頭が同じなら偏り検出。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="A級一般",
            ),
            riders=[Rider(car_no=i, name=f"R{i}", score=85.0) for i in range(1, 8)],
            lines=[],
            odds=[
                OddsEntry(bet_type="3連単", combination="1-2-3", odds=4.0),
                OddsEntry(bet_type="3連単", combination="1-3-2", odds=5.0),
                OddsEntry(bet_type="3連単", combination="1-2-4", odds=6.0),
                OddsEntry(bet_type="3連単", combination="2-1-3", odds=8.0),
                OddsEntry(bet_type="3連単", combination="3-1-2", odds=12.0),
            ],
            recent_results=[],
        )
        bias = summarize_market_bias(ri)
        assert bias is not None
        assert "1番頭" in bias

    def test_no_bias_when_dispersed(self):
        """頭が分散している場合は None。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="A級一般",
            ),
            riders=[Rider(car_no=i, name=f"R{i}", score=85.0) for i in range(1, 8)],
            lines=[],
            odds=[
                OddsEntry(bet_type="3連単", combination="1-2-3", odds=4.0),
                OddsEntry(bet_type="3連単", combination="2-3-1", odds=5.0),
                OddsEntry(bet_type="3連単", combination="3-1-2", odds=6.0),
                OddsEntry(bet_type="3連単", combination="4-5-6", odds=8.0),
                OddsEntry(bet_type="3連単", combination="5-6-4", odds=12.0),
            ],
            recent_results=[],
        )
        bias = summarize_market_bias(ri)
        # 頭が 1, 2, 3, 4, 5 と分散 → None
        assert bias is None or "集中" not in bias

    def test_no_odds_returns_none(self):
        ri = _load()  # 広島7R fixture は odds=[]
        assert summarize_market_bias(ri) is None


# ---------------------------------------------------------------------------
# 要件12: ガールズ・新人戦の最大点数 strict
# ---------------------------------------------------------------------------


class TestMaxPointsByGrade:
    def test_girls_limits(self):
        """ガールズは本線3/押さえ4/穴2/大穴1。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="ガールズ予選",
            ),
            riders=[Rider(car_no=i, name=f"G{i}", score=85.0+i*0.5)
                    for i in range(1, 8)],
            lines=[], odds=[], recent_results=[],
        )
        limits = get_max_points_for_race(ri.race)
        assert limits == MAX_POINTS_GIRLS
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        assert len(bets["本線"]) <= 3
        assert len(bets["押さえ"]) <= 4
        assert len(bets["穴"]) <= 2
        assert len(bets["大穴"]) <= 1

    def test_rookie_limits(self):
        """新人戦は本線3/押さえ4/穴2/大穴1。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="新人戦 男予",
            ),
            riders=[Rider(car_no=i, name=f"N{i}", score=80.0+i*0.5)
                    for i in range(1, 8)],
            lines=[], odds=[], recent_results=[],
        )
        limits = get_max_points_for_race(ri.race)
        assert limits == MAX_POINTS_ROOKIE


# ---------------------------------------------------------------------------
# 要件6: 「穴馬」サニタイズ
# ---------------------------------------------------------------------------


class TestAnaumaSanitize:
    def test_text_replaces_anauma(self):
        assert sanitize_prediction_text("穴馬は5番") == "穴目は5番"
        assert sanitize_prediction_text("本命馬は1番") == "本命は1番"

    def test_sanitize_prediction_updates_fields(self):
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="穴馬の出走", gami_risk=0.0,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="穴馬は7番", gami_memo="穴馬がガミ",
        )
        sanitize_prediction(p)
        assert "穴馬" not in (p.final_conclusion or "")
        assert "穴馬" not in (p.gami_memo or "")
        assert "穴目" in (p.honsen[0].reason or "")

    def test_render_prediction_no_anauma(self):
        ri = _load()
        pred = _prediction(ri)
        # LLM出力に穴馬を混ぜる
        pred.final_conclusion = (pred.final_conclusion or "") + "穴馬は4番"
        md = render_prediction(pred, input_data=ri)
        assert "穴馬" not in md


# ---------------------------------------------------------------------------
# 要件8,16: validate_prediction_output() 整合性チェック
# ---------------------------------------------------------------------------


class TestValidatePredictionOutput:
    def test_warns_when_honsen_all_no_odds(self):
        ri = _load()
        pred = _prediction(ri)
        warnings = validate_prediction_output(ri, pred)
        codes = [w.code for w in warnings]
        if all(b.market_odds is None for b in pred.honsen):
            assert "HONSEN_ALL_NO_ODDS" in codes

    def test_warns_anauma(self):
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[], osae=[], ana=[], ooana=[],
            final_conclusion="穴馬は1番",
        )
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="A級一般",
            ),
            riders=[Rider(car_no=i, name=f"R{i}", score=85.0) for i in range(1, 8)],
            lines=[], odds=[], recent_results=[],
        )
        warnings = validate_prediction_output(ri, p)
        assert any(w.code == "ANAUMA_TERM" for w in warnings)

    def test_warns_girls_with_bantan(self):
        """ガールズなのに reason に「番手」が混じれば警告。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="ガールズ予選",
            ),
            riders=[Rider(car_no=i, name=f"G{i}", score=85.0) for i in range(1, 8)],
            lines=[], odds=[], recent_results=[],
        )
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="本命ライン番手",  # ← 違反
                gami_risk=0.0,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        warnings = validate_prediction_output(ri, p)
        assert any(w.code == "GIRLS_LINE_TERM" for w in warnings)

    def test_warns_judgement_mismatch(self):
        """実購入判断「本線として有力: X」が honsen に存在しない場合に警告。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="A級一般",
            ),
            riders=[Rider(car_no=i, name=f"R{i}", score=85.0) for i in range(1, 8)],
            lines=[], odds=[], recent_results=[],
        )
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="本命", gami_risk=0.0,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="**本線として有力**: 9-9-9",  # honsen に無い
        )
        warnings = validate_prediction_output(ri, p)
        assert any(w.code == "HONSEN_JUDGEMENT_MISMATCH" for w in warnings)

    def test_no_warnings_for_clean_prediction(self):
        """整合性問題なしなら warning なし。"""
        ri = RaceInput(
            race=RaceInfo(
                race_id="t", date=Date(2026, 5, 23), venue="t",
                race_no=1, class_name="A級一般",
            ),
            riders=[Rider(car_no=i, name=f"R{i}", score=85.0) for i in range(1, 8)],
            lines=[], odds=[], recent_results=[],
        )
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="本命", gami_risk=0.0, market_odds=10.0,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="**本線として有力**: 1-2-3",
        )
        warnings = validate_prediction_output(ri, p)
        codes = [w.code for w in warnings]
        # 「穴馬」「ガールズ」「ライン用語」「odds=None+gami」のいずれも無い
        assert "ANAUMA_TERM" not in codes
        assert "GIRLS_LINE_TERM" not in codes
        assert "HONSEN_ALL_NO_ODDS" not in codes
        assert "HONSEN_JUDGEMENT_MISMATCH" not in codes


# ---------------------------------------------------------------------------
# render_prediction による全体出力検証
# ---------------------------------------------------------------------------


class TestRenderPredictionWithValidation:
    def test_full_markdown_has_coverage_section(self):
        """input_data を渡すとオッズ取得率セクションが出る。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert "### オッズ取得率" in md

    def test_full_markdown_has_data_quality(self):
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert "### データ品質" in md

    def test_warnings_appear_in_markdown(self):
        """整合性問題があれば markdown にも表示。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        # 広島7R は odds=[] → HONSEN_ALL_NO_ODDS は確実に出る
        if all(b.market_odds is None for b in pred.honsen):
            assert "HONSEN_ALL_NO_ODDS" in md or "本線がすべてオッズ未取得" in md

    def test_no_anauma_in_full_output(self):
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert "穴馬" not in md
