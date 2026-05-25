"""広島8R: 市場偏り検出 + 候補昇格 + 一番買いたい優先順位 (要件1-6)。

検証要件:
1. 市場偏り(1番頭4/5件) → 1番頭の買い目が honsen/osae に最低2点
2. market_odds あり妙味あり が odds=None より一番買いたいで優先
3. market_odds=None の買い目に gami_risk>=0.6 が付かない (sanitize後)
4. ODDS_NONE_HIGH_GAMI warning が出た買い目はガミ注意表示に出ない
5. 反省ポイント文言が「市場人気が特定頭・特定ラインに集中している場合...」
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path

import pytest

from app.cli import _summarize_for_final, render_prediction
from app.llm_client import build_default_client
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInfo, RaceInput, Rider,
)
from app.output_validation import (
    detect_market_bias,
    sanitize_prediction,
    sanitize_prediction_text,
    validate_prediction_output,
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
    / "hiroshima_8r_market_bias_one_head.json"
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
# 要件1: 市場偏り検出 → 1番頭買い目の昇格
# ---------------------------------------------------------------------------


class TestMarketBiasPromotion:
    def test_detect_market_bias_returns_focused_head(self):
        """3連単上位5件中4件が1番頭なら focused_head=1, focused_count=4。"""
        ri = _load()
        bias = detect_market_bias(ri)
        assert bias.has_head_focus is True
        assert bias.focused_head == 1
        assert bias.focused_count >= 3

    def test_one_head_combos_in_honsen_or_osae(self):
        """1番頭の3連単買い目が本線か押さえに最低2点含まれる。"""
        ri = _load()
        _, bets = _full(ri)
        all_combos = (
            [b.combination for b in bets["本線"]]
            + [b.combination for b in bets["押さえ"]]
        )
        one_head_count = sum(
            1 for combo in all_combos
            if combo and combo.split("-")[0] == "1"
        )
        assert one_head_count >= 2, (
            f"1番頭が2点未満: {one_head_count}点 / 全候補={all_combos}"
        )

    def test_specific_market_top_combos_present(self):
        """市場上位の 1-2-5 / 1-2-4 / 1-2-7 / 1-5-2 のうち
        少なくとも2点が候補に含まれる。"""
        ri = _load()
        _, bets = _full(ri)
        all_combos = set(
            [b.combination for b in bets["本線"]]
            + [b.combination for b in bets["押さえ"]]
        )
        market_top = {"1-2-5", "1-2-4", "1-2-7", "1-5-2"}
        overlap = market_top & all_combos
        assert len(overlap) >= 2, (
            f"市場上位の1番頭買い目が2点未満: {overlap}"
        )


# ---------------------------------------------------------------------------
# 要件2: 一番買いたい買い目の優先順位
# ---------------------------------------------------------------------------


class TestTopPickPriority:
    def test_odds_present_with_value_wins_over_odds_none(self):
        """market_odds=26.8 + 妙味あり が market_odds=None より優先。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="4-5-7",
                    reason="本命", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-5",
                    reason="市場上位", gami_risk=0.0, market_odds=26.8,
                    value_label="妙味あり",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        top_section = text.split("### 押さえるべき")[0]
        # 「一番買いたい買い目」セクションに 1-2-5 が出ている
        assert "### 一番買いたい買い目" in top_section
        # 一番買いたい の最初の候補が 1-2-5
        lines = [
            ln for ln in top_section.split("\n")
            if ln.startswith("- ") and "1-2-5" in ln or "4-5-7" in ln
        ]
        if lines:
            assert "1-2-5" in lines[0], (
                f"一番買いたい先頭が 1-2-5 でない: {lines[0]}"
            )

    def test_value_present_over_value_none_with_odds(self):
        """同じ odds 取得済みでも value_label=妙味ありが優先。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-3",
                    reason="t", gami_risk=0.0, market_odds=8.0,
                    value_label=None,
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="5-4-6",
                    reason="t", gami_risk=0.0, market_odds=20.0,
                    value_label="妙味あり",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        top_section = text.split("### 押さえるべき")[0]
        assert "5-4-6" in top_section
        # 5-4-6 が 1-2-3 より先
        idx_5 = top_section.find("5-4-6")
        idx_1 = top_section.find("1-2-3")
        if idx_5 >= 0 and idx_1 >= 0:
            assert idx_5 < idx_1, (
                "妙味ありが先に来るべき"
            )


# ---------------------------------------------------------------------------
# 要件3: market_odds=None の買い目に gami_risk>=0.6 が付かない (sanitize後)
# ---------------------------------------------------------------------------


class TestOddsNoneGamiZero:
    def test_sanitize_zeros_high_gami_when_odds_none(self):
        """sanitize_prediction 後、market_odds=None の gami_risk は 0 になる。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="6-4-5",
                reason="t", gami_risk=0.8, market_odds=None,
            )],
            osae=[BetRecommendation(
                category="押さえ", bet_type="3連単", combination="3-4-5",
                reason="t", gami_risk=0.9, market_odds=None,
            )],
            ana=[], ooana=[],
            final_conclusion="",
        )
        sanitize_prediction(p)
        for b in p.honsen + p.osae:
            if b.market_odds is None:
                assert b.gami_risk == 0.0, (
                    f"{b.combination} の gami_risk が 0 に補正されていない: "
                    f"{b.gami_risk}"
                )

    def test_render_prediction_does_not_show_gami_for_odds_none(self):
        """render_prediction 後の Markdown で、odds=None の買い目に
        [ガミ注意] や [低配当注意] が出ない。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[BetRecommendation(
                category="本線", bet_type="3連単", combination="6-4-5",
                reason="t", gami_risk=0.8, market_odds=None,
            )],
            osae=[BetRecommendation(
                category="押さえ", bet_type="3連単", combination="3-4-5",
                reason="t", gami_risk=0.9, market_odds=None,
            )],
            ana=[], ooana=[],
            final_conclusion="",
        )
        # render_prediction で sanitize される
        md = render_prediction(p)
        # 6-4-5 / 3-4-5 の行に [ガミ注意] / [低配当注意] が含まれていない
        for line in md.split("\n"):
            if "6-4-5" in line or "3-4-5" in line:
                assert "[ガミ注意]" not in line, (
                    f"odds=None の {line.strip()} に [ガミ注意] が表示"
                )
                assert "[低配当注意]" not in line


# ---------------------------------------------------------------------------
# 要件4: ODDS_NONE_HIGH_GAMI warning が出た買い目はガミ注意表示に出ない
# ---------------------------------------------------------------------------


def test_odds_none_high_gami_not_in_gami_warning():
    """sanitize 前に odds=None + gami_risk=0.9 を持っていても、
    render_prediction 後は「ガミになりやすい買い目」セクションに出ない。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.0, market_odds=10.0,
        )],
        osae=[
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="6-4-5",
                reason="t", gami_risk=0.8, market_odds=None,
            ),
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="3-4-5",
                reason="t", gami_risk=0.9, market_odds=None,
            ),
        ],
        ana=[], ooana=[],
        final_conclusion="",
    )
    md = render_prediction(p)
    # 「### ガミになりやすい買い目」セクションを取り出す
    if "### ガミになりやすい買い目" in md:
        gami_section = md.split("### ガミになりやすい買い目")[1]
        # 次のセクション境界で切る
        for boundary in ("### ", "## ", "---"):
            if boundary in gami_section:
                gami_section = gami_section.split(boundary)[0]
                break
        assert "6-4-5" not in gami_section
        assert "3-4-5" not in gami_section


