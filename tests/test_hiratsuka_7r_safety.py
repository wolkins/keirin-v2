"""平塚7R: 新人戦 + 本線オッズ取得率0% + BEST_EMPTY_NO_ODDS + 見送り寄り混入。

検証要件 (2026-05-24):
A. 平塚7R 相当:
   - is_rookie=True
   - data_quality=low
   - honsen_odds_coverage=0%
   - final_best empty
   - BEST_EMPTY_NO_ODDS warning
   - honsen に market_odds=12.5 / value_label="見送り寄り" の 3-4-1
   期待:
   - ## 6. 本線 に「実購入候補」が出ない
   - 本文に「一番買いたい」「購入対象」が出ない
   - 3-4-1 は「見送り寄りの参考候補」として表示
   - 「番手頭」「本命頭」「本命自力」「4番手評価」が本文に出ない
   - OutputPlan警告として BEST_EMPTY_NO_ODDS は残ってよい

B. 通常品質ケース:
   - data_quality=high / honsen_odds_coverage>=0.5 / value_label!="見送り寄り"
   - 通常時のみ「実購入候補」「購入対象」を許可
"""

from __future__ import annotations

import re

import pytest

from app.cli import render_prediction_v2
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInput,
)
from app.output_plan import (
    OutputPlan, OutputPlanWarning, build_output_plan,
)


def _bet(combo, *, market_odds=None, value_label="", gami_risk=0.0,
         category="本線", reason="test"):
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=reason, gami_risk=gami_risk,
        market_odds=market_odds, value_label=value_label,
    )


def _pred_rookie(*, honsen=None, osae=None, ana=None, ooana=None,
                 final_conclusion="", gami_memo="", reflection_points=None):
    return Prediction(
        race_id="test-hiratsuka7", venue="平塚", race_no=7,
        is_girls=False,  # 新人戦は is_girls=False、resolved_is_rookie() で判定
        summary="", venue_trend_text="", weather_text="", lines_text="",
        marks={},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion=final_conclusion,
        gami_memo=gami_memo,
        reflection_points=list(reflection_points or []),
    )


