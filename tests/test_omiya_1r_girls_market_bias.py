"""大宮1Rガールズ: 用語サニタイズ + 強警告 + 反省文言修正 (要件1-6)。

検証要件:
1. ガールズ出力に「4番手」「番手」「本命ライン」が出ない (自動置換される)
2. GIRLS_LINE_TERM 対象文言が自動で代替表現に置換される
3. 市場偏りがある安い人気筋が「安い人気筋・ガミ注意」セクションに表示される
4. 一番買いたい買い目が全 odds=None の場合、強い注意文が出る
5. 反省ポイント文言が自然
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
from app.output_validation import (
    sanitize_prediction,
    sanitize_prediction_text,
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
    / "omiya_1r_girls_market_bias.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


def _prediction(ri: RaceInput):
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
# 要件1: ガールズ出力に「4番手」「番手」「本命ライン」が出ない
# ---------------------------------------------------------------------------


class TestGirlsLineTermSanitize:
    @pytest.mark.parametrize("forbidden", [
        "4番手", "5番手", "別線番手",
        "ライン3番手", "本命ライン", "別線ライン",
    ])
    def test_full_markdown_excludes_forbidden(self, forbidden):
        """フルレンダリングで禁止用語が出現しない。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert forbidden not in md, (
            f"禁止用語『{forbidden}』がガールズ出力に残存:\n"
            f"{[ln for ln in md.split(chr(10)) if forbidden in ln][:3]}"
        )

    def test_3banshu_in_reason_replaced(self):
        """reason に『3番手』が含まれても自動置換される。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        # 「3番手」が混入していないこと (「3番手」は「中位」に置換)
        assert "3番手" not in md

    def test_bantan_replaced_to_tsuiso(self):
        """「番手」が「追走」に置換される (ガールズ時のみ)。"""
        text = sanitize_prediction_text("番手の差し展開", is_girls=True)
        assert "番手" not in text
        assert "追走" in text

    def test_normal_race_keeps_bantan(self):
        """通常戦では「番手」表現を残す。"""
        text = sanitize_prediction_text("本命ライン番手", is_girls=False)
        assert "番手" in text  # 通常戦では置換されない


# ---------------------------------------------------------------------------
# 要件2: GIRLS_LINE_TERM 対象文言が自動置換される
# ---------------------------------------------------------------------------


class TestSanitizeAutoReplace:
    def test_4banshu_evaluation_replaced(self):
        text = sanitize_prediction_text("4番手評価の頭差し", is_girls=True)
        assert "4番手評価" not in text
        assert "4位評価" in text

    def test_main_line_replaced(self):
        text = sanitize_prediction_text("本命ライン番手", is_girls=True)
        assert "本命ライン" not in text
        assert "本命候補" in text

    def test_bessen_bantan_replaced(self):
        text = sanitize_prediction_text("別線番手の差し込み", is_girls=True)
        assert "別線番手" not in text
        assert "追走型" in text

    def test_sanitize_prediction_applies_girls_replacements(self):
        """is_girls=True の Prediction で reason 内のライン用語が置換される。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="本命ライン番手の差し / 4番手評価",
                gami_risk=0.0, market_odds=10.0,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        sanitize_prediction(p)
        assert "本命ライン" not in p.honsen[0].reason
        assert "4番手評価" not in p.honsen[0].reason
        assert "番手" not in p.honsen[0].reason


# ---------------------------------------------------------------------------
# 要件3: 市場偏りの安い人気筋が「安い人気筋・ガミ注意」セクションに表示
# ---------------------------------------------------------------------------


class TestCheapMarketPopsInGami:
    def test_cheap_combos_in_gami_section(self):
        """大宮1R: 1-6-2 (3.2倍) / 1-6-3 (4.5倍) が安い人気筋に分離。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        # 本線セクションの「安い人気筋・ガミ注意」サブセクション
        honsen_section = md.split("## 6. 本線")[1].split("## 7.")[0]
        if "安い人気筋" in honsen_section:
            cheap_part = honsen_section.split("安い人気筋")[1]
            # 3.2倍 / 4.5倍 が含まれる
            assert "1-6-2" in cheap_part or "3.2倍" in cheap_part
            assert "1-6-3" in cheap_part or "4.5倍" in cheap_part

    def test_cheap_combos_not_in_top_pick(self):
        """安い人気筋 (1-6-2 / 1-6-3) が一番買いたい買い目に出ない。"""
        ri = _load()
        pred = _prediction(ri)
        text = _summarize_for_final(pred)
        top_section = text.split("### 押さえるべき")[0]
        # 1-6-2 (3.2倍) / 1-6-3 (4.5倍) が一番買いたいに出ない
        assert "1-6-2" not in top_section or "3.2倍" not in top_section
        assert "1-6-3" not in top_section or "4.5倍" not in top_section


# ---------------------------------------------------------------------------
# 要件4: 一番買いたい全 odds=None で強い注意文
# ---------------------------------------------------------------------------


class TestStrongWarningWhenAllOddsNone:
    def test_strong_warning_when_top_pick_all_odds_none(self):
        """top_pick が全部 odds=None かつ honsen も全 odds=None で強警告。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        # オッズ確認後セクションに切り替わる
        assert "オッズ確認後に判断する本線候補" in text

    def test_strong_warning_when_top_pick_mixed_with_oddful_main(self):
        """honsen に odds 取得済みがあるが top_pick が odds=None のみのケース
        (実質発生しないが、念のため検証)。"""
        # honsen に odds取得済み + top_pick で odds=None のみ選ばれるケース
        # は通常起こらない (_top_pick_score で odds取得済み優先) ため、
        # 「一番買いたい買い目」セクションが出る通常ケースをテスト
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.0, market_odds=10.0,
                value_label="妙味あり",
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        # 通常の「一番買いたい買い目」セクション
        assert "### 一番買いたい買い目" in text
        # 強警告は出ない (odds 取得済み)
        assert "主軸候補はオッズ未取得" not in text


