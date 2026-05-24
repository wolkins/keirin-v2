"""OutputPlan 整合性: render_prediction_v2 + MarkdownRenderer の検証。

ユーザー要件 (2026-05-24):
- OutputPlan が唯一の source of truth
- LLM の final_conclusion / honsen / osae / ana / ooana は無視
- Markdown 中の3連単 combo が OutputPlan 内に必ず存在
- 未登録 combo があったらテンプレートフォールバック
"""

from __future__ import annotations

import re

import pytest

from app.cli import render_prediction_v2
from app.markdown_renderer import (
    render_final_conclusion,
    render_output_plan,
    verify_markdown_combos,
)
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import (
    OutputPlan,
    OutputPlanWarning,
    build_output_plan,
    from_final_selection,
)


def _bet(combo, *, market_odds=None, value_label="", gami_risk=0.0,
         category="本線", reason="test"):
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=reason, gami_risk=gami_risk,
        market_odds=market_odds, value_label=value_label,
    )


def _pred(*, honsen=None, osae=None, ana=None, ooana=None,
          marks=None, final_conclusion="", summary="", is_girls=False):
    return Prediction(
        race_id="test", venue="テスト", race_no=1, is_girls=is_girls,
        summary=summary, venue_trend_text="trend",
        weather_text="weather", lines_text="lines",
        marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion=final_conclusion,
        gami_memo="", reflection_points=[],
    )


def _input(*, class_name="A級一般", lines=None, odds=None,
           recent_results=None):
    return RaceInput.model_validate({
        "race": {
            "race_id": "test", "date": "2026-05-24",
            "venue": "テスト", "race_no": 1,
            "class_name": class_name, "start_time": "10:00",
        },
        "weather": {
            "condition": "晴れ", "rain_mm_per_hour": 0.0,
            "wind_speed_mps": 2.0,
        },
        "lines": lines or [
            {"line_name": "本命", "cars": [1, 2, 3]},
            {"line_name": "別線", "cars": [4, 5, 6]},
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 85.0, "b_count": 0,
             "nige": 1, "makuri": 1, "sashi": 1, "mark": 1,
             "comment": "", "home_area": "近畿"}
            for i in range(1, 8)
        ],
        # data_quality=high にするため odds と recent_results を含めるデフォルト
        "odds": odds if odds is not None else [
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 10.0},
        ],
        "recent_results": recent_results if recent_results is not None else [
            {"date": "2026-05-23", "venue": "テスト",
             "race_no": 1, "result": "1-2-3", "memo": "sample"},
        ],
    })


# ---------------------------------------------------------------------------
# OutputPlan 構造
# ---------------------------------------------------------------------------


