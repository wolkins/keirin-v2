"""静岡4R: 出力整合性チェックの強化 (修正方針1-3 + 静岡4R 1, 3, 5)。

検証項目:
- 修正方針1,2 / 静岡4R-1: final_conclusion に未登録 combo があると
  CONCLUSION_COMBO_UNREGISTERED 警告 + テンプレートフォールバック
- 修正方針3: ◎ が honsen の1-2着に出ない場合 HONMEI_NOT_IN_HONSEN_TOP2 警告
- 静岡4R-3: 「穴として少額」を best_bets に入れない
- 修正方針6: gami_warning から market_odds=None を除外
"""

from __future__ import annotations

import re

import pytest

from app.cli import render_prediction
from app.final_selection import build_final_selection
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_validation import validate_prediction_output


def _bet(combo, *, market_odds=None, value_label="", gami_risk=0.0,
         category="本線", reason="test"):
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=reason, gami_risk=gami_risk,
        market_odds=market_odds, value_label=value_label,
    )


def _pred(*, honsen=None, osae=None, ana=None, ooana=None,
          marks=None, final_conclusion="", is_girls=False):
    return Prediction(
        race_id="test", venue="静岡", race_no=4, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="", lines_text="",
        marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion=final_conclusion,
        gami_memo="", reflection_points=[],
    )


def _input(*, class_name="A級一般", lines=None):
    return RaceInput.model_validate({
        "race": {
            "race_id": "test", "date": "2026-05-24",
            "venue": "静岡", "race_no": 4,
            "class_name": class_name, "start_time": "11:30",
        },
        "weather": {
            "condition": "晴れ", "rain_mm_per_hour": 0.0,
            "wind_speed_mps": 2.0,
        },
        "lines": lines or [
            {"line_name": "本命", "cars": [1, 2, 3]},
            {"line_name": "別線", "cars": [4, 5, 6]},
            {"line_name": "単", "cars": [7]},
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 85.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# 修正方針1: CONCLUSION_COMBO_UNREGISTERED 警告
# ---------------------------------------------------------------------------


class TestConclusionUnregistered:
    def test_unregistered_combo_in_conclusion_triggers_error(self):
        """final_conclusion に honsen/osae/ana/ooana に無い 4-3-6 が出ると
        CONCLUSION_COMBO_UNREGISTERED (error) が出る。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
            final_conclusion=(
                "本線は 4-3-6, 3-4-6, 4-6-3 を中心に据える。"  # 全て未登録
            ),
        )
        warnings = validate_prediction_output(_input(), pred)
        codes = [w.code for w in warnings]
        assert "CONCLUSION_COMBO_UNREGISTERED" in codes, (
            f"未登録 combo 警告が出るべき: {codes}"
        )
        # severity が error
        target = next(
            w for w in warnings if w.code == "CONCLUSION_COMBO_UNREGISTERED"
        )
        assert target.severity == "error"

    def test_registered_combo_in_conclusion_no_warning(self):
        """final_conclusion の combo が全部 honsen に存在すれば警告無し。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
            final_conclusion="本線は 1-2-3 を中心に据える。",
        )
        warnings = validate_prediction_output(_input(), pred)
        codes = [w.code for w in warnings]
        assert "CONCLUSION_COMBO_UNREGISTERED" not in codes


# ---------------------------------------------------------------------------
# 修正方針2: テンプレート再生成フォールバック (render_prediction)
# ---------------------------------------------------------------------------


class TestConclusionTemplateFallback:
    def test_render_falls_back_to_template_when_unregistered(self):
        """render_prediction が未登録 combo を検出したら、final_conclusion を
        テンプレート再生成に切り替える。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
            ],
            final_conclusion=(
                "スコア最上位は1番。本線は 4-3-6, 3-4-6 を中心に据える。"
                " 配当狙いとして 5-2-4 を少額で残す。"
            ),
        )
        ri = _input()
        out = render_prediction(pred, input_data=ri)
        # テンプレートフォールバックメッセージが出る
        assert "整合性フォールバック" in out, (
            f"未登録 combo 検出でテンプレート再生成すべき:\n"
            f"{out.split('## 10.')[1].split('##')[0] if '## 10.' in out else out}"
        )

    def test_no_fallback_when_conclusion_valid(self):
        """全 combo が登録済みならフォールバックしない (LLM 出力を尊重)。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
            ],
            final_conclusion="本線は 1-2-3, 2-1-3 を中心に据える。",
        )
        out = render_prediction(pred, input_data=_input())
        assert "整合性フォールバック" not in out


# ---------------------------------------------------------------------------
# 修正方針3: HONMEI_NOT_IN_HONSEN_TOP2 警告
# ---------------------------------------------------------------------------


