"""ユーザー要件 (2026-05-24) の整合性ルールを global 回帰テストで担保する。

要件:
1. 静岡4R 再現: honsen に存在しない 4-3-6 / 3-4-6 / 4-6-3 が最終結論に出ない
2. best_bets に value_label="見送り寄り" が入らない
3. best_bets に gami_risk >= 0.8 が入らない
4. market_odds < 5 は best_bets ではなくガミ注意枠 (cheap_popular_bets) に隔離
5. market_odds is None だけで best_bets が埋まらない
6. market_odds is None はガミ注意 (cheap_popular_bets) にしない
7. ガールズ/新人戦で「番手」「本命ライン」等のライン表現が出ない
"""

from __future__ import annotations

import re

import pytest

from app.cli import render_prediction_v2
from app.final_selection import build_final_selection
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import build_output_plan


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
        race_id="test", venue="テスト", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="t", weather_text="w", lines_text="l",
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
            {"line_name": "単", "cars": [7]},
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 85.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "近畿"}
            for i in range(1, 8)
        ],
        "odds": [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# 要件1: 静岡4R 再現 (LLM 捏造 combo の排除)
# ---------------------------------------------------------------------------


class TestShizuoka4rRegression:
    def test_unregistered_combos_never_appear_in_conclusion(self):
        """honsen に 2-5-3 等しか無いのに LLM が 4-3-6 を主張 → 排除。"""
        pred = _pred(
            honsen=[
                _bet("2-5-3", market_odds=12.0, value_label="妙味あり"),
                _bet("2-5-4", market_odds=18.0, value_label="妙味あり"),
            ],
            osae=[
                _bet("5-2-4", market_odds=126.0, value_label="穴として少額",
                     category="押さえ"),
            ],
            final_conclusion="本線では 4-3-6, 3-4-6, 4-6-3 を中心に据える。",
        )
        md = render_prediction_v2(pred, input_data=_input())
        body = md.split("## 10. 最終結論")[1].split("\n---\n")[0]
        for bad in ("4-3-6", "3-4-6", "4-6-3"):
            assert bad not in body, (
                f"LLM 捏造 combo {bad} が結論部に残存:\n{body}"
            )


# ---------------------------------------------------------------------------
# 要件2-5: best_bets の絞り込み
# ---------------------------------------------------------------------------


class TestBestBetsExclusions:
    def test_miokuri_yori_excluded(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="見送り寄り"),
                _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
            ],
        )
        plan = build_output_plan(pred, _input())
        combos = {b.combination for b in plan.final_best}
        assert "1-2-3" not in combos
        assert "2-1-3" in combos

    def test_high_gami_risk_excluded(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, gami_risk=0.85,
                     value_label="妙味あり"),
                _bet("2-1-3", market_odds=12.0, gami_risk=0.3,
                     value_label="妙味あり"),
            ],
        )
        plan = build_output_plan(pred, _input())
        combos = {b.combination for b in plan.final_best}
        assert "1-2-3" not in combos
        assert "2-1-3" in combos

    def test_cheap_odds_segregated_to_gami_warning(self):
        """market_odds<5 は best_bets ではなく cheap_popular_bets。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=3.5, value_label="本線向き"),
                _bet("2-1-3", market_odds=12.0, value_label="本線向き"),
            ],
        )
        plan = build_output_plan(pred, _input())
        best_combos = {b.combination for b in plan.final_best}
        cheap_combos = {b.combination for b in plan.gami_warning}
        assert "1-2-3" in cheap_combos
        assert "1-2-3" not in best_combos

    def test_best_bets_empty_when_no_odds_present(self):
        """全 odds=None なら best_bets は空 (odds=None で埋めない)。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None, value_label=""),
                _bet("2-1-3", market_odds=None, value_label=""),
            ],
        )
        plan = build_output_plan(pred, _input())
        assert plan.final_best == []

    def test_none_odds_not_in_gami_warning(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None, value_label=""),
                _bet("2-1-3", market_odds=4.0, value_label="本線向き"),
            ],
        )
        plan = build_output_plan(pred, _input())
        cheap_combos = {b.combination for b in plan.gami_warning}
        assert "1-2-3" not in cheap_combos
        assert "2-1-3" in cheap_combos


# ---------------------------------------------------------------------------
# 要件7: ガールズ/新人戦の表現分離
# ---------------------------------------------------------------------------


