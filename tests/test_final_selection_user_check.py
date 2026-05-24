"""final_selection 検証: ユーザー要望 7項目 (2026-05-24)。

各項目をテストで明示検証する。現状で通らなければ実装追加して通す。
"""

from __future__ import annotations

import re

import pytest

from app.cli import render_prediction
from app.final_selection import (
    BEST_BETS_MAX_RESTRICTED,
    build_final_selection,
)
from app.models import (
    BetRecommendation,
    OddsEntry,
    Prediction,
    RaceInput,
)


def _bet(combo, *, market_odds=None, value_label="", gami_risk=0.0,
         category="本線", reason="test"):
    return BetRecommendation(
        category=category, bet_type="3連単", combination=combo,
        reason=reason, gami_risk=gami_risk,
        market_odds=market_odds, value_label=value_label,
    )


def _pred(*, honsen=None, osae=None, ana=None, ooana=None,
          is_girls=False, final_conclusion=""):
    return Prediction(
        race_id="test", venue="テスト", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="", lines_text="",
        marks={},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion=final_conclusion,
        gami_memo="", reflection_points=[],
    )


def _input(*, class_name="A級一般", lines=None, odds=None):
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
            {"line_name": "別線", "cars": [4, 5]},
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 80.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "近畿"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# 要件1: odds取得済み少 + 展開上必須の本命ライン → must_cover に残る
# ---------------------------------------------------------------------------


class TestReq1MainLineRetained:
    def test_main_line_direct_kept_in_display_honsen_when_pool_full(self):
        """codex review 反映: best_bets 2点 + leftover 1点 + 本命ライン直行
        odds=None のケース。display_honsen は3点で切られるが、本命ライン直行は
        leftover より優先して保持される。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None,
                     reason="本命ライン: 先頭-番手-3番手 (直行)"),
            ],
            osae=[
                _bet("4-5-1", market_odds=15.0, value_label="妙味あり",
                     category="押さえ"),
                _bet("5-4-1", market_odds=18.0, value_label="妙味あり",
                     category="押さえ"),
                _bet("4-1-5", market_odds=22.0, value_label="本線向き",
                     category="押さえ"),  # leftover odds取得済み 1点
            ],
        )
        sel = build_final_selection(pred, _input())
        display_combos = [b.combination for b in sel.display_honsen]
        assert "1-2-3" in display_combos, (
            f"本命ライン直行 1-2-3 が display_honsen から落ちている: "
            f"{display_combos}\n"
            f"best={[b.combination for b in sel.best_bets]}\n"
            f"must_cover={[b.combination for b in sel.must_cover_bets]}"
        )

    def test_main_line_direct_kept_in_must_cover_when_no_odds(self):
        """本命ライン直行 1-2-3 が odds=None で honsen にあり、
        odds取得済みは別線 4-5-X のみという状況で、
        1-2-3 が must_cover_bets に残るか。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None,
                     reason="本命ライン: 先頭-番手-3番手 (直行)"),
            ],
            osae=[
                _bet("4-5-1", market_odds=15.0, value_label="妙味あり",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input())
        # best_bets には 4-5-1 が入る (odds取得済み)
        best_combos = {b.combination for b in sel.best_bets}
        assert "4-5-1" in best_combos
        # 1-2-3 (本命ライン直行) は must_cover_bets に残ってほしい
        must_cover_combos = {b.combination for b in sel.must_cover_bets}
        assert "1-2-3" in must_cover_combos, (
            f"本命ライン直行 1-2-3 (odds=None) が must_cover に残らない: "
            f"must_cover={must_cover_combos}\n"
            f"best={best_combos}\n"
            f"display_honsen={[b.combination for b in sel.display_honsen]}"
        )


# ---------------------------------------------------------------------------
# 要件2: best_bets 空時メッセージ
# ---------------------------------------------------------------------------


class TestReq2EmptyBestMessage:
    def test_empty_best_warning_message(self):
        """best_bets が空のとき、warnings に
        「オッズ取得済みで買える候補なし。オッズ確認後に判断」と出る。"""
        pred = _pred(
            honsen=[_bet("1-2-3", market_odds=None)],
        )
        sel = build_final_selection(pred, _input())
        assert sel.best_bets == [], "前提: best_bets は空"
        joined = " ".join(sel.warnings)
        assert "オッズ確認後" in joined or "オッズ取得済み" in joined, (
            f"best_bets 空時メッセージが warnings に出るべき: {sel.warnings}"
        )


# ---------------------------------------------------------------------------
# 要件3: 低オッズは cheap_popular_bets で best_bets に入らない
# ---------------------------------------------------------------------------


class TestReq3LowOddsExcluded:
    def test_low_odds_in_cheap_not_best(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=3.5, value_label="本線向き"),
                _bet("2-1-3", market_odds=10.0, value_label="本線向き"),
            ],
        )
        sel = build_final_selection(pred, _input())
        best_combos = {b.combination for b in sel.best_bets}
        cheap_combos = {b.combination for b in sel.cheap_popular_bets}
        assert "1-2-3" in cheap_combos
        assert "1-2-3" not in best_combos


# ---------------------------------------------------------------------------
# 要件4: ガールズで cheap_popular_bets が best_bets に混ざらない
# ---------------------------------------------------------------------------