class TestHonmeiNotInTopTwo:
    def test_honmei_missing_from_honsen_top2_triggers_warning(self):
        """◎=1 だが honsen の全買い目が 4-X-Y (1着が1番でも2着が1番でもない)
        の場合に警告。"""
        pred = _pred(
            honsen=[
                _bet("4-5-6", market_odds=10.0, value_label="妙味あり"),
                _bet("5-4-6", market_odds=12.0, value_label="妙味あり"),
            ],
            marks={"◎": 1, "◯": 4, "▲": 5},
        )
        warnings = validate_prediction_output(_input(), pred)
        codes = [w.code for w in warnings]
        assert "HONMEI_NOT_IN_HONSEN_TOP2" in codes, (
            f"◎の警告が出るべき: {codes}"
        )

    def test_honmei_in_honsen_top2_no_warning(self):
        """◎ が 1着 or 2着 に出ていれば警告無し。"""
        pred = _pred(
            honsen=[
                _bet("1-4-5", market_odds=10.0, value_label="妙味あり"),
            ],
            marks={"◎": 1, "◯": 4},
        )
        warnings = validate_prediction_output(_input(), pred)
        codes = [w.code for w in warnings]
        assert "HONMEI_NOT_IN_HONSEN_TOP2" not in codes


# ---------------------------------------------------------------------------
# 静岡4R-3: 「穴として少額」を best_bets に入れない
# ---------------------------------------------------------------------------


class TestAnaToShogakuExcluded:
    def test_value_ana_to_shogaku_not_in_best_bets(self):
        """value_label="穴として少額" は best_bets/must_cover_bets に入らない。
        小額穴は small_longshots だけで扱う。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
            osae=[
                _bet("5-2-4", market_odds=126.0, value_label="穴として少額",
                     category="押さえ"),
            ],
            ana=[
                _bet("5-2-4", market_odds=126.0, value_label="穴として少額",
                     category="穴"),
            ],
        )
        sel = build_final_selection(pred, _input())
        # 5-2-4 (穴として少額) は best/must_cover には入らない
        best_combos = {b.combination for b in sel.best_bets}
        must_combos = {b.combination for b in sel.must_cover_bets}
        assert "5-2-4" not in best_combos
        assert "5-2-4" not in must_combos
        # small_longshots には入ってよい
        longshot_combos = {b.combination for b in sel.small_longshots}
        assert "5-2-4" in longshot_combos


# ---------------------------------------------------------------------------
# 修正方針6: market_odds=None は gami_warning に入らない
# ---------------------------------------------------------------------------


class TestNoneOddsNotInGamiWarning:
    def test_none_odds_not_in_cheap_popular(self):
        """odds=None は cheap_popular_bets (= gami_warning 相当) に入らない。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None, value_label=""),
                _bet("2-1-3", market_odds=4.0, value_label="本線向き"),
            ],
        )
        sel = build_final_selection(pred, _input())
        cheap_combos = {b.combination for b in sel.cheap_popular_bets}
        assert "1-2-3" not in cheap_combos  # odds=None
        assert "2-1-3" in cheap_combos      # odds=4<5


# ---------------------------------------------------------------------------
# codex review 反映: フォールバック後の再検出回避 + small_longshots 拡張
# ---------------------------------------------------------------------------


class TestCodexReviewFixes:
    def test_fallback_template_does_not_retrigger_warning(self):
        """テンプレート再生成後、出力された Markdown 内の整合性チェック
        セクションに CONCLUSION_COMBO_UNREGISTERED が再発しない。

        フォールバック理由文に未登録 combo を埋め込むと validate が再検出する
        バグを回帰させない。
        """
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
            final_conclusion="本線は 4-3-6 を中心に据える。",
        )
        ri = _input()
        out = render_prediction(pred, input_data=ri)
        # 出力に整合性チェックセクションが含まれる場合、
        # CONCLUSION_COMBO_UNREGISTERED が出ない (フォールバックで解消済み)
        if "### 出力整合性チェック" in out:
            check_section = out.split("### 出力整合性チェック")[1].split("---")[0]
            assert "CONCLUSION_COMBO_UNREGISTERED" not in check_section, (
                f"テンプレート再生成後も整合性警告が再発: {check_section}"
            )
        # 整合性チェックセクションが出ない = 警告ゼロ (OK)

    def test_fallback_uses_compute_top_pick_when_final_sel_none(self):
        """input_data=None で final_sel が None のフォールバック時、
        _compute_top_pick で best_list を埋める (「該当なし」に落ちない)。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
            ],
            final_conclusion="本線は 4-3-6 を中心に据える。",
        )
        # input_data=None で render
        out = render_prediction(pred, input_data=None)
        conclusion_block = out.split("## 10. 最終結論")[1].split("##")[0]
        # 「該当なし」に落ちず、_compute_top_pick の結果 (1-2-3 や 2-1-3) が入る
        assert "1-2-3" in conclusion_block or "2-1-3" in conclusion_block, (
            f"input_data=None でも _compute_top_pick で fallback すべき:\n"
            f"{conclusion_block}"
        )

    def test_ana_to_shogaku_in_honsen_still_shows_in_small_longshots(self):
        """honsen 側の「穴として少額」が small_longshots でも拾える。

        _qualifies_best で除外された後、small_longshots が拾わないと表示から
        完全に消えるバグを回帰させない。
        """
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
                _bet("5-2-4", market_odds=126.0, value_label="穴として少額"),
            ],
        )
        sel = build_final_selection(pred, _input())
        # best/must_cover からは除外
        all_buyable = {b.combination for b in (sel.best_bets + sel.must_cover_bets)}
        assert "5-2-4" not in all_buyable
        # small_longshots には入る (codex review 反映)
        longshot_combos = {b.combination for b in sel.small_longshots}
        assert "5-2-4" in longshot_combos, (
            f"honsen 側の穴として少額も small_longshots で拾うべき: "
            f"{longshot_combos}"
        )