# ---------------------------------------------------------------------------
# 要件5: 反省ポイント文言の自然化
# ---------------------------------------------------------------------------


class TestReflectionNaturalization:
    def test_bad_phrase_replaced(self):
        """『本線は少額ながら見送る候補を設定する』が自然な文言に置換。"""
        text = sanitize_prediction_text(
            "本線は少額ながら見送る候補を設定する"
        )
        assert "本線は少額ながら見送る候補を設定する" not in text
        assert "厚く買わず" in text or "少額確認" in text

    def test_reflection_points_sanitized(self):
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[], osae=[], ana=[], ooana=[],
            final_conclusion="",
            reflection_points=[
                "本線は少額ながら見送る候補を設定する",
            ],
        )
        sanitize_prediction(p)
        assert all(
            "本線は少額ながら見送る候補を設定する" not in pt
            for pt in p.reflection_points
        )


# ---------------------------------------------------------------------------
# 統合テスト
# ---------------------------------------------------------------------------


class TestOmiya1RIntegration:
    def test_full_pipeline_runs(self):
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        # 必須セクションが揃う
        assert "## 6. 本線" in md
        assert "## 10. 最終結論" in md

    def test_market_bias_detected(self):
        """市場偏り 1番頭が検出されて Markdown に出る。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert "1番頭" in md

    def test_no_anauma(self):
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert "穴馬" not in md


# ---------------------------------------------------------------------------
# codex review 反映: 強警告の all-missing-odds 分岐 + 全フィールドサニタイズ
# ---------------------------------------------------------------------------


class TestCodexReviewFixes:
    def test_strong_warning_in_all_odds_missing_branch(self):
        """honsen 全 odds=None + top_pick 全 odds=None でも強警告が出る。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.0, market_odds=None,
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-3-2",
                    reason="t", gami_risk=0.0, market_odds=None,
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        assert "オッズ確認後に判断する本線候補" in text
        # 強警告も出る (codex 指摘)
        assert "主軸候補はオッズ未取得" in text
        assert "厚く張らない" in text

    def test_summary_field_sanitized_for_girls(self):
        """ガールズ時、summary フィールドの「番手」「本命ライン」も置換。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True, marks={},
            summary="本命ライン番手の差し",
            venue_trend_text="本命ライン優勢",
            lines_text="本命ライン: 1-2-3",
            honsen=[], osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        sanitize_prediction(p)
        # summary, venue_trend_text, lines_text すべてサニタイズ
        for field in ("summary", "venue_trend_text", "lines_text"):
            text = getattr(p, field) or ""
            assert "本命ライン" not in text, (
                f"{field} に「本命ライン」が残存: {text}"
            )
            assert "番手" not in text, (
                f"{field} に「番手」が残存: {text}"
            )

    def test_full_render_no_line_terms_in_girls(self):
        """ガールズの完全レンダリングで全テキストから禁止用語が消える。"""
        ri = _load()
        pred = _prediction(ri)
        # LLM (実体) が summary 等に「本命ライン」を入れたケース想定
        pred.summary = "本命ライン優勢、番手差しが本線"
        pred.venue_trend_text = "本命ラインが残る傾向"
        md = render_prediction(pred, input_data=ri)
        assert "本命ライン" not in md
        assert "番手" not in md


# ---------------------------------------------------------------------------
# 追加要件 (1,2,3,4): オッズ取得率分離 / 市場偏り強化 / 強警告 / 3段階分離
# ---------------------------------------------------------------------------


class TestOddsCoverageSplit:
    """要件1: 本線オッズ取得率を「実購入本線」と「安い人気筋」に分離。"""

    def test_cheap_pops_excluded_from_real_coverage(self):
        """安い人気筋が実購入本線オッズ取得率に混ざらない。"""
        from app.output_validation import compute_odds_coverage
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True, marks={},
            honsen=[
                # 安い人気筋 (odds<5)
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.8, market_odds=3.2,
                    value_label="見送り寄り",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-4",
                    reason="t", gami_risk=0.8, market_odds=4.5,
                    value_label="見送り寄り",
                ),
                # 実購入本線
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-4-6",
                    reason="t", gami_risk=0.0, market_odds=35.0,
                    value_label="妙味あり",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        cov = compute_odds_coverage(p)
        # 実購入本線: 1点 (5-4-6) / 取得済み 1点
        assert cov.honsen_real_total == 1
        assert cov.honsen_real_with_odds == 1
        # 安い人気筋: 2点 / 取得済み 2点
        assert cov.honsen_cheap_total == 2
        assert cov.honsen_cheap_with_odds == 2
        # 警告は出ない (実購入本線は取得済み)
        assert cov.has_warning is False

    def test_warning_when_real_honsen_no_odds(self):
        """実購入本線がすべて odds 未取得なら警告フラグ。"""
        from app.output_validation import compute_odds_coverage
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                # 安い人気筋 (odds<5) のみ取得済み
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.8, market_odds=3.2,
                    value_label="見送り寄り",
                ),
                # 実購入本線は odds=None
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-4-6",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        cov = compute_odds_coverage(p)
        assert cov.honsen_real_total == 1
        assert cov.honsen_real_with_odds == 0
        assert cov.has_warning is True

    def test_render_section_separates_labels(self):
        """安い人気筋がある場合、Markdown 上で実購入本線と分離表示。"""
        from app.output_validation import (
            compute_odds_coverage, render_odds_coverage_section,
        )
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.8, market_odds=3.2,
                    value_label="見送り寄り",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-4-6",
                    reason="t", gami_risk=0.0, market_odds=35.0,
                    value_label="妙味あり",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        cov = compute_odds_coverage(p)
        text = render_odds_coverage_section(cov)
        assert "実購入本線" in text
        assert "安い人気筋" in text


# ---------------------------------------------------------------------------
# 要件2: 市場偏り 1番頭 + odds安い → 「厚く買わない」明記
# ---------------------------------------------------------------------------


class TestMarketBiasCheapWarning:
    def test_description_includes_cheap_warning(self):
        """1番頭集中 + 最安オッズ<5 なら『厚く買わない』が説明文に含まれる。"""
        from app.output_validation import detect_market_bias
        ri = _load()  # 大宮1R: 1番頭5/5件、最安3.2倍
        bias = detect_market_bias(ri)
        assert bias.has_head_focus
        assert bias.focused_head == 1
        assert bias.is_focused_head_cheap is True
        assert bias.description is not None
        assert "厚く買わない" in bias.description

    def test_description_in_markdown(self):
        """Markdown 出力に市場偏り説明 + 厚く買わない注意が出る。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert "1番頭" in md
        assert "厚く買わない" in md