class TestOutputPlanStructure:
    def test_all_required_fields_present(self):
        plan = OutputPlan()
        for field in ("honsen", "osae", "ana", "ooana",
                      "final_best", "final_osae", "final_ana",
                      "gami_warning", "watch_only", "warnings"):
            assert hasattr(plan, field), f"OutputPlan に {field} が無い"

    def test_all_combos_aggregates_all_buckets(self):
        plan = OutputPlan(
            honsen=[_bet("1-2-3")],
            osae=[_bet("2-1-3")],
            ana=[_bet("4-5-6")],
            ooana=[_bet("5-4-6")],
            final_best=[_bet("3-1-2")],
            gami_warning=[_bet("1-3-2")],
        )
        combos = plan.all_combos()
        assert combos == {"1-2-3", "2-1-3", "4-5-6", "5-4-6", "3-1-2", "1-3-2"}

    def test_build_output_plan_from_prediction(self):
        """build_output_plan が Prediction から OutputPlan を構築する。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
        )
        plan = build_output_plan(pred, _input())
        assert "1-2-3" in plan.all_combos()
        # final_best に odds取得済み妙味が入る
        assert "1-2-3" in {b.combination for b in plan.final_best}


# ---------------------------------------------------------------------------
# MarkdownRenderer: deterministic 生成
# ---------------------------------------------------------------------------


class TestMarkdownRendererDeterministic:
    def test_render_final_conclusion_from_plan_only(self):
        """render_final_conclusion は OutputPlan からのみ生成する。"""
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
            ],
        )
        text = render_final_conclusion(plan)
        assert "1-2-3" in text
        assert "2-1-3" in text
        # 文言整合性 (2026-05-24 修正): final_best ありなら「一番買いたい買い目は ...」
        assert "一番買いたい買い目は" in text

    def test_render_uses_plan_not_prediction_final_conclusion(self):
        """LLM の final_conclusion は無視され、OutputPlan からのみ生成。"""
        plan = OutputPlan(
            final_best=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
        )
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
            # 故意に矛盾した LLM final_conclusion
            final_conclusion="本線は 4-3-6, 3-4-6, 4-6-3 を中心に据える。",
        )
        md = render_output_plan(plan, pred, _input())
        # 結論文には plan.final_best (1-2-3) のみが出る
        conclusion_block = md.split("## 10. 最終結論")[1].split("##")[0]
        assert "1-2-3" in conclusion_block
        # LLM の矛盾 combo は出ない
        assert "4-3-6" not in conclusion_block
        assert "3-4-6" not in conclusion_block
        assert "4-6-3" not in conclusion_block


# ---------------------------------------------------------------------------
# 整合性検証: Markdown 中の combo が全て OutputPlan に存在
# ---------------------------------------------------------------------------


class TestMarkdownCombosVerification:
    def test_verify_returns_unregistered_combos(self):
        plan = OutputPlan(
            final_best=[_bet("1-2-3", market_odds=10.0)],
        )
        md = "本線は 1-2-3, 4-5-6 を中心に据える。"
        unregistered = verify_markdown_combos(md, plan)
        assert unregistered == {"4-5-6"}, (
            f"OutputPlan に無い 4-5-6 のみ検出: {unregistered}"
        )

    def test_verify_returns_empty_when_all_registered(self):
        plan = OutputPlan(
            final_best=[_bet("1-2-3"), _bet("2-1-3")],
        )
        md = "本線は 1-2-3, 2-1-3 を中心に据える。"
        assert verify_markdown_combos(md, plan) == set()

    def test_render_v2_no_unregistered_combos_normal_case(self):
        """通常ケース: render_prediction_v2 の出力は OutputPlan 内の combo
        だけで構成される。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
            osae=[_bet("2-1-3", market_odds=12.0, value_label="本線向き",
                       category="押さえ")],
        )
        md = render_prediction_v2(pred, input_data=_input())
        plan = build_output_plan(pred, _input())
        # Markdown 中の全 combo が plan に存在
        unregistered = verify_markdown_combos(md, plan)
        # 警告セクション等の表示で出る場合があるので、本線/結論セクションのみ確認
        body = md.split("## 6. 本線")[1].split("---")[0]
        body_combos = set(re.findall(r"\b\d-\d-\d\b", body))
        plan_combos = plan.all_combos()
        body_unregistered = body_combos - plan_combos
        assert not body_unregistered, (
            f"本線/結論/実購入判断に未登録 combo: {body_unregistered}"
        )


# ---------------------------------------------------------------------------
# テンプレートフォールバック (render_prediction_v2)
# ---------------------------------------------------------------------------


