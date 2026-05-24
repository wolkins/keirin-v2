"""Phase 1: PurchaseMode / DecisionContext / 文言分岐の回帰テスト.

検証内容:
A. derive_purchase_mode の各ルール (単体)
B. build_output_plan が plan.purchase_mode を設定する
C. Renderer (render_final_conclusion / render_purchase_judgement_block) が
   purchase_mode で文言を切り替える
D. PostRenderValidator (validate_purchase_mode_markdown) が違反を検出する
E. 3 シナリオ E2E (広島3R 風 SKIP / 平塚10R 風 WATCH_ONLY / 通常 BUYABLE)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cli import render_prediction_v2
from app.decision import (
    DecisionContext,
    PurchaseMode,
    derive_purchase_mode,
)
from app.markdown_renderer import (
    render_final_conclusion,
    render_purchase_judgement_block,
    validate_purchase_mode_markdown,
)
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import OutputPlan, OutputPlanWarning, build_output_plan


# ---------------------------------------------------------------------------
# Section A: derive_purchase_mode の単体ルール
# ---------------------------------------------------------------------------


def _ctx(**overrides) -> DecisionContext:
    base = dict(
        odds_overall_coverage=0.8,
        honsen_odds_coverage=0.8,
        purchase_odds_coverage=0.8,
        data_quality="high",
        race_complexity="medium",
        is_girls=False,
        is_rookie=False,
        final_best_count=2,
    )
    base.update(overrides)
    return DecisionContext(**base)


class TestDerivePurchaseMode:
    def test_default_is_buyable(self):
        m = derive_purchase_mode(_ctx())
        assert m == PurchaseMode.BUYABLE

    def test_low_overall_coverage_is_skip(self):
        m = derive_purchase_mode(_ctx(odds_overall_coverage=0.15))
        assert m == PurchaseMode.SKIP

    def test_very_high_complexity_low_coverage_is_skip(self):
        m = derive_purchase_mode(_ctx(
            race_complexity="very_high",
            odds_overall_coverage=0.35,
        ))
        assert m == PurchaseMode.SKIP

    def test_data_quality_low_is_watch_only_cap(self):
        # data_quality=low かつ他全て十分 → WATCH_ONLY
        m = derive_purchase_mode(_ctx(data_quality="low"))
        assert m == PurchaseMode.WATCH_ONLY

    def test_data_quality_very_low_is_watch_only_cap(self):
        m = derive_purchase_mode(_ctx(data_quality="very_low"))
        assert m == PurchaseMode.WATCH_ONLY

    def test_girls_low_quality_is_watch_only(self):
        m = derive_purchase_mode(_ctx(is_girls=True, data_quality="low"))
        assert m == PurchaseMode.WATCH_ONLY

    def test_rookie_low_quality_is_watch_only(self):
        m = derive_purchase_mode(_ctx(is_rookie=True, data_quality="low"))
        assert m == PurchaseMode.WATCH_ONLY

    def test_honsen_low_coverage_is_tentative(self):
        m = derive_purchase_mode(_ctx(honsen_odds_coverage=0.4))
        assert m == PurchaseMode.TENTATIVE

    def test_purchase_low_coverage_is_tentative(self):
        m = derive_purchase_mode(_ctx(purchase_odds_coverage=0.3))
        assert m == PurchaseMode.TENTATIVE

    def test_final_best_empty_is_watch_only(self):
        m = derive_purchase_mode(_ctx(final_best_count=0))
        assert m == PurchaseMode.WATCH_ONLY

    def test_more_dangerous_wins(self):
        # data_quality=low (→WATCH_ONLY) + honsen_coverage<0.5 (→TENTATIVE)
        # → WATCH_ONLY (より危険側)
        m = derive_purchase_mode(_ctx(
            data_quality="low",
            honsen_odds_coverage=0.4,
        ))
        assert m == PurchaseMode.WATCH_ONLY

    def test_reasons_recorded(self):
        ctx = _ctx(odds_overall_coverage=0.15)
        derive_purchase_mode(ctx)
        assert ctx.reasons
        assert any("15%" in r or "20%" in r for r in ctx.reasons)


# ---------------------------------------------------------------------------
# Section D: PostRenderValidator
# ---------------------------------------------------------------------------


def _empty_plan(mode: PurchaseMode) -> OutputPlan:
    return OutputPlan(purchase_mode=mode)


class TestValidatePurchaseModeMarkdown:
    def test_buyable_skips_check(self):
        plan = _empty_plan(PurchaseMode.BUYABLE)
        v = validate_purchase_mode_markdown(
            plan, "ここに 購入対象 / 一番買いたい / 実購入候補 がある"
        )
        assert v == []

    def test_watch_only_detects_basic_forbidden(self):
        plan = _empty_plan(PurchaseMode.WATCH_ONLY)
        v = validate_purchase_mode_markdown(plan, "本文に 購入対象 が残る")
        codes = [w.code for w in v]
        assert "PURCHASE_MODE_VIOLATION" in codes

    def test_skip_detects_strict_forbidden(self):
        plan = _empty_plan(PurchaseMode.SKIP)
        v = validate_purchase_mode_markdown(
            plan, "見送りなのに 本線向き が出る"
        )
        codes = [w.code for w in v]
        assert "PURCHASE_MODE_VIOLATION_STRICT" in codes

    def test_tentative_does_not_check_strict(self):
        """TENTATIVE は basic のみチェック (本線向きは許容)。"""
        plan = _empty_plan(PurchaseMode.TENTATIVE)
        v = validate_purchase_mode_markdown(plan, "本線向き は OK")
        # 本線向き は basic にも strict にも入らない
        assert all(w.code != "PURCHASE_MODE_VIOLATION_STRICT" for w in v)


# ---------------------------------------------------------------------------
# Section C: render_final_conclusion / render_purchase_judgement_block
# ---------------------------------------------------------------------------


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


class TestRenderByPurchaseMode:
    def test_buyable_keeps_strong_words(self):
        plan = OutputPlan(
            purchase_mode=PurchaseMode.BUYABLE,
            final_best=[_bet("1-2-3", market_odds=8.0)],
        )
        text = render_final_conclusion(plan)
        assert "一番買いたい" in text or "中心に据える" in text

    def test_tentative_uses_provisional_label(self):
        plan = OutputPlan(
            purchase_mode=PurchaseMode.TENTATIVE,
            final_best=[_bet("1-2-3", market_odds=8.0)],
        )
        text = render_final_conclusion(plan)
        assert "暫定候補" in text
        assert "一番買いたい" not in text

    def test_watch_only_uses_miokuri_label(self):
        plan = OutputPlan(
            purchase_mode=PurchaseMode.WATCH_ONLY,
            final_best=[_bet("1-2-3", market_odds=8.0)],
        )
        text = render_final_conclusion(plan)
        assert "見送り寄り" in text
        assert "一番買いたい" not in text
        assert "厚く買わない" in text or "確認程度" in text

    def test_skip_uses_skip_label(self):
        plan = OutputPlan(
            purchase_mode=PurchaseMode.SKIP,
            final_best=[_bet("1-2-3", market_odds=8.0)],
        )
        text = render_final_conclusion(plan)
        assert "見送り" in text
        assert "一番買いたい" not in text

    def test_buyable_purchase_block_has_strong_label(self):
        plan = OutputPlan(
            purchase_mode=PurchaseMode.BUYABLE,
            final_best=[_bet("1-2-3", market_odds=8.0)],
        )
        block = "\n".join(render_purchase_judgement_block(plan))
        assert "買える候補" in block
        assert "購入対象" in block

    def test_watch_only_purchase_block_no_strong_label(self):
        plan = OutputPlan(
            purchase_mode=PurchaseMode.WATCH_ONLY,
            final_best=[_bet("1-2-3", market_odds=8.0)],
        )
        block = "\n".join(render_purchase_judgement_block(plan))
        assert "見送り寄り" in block
        assert "購入対象" not in block
        assert "買える候補" not in block


# ---------------------------------------------------------------------------
# Section E: シナリオ E2E
# ---------------------------------------------------------------------------


def _make_prediction(*, is_girls=False, honsen=None, osae=None):
    return Prediction(
        race_id="t", venue="t", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="",
        lines_text="", marks={},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=[], ooana=[],
        final_conclusion="", gami_memo="", reflection_points=[],
    )


class TestHiroshima3rSkipScenario:
    """広島3R 風シナリオ: 通常戦 + 1番頭 HeadBias + 全オッズ取得率 0.17 →
    purchase_mode=SKIP / 「見送り」または「見送り寄り」が本文に出る /
    「購入対象」「一番買いたい」「実購入候補」が出ない。"""

    def _ri(self) -> RaceInput:
        # 全 3連単候補は 30件くらい想定 (3連単のフルセットは 210)。
        # ここでは odds_overall_coverage<0.20 を確実に出すため、
        # honsen/osae の総点数に対して取得済みを少なくする fixture を使う。
        # build_output_plan の coverage は plan のサンプリングに依存するため、
        # シンプルに odds=5件のみ (1番頭) + recent_results なしで再現。
        return RaceInput.model_validate({
            "race": {
                "race_id": "t-h3", "date": "2026-05-24",
                "venue": "広島", "race_no": 3,
                "class_name": "A級一般", "start_time": "11:00",
            },
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "L1", "cars": [5, 3, 6]},
                {"line_name": "L2", "cars": [1, 2, 4]},
                {"line_name": "L3", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 85.0,
                 "b_count": 1 if i == 1 else 0,
                 "nige": 1 if i in (1, 5) else 0,
                 "makuri": 0, "sashi": 1 if i in (2, 3) else 0,
                 "mark": 0, "comment": "", "home_area": "中国"}
                for i in range(1, 8)
            ],
            "odds": [
                # 1 番頭の上位5件のみ取得 (overall coverage 低)
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.5},
                {"bet_type": "3連単", "combination": "1-2-4", "odds": 7.8},
                {"bet_type": "3連単", "combination": "1-2-5", "odds": 9.5},
                {"bet_type": "3連単", "combination": "1-2-6", "odds": 11.2},
                {"bet_type": "3連単", "combination": "1-2-7", "odds": 13.0},
            ],
            "recent_results": [],
        })

    def test_purchase_mode_is_skip(self):
        ri = self._ri()
        # honsen はそこそこ厚めに用意 (3連単 ~30 件規模 → coverage <20%)
        pred = _make_prediction(honsen=[
            _bet(c, market_odds=None) for c in [
                "5-3-6", "5-3-1", "3-5-6", "1-2-3", "1-2-4",
                "5-1-3", "1-5-3", "5-6-3", "5-3-2", "3-1-5",
            ]
        ])
        plan = build_output_plan(pred, ri)
        # honsen 10点 + そこから派生される osae 等を含めて 30点以上に
        # 達することを期待。assert coverage<0.2:
        from app.output_validation import compute_odds_coverage
        cov = compute_odds_coverage(pred, plan=plan)
        assert cov.coverage_ratio < 0.20, (
            f"fixture coverage が 20% 以上で SKIP 条件を満たさない: "
            f"with_odds={cov.with_odds} total={cov.total} "
            f"ratio={cov.coverage_ratio:.0%}"
        )
        assert plan.purchase_mode == PurchaseMode.SKIP, (
            f"purchase_mode={plan.purchase_mode.name} "
            f"reasons={plan.decision_notes}"
        )

    def test_skip_markdown_uses_miokuri_words(self):
        ri = self._ri()
        pred = _make_prediction(honsen=[
            _bet(c, market_odds=None) for c in [
                "5-3-6", "5-3-1", "3-5-6", "1-2-3", "1-2-4",
                "5-1-3", "1-5-3", "5-6-3", "5-3-2", "3-1-5",
            ]
        ])
        md = render_prediction_v2(pred, input_data=ri)
        # 本文 (警告セクション以前) を取り出す
        body = md
        for sep in ("### 出力整合性チェック", "### OutputPlan 警告"):
            if sep in body:
                body = body[:body.rfind(sep)]
        # 「見送り」が出る
        assert "見送り" in body
        # 強い購入表現が出ない
        for word in ("購入対象", "一番買いたい", "実購入候補"):
            assert word not in body, (
                f"SKIP モードで「{word}」が本文に残存:\n{body[-1500:]}"
            )


class TestHiratsuka10rWatchOnlyScenario:
    """平塚10R 風: ガールズ新人決勝 + data_quality=low + 取得率低 →
    purchase_mode=WATCH_ONLY / 「見送り寄り」「参考候補」が本文に出る。"""

    def _ri(self) -> RaceInput:
        # data_quality=low を確実に発動: riders 5名 stats_missing
        return RaceInput.model_validate({
            "race": {
                "race_id": "t-h10", "date": "2026-05-24",
                "venue": "平塚", "race_no": 10,
                "class_name": "ガールズ新人決勝", "start_time": "16:30",
                "is_girls": True,
            },
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [{"line_name": f"L{i}", "cars": [i]} for i in range(1, 8)],
            "riders": [
                {"car_no": 1, "name": "R1", "score": 70.0, "b_count": 0,
                 "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
                 "comment": "", "home_area": "南関東"},
                {"car_no": 2, "name": "R2", "score": 70.0, "b_count": 0,
                 "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
                 "comment": "", "home_area": "南関東"},
            ] + [
                {"car_no": i, "name": f"R{i}", "score": 0.0, "b_count": 0,
                 "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
                 "comment": "", "stats_missing": True,
                 "home_area": "南関東"}
                for i in range(3, 8)
            ],
            "odds": [
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0},
                {"bet_type": "3連単", "combination": "2-1-3", "odds": 10.0},
            ],
            "recent_results": [],
        })

    def test_purchase_mode_is_watch_only(self):
        ri = self._ri()
        pred = _make_prediction(
            is_girls=True,
            honsen=[
                _bet("1-2-3", market_odds=8.0, value_label="本線向き"),
                _bet("2-1-3", market_odds=10.0),
                _bet("3-1-2", market_odds=None),
            ],
        )
        plan = build_output_plan(pred, ri)
        # data_quality=low が立つ → WATCH_ONLY cap
        assert plan.purchase_mode in (
            PurchaseMode.WATCH_ONLY, PurchaseMode.SKIP
        ), (
            f"purchase_mode={plan.purchase_mode.name} "
            f"reasons={plan.decision_notes}"
        )

    def test_watch_only_markdown_no_strong_words(self):
        ri = self._ri()
        pred = _make_prediction(
            is_girls=True,
            honsen=[
                _bet("1-2-3", market_odds=8.0, value_label="本線向き"),
                _bet("2-1-3", market_odds=10.0),
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        body = md
        for sep in ("### 出力整合性チェック", "### OutputPlan 警告"):
            if sep in body:
                body = body[:body.rfind(sep)]
        for word in ("購入対象", "一番買いたい"):
            assert word not in body, (
                f"WATCH_ONLY モードで「{word}」が本文に残存"
            )


class TestNormalBuyableScenario:
    """通常 高品質: data_quality=high / odds 十分 → purchase_mode=BUYABLE /
    「購入対象」「実購入候補」「一番買いたい」が許可される。"""

    def _ri(self) -> RaceInput:
        return RaceInput.model_validate({
            "race": {
                "race_id": "t-norm", "date": "2026-05-24",
                "venue": "テスト", "race_no": 5,
                "class_name": "A級一般", "start_time": "12:00",
            },
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 2.0},
            "lines": [
                {"line_name": "本命", "cars": [1, 2, 3]},
                {"line_name": "別線", "cars": [5, 4, 6]},
                {"line_name": "単", "cars": [7]},
            ],
            "riders": [
                {"car_no": i, "name": f"R{i}", "score": 92.0,
                 "b_count": 1, "nige": 1, "makuri": 1, "sashi": 1,
                 "mark": 1, "comment": "", "home_area": "中部"}
                for i in range(1, 8)
            ],
            "odds": [
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.5},
                {"bet_type": "3連単", "combination": "1-2-5", "odds": 9.0},
                {"bet_type": "3連単", "combination": "1-3-2", "odds": 11.0},
                {"bet_type": "3連単", "combination": "2-1-3", "odds": 13.0},
                {"bet_type": "3連単", "combination": "1-5-2", "odds": 14.5},
                {"bet_type": "3連単", "combination": "5-1-2", "odds": 16.0},
                {"bet_type": "3連単", "combination": "1-2-4", "odds": 18.0},
                {"bet_type": "3連単", "combination": "1-2-7", "odds": 21.0},
                {"bet_type": "3連単", "combination": "1-3-5", "odds": 23.0},
                {"bet_type": "3連単", "combination": "5-1-3", "odds": 24.0},
            ],
            "recent_results": [
                {"date": "2026-05-23", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "sample"},
            ],
        })

    def test_purchase_mode_is_buyable(self):
        ri = self._ri()
        pred = _make_prediction(honsen=[
            _bet("1-2-3", market_odds=6.5, value_label="本線向き"),
            _bet("1-2-5", market_odds=9.0, value_label="妙味あり"),
            _bet("2-1-3", market_odds=13.0),
        ])
        plan = build_output_plan(pred, ri)
        assert plan.purchase_mode == PurchaseMode.BUYABLE, (
            f"purchase_mode={plan.purchase_mode.name} "
            f"reasons={plan.decision_notes}"
        )

    def test_PHASE1_P1_purchase_coverage_pre_filter_takeo_12r(self):
        """codex P1 回帰: prediction.honsen + osae で coverage 計算しないと
        本来 SKIP すべきケースが BUYABLE になる。

        武雄12R 風: prediction.honsen+osae 10点中 2点しか odds 取得済みでない。
        final_selection 後の plan.final_best/osae で計算すると 100% に見えるが
        本来は 20% で PURCHASE_SKIP_RECOMMENDED が立つはず。
        """
        # 既存テストの fixture を借りる
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'tk12', 'tests/test_takeo_12r_safety_controls.py'
        )
        tk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tk)
        pred = tk.TestCoverageSafetyControl()._make_low_coverage_pred()
        from app.models import OddsEntry
        odds = [
            OddsEntry(bet_type='3連単', combination=c, odds=o)
            for c, o in [('1-7-3', 8.0), ('1-7-4', 10.0),
                         ('3-5-1', 22.0), ('5-3-1', 28.0)]
        ]
        riders = [
            {'car_no': i, 'name': f'R{i}', 'score': 95.0, 'b_count': 1,
             'nige': 1, 'makuri': 0, 'sashi': 1, 'mark': 0,
             'comment': '', 'home_area': '九州'}
            for i in range(1, 10)
        ]
        ri = tk._input(riders=riders, odds=odds)
        ri.recent_results = [
            type(ri.recent_results[0]).model_validate({
                'date': '2026-05-23', 'venue': '武雄', 'race_no': 1,
                'result': '1-2-3', 'memo': 'x',
            })
        ] if ri.recent_results else []

        plan = build_output_plan(pred, ri)
        # PURCHASE_SKIP_RECOMMENDED → SKIP
        codes = [w.code for w in plan.warnings]
        assert "PURCHASE_SKIP_RECOMMENDED" in codes, codes
        assert plan.purchase_mode == PurchaseMode.SKIP, (
            f"purchase_mode={plan.purchase_mode.name} "
            f"reasons={plan.decision_notes}"
        )

    def test_PHASE1_P2_empty_final_best_no_buyable_phrase(self):
        """codex P2 回帰: SKIP / WATCH_ONLY で final_best が空の場合、
        「買える候補」「購入対象」が本文に残らない。"""
        plan = OutputPlan(purchase_mode=PurchaseMode.SKIP)
        text = render_final_conclusion(plan)
        assert "買える候補" not in text
        assert "見送り" in text

        plan2 = OutputPlan(purchase_mode=PurchaseMode.WATCH_ONLY)
        block = "\n".join(render_purchase_judgement_block(plan2))
        assert "買える候補" not in block
        assert "参考候補" in block or "見送り" in block

    def test_buyable_markdown_allows_strong_words(self):
        ri = self._ri()
        pred = _make_prediction(honsen=[
            _bet("1-2-3", market_odds=6.5, value_label="本線向き"),
            _bet("1-2-5", market_odds=9.0, value_label="妙味あり"),
            _bet("2-1-3", market_odds=13.0),
        ])
        md = render_prediction_v2(pred, input_data=ri)
        body = md
        for sep in ("### 出力整合性チェック", "### OutputPlan 警告"):
            if sep in body:
                body = body[:body.rfind(sep)]
        # BUYABLE では強い購入表現が出る
        assert "一番買いたい" in body or "中心に据える" in body
        # final_best がある場合は「購入対象」or「買える候補」が出る
        assert "購入対象" in body or "買える候補" in body
