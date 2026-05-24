"""平塚10R: ガールズ新人決勝 + 低品質 + 見送り寄り混入 の文言制御。

検証要件 (2026-05-24):
A. 平塚10R 相当 (is_girls + is_rookie + data_quality=low):
   - ガミ回避メモに "3-4-7(本線)" "3-4-2(本線)" が出ない
   - 「購入対象」が本文に出ない
   - 3-4-2 (本線向き) は「暫定候補」表記
   - 3-4-7 (見送り寄り) は「見送り寄り」または「ガミ注意」表記
   - 「オッズ確認後の本線候補」ではなく「オッズ確認後の上位候補」

B. 通常ライン戦 + 高品質:
   - 「本線向き」「実購入候補」を維持
"""

from __future__ import annotations

import re

import pytest

from app.cli import render_prediction_v2
from app.llm_client import _build_gami_memo
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInput,
)
from app.output_plan import build_output_plan
from app.output_validation import sanitize_low_quality_text


def _bet(combo, *, market_odds=None, value_label="", gami_risk=0.0,
         category="本線", reason="test"):
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=reason, gami_risk=gami_risk,
        market_odds=market_odds, value_label=value_label,
    )


def _pred(*, honsen=None, osae=None, ana=None, ooana=None,
          is_girls=True, gami_memo="", reflection_points=None,
          final_conclusion=""):
    return Prediction(
        race_id="test-hiratsuka10", venue="平塚", race_no=10,
        is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="", lines_text="",
        marks={},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion=final_conclusion,
        gami_memo=gami_memo,
        reflection_points=list(reflection_points or []),
    )