class TestTemplateFallback:
    def test_fallback_when_llm_final_conclusion_has_unregistered_combo(self):
        """LLM の final_conclusion に矛盾 combo があっても、render_v2 では
        plan のみから出力される (LLM final_conclusion は無視される)。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
            final_conclusion="本線は 4-3-6, 3-4-6 を中心に据える。",  # 未登録
        )
        md = render_prediction_v2(pred, input_data=_input())
        # 結論文に未登録 combo が出ない
        conclusion_block = md.split("## 10. 最終結論")[1].split("##")[0]
        assert "4-3-6" not in conclusion_block
        assert "3-4-6" not in conclusion_block
        # 出るのは plan.final_best (1-2-3) だけ
        assert "1-2-3" in conclusion_block


# ---------------------------------------------------------------------------
# 静岡4R シナリオの回帰テスト
# ---------------------------------------------------------------------------


class TestShizuoka4rScenario:
    def test_shizuoka_4r_no_combo_injection(self):
        """静岡4R 再現: honsen に 2-5-3 / 2-5-4 / 5-2-4 / 2-5-7 があるのに
        LLM final_conclusion が 4-3-6 / 3-4-6 / 4-6-3 を主張するケース。
        render_v2 では 4-3-6 等が一切表示されない。"""
        pred = _pred(
            honsen=[
                _bet("2-5-3", market_odds=12.0, value_label="妙味あり"),
                _bet("2-5-4", market_odds=18.0, value_label="妙味あり"),
            ],
            osae=[
                _bet("5-2-4", market_odds=126.0, value_label="穴として少額",
                     category="押さえ"),
                _bet("2-5-7", market_odds=3.5, value_label="本線向き",
                     category="押さえ"),  # 安い人気筋 (cheap)
            ],
            ana=[
                _bet("5-2-4", market_odds=126.0, value_label="穴として少額",
                     category="穴"),
            ],
            final_conclusion=(
                "本線では4-3-6、3-4-6、4-6-3 を中心に据える。"  # LLM の捏造
            ),
            marks={"◎": 2, "◯": 5},
        )
        md = render_prediction_v2(pred, input_data=_input())
        # 結論文と実購入判断には LLM が捏造した combo が一切出ない
        body = md.split("## 10. 最終結論")[1].split("---")[0]
        for bad in ("4-3-6", "3-4-6", "4-6-3"):
            assert bad not in body, (
                f"LLM 捏造 combo {bad} が結論部に出ている:\n{body}"
            )
        # plan に登録された 2-5-3 / 2-5-4 が出る
        plan = build_output_plan(pred, _input())
        all_combos = plan.all_combos()
        # 結論文に出る combo は plan の combo に含まれる
        conclusion_combos = set(re.findall(r"\b\d-\d-\d\b", body))
        rogue = conclusion_combos - all_combos
        assert not rogue, (
            f"結論部に未登録 combo: {rogue}\n本文: {body}"
        )

    def test_v2_full_markdown_no_unregistered_combo_anywhere(self):
        """837b8ee 後続レビュー反映: v2 Markdown 全体 (整合性フッタ含む) に
        未登録 combo (4-3-6 / 3-4-6 / 4-6-3) が一切混入しない。

        ※ LLM の summary 等にも combo が含まれる場合は別途サニタイズが必要。
        本テストは final_conclusion 経路の混入を完全排除することを担保する。
        """
        from app.cli import render_prediction_v2
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
            ],
            final_conclusion=(
                "本線では 4-3-6, 3-4-6, 4-6-3 を中心に据える。"  # LLM 捏造
            ),
            # summary は装飾文として通すが、combo を含めない (ユーザー要件: pred.final_conclusion
            # に仕込む。装飾文サニタイズは別テーマ)
        )
        md = render_prediction_v2(pred, input_data=_input())
        # final_conclusion 経由の未登録 combo は完全排除される
        # 結論部 (10.) + 押さえ/穴/大穴/実購入判断 + 整合性フッタ
        # まで含めて 4-3-6 等が出ないこと
        for bad in ("4-3-6", "3-4-6", "4-6-3"):
            assert bad not in md, (
                f"v2 Markdown に LLM 捏造 combo {bad} が残存:\n"
                f"--- 該当周辺 ---\n"
                f"{md[max(0, md.find(bad) - 80):md.find(bad) + 80] if bad in md else ''}"
            )

    def test_v2_renderer_uses_new_final_conclusion_format(self):
        """final_best ありで「一番買いたい買い目は ...」フォーマットになる。"""
        from app.cli import render_prediction_v2
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
        )
        md = render_prediction_v2(pred, input_data=_input())
        conclusion = md.split("## 10. 最終結論")[1].split("\n##")[0]
        assert "一番買いたい買い目は" in conclusion, (
            f"新フォーマットが適用されていない:\n{conclusion}"
        )

    def test_render_final_conclusion_osae_only_format(self):
        """単体テスト: final_best 空 + final_osae あり → 「本線はオッズ確認後の
        判断とし、押さえるべき買い目は ...」フォーマット。render_final_conclusion
        を直接呼び出して挙動を確認する (build_output_plan 経由は best_bets
        昇格ルールがあるため、osae のみの状態を build から自然に作るのは困難)。
        """
        plan = OutputPlan(
            final_best=[],
            final_osae=[
                _bet("4-5-6", market_odds=15.0, value_label="妙味あり"),
            ],
        )
        text = render_final_conclusion(plan)
        assert "本線はオッズ確認後の判断" in text, (
            f"final_best 空時のフォーマットになっていない:\n{text}"
        )
        assert "4-5-6" in text
        # 「本線は X を中心に据える」は出ない (osae を本線扱いしない)
        assert "本線は 4-5-6 を中心に据える" not in text
        assert "一番買いたい買い目は" not in text

    def test_gami_memo_combo_not_treated_as_unregistered(self):
        """ba87962 後続レビュー反映: gami_memo の自然文 combo (「前回 4-3-6 は
        買わずに失敗」等) で fallback が誤発動しない。"""
        from app.cli import render_prediction_v2
        from app.markdown_renderer import verify_markdown_combos
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
        )
        pred.gami_memo = "前回 4-3-6 は買わずに失敗、押さえに足すべきだった"
        md = render_prediction_v2(pred, input_data=_input())
        # fallback marker が Markdown に残らない (誤発動していない)
        assert "MARKDOWN_COMBO_UNREGISTERED" not in md, (
            f"gami_memo の自然文 combo で fallback 誤発動:\n{md[-500:]}"
        )
        assert "MARKDOWN_FALLBACK_LEAKED" not in md
        # gami_memo の文字列はそのまま残る
        assert "4-3-6" in md
        # verify_markdown_combos が unregistered を返さない
        plan = build_output_plan(pred, _input())
        unreg = verify_markdown_combos(md, plan)
        assert "4-3-6" not in unreg, (
            f"gami_memo の自然文 combo を verify が未登録扱い: {unreg}"
        )

    def test_reflection_points_combo_not_treated_as_unregistered(self):
        """reflection_points の自然文 combo (「3-4-6 を切った反省」等) で
        fallback が誤発動しない。"""
        from app.cli import render_prediction_v2
        from app.markdown_renderer import verify_markdown_combos
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
        )
        pred.reflection_points = [
            "3-4-6 を切った反省: 別線2着上がりを軽視",
            "次回は別線番手を厚く扱う",
        ]
        md = render_prediction_v2(pred, input_data=_input())
        assert "MARKDOWN_COMBO_UNREGISTERED" not in md
        assert "MARKDOWN_FALLBACK_LEAKED" not in md
        assert "3-4-6" in md
        plan = build_output_plan(pred, _input())
        unreg = verify_markdown_combos(md, plan)
        assert "3-4-6" not in unreg

    def test_both_gami_memo_and_reflection_excluded_from_verify(self):
        """gami_memo + reflection_points の両方に未登録 combo があっても OK。"""
        from app.cli import render_prediction_v2
        from app.markdown_renderer import verify_markdown_combos
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
        )
        pred.gami_memo = "前回 4-3-6 は買わずに失敗"
        pred.reflection_points = ["3-4-6 を切った反省"]
        md = render_prediction_v2(pred, input_data=_input())
        # 両方の自然文 combo が Markdown に残る (装飾文として尊重)
        assert "4-3-6" in md and "3-4-6" in md
        # fallback は発動していない
        assert "MARKDOWN_COMBO_UNREGISTERED" not in md
        assert "MARKDOWN_FALLBACK_LEAKED" not in md
        # verify でも未登録扱いされない
        plan = build_output_plan(pred, _input())
        unreg = verify_markdown_combos(md, plan)
        assert unreg == set() or ("4-3-6" not in unreg and "3-4-6" not in unreg), (
            f"装飾文 combo が未登録扱いされている: {unreg}"
        )

    def test_render_final_conclusion_includes_ana_and_gami(self):
        """final_ana / gami_warning がそれぞれ追記される。"""
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
            final_ana=[
                _bet("7-8-9", market_odds=80.0, value_label="妙味あり"),
            ],
            gami_warning=[
                _bet("2-1-3", market_odds=3.5, value_label="本線向き"),
            ],
        )
        text = render_final_conclusion(plan)
        assert "一番買いたい買い目は 1-2-3" in text
        assert "少額で足す穴は 7-8-9" in text
        assert "安い人気筋・ガミ注意は 2-1-3" in text
        assert "厚く買わない" in text

    def test_ana_to_shogaku_not_in_final_best(self):
        """穴として少額 (5-2-4 126倍級) は final_best には入らない。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            ],
            ana=[
                _bet("5-2-4", market_odds=126.0, value_label="穴として少額",
                     category="穴"),
            ],
        )
        plan = build_output_plan(pred, _input())
        best_combos = {b.combination for b in plan.final_best}
        assert "5-2-4" not in best_combos
        # final_ana (small_longshots) には入る
        ana_combos = {b.combination for b in plan.final_ana}
        assert "5-2-4" in ana_combos


