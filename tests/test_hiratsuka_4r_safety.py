"""平塚4R: 新人戦 + 強風 + 低カバレッジ + HeadBias 過剰昇格防止。

検証要件 (2026-05-24):
1. 新人戦 → Markdown 全体に「番手」「本命ライン」「別線番手」「ライン3番手」
   「4番手」が出ない (サニタイズ網羅)
2. 同一 combination が honsen と ana に重複しない (validator 重複除外)
3. data_quality=low / honsen_odds_coverage<0.5 → 「購入対象」「一番買いたい」
   が出ない (文言弱体化)
4. HeadBias(5番頭3/5) のみで final_best/final_osae に 5-1-* が 1点まで
5. AxisBias(5-1) ありなら 5-1-* 複数昇格を許可
"""

from __future__ import annotations

import re

import pytest

from app.cli import render_prediction_v2
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInput,
)
from app.output_plan import (
    OutputPlan, build_output_plan, validate_output_plan,
)
from app.output_validation import detect_market_bias


def _bet(combo, *, market_odds=None, value_label="", gami_risk=0.0,
         category="本線", reason="test"):
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=reason, gami_risk=gami_risk,
        market_odds=market_odds, value_label=value_label,
    )


def _pred(*, honsen=None, osae=None, ana=None, ooana=None,
          final_conclusion="", is_girls=False, marks=None,
          gami_memo="", reflection_points=None):
    return Prediction(
        race_id="test-hiratsuka4", venue="平塚", race_no=4, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="", lines_text="",
        marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion=final_conclusion,
        gami_memo=gami_memo,
        reflection_points=list(reflection_points or []),
    )


