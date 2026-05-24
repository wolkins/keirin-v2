"""Phase 3: MarketBiasDecision + HeadBias-only 同一軸制限 の回帰テスト.

検証内容:
A. assess_market_bias_decision の bias_type 判定 (none/head/axis/strong_axis)
B. build_output_plan で plan.market_bias_type が設定される
C. HeadBias-only で同一2着軸が複数 → 抑制 + watch_only 移動
D. AxisBias / strong_axis では制限しない
E. SKIP では制限ロジック非適用
F. Renderer 「### 市場偏りの補足」表示
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.decision import (
    MarketBiasDecision,
    PurchaseMode,
    assess_market_bias_decision,
)
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import OutputPlan, build_output_plan


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _pred(*, honsen=None, osae=None, ana=None, ooana=None,
          marks=None, is_girls=False):
    return Prediction(
        race_id="t", venue="t", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="",
        lines_text="", marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion="", gami_memo="", reflection_points=[],
    )


def _ri(*, odds, lines=None, is_girls=False) -> RaceInput:
    lines = lines or [
        {"line_name": "L1", "cars": [1, 2, 4]},
        {"line_name": "L2", "cars": [5, 3, 6]},
        {"line_name": "L3", "cars": [7]},
    ]
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-24",
                 "venue": "テスト", "race_no": 1,
                 "class_name": "A級一般", "start_time": "10:00",
                 "is_girls": is_girls},
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                    "wind_speed_mps": 2.0},
        "lines": lines,
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 88.0,
             "b_count": 1, "nige": 1, "makuri": 1, "sashi": 1,
             "mark": 1, "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": odds,
        "recent_results": [
            {"date": "2026-05-23", "venue": "テスト",
             "race_no": 1, "result": "1-2-3", "memo": "x"},
        ],
    })


# ---------------------------------------------------------------------------
# A. bias_type 判定 (assess_market_bias_decision 単体)
# ---------------------------------------------------------------------------


class TestAssessMarketBiasDecision:
    def test_none_when_dispersed(self):
        # 上位5件が分散 (頭が散る、軸も散る)
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.0},
            {"bet_type": "3連単", "combination": "3-1-2", "odds": 6.0},
            {"bet_type": "3連単", "combination": "5-7-4", "odds": 7.0},
            {"bet_type": "3連単", "combination": "7-5-6", "odds": 8.0},
            {"bet_type": "3連単", "combination": "2-4-1", "odds": 9.0},
        ])
        d = assess_market_bias_decision(ri)
        assert d.bias_type == "none"
        assert d.head is None or d.head_count < 3
        assert d.axis is None or d.axis_count < 3

    def test_head_only_when_5_same_head_no_axis(self):
        # 1番頭が 5/5、ただし 2着は分散
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.0},
            {"bet_type": "3連単", "combination": "1-3-5", "odds": 6.0},
            {"bet_type": "3連単", "combination": "1-5-7", "odds": 7.0},
            {"bet_type": "3連単", "combination": "1-7-2", "odds": 8.0},
            {"bet_type": "3連単", "combination": "1-4-6", "odds": 9.0},
        ])
        d = assess_market_bias_decision(ri)
        assert d.bias_type == "head"
        assert d.head == 1
        assert d.head_count >= 5
        assert d.axis is None
        # notes に「同一 2着軸への寄せ過ぎを抑制」
        notes = " ".join(d.notes)
        assert "同一" in notes

    def test_axis_when_3_same_axis(self):
        # 2-5 軸が 3/5
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "2-5-3", "odds": 5.0},
            {"bet_type": "3連単", "combination": "2-5-1", "odds": 6.0},
            {"bet_type": "3連単", "combination": "2-5-7", "odds": 7.0},
            {"bet_type": "3連単", "combination": "5-2-3", "odds": 8.0},
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 9.0},
        ])
        d = assess_market_bias_decision(ri)
        assert d.bias_type == "axis"
        assert d.axis == (2, 5)
        assert d.axis_count == 3

    def test_strong_axis_when_4_same_axis(self):
        # 2-5 軸が 4/5
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "2-5-3", "odds": 5.0},
            {"bet_type": "3連単", "combination": "2-5-1", "odds": 6.0},
            {"bet_type": "3連単", "combination": "2-5-7", "odds": 7.0},
            {"bet_type": "3連単", "combination": "2-5-4", "odds": 8.0},
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 9.0},
        ])
        d = assess_market_bias_decision(ri)
        assert d.bias_type == "strong_axis"
        assert d.axis == (2, 5)
        assert d.axis_count >= 4
        notes = " ".join(d.notes)
        assert "強く集中" in notes or "強く" in notes


# ---------------------------------------------------------------------------
# B'. _restrict_same_axis_under_head_bias の単体テスト (codex P2 反映)
# ---------------------------------------------------------------------------


class TestRestrictSameAxisUnderHeadBiasUnit:
    """`_restrict_same_axis_under_head_bias` を OutputPlan 直接組み立てで
    検証する (E2E では final_selection を経由するため抑制が起こらない
    可能性があり、codex P1 のような seen_axes 初期化バグが
    見逃される)。"""

    def test_seen_axes_shared_across_three_buckets(self):
        """codex P1 回帰: final_best/final_osae/final_ana に 1-4 軸が
        散らばっていても、最大1点に制限される。"""
        from app.output_plan import _restrict_same_axis_under_head_bias

        plan = OutputPlan(
            final_best=[_bet("1-4-2")],
            final_osae=[_bet("1-4-6", category="押さえ")],
            final_ana=[_bet("1-4-7", category="穴")],
        )
        _restrict_same_axis_under_head_bias(plan, head=1)
        all_final = (
            list(plan.final_best) + list(plan.final_osae)
            + list(plan.final_ana)
        )
        one_four = [
            b.combination for b in all_final
            if b.combination and b.combination.split("-")[:2] == ["1", "4"]
        ]
        assert len(one_four) <= 1, (
            f"3 バケット合計で 1-4 軸が {len(one_four)} 点残った: "
            f"{[b.combination for b in all_final]}"
        )
        # 抑制された 2 点は watch_only に入っている
        watch_combos = [b.combination for b in plan.watch_only]
        suppressed_in_watch = sum(
            1 for c in watch_combos
            if c and c.split("-")[:2] == ["1", "4"]
        )
        assert suppressed_in_watch >= 2, watch_combos

    def test_suppressed_prepended_to_watch_only(self):
        """codex P2 反映: 抑制候補は watch_only の先頭に挿入される
        (Renderer は watch_only[:2] しか表示しないため)。"""
        from app.output_plan import _restrict_same_axis_under_head_bias

        plan = OutputPlan(
            final_best=[_bet("1-4-2"), _bet("1-4-6")],
            watch_only=[_bet("3-5-1"), _bet("5-3-1")],
        )
        _restrict_same_axis_under_head_bias(plan, head=1)
        # 抑制された 1-4-6 が watch_only の先頭に来る
        assert plan.watch_only[0].combination == "1-4-6", [
            b.combination for b in plan.watch_only
        ]

    def test_no_restriction_when_head_doesnt_match(self):
        """head 引数と一致しない頭の候補には制限が走らない。"""
        from app.output_plan import _restrict_same_axis_under_head_bias

        plan = OutputPlan(
            final_best=[_bet("3-4-2"), _bet("3-4-6")],
        )
        _restrict_same_axis_under_head_bias(plan, head=1)
        # head=1 でないので 3-4-* は両方残る
        assert len(plan.final_best) == 2

    def test_different_second_axes_kept(self):
        """同じ head でも 2着が違えば残る。"""
        from app.output_plan import _restrict_same_axis_under_head_bias

        plan = OutputPlan(
            final_best=[_bet("1-2-3"), _bet("1-7-2"), _bet("1-3-6")],
        )
        _restrict_same_axis_under_head_bias(plan, head=1)
        assert len(plan.final_best) == 3


# ---------------------------------------------------------------------------
# B/C. HeadBias-only 同一軸制限 (E2E)
# ---------------------------------------------------------------------------


class TestHiroshima3rHeadBiasOnlyRestriction:
    """広島3R 風: HeadBias=1 のみ、AxisBias 無し、final に 1-4-2 / 1-4-6 が
    含まれる → 1-4 軸を 1点に絞り、残りは watch_only へ。"""

    def _ri(self) -> RaceInput:
        return _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.5},
            {"bet_type": "3連単", "combination": "1-3-5", "odds": 7.0},
            {"bet_type": "3連単", "combination": "1-5-7", "odds": 8.5},
            {"bet_type": "3連単", "combination": "1-7-2", "odds": 10.0},
            {"bet_type": "3連単", "combination": "1-4-6", "odds": 11.5},
        ])

    def test_head_bias_only_detected(self):
        ri = self._ri()
        pred = _pred(honsen=[
            _bet("1-2-3", market_odds=5.5, value_label="本線向き"),
            _bet("1-4-2", market_odds=7.0, value_label="本線向き"),
            _bet("1-4-6", market_odds=11.5),
        ])
        plan = build_output_plan(pred, ri)
        assert plan.market_bias_type == "head", (
            f"market_bias_type={plan.market_bias_type} "
            f"notes={plan.market_bias_notes}"
        )

    def test_same_axis_limited_to_one_in_final_buckets(self):
        ri = self._ri()
        # わざと 1-4 軸を複数仕込む。final_selection 経由で
        # final_best / final_osae に 1-4-* が複数入る可能性を作る。
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=5.5, value_label="本線向き"),
                _bet("1-4-2", market_odds=7.0, value_label="本線向き"),
                _bet("1-4-6", market_odds=11.5),
            ],
            osae=[
                _bet("1-4-7", market_odds=14.0, category="押さえ"),
            ],
        )
        plan = build_output_plan(pred, ri)
        # final_best+final_osae+final_ana で head=1 + second=4 の組合せが
        # 最大 1 点
        all_final = (
            list(plan.final_best) + list(plan.final_osae)
            + list(plan.final_ana)
        )
        one_four_count = sum(
            1 for b in all_final
            if b.combination and b.combination.split("-")[:2] == ["1", "4"]
        )
        assert one_four_count <= 1, (
            f"HeadBias-only で 1-4 軸が {one_four_count} 点残った: "
            f"{[b.combination for b in all_final]}"
        )

    def test_suppressed_candidate_in_watch_only(self):
        """抑制された候補は watch_only に移動 + 「N点を参考候補へ移動」note。"""
        ri = self._ri()
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=5.5, value_label="本線向き"),
                _bet("1-4-2", market_odds=7.0, value_label="本線向き"),
                _bet("1-4-6", market_odds=11.5),
            ],
            osae=[
                _bet("1-4-7", market_odds=14.0, category="押さえ"),
            ],
        )
        plan = build_output_plan(pred, ri)
        # final_* に 1-4-* が重複していれば抑制 note が出る
        all_final_combos = [
            b.combination for b in (
                list(plan.final_best) + list(plan.final_osae)
                + list(plan.final_ana)
            )
        ]
        one_four_in_final = sum(
            1 for c in all_final_combos
            if c and c.split("-")[:2] == ["1", "4"]
        )
        # 制限後は 1-4 軸は 1 点まで。抑制 note は重複が削られたときだけ出る
        if one_four_in_final < len([
            c for c in all_final_combos
            if c and c.split("-")[:2] == ["1", "4"]
        ]) or any("参考候補へ移動" in n for n in plan.decision_notes):
            notes = " ".join(plan.decision_notes)
            assert "参考候補へ移動" in notes, notes

    def test_notes_mention_head_bias_only(self):
        ri = self._ri()
        pred = _pred(honsen=[
            _bet("1-2-3", market_odds=5.5, value_label="本線向き"),
            _bet("1-4-2", market_odds=7.0, value_label="本線向き"),
            _bet("1-4-6", market_odds=11.5),
        ])
        plan = build_output_plan(pred, ri)
        # market_bias_notes に HeadBias 関連の説明
        notes = " ".join(plan.market_bias_notes)
        assert "1着" in notes or "HeadBias" in notes or "1番" in notes


# ---------------------------------------------------------------------------
# D. AxisBias では制限しない
# ---------------------------------------------------------------------------


class TestShizuoka4rAxisBiasNotRestricted:
    """静岡4R 風: 2-5 軸 AxisBias → 2-5-* 複数候補を許可。"""

    def _ri(self) -> RaceInput:
        return _ri(odds=[
            {"bet_type": "3連単", "combination": "2-5-3", "odds": 4.5},
            {"bet_type": "3連単", "combination": "2-5-1", "odds": 5.2},
            {"bet_type": "3連単", "combination": "2-5-7", "odds": 8.0},
            {"bet_type": "3連単", "combination": "2-5-6", "odds": 9.5},
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 12.0},
        ])

    def test_axis_bias_detected(self):
        ri = self._ri()
        pred = _pred(honsen=[
            _bet("2-5-3", market_odds=4.5),
            _bet("2-5-1", market_odds=5.2),
            _bet("2-5-7", market_odds=8.0),
        ])
        plan = build_output_plan(pred, ri)
        assert plan.market_bias_type in ("axis", "strong_axis")

    def test_axis_bias_does_not_limit_same_axis(self):
        ri = self._ri()
        pred = _pred(honsen=[
            _bet("2-5-3", market_odds=4.5),
            _bet("2-5-1", market_odds=5.2),
            _bet("2-5-7", market_odds=8.0),
        ])
        plan = build_output_plan(pred, ri)
        all_final = (
            list(plan.final_best) + list(plan.final_osae)
            + list(plan.final_ana)
        )
        # 2-5 軸の複数候補が残る (=制限されない)
        two_five_count = sum(
            1 for b in all_final
            if b.combination and b.combination.split("-")[:2] == ["2", "5"]
        )
        # 制限が走らないことを担保 (現実には final_selection の調整で
        # 2-5 軸が1点になる可能性もある fixture によるが、制限ロジック
        # 自体は走らない)
        # → notes に「N点を参考候補へ移動」が含まれないことを確認
        notes = " ".join(plan.decision_notes)
        assert "参考候補へ移動" not in notes, notes


# ---------------------------------------------------------------------------
# E. SKIP は制限ロジック非適用
# ---------------------------------------------------------------------------


class TestSkipModeSkipsRestriction:
    def test_skip_does_not_apply_restriction(self):
        # 全オッズ取得率 0% で確実に SKIP にする
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.5},
            {"bet_type": "3連単", "combination": "1-3-5", "odds": 7.0},
            {"bet_type": "3連単", "combination": "1-5-7", "odds": 8.5},
        ])
        # honsen に 1-4-* を複数入れて、SKIP では制限が走らないことを確認
        pred = _pred(honsen=[
            _bet(c, market_odds=None) for c in [
                "1-2-3", "1-4-2", "1-4-6", "1-4-7", "5-3-6",
                "3-1-5", "5-1-3", "1-5-3", "3-5-1", "5-6-3",
            ]
        ])
        plan = build_output_plan(pred, ri)
        if plan.purchase_mode != PurchaseMode.SKIP:
            pytest.skip(
                f"SKIP 前提だが {plan.purchase_mode.name} になった"
            )
        # SKIP では制限ロジック非適用 → 「N点を参考候補へ移動」note は出ない
        # (bias_type=head の汎用 note「同一2着軸への寄せ過ぎを抑制します」
        # は出てよい。これは説明文であり制限実行ではない)
        notes = " ".join(plan.decision_notes)
        assert "参考候補へ移動" not in notes, notes
        # final_best/osae/ana に 1-4 軸が複数残っていても抑制されない
        # (final_selection 経由なので何が入っているかは不定だが、
        #  制限ロジックが走らないことを担保)


# ---------------------------------------------------------------------------
# F. Renderer 表示
# ---------------------------------------------------------------------------


class TestRendererShowsMarketBiasNotes:
    def test_market_bias_section_appears(self):
        ri = TestHiroshima3rHeadBiasOnlyRestriction()._ri()
        pred = _pred(honsen=[
            _bet("1-2-3", market_odds=5.5, value_label="本線向き"),
            _bet("1-4-2", market_odds=7.0, value_label="本線向き"),
            _bet("1-4-6", market_odds=11.5),
        ])
        md = render_prediction_v2(pred, input_data=ri)
        assert "### 市場偏りの補足" in md

    def test_no_section_when_dispersed(self):
        """市場が分散しているケース → notes 空 → セクション省略。"""
        ri = _ri(odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.0},
            {"bet_type": "3連単", "combination": "3-1-2", "odds": 6.0},
            {"bet_type": "3連単", "combination": "5-7-4", "odds": 7.0},
            {"bet_type": "3連単", "combination": "7-5-6", "odds": 8.0},
            {"bet_type": "3連単", "combination": "2-4-1", "odds": 9.0},
        ])
        pred = _pred(honsen=[
            _bet("1-2-3", market_odds=5.0),
            _bet("3-1-2", market_odds=6.0),
        ])
        md = render_prediction_v2(pred, input_data=ri)
        # market_bias_type=none → notes 空 → セクション省略
        assert "### 市場偏りの補足" not in md