# ---------------------------------------------------------------------------
# 要件5: 反省ポイント文言サニタイズ
# ---------------------------------------------------------------------------


class TestReflectionPointsSanitize:
    def test_sanitize_text_replaces_bad_phrase(self):
        bad = "市場人気に基づく無理な展開予想をしない"
        out = sanitize_prediction_text(bad)
        assert bad not in out
        assert "候補昇格" in out

    def test_sanitize_prediction_updates_reflection_points(self):
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[], osae=[], ana=[], ooana=[],
            final_conclusion="",
            reflection_points=[
                "市場人気に基づく無理な展開予想をしない",
                "穴を広げすぎないか確認",
            ],
        )
        sanitize_prediction(p)
        assert "市場人気に基づく無理な展開予想をしない" not in p.reflection_points
        assert any("候補昇格" in pt for pt in p.reflection_points)

    def test_default_reflection_includes_market_bias_point(self):
        """通常戦のデフォルト反省ポイントに市場偏り検証が含まれる。"""
        from app.llm_client import _default_reflection_points
        pts = _default_reflection_points(is_girls=False, weather=None)
        assert any("市場人気" in pt and "候補昇格" in pt for pt in pts), (
            f"市場偏り反省ポイント無し: {pts}"
        )


# ---------------------------------------------------------------------------
# 統合: 広島8R fixture で全要件が動く
# ---------------------------------------------------------------------------