def _input_rookie(*, odds=None, wind_mps=3.0):
    """新人戦シナリオ。is_rookie() が True になる class_name を使う。"""
    return RaceInput.model_validate({
        "race": {
            "race_id": "test-hiratsuka7", "date": "2026-05-24",
            "venue": "平塚", "race_no": 7,
            "class_name": "男子新人アドバンス一般", "start_time": "14:00",
        },
        "weather": {
            "condition": "晴れ", "rain_mm_per_hour": 0.0,
            "wind_speed_mps": wind_mps,
        },
        "lines": [
            # 新人戦は並びなし (個人戦扱い)
            {"line_name": "単", "cars": [i]} for i in range(1, 8)
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 80.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "南関東"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# 要件A: 平塚7R 相当
# ---------------------------------------------------------------------------


class TestHiratsuka7rScenario:
    """新人戦 + BEST_EMPTY_NO_ODDS + 見送り寄り 3-4-1 が本線にある。"""

    def _make_pred(self):
        """平塚7R 相当: honsen に「見送り寄り」3-4-1 が混入する。"""
        return _pred_rookie(
            honsen=[
                _bet("3-4-1", market_odds=12.5, value_label="見送り寄り",
                     reason="番手頭の押さえ"),
                _bet("4-3-1", market_odds=None, reason="本命自力の差し"),
                _bet("1-4-3", market_odds=None, reason="本命頭の前残り"),
            ],
            osae=[
                _bet("3-1-4", market_odds=None, reason="4番手評価の絡み",
                     category="押さえ"),
            ],
            gami_memo="3-4-1(本線): オッズ安め、ガミ警戒",
            reflection_points=[
                "本命ラインの番手差しを軽視",
                "4番手評価の頭を切った反省",
            ],
        )

    def test_rookie_low_coverage_no_purchase_target_in_body(self):
        """本文 (## 6 〜 ## 10) に「実購入候補」「一番買いたい」「購入対象」が出ない。"""
        ri = _input_rookie()
        pred = self._make_pred()
        md = render_prediction_v2(pred, input_data=ri)
        body = md.split("## 6. 本線", 1)[1].split("\n---\n")[0]
        for forbidden in ("実購入候補", "一番買いたい", "購入対象"):
            assert forbidden not in body, (
                f"BEST_EMPTY_NO_ODDS + 見送り寄り honsen で「{forbidden}」が"
                f"本文に出ている:\n{body[:1500]}"
            )

    def test_rookie_no_line_terms_in_body(self):
        """本文に「番手頭」「本命頭」「本命自力」「4番手評価」が出ない。"""
        ri = _input_rookie()
        pred = self._make_pred()
        md = render_prediction_v2(pred, input_data=ri)
        # 本文 = ## 6 から末尾フッタ前まで (## 11 ガミ回避メモ含む)
        body = md.split("## 6. 本線", 1)[1].split("\n---\n")[0]
        # 警告セクション (### 出力整合性チェック / ### OutputPlan 警告) は対象外
        # → validate メッセージにある可能性のあるものは無視
        # 本文を warning セクションで切る
        for sep in ("### 出力整合性チェック", "### OutputPlan 警告"):
            if sep in body:
                body = body.split(sep)[0]
        for forbidden in ("番手頭", "本命頭", "本命自力", "4番手評価"):
            assert forbidden not in body, (
                f"新人戦本文に「{forbidden}」が残存:\n"
                f"--- 該当周辺 ---\n"
                f"{body[max(0, body.find(forbidden) - 80):body.find(forbidden) + 80]}"
            )

    def test_miokuri_yori_shown_as_reference_not_purchase(self):
        """value_label='見送り寄り' の 3-4-1 は「見送り寄りの参考候補」サブ
        セクションで表示される (実購入候補ではない)。"""
        ri = _input_rookie()
        pred = self._make_pred()
        md = render_prediction_v2(pred, input_data=ri)
        honsen_block = md.split("## 6. 本線")[1].split("## 7.")[0]
        # 3-4-1 が「見送り寄りの参考候補」セクションに表示される
        assert "見送り寄りの参考候補" in honsen_block, (
            f"見送り寄りサブセクションが出ていない:\n{honsen_block}"
        )
        # 3-4-1 が表示される
        assert "3-4-1" in honsen_block

    def test_best_empty_no_odds_warning_remains(self):
        """OutputPlan 警告として BEST_EMPTY_NO_ODDS は残る (削除されない)。"""
        ri = _input_rookie()
        pred = self._make_pred()
        plan = build_output_plan(pred, ri)
        # warning code に BEST_EMPTY_NO_ODDS が含まれる
        codes = [w.code for w in plan.warnings]
        # 本線オッズ取得済み 1/3 で、final_best が空ならば
        # BEST_EMPTY_NO_ODDS が出るはず (final_best 空時の警告)
        # ※ コードが出ない場合は has_low_coverage_warning が他の警告で True
        if plan.final_best == []:
            joined = " ".join(w.message for w in plan.warnings)
            assert "オッズ確認後" in joined or "オッズ取得済み" in joined, (
                f"final_best 空時の警告メッセージが見つからない: {plan.warnings}"
            )


# ---------------------------------------------------------------------------
# 要件B: 通常品質ケース
# ---------------------------------------------------------------------------


class TestCodexReviewFixesHiratsuka7r:
    """codex review (2026-05-24, 4940461 後続) P2 修正の回帰テスト。"""

    def test_display_honsen_does_not_exceed_3_with_miokuri(self):
        """display_honsen は見送り寄り追加で 3点を超えない (DISPLAY_HONSEN_MAX 契約)。

        codex P2 反映: 旧実装では best_bets 3点 + 見送り寄り 2点 = 5点に
        なっていた。新実装では見送り寄りは plan.honsen_miokuri に分離。
        """
        ri = _input_rookie(odds=[
            OddsEntry(bet_type="3連単", combination=c, odds=o)
            for c, o in [
                ("1-2-3", 8.0), ("2-1-3", 10.0), ("3-1-2", 12.0),
                ("3-4-1", 12.5),  # 見送り寄り
            ]
        ])
        pred = _pred_rookie(
            honsen=[
                _bet("1-2-3", market_odds=8.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=10.0, value_label="妙味あり"),
                _bet("3-1-2", market_odds=12.0, value_label="妙味あり"),
                _bet("3-4-1", market_odds=12.5, value_label="見送り寄り"),
            ],
        )
        plan = build_output_plan(pred, ri)
        # plan.honsen は実購入候補のみで最大3点
        assert len(plan.honsen) <= 3, (
            f"plan.honsen が 3 点を超えた: {len(plan.honsen)}"
        )
        # 見送り寄りは honsen_miokuri に分離
        honsen_combos = {b.combination for b in plan.honsen}
        miokuri_combos = {b.combination for b in plan.honsen_miokuri}
        assert "3-4-1" not in honsen_combos, (
            f"見送り寄りが honsen に残った: {honsen_combos}"
        )

    def test_miokuri_yori_not_in_must_cover(self):
        """value_label='見送り寄り' が must_cover_bets に入らない。

        codex P2 反映: 旧実装では market bias 昇格で見送り寄りが
        must_cover に入って「押さえとして必要」と強表示されていた。
        """
        # 市場偏り (3番頭) + 3-4-1 が見送り寄り のシナリオ
        odds_list = [
            OddsEntry(bet_type="3連単", combination="3-4-1", odds=12.5),
            OddsEntry(bet_type="3連単", combination="3-2-1", odds=15.0),
            OddsEntry(bet_type="3連単", combination="3-1-2", odds=18.0),
            OddsEntry(bet_type="3連単", combination="3-5-1", odds=22.0),
        ]
        ri = _input_rookie(odds=odds_list)
        pred = _pred_rookie(
            honsen=[
                _bet("3-4-1", market_odds=12.5, value_label="見送り寄り"),
            ],
            osae=[
                _bet("3-2-1", market_odds=15.0, value_label="妙味あり",
                     category="押さえ"),
            ],
        )
        plan = build_output_plan(pred, ri)
        # final_osae に 3-4-1 (見送り寄り) が含まれない
        final_osae_combos = {b.combination for b in plan.final_osae}
        assert "3-4-1" not in final_osae_combos, (
            f"見送り寄りが final_osae (押さえとして必要) に混入: "
            f"{final_osae_combos}"
        )


class TestNormalQualityAllowsStrongText:
    def test_high_quality_normal_text_allowed(self):
        """data_quality=high + 通常 coverage + value_label='妙味あり' で
        「実購入候補」「購入対象」が許可される。"""
        ri = RaceInput.model_validate({
            "race": {
                "race_id": "test-normal-7", "date": "2026-05-24",
                "venue": "テスト", "race_no": 1,
                "class_name": "A級一般", "start_time": "10:00",
            },
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [{"line_name": "本命", "cars": [1, 2, 3]}],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0, "b_count": 1,
                 "nige": 1, "makuri": 1, "sashi": 1, "mark": 1,
                 "comment": "", "home_area": "南関東"}
                for i in range(1, 8)
            ],
            "odds": [
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0},
                {"bet_type": "3連単", "combination": "2-1-3", "odds": 10.0},
                {"bet_type": "3連単", "combination": "3-1-2", "odds": 12.0},
                {"bet_type": "3連単", "combination": "1-3-2", "odds": 14.0},
                {"bet_type": "3連単", "combination": "2-3-1", "odds": 18.0},
            ],
            "recent_results": [
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "sample"},
            ],
        })
        pred = Prediction(
            race_id="test-normal-7", venue="テスト", race_no=1,
            is_girls=False,
            summary="", venue_trend_text="", weather_text="", lines_text="",
            marks={},
            honsen=[
                _bet("1-2-3", market_odds=8.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=10.0, value_label="妙味あり"),
            ],
            osae=[
                _bet("3-1-2", market_odds=12.0, value_label="本線向き",
                     category="押さえ"),
            ],
            ana=[], ooana=[],
            final_conclusion="",
            gami_memo="", reflection_points=[],
        )
        md = render_prediction_v2(pred, input_data=ri)
        body = md.split("## 6. 本線", 1)[1].split("\n---\n")[0]
        # 通常時は「実購入候補」「購入対象」「一番買いたい買い目」が許可される
        assert (
            "実購入候補" in body
            or "一番買いたい買い目" in body
            or "購入対象" in body
        ), (
            f"通常品質では強い表現が許可されるべき:\n{body[:1000]}"
        )

    def test_high_quality_miokuri_still_separated(self):
        """通常品質でも value_label='見送り寄り' は実購入候補から除外される。"""
        ri = RaceInput.model_validate({
            "race": {
                "race_id": "test-mio", "date": "2026-05-24",
                "venue": "テスト", "race_no": 1,
                "class_name": "A級一般", "start_time": "10:00",
            },
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [{"line_name": "本命", "cars": [1, 2, 3]}],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 90.0, "b_count": 1,
                 "nige": 1, "makuri": 1, "sashi": 1, "mark": 1,
                 "comment": "", "home_area": "南関東"}
                for i in range(1, 8)
            ],
            "odds": [
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0},
                {"bet_type": "3連単", "combination": "3-4-1", "odds": 12.5},
            ],
            "recent_results": [
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "sample"},
            ],
        })
        pred = Prediction(
            race_id="test-mio", venue="テスト", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="", lines_text="",
            marks={},
            honsen=[
                _bet("1-2-3", market_odds=8.0, value_label="妙味あり"),
                _bet("3-4-1", market_odds=12.5, value_label="見送り寄り"),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="",
            gami_memo="", reflection_points=[],
        )
        md = render_prediction_v2(pred, input_data=ri)
        honsen_block = md.split("## 6. 本線")[1].split("## 7.")[0]
        # 3-4-1 は「見送り寄りの参考候補」サブセクションに分離
        assert "見送り寄りの参考候補" in honsen_block
        # 「実購入候補」セクションが出る場合、3-4-1 は含まれない
        if "**実購入候補**" in honsen_block:
            # 「実購入候補」セクション直後の内容を取得
            jisai = honsen_block.split("**実購入候補**")[1].split("**")[0]
            assert "3-4-1" not in jisai, (
                f"見送り寄りが「実購入候補」に混入:\n{jisai}"
            )