class TestReq4GirlsCheapNotInBest:
    def test_girls_cheap_not_in_best(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=3.0, value_label="本線向き"),
                _bet("2-1-3", market_odds=12.0, value_label="本線向き"),
            ],
            is_girls=True,
        )
        ri = _input(class_name="ガールズ一般")
        sel = build_final_selection(pred, ri)
        best_combos = {b.combination for b in sel.best_bets}
        cheap_combos = {b.combination for b in sel.cheap_popular_bets}
        # ガールズでも低オッズは cheap_popular に分離
        assert "1-2-3" in cheap_combos
        assert "1-2-3" not in best_combos
        # ガールズの best_bets 上限 (1点) を守る
        assert len(sel.best_bets) <= BEST_BETS_MAX_RESTRICTED


# ---------------------------------------------------------------------------
# 要件5: 新人戦オッズ未取得だらけ → best_bets 無理に作らず分散
# ---------------------------------------------------------------------------


class TestReq5RookieNoOddsDistribute:
    def test_rookie_no_odds_does_not_force_best(self):
        """新人戦でオッズ全 None → best_bets は空、must_cover/watch_only に
        分散表示される。"""
        # 新人戦は class_name で判定
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None,
                     reason="本命ライン直行"),
                _bet("2-1-3", market_odds=None,
                     reason="本命番手頭"),
            ],
            osae=[
                _bet("4-5-1", market_odds=None,
                     reason="別線頭", category="押さえ"),
            ],
        )
        ri = _input(class_name="新人戦")
        sel = build_final_selection(pred, ri)
        # best_bets は無理に作らない (空 or 1点)
        assert len(sel.best_bets) <= BEST_BETS_MAX_RESTRICTED
        # 全部 odds=None なら best_bets は空
        assert sel.best_bets == [], (
            f"odds 全 None なら best_bets は空: "
            f"{[b.combination for b in sel.best_bets]}"
        )
        # 候補が must_cover や watch_only / display_honsen に出ている
        all_displayed = (
            sel.must_cover_bets + sel.watch_only_bets + sel.display_honsen
        )
        displayed_combos = {b.combination for b in all_displayed}
        # 少なくとも何点かは表示に残る
        assert displayed_combos, (
            f"odds全Noneでも何かしらの表示枠に残るべき: "
            f"must_cover={[b.combination for b in sel.must_cover_bets]}, "
            f"watch_only={[b.combination for b in sel.watch_only_bets]}, "
            f"display_honsen={[b.combination for b in sel.display_honsen]}"
        )


# ---------------------------------------------------------------------------
# 要件6: display_honsen と final_conclusion の整合
# ---------------------------------------------------------------------------


class TestReq6DisplayHonsenConclusionConsistent:
    def test_conclusion_matches_best_bets_via_display_honsen(self):
        """render_prediction 通過後、final_conclusion の本線推奨が
        display_honsen の先頭 (= best_bets の先頭) と一致。"""
        pred = _pred(
            honsen=[
                _bet("9-3-4", market_odds=7.2, value_label="妙味あり"),
                _bet("9-4-3", market_odds=9.5, value_label="妙味あり"),
                _bet("5-3-8", market_odds=None),
            ],
            osae=[
                _bet("3-5-8", market_odds=None, category="押さえ"),
            ],
            final_conclusion="本線は 5-3-8, 3-5-8 を中心に据える。",
        )
        ri = _input()
        out = render_prediction(pred, input_data=ri)
        # 結論文を抽出
        conclusion = out.split("## 10. 最終結論")[1].split("##")[0]
        m = re.search(r"本線は\s*([\d\-, ]+)を中心に据える", conclusion)
        assert m, "本線推奨文が見つからない"
        conclusion_combos = [c.strip() for c in m.group(1).split(",")]
        # 一番買いたいセクション先頭の combo
        top_section = out.split("### 一番買いたい買い目")[1].split("###")[0]
        top_lines = [
            ln for ln in top_section.split("\n") if ln.strip().startswith("- ")
        ]
        assert top_lines
        first_top = re.search(r"\d-\d-\d", top_lines[0]).group(0)
        # 結論文先頭 == 一番買いたい先頭
        assert conclusion_combos[0] == first_top, (
            f"結論文 {conclusion_combos[0]} と一番買いたい {first_top} が不一致"
        )


# ---------------------------------------------------------------------------
# 要件7: Prediction 本体の DB 保存用データが破壊されない
# ---------------------------------------------------------------------------


class TestReq7PredictionPreserved:
    def test_original_prediction_honsen_intact_after_render(self):
        """render_prediction を通しても、元の prediction.honsen が変更されない。"""
        original_honsen = [
            _bet("1-2-3", market_odds=10.0, value_label="妙味あり"),
            _bet("2-1-3", market_odds=12.0, value_label="妙味あり"),
            _bet("3-1-2", market_odds=None,
                 reason="本命番手頭"),
        ]
        original_osae = [
            _bet("4-5-1", market_odds=15.0, value_label="本線向き",
                 category="押さえ"),
        ]
        pred = _pred(
            honsen=original_honsen,
            osae=original_osae,
            final_conclusion="本線は 1-2-3 を中心に据える。",
        )
        # render 前のスナップショット
        before_honsen = [b.combination for b in pred.honsen]
        before_osae = [b.combination for b in pred.osae]
        # render 実行
        ri = _input()
        _ = render_prediction(pred, input_data=ri)
        # 元の Prediction が保持されている (in-place 上書きされていない)
        after_honsen = [b.combination for b in pred.honsen]
        after_osae = [b.combination for b in pred.osae]
        assert before_honsen == after_honsen, (
            f"prediction.honsen が render で破壊された:\n"
            f"before: {before_honsen}\nafter:  {after_honsen}"
        )
        assert before_osae == after_osae, (
            f"prediction.osae が render で破壊された:\n"
            f"before: {before_osae}\nafter:  {after_osae}"
        )