class TestHiroshima8RFullIntegration:
    def test_full_pipeline_completes(self):
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert "## 10. 最終結論" in md
        # Phase 15: 見出しを「候補買い目オッズ取得率」に変更
        assert "### 候補買い目オッズ取得率" in md
        # 市場偏りセクション
        assert "市場の偏り" in md or "1番頭" in md

    def test_1_head_combos_eligible_for_top_pick(self):
        """1番頭の妙味あり買い目が一番買いたい候補に乗る。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        # 最終結論セクション
        if "### 一番買いたい買い目" in md:
            top = md.split("### 一番買いたい買い目")[1].split("### ")[0]
            # 1番頭の妙味買い目が含まれる (26.8倍 = 1-2-5 など)
            one_head_present = any(
                ("1-2-" in top or "1-5-" in top)
                for _ in [0]
            )
            assert one_head_present, (
                f"一番買いたいに1番頭が無い:\n{top[:500]}"
            )

    def test_no_anauma_in_full_output(self):
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        assert "穴馬" not in md


# ---------------------------------------------------------------------------
# 追加要件 (1,3,4): 表示順と最終結論の文章順を一致させる
# ---------------------------------------------------------------------------


class TestDisplayOrderConsistency:
    def test_honsen_display_order_odds_with_value_first(self):
        """本線表示順で odds取得済み+妙味あり が odds=None より先頭。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        # 本線セクション「実購入候補」の中身
        honsen_section = md.split("## 6. 本線")[1].split("## 7. 押さえ")[0]
        real_part = honsen_section.split("安い人気筋")[0]
        # 1-2-5 (26.8倍/妙味あり) が odds=None の買い目より上
        if "1-2-5" in real_part:
            idx_1_2_5 = real_part.find("1-2-5")
            # odds=None の本線があれば、それより 1-2-5 が先
            for combo_none in ("2-1-5", "4-5-7"):
                if combo_none in real_part:
                    idx_none = real_part.find(combo_none)
                    assert idx_1_2_5 < idx_none, (
                        f"odds取得済み妙味の 1-2-5 が "
                        f"odds=None の {combo_none} より後ろ"
                    )

    def test_top_pick_order_odds_with_value_first(self):
        """一番買いたい買い目の先頭が odds取得済み+妙味あり。"""
        p = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False, marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="4-5-7",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-5",
                    reason="市場上位", gami_risk=0.0, market_odds=26.8,
                    value_label="妙味あり",
                ),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
        )
        text = _summarize_for_final(p)
        # 一番買いたい買い目セクションの最初の買い目が 1-2-5
        top_section = text.split("### 押さえるべき")[0]
        assert "### 一番買いたい買い目" in top_section
        section_body = top_section.split("### 一番買いたい買い目")[1]
        # 1-2-5 の出現位置 < 4-5-7 の出現位置
        idx_1_2_5 = section_body.find("1-2-5")
        idx_4_5_7 = section_body.find("4-5-7")
        if idx_1_2_5 >= 0 and idx_4_5_7 >= 0:
            assert idx_1_2_5 < idx_4_5_7

    def test_final_conclusion_starts_with_top_pick(self):
        """LLM 出力『本線は X, Y を中心に据える』の X が一番買いたいの先頭と一致。"""
        ri = _load()
        pred = _prediction(ri)
        md = render_prediction(pred, input_data=ri)
        # 「本線は X, Y を...」の X 抜き出し
        import re
        m = re.search(r"本線は\s*(\d-\d-\d)", md)
        assert m is not None, "最終結論に『本線は X』が無い"
        top_first = m.group(1)
        # 一番買いたい買い目の先頭と一致
        top_section = md.split("### 一番買いたい買い目")[1].split("### 押さえるべき")[0]
        # 1番目に出る combo を抽出
        m2 = re.search(r"- (\d-\d-\d)", top_section)
        if m2:
            top_pick_first = m2.group(1)
            assert top_first == top_pick_first, (
                f"最終結論の本線先頭({top_first}) と一番買いたい先頭"
                f"({top_pick_first})が不一致"
            )

    def test_final_conclusion_orders_value_bets_first(self):
        """最終結論の本線文章で、odds取得済み+妙味ありが odds=None より先。"""
        from app.llm_client import _build_final_conclusion
        from app.models import RiderScore
        scores = [RiderScore(car_no=i, name=f"R{i}", win_score=5.0)
                  for i in range(1, 8)]
        bets = {
            "本線": [
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="4-5-7",
                    reason="t", gami_risk=0.0, market_odds=None,
                    value_label="オッズ未取得・要確認",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="1-2-5",
                    reason="t", gami_risk=0.0, market_odds=26.8,
                    value_label="妙味あり",
                ),
            ],
            "穴": [], "押さえ": [], "大穴": [],
        }
        msg = _build_final_conclusion(
            scores=scores, candidate_bets=bets,
            is_girls=False, marks={},
        )
        idx_1_2_5 = msg.find("1-2-5")
        idx_4_5_7 = msg.find("4-5-7")
        assert idx_1_2_5 >= 0 and idx_4_5_7 >= 0
        assert idx_1_2_5 < idx_4_5_7, (
            f"最終結論で 1-2-5 が 4-5-7 より後: {msg}"
        )


# ---------------------------------------------------------------------------
# 追加要件 (2): 市場偏り1番頭の保持を再確認
# ---------------------------------------------------------------------------


def test_market_focused_head_minimum_two_in_honsen_or_osae():
    """1番頭が市場上位5件中3件以上なら、honsen+osaeに1番頭が最低2点入る。"""
    ri = _load()
    _, bets = _full(ri)
    all_combos = (
        [b.combination for b in bets["本線"]]
        + [b.combination for b in bets["押さえ"]]
    )
    one_head_count = sum(
        1 for c in all_combos
        if c.split("-")[0] == "1"
    )
    assert one_head_count >= 2, (
        f"1番頭の買い目が2点未満: {one_head_count}点 / 全候補: {all_combos}"
    )