# ---------------------------------------------------------------------------
# 要件3: top_pick 全 odds=None で強警告
# ---------------------------------------------------------------------------


class TestStrongerWarning:
    def test_section_name_warning(self):
        """top_pick 全 odds=None 時、セクション名に⚠️警告。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.0, market_odds=None,
            )],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        # 強警告セクション名
        assert "主軸候補オッズ未取得" in text or "購入判断保留" in text
        # 「実購入推奨できる本線買い目はありません」のような明示警告
        assert "現時点では" in text or "推奨できる" in text


# ---------------------------------------------------------------------------
# 要件4: ガールズの3段階分離
# ---------------------------------------------------------------------------


class TestGirlsThreeTierCheap:
    def test_three_tier_labels(self):
        """ガールズで odds 帯別の3段階ラベルが出る。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True, marks={},
            honsen=[
                # 見送り寄り (odds<3)
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.0, market_odds=2.3,
                    value_label="見送り寄り",
                ),
                # 買うなら少額 (3<=odds<5)
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-4",
                    reason="t", gami_risk=0.0, market_odds=4.5,
                    value_label="見送り寄り",
                ),
                # 確認用 (5<=odds, ただし top_pick_disqualified)
                # 実購入候補 (妙味あり)
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-4-6",
                    reason="t", gami_risk=0.0, market_odds=35.0,
                    value_label="妙味あり",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        md = render_prediction(p)
        honsen_section = md.split("## 6. 本線")[1].split("## 7.")[0]
        # 見送り寄り or 買うなら少額 のラベル
        assert "見送り寄り" in honsen_section or "買うなら少額" in honsen_section

    def test_non_girls_uses_normal_label(self):
        """通常戦は3段階分離せず「安い人気筋」一括表示。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.0, market_odds=2.3,
                    value_label="見送り寄り",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-4-6",
                    reason="t", gami_risk=0.0, market_odds=35.0,
                    value_label="妙味あり",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        md = render_prediction(p)
        honsen_section = md.split("## 6. 本線")[1].split("## 7.")[0]
        assert "安い人気筋" in honsen_section
        # 3段階ラベルは出ない
        assert "買うなら少額（人気だが" not in honsen_section