# ---------------------------------------------------------------------------
# codex review 反映: 回帰テスト
# ---------------------------------------------------------------------------


class TestCodexReviewFixes:
    def test_line_text_combo_not_false_positive(self):
        """lines_text に並び表記の数字列があってもフォールバックが誤発動しない。

        例: lines_text = "[本命] 1-2-3 / [別線] 4-5-6"
        plan に 1-2-3 や 4-5-6 が無くても、verify は本線セクション以降だけ
        見るので誤発動しない。
        """
        pred = _pred(
            honsen=[
                _bet("7-8-9", market_odds=10.0, value_label="妙味あり"),
            ],
        )
        # 並び表記に 1-2-3 を含む状況を模擬
        pred.lines_text = "[本命] 1-2-3 / [別線] 4-5-6"
        md = render_prediction_v2(pred, input_data=_input())
        # フォールバック警告が出ない (verify が並び表記を無視)
        assert "MARKDOWN_COMBO_UNREGISTERED" not in md, (
            f"並び表記が誤検出されてフォールバックが発動した:\n"
            f"{md[:500]}"
        )
        # 並び表記は維持される
        assert "1-2-3" in md.split("## 6.")[0]

    def test_fallback_clears_final_conclusion_no_re_leak(self):
        """フォールバック後の Markdown に未登録 combo が残らない (再検証)。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=10.0, value_label="妙味あり")],
            final_conclusion="本線は 4-3-6, 3-4-6 を中心に据える。",
        )
        md = render_prediction_v2(pred, input_data=_input())
        # 結論部 (本線セクション以降〜フッタ前) に 4-3-6 / 3-4-6 が出ない
        body = md.split("## 6. 本線", 1)[1] if "## 6. 本線" in md else md
        if "\n---\n" in body:
            body = body.rsplit("\n---\n", 1)[0]
        assert "4-3-6" not in body and "3-4-6" not in body, (
            f"フォールバック後も未登録 combo が残存:\n{body}"
        )

    def test_sanitize_applied_before_build_output_plan(self):
        """sanitize → build の順で動く (穴馬→穴目 等の置換が plan に反映)。"""
        # reason に「穴馬」を含む買い目を honsen に置く
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="妙味あり",
                     reason="穴馬の頭差し"),
            ],
        )
        md = render_prediction_v2(pred, input_data=_input())
        # 「穴馬」は「穴目」にサニタイズされている
        assert "穴目" in md or "穴馬" not in md.split("## 6. 本線")[1].split("---")[0], (
            f"sanitize が build より先に適用されていない可能性"
        )