class TestGirlsRookieTermSanitization:
    """ガールズ/新人戦の出力に「番手」「本命ライン」「別線番手」「ライン3番手」が出ない。

    codex review 反映: 「番手」を厳密にチェック (「3番手」「4番手」は
    別ロジックで対応するため、ここでは「番手単独」を見る)。
    """

    GIRLS_FORBIDDEN_STRICT = ("本命ライン", "別線番手", "ライン3番手")
    # 「番手」は厳格にしたいが「3番手/4番手」と区別が必要 → 正規表現で対応

    def _assert_no_forbidden_terms(self, body: str, *, label: str):
        # 厳格に禁止 (文字列マッチ)
        for term in self.GIRLS_FORBIDDEN_STRICT:
            assert term not in body, (
                f"{label} 出力に禁止用語「{term}」が含まれる:\n"
                f"{body[:600]}..."
            )
        # 「番手」単独 (= 直前に数字を伴わない) も禁止
        # 例: NG = 「先頭-番手」、OK = 「先頭-3番手」
        bare_bantan = re.findall(r"(?<!\d)番手(?!頭)", body)
        # 「番手頭」「3番手」は OK
        # bare_bantan が空でなければ「番手」単独が出ている
        assert not bare_bantan, (
            f"{label} 出力に「番手」単独が含まれる ({len(bare_bantan)}件):\n"
            f"{body[:600]}..."
        )

    def _extract_buyable_body(self, md: str) -> str:
        """本線〜実購入判断 (## 6 〜 ## 11 直前) を抽出する。

        ガミ回避メモ (## 11) と反省ポイント (## 12) は LLM/テンプレートの
        自然文として line 用語サニタイズが弱いため、本テストの対象外。
        (verify_markdown_combos と同じ検証範囲)
        """
        if "## 6. 本線" not in md:
            return md
        body = md.split("## 6. 本線", 1)[1]
        if "## 11. ガミ回避メモ" in body:
            body = body.split("## 11. ガミ回避メモ", 1)[0]
        elif "\n---\n" in body:
            body = body.rsplit("\n---\n", 1)[0]
        return body

    def test_girls_output_has_no_line_terms(self):
        """ガールズ予想出力 (本線〜実購入判断) に line 用語が出ない。"""
        from tests.test_omiya_1r_girls_market_bias import (
            _load as load_omiya, _prediction as pred_omiya,
        )
        ri = load_omiya()
        pred = pred_omiya(ri)
        md = render_prediction_v2(pred, input_data=ri)
        body = self._extract_buyable_body(md)
        self._assert_no_forbidden_terms(body, label="ガールズ")

    def test_rookie_output_has_no_line_terms(self):
        """新人戦予想出力 (本線〜実購入判断) にも line 用語が出ない。"""
        from tests.test_omiya_1r_girls_market_bias import (
            _load as load_omiya, _prediction as pred_omiya,
        )
        ri = load_omiya()
        ri.race.class_name = "A級新人戦"
        pred = pred_omiya(ri)
        md = render_prediction_v2(pred, input_data=ri)
        body = self._extract_buyable_body(md)
        self._assert_no_forbidden_terms(body, label="新人戦")

    def test_rookie_gami_memo_sanitized(self):
        """2026-05-24: 新人戦の gami_memo に line 用語があっても v2 で置換。

        gami_memo に「本命ライン」「番手差し」「別線番手」を仕込んでも、
        v2 出力では置換され、Markdown 全体に禁止語が残らない。
        """
        from tests.test_omiya_1r_girls_market_bias import (
            _load as load_omiya, _prediction as pred_omiya,
        )
        ri = load_omiya()
        ri.race.class_name = "A級新人戦"
        pred = pred_omiya(ri)
        pred.gami_memo = (
            "前回は本命ラインに寄せすぎた。"
            "番手差しと別線番手の押さえを増やすべきだった。"
        )
        md = render_prediction_v2(pred, input_data=ri)
        # gami_memo セクションを含めて Markdown 全体で禁止語チェック
        for term in self.GIRLS_FORBIDDEN_STRICT:
            assert term not in md, (
                f"新人戦の Markdown 全体に「{term}」が残存:\n"
                f"--- gami_memo 周辺 ---\n{md.split('## 11.')[1][:400] if '## 11.' in md else md[-400:]}"
            )

    def test_rookie_reflection_points_sanitized(self):
        """新人戦の reflection_points に line 用語があっても v2 で置換。

        「ライン3番手」「4番手流れ込み」「別線番手の2着上がり」等を含めても
        Markdown 全体に禁止語が出ない。
        """
        from tests.test_omiya_1r_girls_market_bias import (
            _load as load_omiya, _prediction as pred_omiya,
        )
        ri = load_omiya()
        ri.race.class_name = "A級新人戦"
        pred = pred_omiya(ri)
        pred.reflection_points = [
            "ライン3番手の伸びを軽視した反省",
            "4番手流れ込み候補を切ったのが致命的だった",
            "別線番手の2着上がりを軽視した",
        ]
        md = render_prediction_v2(pred, input_data=ri)
        for term in self.GIRLS_FORBIDDEN_STRICT:
            assert term not in md, (
                f"新人戦の reflection_points に「{term}」が残存:\n"
                f"--- ## 12 周辺 ---\n{md.split('## 12.')[1][:400] if '## 12.' in md else md[-400:]}"
            )
        # 「4番手」単独もチェック (「4位評価」「4位」に置換されているはず)
        if "## 12." in md:
            block = md.split("## 12.")[1].split("\n---\n")[0]
            assert "4番手" not in block, (
                f"reflection_points に「4番手」が残存:\n{block}"
            )

    def test_rookie_v2_full_markdown_no_forbidden_terms(self):
        """新人戦の Markdown 全体 (## 11 + ## 12 + フッタ含む) で禁止語ゼロ。

        本テストは検証範囲を Markdown 全体に広げて、ガミ回避メモ・反省ポイント
        + 整合性チェックのフッタも含めて line 用語が漏れないことを担保する。
        codex review 反映: validate_prediction_output のメッセージにも
        「ライン」が残らないこと (HONMEI_NOT_IN_HONSEN_TOP2 等) を見る。
        """
        from tests.test_omiya_1r_girls_market_bias import (
            _load as load_omiya, _prediction as pred_omiya,
        )
        ri = load_omiya()
        ri.race.class_name = "A級新人戦"
        pred = pred_omiya(ri)
        # gami_memo + reflection_points に故意に line 用語を仕込む
        pred.gami_memo = "本命ライン依存を避け、別線番手を厚く"
        pred.reflection_points = [
            "本命ライン番手を過信",
            "別線番手の2着上がりを軽視",
            "ライン3番手の伸びを軽視",
        ]
        md = render_prediction_v2(pred, input_data=ri)
        # Markdown 全体で禁止語ゼロ
        for term in self.GIRLS_FORBIDDEN_STRICT:
            assert term not in md, (
                f"新人戦 Markdown 全体に「{term}」が残存。\n"
                f"該当周辺: {md[max(0, md.find(term) - 80):md.find(term) + 80]}"
            )
        # codex review 反映: 「ライン」単独もフッタを含めて検出
        # (HONMEI_NOT_IN_HONSEN_TOP2 等の validate メッセージ漏れを防ぐ)
        # 「ラインナップ」「並び」等を avoid したい意図はないので
        # 「ライン」だけを検出
        assert "ライン" not in md, (
            f"新人戦 Markdown 全体に「ライン」単独が残存:\n"
            f"該当周辺: {md[max(0, md.find('ライン') - 100):md.find('ライン') + 100]}"
        )

    def test_render_v2_does_not_mutate_original_prediction_for_rookie(self):
        """8b56ba2 後続レビュー反映 (deep=True 副作用対策):
        render_prediction_v2 で sanitize が走っても、元の Prediction の
        BetRecommendation.reason / gami_risk が変更されない。

        理由:
        sanitize_prediction は honsen/osae/ana/ooana 内の
        BetRecommendation.reason / gami_risk を破壊的に書き換える。
        model_copy(deep=False) では BetRecommendation オブジェクトが
        共有されるため、shallow copy では元の pred も巻き込まれる。
        deep=True で守られていることを assert で担保する。
        """
        from app.cli import render_prediction_v2
        from app.models import RaceInput
        # 新人戦シナリオ + line 用語入りの reason を仕込む
        ri = _input(class_name="A級新人戦")
        pred = _pred(
            honsen=[
                _bet(
                    "1-2-3", market_odds=10.0, value_label="妙味あり",
                    reason="本命ラインの番手差し",
                ),
                # market_odds=None + gami_risk>0 (sanitize で 0 に補正される)
                _bet(
                    "2-1-3", market_odds=None,
                    reason="別線番手の絡み",
                    gami_risk=0.5,
                ),
            ],
            osae=[
                _bet(
                    "3-1-2", market_odds=15.0, value_label="本線向き",
                    reason="ライン3番手の伸び",
                    category="押さえ",
                ),
            ],
            ana=[
                _bet(
                    "4-5-6", market_odds=80.0, value_label="妙味あり",
                    reason="別線番手の頭",
                    category="穴",
                ),
            ],
            ooana=[
                _bet(
                    "5-4-6", market_odds=120.0,
                    reason="4番手流れ込み",
                    category="大穴",
                ),
            ],
        )
        # render 前のスナップショット (元の pred の状態)
        original_honsen_reasons = [b.reason for b in pred.honsen]
        original_osae_reasons = [b.reason for b in pred.osae]
        original_ana_reasons = [b.reason for b in pred.ana]
        original_ooana_reasons = [b.reason for b in pred.ooana]
        original_gami_risk_2_1_3 = pred.honsen[1].gami_risk  # 0.5
        # render_v2 実行
        md = render_prediction_v2(pred, input_data=ri)
        # 1. 出力 Markdown では line 用語が置換されている
        for term in self.GIRLS_FORBIDDEN_STRICT:
            assert term not in md, (
                f"render_v2 出力に「{term}」が残存 (サニタイズ失敗)"
            )
        # 2. 元の pred.honsen[*].reason は変更されない (deep=True 担保)
        after_honsen_reasons = [b.reason for b in pred.honsen]
        assert original_honsen_reasons == after_honsen_reasons, (
            f"元の pred.honsen.reason が render_v2 で変更された:\n"
            f"before: {original_honsen_reasons}\n"
            f"after:  {after_honsen_reasons}"
        )
        # 3. osae / ana / ooana も同様に保護される
        assert original_osae_reasons == [b.reason for b in pred.osae]
        assert original_ana_reasons == [b.reason for b in pred.ana]
        assert original_ooana_reasons == [b.reason for b in pred.ooana]
        # 4. line 用語が **元** の reason には残っている (置換されていない)
        assert "本命ライン" in pred.honsen[0].reason
        assert "別線番手" in pred.honsen[1].reason
        assert "ライン3番手" in pred.osae[0].reason
        # 5. market_odds=None の gami_risk も元の pred では保持される
        # (サニタイズで 0 に補正されるのは copy 側だけ)
        assert pred.honsen[1].gami_risk == original_gami_risk_2_1_3, (
            f"元の pred.honsen[1].gami_risk が変更された: "
            f"before={original_gami_risk_2_1_3}, "
            f"after={pred.honsen[1].gami_risk}"
        )

    def test_render_v2_does_not_mutate_original_prediction_for_normal(self):
        """通常戦 (非ガールズ非新人) でも shallow copy 副作用が無い。

        新人戦判定が False でも sanitize_prediction は穴馬→穴目 等の置換と
        market_odds=None の gami_risk 補正を行うため、deep=True が必要。
        """
        from app.cli import render_prediction_v2
        ri = _input()  # 通常 A級一般
        pred = _pred(
            honsen=[
                _bet(
                    "1-2-3", market_odds=None,
                    reason="穴馬を頭固定",  # 「穴馬」→「穴目」サニタイズ対象
                    gami_risk=0.7,  # market_odds=None なので 0 補正対象
                ),
            ],
        )
        original_reason = pred.honsen[0].reason
        original_gami = pred.honsen[0].gami_risk
        _ = render_prediction_v2(pred, input_data=ri)
        # 元の pred の reason / gami_risk は変わらない
        assert pred.honsen[0].reason == original_reason, (
            f"通常戦でも元の reason が変更された: "
            f"before={original_reason!r}, after={pred.honsen[0].reason!r}"
        )
        assert pred.honsen[0].gami_risk == original_gami

    def test_girls_sanitization_still_works(self):
        """既存のガールズサニタイズが新人戦対応で壊れていない。"""
        from tests.test_omiya_1r_girls_market_bias import (
            _load as load_omiya, _prediction as pred_omiya,
        )
        ri = load_omiya()
        # class_name はそのまま (ガールズ)
        pred = pred_omiya(ri)
        pred.gami_memo = "本命ラインの番手差しを優先"
        md = render_prediction_v2(pred, input_data=ri)
        for term in self.GIRLS_FORBIDDEN_STRICT:
            assert term not in md, (
                f"ガールズの既存サニタイズが壊れた: 「{term}」が残存"
            )