def _input_girls_rookie(*, odds=None):
    """ガールズ新人決勝 シナリオ。"""
    return RaceInput.model_validate({
        "race": {
            "race_id": "test-hiratsuka10", "date": "2026-05-24",
            "venue": "平塚", "race_no": 10,
            "class_name": "ガールズ新人決勝", "start_time": "16:30",
            "is_girls": True,
        },
        "weather": {
            "condition": "晴れ", "rain_mm_per_hour": 0.0,
            "wind_speed_mps": 2.0,
        },
        "lines": [
            {"line_name": "単", "cars": [i]} for i in range(1, 8)
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 70.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "南関東"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# 要件1: _build_gami_memo の見送り寄り別表記
# ---------------------------------------------------------------------------


class TestGamiMemoSeesMiokuri:
    def test_miokuri_yori_gets_dedicated_label(self):
        """value_label='見送り寄り' は (見送り寄り・ガミ注意) と表記される。"""
        candidate_bets = {
            "本線": [
                _bet("3-4-7", market_odds=9.3, value_label="見送り寄り",
                     gami_risk=0.7),
                _bet("3-4-2", market_odds=12.3, value_label="本線向き",
                     gami_risk=0.5),  # gami_risk<0.6 → gami_memo に入らない
            ],
            "押さえ": [],
        }
        memo = _build_gami_memo(candidate_bets)
        # 3-4-7 は「(見送り寄り・ガミ注意)」
        assert "3-4-7(見送り寄り・ガミ注意)" in memo, (
            f"見送り寄り専用ラベルが出ていない:\n{memo}"
        )
        # 3-4-7 を「(本線)」と呼ばない
        assert "3-4-7(本線)" not in memo

    def test_gami_risk_high_combo_gets_ganmi_label(self):
        """gami_risk>=0.8 は (ガミ注意) と表記。"""
        candidate_bets = {
            "本線": [
                _bet("1-2-3", market_odds=8.0, value_label="本線向き",
                     gami_risk=0.85),
            ],
            "押さえ": [],
        }
        memo = _build_gami_memo(candidate_bets)
        assert "1-2-3(ガミ注意)" in memo
        assert "1-2-3(本線)" not in memo


# ---------------------------------------------------------------------------
# 要件2: sanitize_low_quality_text の弱体化
# ---------------------------------------------------------------------------


class TestSanitizeLowQuality:
    def test_replaces_honsen_label_with_provisional(self):
        text = "3-4-2(本線): オッズ安め、ガミ警戒"
        out = sanitize_low_quality_text(text)
        assert "(暫定候補)" in out
        assert "(本線)" not in out

    def test_replaces_value_label_in_display(self):
        text = "  - 3連単 3-4-2 / test (12.3倍 / 本線向き)"
        out = sanitize_low_quality_text(text)
        assert "暫定候補" in out
        assert "本線向き" not in out

    def test_preserves_section_heading(self):
        """「## 6. 本線」のような大見出しは置換しない。"""
        text = "## 6. 本線\n\n**実購入候補** (最大3点):"
        out = sanitize_low_quality_text(text)
        # 大見出しは維持
        assert "## 6. 本線" in out


# ---------------------------------------------------------------------------
# 要件3: 平塚10R 統合テスト
# ---------------------------------------------------------------------------


class TestHiratsuka10rScenario:
    def _make_pred(self):
        """平塚10R 相当: ガールズ新人決勝 + low quality + 見送り寄り混入。"""
        return _pred(
            is_girls=True,
            honsen=[
                _bet("3-4-2", market_odds=12.3, value_label="本線向き",
                     gami_risk=0.5),
                _bet("3-4-7", market_odds=9.3, value_label="見送り寄り",
                     gami_risk=0.7),
                _bet("4-3-2", market_odds=None),
            ],
            gami_memo="\n".join([
                "- 3-4-7(本線): オッズ安め、ガミ警戒",
                "- 3-4-2(本線): やや低配当、点数を絞る",
            ]),
        )

    def test_no_honsen_label_in_gami_memo(self):
        """ガミ回避メモに「3-4-7(本線)」「3-4-2(本線)」が出ない。"""
        ri = _input_girls_rookie()
        pred = self._make_pred()
        md = render_prediction_v2(pred, input_data=ri)
        # ## 11. ガミ回避メモ セクションを抽出
        if "## 11." in md:
            gami_block = md.split("## 11.")[1].split("## 12.")[0]
            assert "3-4-7(本線)" not in gami_block, (
                f"ガミ回避メモに 3-4-7(本線) が残存:\n{gami_block}"
            )
            assert "3-4-2(本線)" not in gami_block, (
                f"ガミ回避メモに 3-4-2(本線) が残存:\n{gami_block}"
            )

    def test_no_purchase_target_in_body(self):
        """本文に「購入対象」が出ない。"""
        ri = _input_girls_rookie()
        pred = self._make_pred()
        md = render_prediction_v2(pred, input_data=ri)
        body = md.split("## 6. 本線", 1)[1].split("\n---\n")[0]
        # warning セクション以降は除外
        for sep in ("### 出力整合性チェック", "### OutputPlan 警告"):
            if sep in body:
                body = body.split(sep)[0]
        assert "購入対象" not in body, (
            f"ガールズ新人 + low quality で「購入対象」が本文に残存"
        )

    def test_honsen_kohho_replaced_with_jouri_kohho(self):
        """「オッズ確認後の本線候補」→「オッズ確認後の上位候補」(ガールズ/新人)。"""
        ri = _input_girls_rookie()
        pred = self._make_pred()
        md = render_prediction_v2(pred, input_data=ri)
        honsen_block = md.split("## 6. 本線")[1].split("## 7.")[0]
        # honsen_no_odds (4-3-2) があるので「オッズ確認後の」サブセクションが出る
        if "オッズ確認後" in honsen_block:
            assert "オッズ確認後の上位候補" in honsen_block, (
                f"ガールズ/新人で「オッズ確認後の上位候補」に書き換わるべき:\n"
                f"{honsen_block}"
            )
            # 本線候補と書かれない
            assert "オッズ確認後の本線候補" not in honsen_block


# ---------------------------------------------------------------------------
# 要件4: 通常ライン戦 + 高品質
# ---------------------------------------------------------------------------


class TestCodexReviewFixesHiratsuka10r:
    """codex review (2026-05-24, 509d501 後続) P2 修正の回帰テスト。"""

    def test_no_odds_value_label_is_also_weakened(self):
        """odds=None で表示される '(本線向き)' 形式も low coverage で置換される。"""
        text = "  - 3連単 4-3-2 / test (本線向き)"
        out = sanitize_low_quality_text(text)
        assert "(暫定候補)" in out
        assert "(本線向き)" not in out

    def test_sanitize_uses_rfind_for_warning_boundary(self):
        """LLM 本文や gami_memo に「### 出力整合性チェック」と同じ文字列が
        含まれていても、末尾の実際の警告セクションだけが保護される。"""
        # 本文に「### 出力整合性チェック」を含む LLM 出力を模擬
        ri = _input_girls_rookie()
        pred = _pred(
            is_girls=True,
            honsen=[
                _bet("3-4-2", market_odds=12.3, value_label="本線向き"),
            ],
            # gami_memo に「(本線)」を仕込む
            gami_memo="- 3-4-2(本線): オッズ安め",
            # reflection_points に「### 出力整合性チェック」と同じ文字列を仕込む
            reflection_points=[
                "### 出力整合性チェック を見直す",  # 紛らわしい文字列
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        # gami_memo の (本線) は (暫定候補) に置換される
        # (reflection_points の同名文字列に引っかかって境界が前にズレない)
        if "## 11." in md:
            gami_block = md.split("## 11.")[1].split("## 12.")[0]
            assert "(本線)" not in gami_block, (
                f"gami_memo の (本線) が置換されていない (boundary 誤検知):\n"
                f"{gami_block}"
            )


class TestNormalRaceHighQualityKeepsStrongText:
    def test_normal_high_quality_keeps_honsen_label(self):
        """通常ライン戦 + 高品質では「本線向き」「実購入候補」を許可。"""
        ri = RaceInput.model_validate({
            "race": {
                "race_id": "test-normal-10", "date": "2026-05-24",
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
        pred = _pred(
            is_girls=False,
            honsen=[
                _bet("1-2-3", market_odds=8.0, value_label="妙味あり"),
                _bet("2-1-3", market_odds=10.0, value_label="本線向き"),
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        body = md.split("## 6. 本線", 1)[1].split("\n---\n")[0]
        # 通常時は「本線向き」「実購入候補」がそのまま出る
        assert "実購入候補" in body or "本線向き" in body, (
            f"通常品質では強い表現が許可されるべき:\n{body[:800]}"
        )
        # 「暫定候補」は出ない (low_coverage warning が出ない)
        assert "暫定候補" not in body
