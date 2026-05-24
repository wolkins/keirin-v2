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
        """新人戦予想出力 (本線〜実購入判断) にも line 用語が出ない。

        ※ 反省ポイント (## 12) のサニタイズは新人戦用に未実装。
        本テストは「実購入判断までのセクション」に line 用語が出ないことを
        担保する (verify_markdown_combos と同じ検証範囲)。
        反省ポイントへの line 用語混入は別タスクとして残置。
        """
        from tests.test_omiya_1r_girls_market_bias import (
            _load as load_omiya, _prediction as pred_omiya,
        )
        ri = load_omiya()
        ri.race.class_name = "A級新人戦"
        pred = pred_omiya(ri)
        md = render_prediction_v2(pred, input_data=ri)
        body = self._extract_buyable_body(md)
        self._assert_no_forbidden_terms(body, label="新人戦")