def _input(*, class_name="男子新人アドバンス一般", wind_mps=5.3,
           odds=None, riders=None, lines=None):
    return RaceInput.model_validate({
        "race": {
            "race_id": "test-hiratsuka4", "date": "2026-05-24",
            "venue": "平塚", "race_no": 4,
            "class_name": class_name, "start_time": "11:00",
        },
        "weather": {
            "condition": "晴れ", "rain_mm_per_hour": 0.0,
            "wind_speed_mps": wind_mps,
        },
        "lines": lines or [
            {"line_name": "別線1", "cars": [5, 1]},
            {"line_name": "別線2", "cars": [4, 7]},
            {"line_name": "単", "cars": [2]},
            {"line_name": "単", "cars": [3]},
            {"line_name": "単", "cars": [6]},
        ],
        "riders": riders or [
            {"car_no": i, "name": f"R{i}", "score": 80.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "南関東"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# 要件1: 新人戦サニタイズ網羅
# ---------------------------------------------------------------------------


class TestRookieSanitizationCoverage:
    FORBIDDEN_TERMS = ("本命ライン", "別線番手", "ライン3番手")
    FORBIDDEN_BARE = ("番手", "4番手")  # 番手単独 / 4番手は新人戦禁止

    def _assert_no_rookie_terms_in_markdown(self, md: str):
        for term in self.FORBIDDEN_TERMS:
            assert term not in md, (
                f"新人戦 Markdown 全体に「{term}」が残存:\n"
                f"該当周辺: {md[max(0, md.find(term) - 80):md.find(term) + 80]}"
            )
        # 「番手」単独 (= 直前に数字を伴わない) も禁止
        bare_bantan = re.findall(r"(?<!\d)番手(?!頭)", md)
        assert not bare_bantan, (
            f"新人戦 Markdown に「番手」単独が {len(bare_bantan)} 件残存"
        )
        # 「4番手」も禁止 (「4位評価」「4位」に置換されるべき)
        assert "4番手" not in md, "新人戦 Markdown に「4番手」が残存"

    def test_rookie_markdown_no_line_terms_in_full_output(self):
        """新人戦の Markdown 全体 (## 11 / ## 12 含む) に line 用語が出ない。"""
        ri = _input()
        pred = _pred(
            honsen=[
                _bet("5-1-4", market_odds=12.0, value_label="妙味あり",
                     reason="本命ライン番手差し"),
            ],
            osae=[
                _bet("1-5-4", market_odds=18.0, value_label="本線向き",
                     reason="別線番手の絡み", category="押さえ"),
            ],
            ana=[
                _bet("4-5-1", market_odds=60.0, value_label="妙味あり",
                     reason="ライン3番手の伸び", category="穴"),
            ],
            gami_memo="本命ラインに寄せすぎた反省、別線番手の浮上を待つべき",
            reflection_points=[
                "4番手評価の頭差しを軽視",
                "別線番手の2着上がりを軽視",
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        self._assert_no_rookie_terms_in_markdown(md)


# ---------------------------------------------------------------------------
# 要件2: 同一 combination の重複禁止
# ---------------------------------------------------------------------------


class TestNoDuplicateCombo:
    def test_same_combo_in_honsen_and_ana_is_deduplicated(self):
        """5-1-4 が honsen と ana 両方にある場合、ana から除外される。"""
        plan = OutputPlan(
            honsen=[_bet("5-1-4", market_odds=12.0, value_label="妙味あり")],
            osae=[_bet("1-5-4", market_odds=18.0, category="押さえ")],
            ana=[
                _bet("5-1-4", market_odds=60.0, value_label="妙味あり",
                     category="穴"),  # 重複!
                _bet("4-5-1", market_odds=80.0, value_label="妙味あり",
                     category="穴"),
            ],
        )
        warnings = validate_output_plan(plan)
        codes = [w.code for w in warnings]
        assert "DISPLAY_DUPLICATE_REMOVED" in codes, (
            f"重複除外の警告が出るべき: {codes}"
        )
        # 5-1-4 は honsen のみに残る
        honsen_combos = {b.combination for b in plan.honsen}
        ana_combos = {b.combination for b in plan.ana}
        assert "5-1-4" in honsen_combos
        assert "5-1-4" not in ana_combos, (
            f"ana から 5-1-4 が除外されるべき: {ana_combos}"
        )
        # 4-5-1 は ana に残る (重複でないため)
        assert "4-5-1" in ana_combos

    def test_final_ana_does_not_rewind_dedup(self):
        """codex review 反映: 重複除外が final_ana 補充で巻き戻らない。

        honsen に 5-1-4 + final_ana にも 5-1-4 → 重複除外で ana から
        5-1-4 削除後、validator 後段の final_ana 補充が ana に再追加して
        重複が復活するバグの回帰テスト。
        """
        plan = OutputPlan(
            honsen=[_bet("5-1-4", market_odds=12.0, value_label="妙味あり")],
            ana=[
                _bet("5-1-4", market_odds=60.0, value_label="妙味あり",
                     category="穴"),  # 重複!
            ],
            # final_ana にも同じ combo → 巻き戻りの危険
            final_ana=[
                _bet("5-1-4", market_odds=60.0, value_label="妙味あり"),
            ],
        )
        validate_output_plan(plan)
        ana_combos = {b.combination for b in plan.ana}
        assert "5-1-4" not in ana_combos, (
            f"final_ana 補充で重複が復活した: {ana_combos}\n"
            f"5-1-4 は honsen に残り、ana には出ないはず"
        )
        # honsen には残る
        assert "5-1-4" in {b.combination for b in plan.honsen}

    def test_priority_honsen_over_osae_over_ana(self):
        """優先順位: honsen > osae > ana > ooana。"""
        plan = OutputPlan(
            honsen=[_bet("1-2-3")],
            osae=[
                _bet("1-2-3"),  # honsen と重複 → 除外
                _bet("4-5-6"),
            ],
            ana=[
                _bet("4-5-6"),  # osae と重複 → 除外
                _bet("7-8-9"),
            ],
            ooana=[
                _bet("7-8-9"),  # ana と重複 → 除外
                _bet("9-8-7"),
            ],
        )
        validate_output_plan(plan)
        assert [b.combination for b in plan.honsen] == ["1-2-3"]
        assert [b.combination for b in plan.osae] == ["4-5-6"]
        assert [b.combination for b in plan.ana] == ["7-8-9"]
        assert [b.combination for b in plan.ooana] == ["9-8-7"]


# ---------------------------------------------------------------------------
# 要件3: data_quality=low / honsen_odds_coverage<0.5 で文言弱体化
# ---------------------------------------------------------------------------


class TestLowDataQualityWeakensText:
    def test_data_quality_low_does_not_say_purchase_target(self):
        """data_quality=low の Markdown に「購入対象」「一番買いたい」が出ない。"""
        # odds 無し → assess_data_quality=low
        ri = _input(odds=[])
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
        )
        md = render_prediction_v2(pred, input_data=ri)
        # 結論部 / 実購入判断 を対象
        body = md.split("## 6. 本線", 1)[1] if "## 6. 本線" in md else md
        if "\n---\n" in body:
            body = body.rsplit("\n---\n", 1)[0]
        assert "購入対象" not in body, (
            f"data_quality=low で「購入対象」が出ている:\n"
            f"{body[max(0, body.find('購入対象') - 80):body.find('購入対象') + 80] if '購入対象' in body else ''}"
        )
        assert "一番買いたい買い目" not in body, (
            f"data_quality=low で「一番買いたい買い目」が出ている"
        )
        # 「暫定候補」「再確認後」「購入見送り推奨」のいずれかが出る
        assert (
            "暫定候補" in body or "再確認後" in body or "購入見送り推奨" in body
        )

    def test_honsen_coverage_under_50pct_weakens_text(self):
        """honsen_odds_coverage<0.5 でも文言弱体化。"""
        # honsen 4点中 1点だけ odds 取得済み (25%)
        ri = _input(odds=[
            OddsEntry(bet_type="3連単", combination="1-2-3", odds=10.0),
        ])
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=None),
                _bet("3-1-2", market_odds=None),
                _bet("3-2-1", market_odds=None),
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        body = md.split("## 6. 本線", 1)[1] if "## 6. 本線" in md else md
        if "\n---\n" in body:
            body = body.rsplit("\n---\n", 1)[0]
        # 「一番買いたい買い目」「中心に据える」「購入対象」が出ない
        assert "購入対象" not in body, (
            f"honsen_coverage<50% で「購入対象」が出ている"
        )


# ---------------------------------------------------------------------------
# 要件4: HeadBias のみで final_best/final_osae 同一軸最大1点
# ---------------------------------------------------------------------------


class TestHeadBiasOnlyLimitsAxis:
    def _odds_head_only_5(self):
        """HeadBias (5番頭 3/5件) のみ、AxisBias なし。"""
        return [
            OddsEntry(bet_type="3連単", combination="5-1-4", odds=8.0),
            OddsEntry(bet_type="3連単", combination="5-1-7", odds=10.0),
            OddsEntry(bet_type="3連単", combination="5-4-1", odds=15.0),
            OddsEntry(bet_type="3連単", combination="1-5-4", odds=22.0),
            OddsEntry(bet_type="3連単", combination="4-5-1", odds=30.0),
        ]

    def test_5_head_only_bias_does_not_concentrate_axis(self):
        """5番頭3件 + (5,1)2件 → AxisBias 未満 → final に 5-1-* は最大1点。"""
        ri = _input(odds=self._odds_head_only_5())
        bias = detect_market_bias(ri)
        assert bias.has_head_focus is True
        assert bias.focused_head == 5
        # (5,1) は 2件のみ → AxisBias 未満
        assert bias.has_axis_focus is False, (
            f"AxisBias 未満想定だが has_axis_focus=True: "
            f"focused_axis={bias.focused_axis}"
        )
        # build_output_plan して final_best/final_osae の 5-1-* を数える
        pred = _pred(
            honsen=[
                _bet("5-1-4", market_odds=8.0, value_label="妙味あり"),
                _bet("5-1-7", market_odds=10.0, value_label="妙味あり"),
                _bet("5-4-1", market_odds=15.0, value_label="本線向き"),
            ],
            osae=[
                _bet("1-5-4", market_odds=22.0, value_label="妙味あり",
                     category="押さえ"),
            ],
        )
        plan = build_output_plan(pred, ri)
        # final_best + final_osae の (5, X) 軸の重複数を数える
        axis_counts: dict = {}
        for b in (plan.final_best + plan.final_osae):
            if not b.combination or "-" not in b.combination:
                continue
            parts = b.combination.split("-")
            if len(parts) < 2:
                continue
            try:
                ax = (int(parts[0]), int(parts[1]))
            except ValueError:
                continue
            axis_counts[ax] = axis_counts.get(ax, 0) + 1
        # 同一 (5, 1) 軸が 2 点以上含まれない
        assert axis_counts.get((5, 1), 0) <= 1, (
            f"HeadBias のみ (AxisBias 無し) なのに (5,1) 軸が "
            f"{axis_counts.get((5, 1), 0)} 点。1点に制限されるべき。"
            f"\nfinal_best={[b.combination for b in plan.final_best]}"
            f"\nfinal_osae={[b.combination for b in plan.final_osae]}"
        )


# ---------------------------------------------------------------------------
# 要件5: AxisBias(5-1) ありで複数昇格許可
# ---------------------------------------------------------------------------


class TestAxisBiasAllowsMultiplePromotion:
    def _odds_axis_5_1(self):
        """AxisBias (5-1 軸) 3件以上。"""
        return [
            OddsEntry(bet_type="3連単", combination="5-1-4", odds=8.0),
            OddsEntry(bet_type="3連単", combination="5-1-7", odds=10.0),
            OddsEntry(bet_type="3連単", combination="5-1-2", odds=15.0),
            OddsEntry(bet_type="3連単", combination="1-5-4", odds=22.0),
            OddsEntry(bet_type="3連単", combination="4-5-1", odds=30.0),
        ]

    def test_axis_bias_detected_and_allows_concentration(self):
        ri = _input(odds=self._odds_axis_5_1())
        bias = detect_market_bias(ri)
        assert bias.has_axis_focus is True
        assert bias.focused_axis == (5, 1)
        # AxisBias 一致軸では 5-1-* が複数残っても許可される
        pred = _pred(
            honsen=[
                _bet("5-1-4", market_odds=8.0, value_label="妙味あり"),
                _bet("5-1-7", market_odds=10.0, value_label="妙味あり"),
            ],
            osae=[
                _bet("5-1-2", market_odds=15.0, value_label="本線向き",
                     category="押さえ"),
            ],
        )
        plan = build_output_plan(pred, ri)
        # AxisBias あり → (5,1) 軸の重複は許可される (1 点に絞らない)
        pair_51_count = sum(
            1 for b in (plan.final_best + plan.final_osae)
            if b.combination and b.combination.startswith("5-1-")
        )
        assert pair_51_count >= 1, (
            f"AxisBias(5-1) でも 5-1-* が少なくとも 1 点は残るべき。"
            f"実際: {pair_51_count} 点"
        )
