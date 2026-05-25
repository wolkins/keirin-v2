"""Phase 12: ガールズ/新人戦の HeadBias 経路整合性テスト.

検証内容:
A. ガールズ新人 + HeadBias: market_head + girls_market が付く、line_*/separate_* なし
B. 男子新人 + HeadBias: market_* + rookie_position、line_*/separate_* なし
C. 通常戦 + HeadBias: 必要なら line_*/separate_* が付いてよい
D. ガールズ + market_odds<5: gami_warning + low_odds が自動付与
E. allow_line_logic=False で HeadBias 候補が watch_only に落ちない
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


def _ri(*, class_name, is_girls=False, lines=None, odds=None,
        recent_results=None, riders=None):
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-25",
                 "venue": "テスト", "race_no": 1,
                 "class_name": class_name, "start_time": "10:00",
                 "is_girls": is_girls},
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                    "wind_speed_mps": 2.0},
        "lines": lines or [{"line_name": f"L{i}", "cars": [i]}
                            for i in range(1, 8)],
        "riders": riders or [
            {"car_no": i, "name": f"R{i}", "score": 70.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 0,
             "mark": 0, "comment": "", "home_area": "南関東"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": recent_results or [],
    })


def _head_bias_odds():
    """1 番頭 5/5 集中の HeadBias シナリオ。"""
    return [
        {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.0},
        {"bet_type": "3連単", "combination": "1-3-5", "odds": 7.0},
        {"bet_type": "3連単", "combination": "1-5-7", "odds": 8.5},
        {"bet_type": "3連単", "combination": "1-7-2", "odds": 10.0},
        {"bet_type": "3連単", "combination": "1-4-6", "odds": 11.5},
    ]


# ---------------------------------------------------------------------------
# A. ガールズ新人 + HeadBias
# ---------------------------------------------------------------------------


class TestGirlsRookieHeadBias:
    def test_market_head_attached_without_line_tags(self):
        """ガールズ新人で HeadBias 集中頭候補に market_head + girls_market
        が付く、line_*/separate_* は付かない。"""
        ri = _ri(class_name="ガールズ新人決勝", is_girls=True,
                 odds=_head_bias_odds())
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 1 番頭の候補
        head1_bets = [
            b for bucket in bets.values() for b in bucket
            if b.combination.startswith("1-")
        ]
        assert len(head1_bets) >= 1
        market_head_bets = [
            b for b in head1_bets
            if "market_head" in (b.source_rules or [])
        ]
        assert len(market_head_bets) >= 1, (
            f"market_head タグが付いた1番頭候補が無い"
        )
        # ガールズなので girls_market が付く
        assert any(
            "girls_market" in (b.source_rules or [])
            for b in market_head_bets
        ), "girls_market タグが無い"
        # line_*/separate_* は付かない
        for b in market_head_bets:
            for tag in (b.source_rules or []):
                assert not tag.startswith("line_"), (
                    f"{b.combination} に line タグ {tag} が付いた"
                )
                assert not tag.startswith("separate_"), (
                    f"{b.combination} に separate タグ {tag} が付いた"
                )

    def test_head_bias_candidates_not_filtered_to_watch_only(self):
        """ガールズ新人で HeadBias 由来の market 候補が watch_only に
        落ちない (line_* タグが付いていないため filter されない)。"""
        ri = _ri(class_name="ガールズ新人決勝", is_girls=True,
                 odds=_head_bias_odds())
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("1-2-3", market_odds=5.0,
                     source_rules=["market_head", "market_popular",
                                   "girls_market"]),
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # 1-2-3 が honsen に残る (watch_only に落ちない)
        honsen_combos = [b.combination for b in plan.honsen]
        assert "1-2-3" in honsen_combos


# ---------------------------------------------------------------------------
# B. 男子新人 + HeadBias
# ---------------------------------------------------------------------------


class TestRookieHeadBias:
    def test_rookie_market_head_without_line_tags(self):
        ri = _ri(class_name="A級新人", odds=_head_bias_odds())
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        head1_bets = [
            b for bucket in bets.values() for b in bucket
            if b.combination.startswith("1-")
            and "market_head" in (b.source_rules or [])
        ]
        assert len(head1_bets) >= 1
        # rookie_position が付く
        assert any(
            "rookie_position" in (b.source_rules or [])
            for b in head1_bets
        ), "rookie_position タグが無い"
        # line_*/separate_* は付かない
        for b in head1_bets:
            for tag in (b.source_rules or []):
                assert not tag.startswith("line_"), (
                    f"{b.combination} に line タグ {tag} が付いた"
                )
                assert not tag.startswith("separate_"), (
                    f"{b.combination} に separate タグ {tag} が付いた"
                )


# ---------------------------------------------------------------------------
# C. 通常戦 + HeadBias (line タグ許可)
# ---------------------------------------------------------------------------


class TestNormalLineHeadBiasKeepsLineTags:
    def test_normal_line_head_bias_can_have_line_tags(self):
        """通常ライン戦の HeadBias 候補は line_* タグを持ってよい
        (本命ライン直行 1-2-3 など)。"""
        ri = _ri(
            class_name="A級一般",
            lines=[
                {"line_name": "本命", "cars": [1, 2, 3]},
                {"line_name": "別線", "cars": [5, 4, 6]},
                {"line_name": "単", "cars": [7]},
            ],
            odds=_head_bias_odds(),
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 1-2-3 は本命ライン直行 + market_head の両方が付く
        bet_123 = next(
            (b for bucket in bets.values() for b in bucket
             if b.combination == "1-2-3"),
            None,
        )
        assert bet_123 is not None
        tags = bet_123.source_rules or []
        assert "line_direct" in tags
        assert "market_head" in tags

    def test_normal_line_no_girls_market_tag(self):
        """通常戦では girls_market / rookie_position タグは付かない。"""
        ri = _ri(
            class_name="A級一般",
            lines=[
                {"line_name": "本命", "cars": [1, 2, 3]},
                {"line_name": "別線", "cars": [5, 4, 6]},
                {"line_name": "単", "cars": [7]},
            ],
            odds=_head_bias_odds(),
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        for bucket in bets.values():
            for b in bucket:
                tags = b.source_rules or []
                assert "girls_market" not in tags
                assert "rookie_position" not in tags


# ---------------------------------------------------------------------------
# D. ガールズ + market_odds < 5 (gami_warning / low_odds 自動付与)
# ---------------------------------------------------------------------------


class TestLowOddsAutoTagged:
    def test_low_odds_auto_tagged(self):
        """market_odds<5 の候補に gami_warning + low_odds が自動付与。"""
        ri = _ri(
            class_name="ガールズ", is_girls=True,
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 2.3},
                {"bet_type": "3連単", "combination": "1-3-5", "odds": 3.5},
                {"bet_type": "3連単", "combination": "1-5-7", "odds": 4.8},
                {"bet_type": "3連単", "combination": "1-7-2", "odds": 6.0},
                {"bet_type": "3連単", "combination": "1-4-6", "odds": 8.0},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 1-2-3 (odds=2.3<5) に gami_warning + low_odds が付く
        bet_123 = next(
            (b for bucket in bets.values() for b in bucket
             if b.combination == "1-2-3"),
            None,
        )
        assert bet_123 is not None
        tags = bet_123.source_rules or []
        assert "low_odds" in tags, f"low_odds 未付与: {tags}"
        assert "gami_warning" in tags, f"gami_warning 未付与: {tags}"
        assert "odds_available" in tags

    def test_odds_8_does_not_get_low_odds(self):
        """odds=8.0 は low_odds の閾値超え (5.0)。low_odds は付かない。"""
        ri = _ri(
            class_name="A級一般",
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        bet_123 = next(
            (b for bucket in bets.values() for b in bucket
             if b.combination == "1-2-3"),
            None,
        )
        assert bet_123 is not None
        tags = bet_123.source_rules or []
        assert "low_odds" not in tags
        assert "odds_available" in tags

    def test_odds_missing_tagged(self):
        """odds 取得なし → odds_missing タグが付く。"""
        ri = _ri(class_name="A級一般")  # odds=[]
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 何らかの候補に odds_missing が付く
        missing_bets = [
            b for bucket in bets.values() for b in bucket
            if "odds_missing" in (b.source_rules or [])
        ]
        assert len(missing_bets) >= 1


# ---------------------------------------------------------------------------
# E. codex P2 反映: 新規 _push_osae 経由にも個人戦タグ + 自動 odds タグ
# ---------------------------------------------------------------------------


class TestCodexP2NewPushOsaeTagging:
    def test_new_market_push_includes_individual_tag(self):
        """codex P2-1 反映: _ensure_market_focused_head_bets が新規 push
        する market 候補に個人戦タグ (girls_market / rookie_position) が
        merge される。_ensure_market_focused_head_bets を直接呼んで検証。"""
        from app.scoring import _ensure_market_focused_head_bets
        # 空の honsen/osae に新規 push される状態を作る
        ri = _ri(
            class_name="ガールズ", is_girls=True,
            odds=[
                {"bet_type": "3連単", "combination": "1-5-7", "odds": 5.0},
                {"bet_type": "3連単", "combination": "1-6-3", "odds": 7.0},
                {"bet_type": "3連単", "combination": "1-3-2", "odds": 8.5},
                {"bet_type": "3連単", "combination": "1-4-6", "odds": 10.0},
                {"bet_type": "3連単", "combination": "1-2-5", "odds": 11.5},
            ],
        )
        honsen: list[BetRecommendation] = []
        osae: list[BetRecommendation] = []
        added = _ensure_market_focused_head_bets(
            honsen, osae, input_data=ri,
        )
        assert added >= 1
        # 新規 push された 1 頭 candidate に girls_market タグが付く
        new_market_bets = [
            b for b in osae
            if "market_head" in (b.source_rules or [])
        ]
        assert len(new_market_bets) >= 1
        assert any(
            "girls_market" in (b.source_rules or [])
            for b in new_market_bets
        ), (
            f"girls_market タグが付いた新規 market 候補が無い: "
            f"{[(b.combination, b.source_rules) for b in new_market_bets]}"
        )

    def test_push_osae_low_odds_auto_tagged(self):
        """codex P2-2 反映: _push_osae で push される odds<5 candidate に
        low_odds + gami_warning が自動付与される。"""
        # HeadBias で 1 番頭の odds<5 候補を _push_osae 経由で push
        ri = _ri(
            class_name="A級一般",
            lines=[
                {"line_name": "本命", "cars": [5, 3, 6]},
                {"line_name": "別線", "cars": [2, 4]},
                {"line_name": "単1", "cars": [1]},
                {"line_name": "単7", "cars": [7]},
            ],
            odds=[
                # 1 番頭 5/5、odds<5
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 3.0},
                {"bet_type": "3連単", "combination": "1-3-5", "odds": 3.5},
                {"bet_type": "3連単", "combination": "1-5-7", "odds": 4.0},
                {"bet_type": "3連単", "combination": "1-7-2", "odds": 4.5},
                {"bet_type": "3連単", "combination": "1-4-6", "odds": 4.8},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 1 番頭の odds<5 候補に low_odds + gami_warning が付く
        low_odds_bets = [
            b for bucket in bets.values() for b in bucket
            if b.combination.startswith("1-")
            and "low_odds" in (b.source_rules or [])
            and "gami_warning" in (b.source_rules or [])
        ]
        assert len(low_odds_bets) >= 1, (
            f"low_odds + gami_warning タグ付き 1 番頭候補が無い"
        )
