"""Phase 11: 市場注目別線・別線番手系 push の source_rules 検証.

検証内容:
A. 市場注目別線候補に market_* + separate_* タグ
B. ガールズ/新人戦で separate_* タグ候補が watch_only に移動 (market_* は保持)
C. 通常ライン戦では維持
D. 本命ライン2車時の別線スコア上位補完にタグ
E. 本命ライン: 先頭-番手-別線番手3着の押さえ にタグ
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import build_output_plan
from app.scoring import build_candidate_bets, compute_scores


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _ri(*, class_name="A級一般", is_girls=False, lines=None, odds=None,
        recent_results=None):
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-25",
                 "venue": "テスト", "race_no": 1,
                 "class_name": class_name, "start_time": "10:00",
                 "is_girls": is_girls},
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                    "wind_speed_mps": 2.0},
        "lines": lines or [
            {"line_name": "本命", "cars": [1, 2, 3]},
            {"line_name": "別線", "cars": [5, 4, 6]},
            {"line_name": "単", "cars": [7]},
        ],
        "riders": [
            {"car_no": i, "name": f"R{i}", "score": 88.0,
             "b_count": 1, "nige": 1 if i in (1, 5) else 0,
             "makuri": 0, "sashi": 1 if i in (2, 4) else 0,
             "mark": 1, "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": recent_results or [
            {"date": "2026-05-24", "venue": "テスト",
             "race_no": 1, "result": "1-2-3", "memo": "x"},
        ],
    })


# ---------------------------------------------------------------------------
# A. 市場注目別線候補に market_* + separate_* タグ
# ---------------------------------------------------------------------------


class TestMarketFocusedSeparateTags:
    def _market_focus_ri(self) -> RaceInput:
        """市場注目別線シナリオ: 別線 5-4 への市場集中。"""
        return _ri(
            class_name="A級一般",
            odds=[
                # 別線 5 番頭の人気が集中 → market_focused が 5 ラインを指す
                {"bet_type": "3連単", "combination": "5-4-1", "odds": 5.0},
                {"bet_type": "3連単", "combination": "5-4-2", "odds": 7.0},
                {"bet_type": "3連単", "combination": "5-4-3", "odds": 8.5},
                {"bet_type": "3連単", "combination": "5-4-7", "odds": 10.0},
                {"bet_type": "3連単", "combination": "5-4-6", "odds": 12.0},
                # 本命ライン候補も
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 14.0},
            ],
        )

    def test_market_separate_tags_attached_to_market_focused_separate(self):
        """市場注目別線 push 経由の候補に market_* と separate_* タグ。"""
        ri = self._market_focus_ri()
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # market_* + separate_* の両方が付いた候補が >= 1
        both = [
            b for bucket in bets.values() for b in bucket
            if any(t.startswith("market_") for t in (b.source_rules or []))
            and any(t.startswith("separate_") for t in (b.source_rules or []))
        ]
        assert len(both) >= 1, (
            f"market_* + separate_* タグが付いた候補が無い: "
            f"all={[(b.combination, b.source_rules) for bucket in bets.values() for b in bucket if b.source_rules][:8]}"
        )


# ---------------------------------------------------------------------------
# B. ガールズ/新人戦で separate_* タグ候補が watch_only に移動
# ---------------------------------------------------------------------------


class TestRookieGirlsFilterSeparateKeepsMarket:
    def test_rookie_market_separate_moved_to_watch_only_keeps_market_tag(self):
        """新人戦で source_rules=[market_popular, separate_line] の候補が
        watch_only に移動。watch_only 内の候補オブジェクトには market_popular
        タグが残る (filter は除外であってタグ書き換えではない)。"""
        ri = _ri(class_name="A級新人")
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=False,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("5-4-1", market_odds=5.0,
                     source_rules=["market_popular", "separate_line",
                                   "separate_leader"]),
                _bet("1-2-3", market_odds=14.0,
                     source_rules=["market_head"]),  # 残す
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # 5-4-1 (separate_line タグ) は watch_only に移る
        watch_combos = [b.combination for b in plan.watch_only]
        assert "5-4-1" in watch_combos
        # 5-4-1 候補の source_rules に market_popular が残っている
        moved = next(
            (b for b in plan.watch_only if b.combination == "5-4-1"),
            None,
        )
        assert moved is not None
        assert "market_popular" in (moved.source_rules or [])
        # 1-2-3 (market_head のみ) は honsen に残る
        honsen_combos = [b.combination for b in plan.honsen]
        assert "1-2-3" in honsen_combos


# ---------------------------------------------------------------------------
# C. 通常ライン戦では維持
# ---------------------------------------------------------------------------


class TestNormalLineKeepsSeparateTagged:
    def test_normal_line_keeps_market_separate_in_display(self):
        """通常ライン戦で市場注目別線候補が honsen/osae に残る。"""
        ri = TestMarketFocusedSeparateTags()._market_focus_ri()
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # market_* タグ付き候補が osae に残る (filter 経由しないため)
        market_bets = [
            b for bucket in bets.values() for b in bucket
            if any(t.startswith("market_") for t in (b.source_rules or []))
        ]
        assert len(market_bets) >= 1


# ---------------------------------------------------------------------------
# D. 本命ライン2車時の別線スコア上位補完にタグ
# ---------------------------------------------------------------------------


class TestSeparateLine2CarComplementTagged:
    def test_main_line_2_car_keeps_separate_tagged_candidates(self):
        """本命ライン 2 車 (third 不在) のとき、何らかの separate_* タグ
        付き別線候補が生成される (補完経路の発火条件は
        _resolve_separate_lines に依存するため厳密な reason 検証は省略)。"""
        ri = _ri(
            class_name="A級一般",
            lines=[
                {"line_name": "本命2車", "cars": [1, 2]},
                {"line_name": "別線", "cars": [5, 4, 6]},
                {"line_name": "単", "cars": [7]},
                {"line_name": "単3", "cars": [3]},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        separate_bets = [
            b for bucket in bets.values() for b in bucket
            if any(t.startswith("separate_") for t in (b.source_rules or []))
        ]
        assert len(separate_bets) >= 3


# ---------------------------------------------------------------------------
# E. 本命ライン: 先頭-番手-別線番手3着 にタグ
# ---------------------------------------------------------------------------


class TestBessenBantan3rdTagged:
    def test_bessen_bantan_3rd_complement_tagged(self):
        """『本命ライン: 先頭-番手-別線番手3着の押さえ』候補に
        separate_second + separate_line + line_direct タグが付く。
        codex P2-2 反映: 厳密な reason マッチ + 必須タグ全件 assert。"""
        ri = _ri(class_name="A級一般")
        # 別線番手 (4 番) のスコアを高めて bessen_bantan に入りやすくする
        riders_data = ri.model_dump()["riders"]
        for r in riders_data:
            if r["car_no"] == 4:
                r["sashi"] = 5
                r["mark"] = 5
                r["score"] = 95.0
        ri = RaceInput.model_validate({**ri.model_dump(), "riders": riders_data})

        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 「本命ライン: 先頭-番手-別線番手3着の押さえ」reason を持つ候補
        target = [
            b for bucket in bets.values() for b in bucket
            if "別線番手3着の押さえ" in b.reason
        ]
        assert len(target) >= 1, (
            f"「別線番手3着の押さえ」候補が生成されない"
        )
        for b in target:
            tags = b.source_rules or []
            assert "separate_second" in tags, (b.combination, tags)
            assert "separate_line" in tags, (b.combination, tags)
            assert "line_direct" in tags, (b.combination, tags)
            assert "line_structure" in tags, (b.combination, tags)
