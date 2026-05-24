"""final_selection レイヤーの 11ルール ユニットテスト。

各ルール (1-11) の deterministic な動作を最小 fixture で検証。
"""

from __future__ import annotations

import pytest

from app.final_selection import (
    BEST_BETS_MAX_RESTRICTED,
    FinalSelection,
    build_final_selection,
)
from app.models import (
    BetRecommendation,
    Line,
    OddsEntry,
    Prediction,
    RaceInfo,
    RaceInput,
    Rider,
    Weather,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bet(
    combo: str,
    *,
    market_odds=None,
    value_label="",
    gami_risk: float = 0.0,
    category: str = "本線",
    reason: str = "test",
) -> BetRecommendation:
    return BetRecommendation(
        category=category,
        bet_type="3連単",
        combination=combo,
        reason=reason,
        gami_risk=gami_risk,
        market_odds=market_odds,
        value_label=value_label,
    )


def _pred(
    *,
    honsen=None, osae=None, ana=None, ooana=None,
    is_girls: bool = False,
) -> Prediction:
    return Prediction(
        race_id="test", venue="テスト", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="", lines_text="",
        marks={},
        honsen=list(honsen or []),
        osae=list(osae or []),
        ana=list(ana or []),
        ooana=list(ooana or []),
        final_conclusion="", gami_memo="", reflection_points=[],
    )


def _input(
    *,
    odds=None,
    class_name: str = "A級一般",
    lines=None,
    riders=None,
) -> RaceInput:
    return RaceInput.model_validate({
        "race": {
            "race_id": "test",
            "date": "2026-05-24",
            "venue": "テスト",
            "race_no": 1,
            "class_name": class_name,
            "start_time": "10:00",
        },
        "weather": {
            "condition": "晴れ",
            "rain_mm_per_hour": 0.0,
            "wind_speed_mps": 2.0,
        },
        "lines": lines or [
            {"line_name": "本命", "cars": [1, 2, 3]},
            {"line_name": "別線", "cars": [4, 5]},
        ],
        "riders": riders or [
            {"car_no": i, "name": f"R{i}", "score": 80.0, "b_count": 0,
             "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "", "home_area": "近畿"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# ルール1: best_bets に market_odds=None だけを並べない
# ---------------------------------------------------------------------------


class TestRule1NoAllNoneInBest:
    def test_best_bets_prefers_odds_present(self):
        """odds取得済みがあるなら、odds=None より優先される。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None, value_label="本線向き"),
                _bet("2-1-3", market_odds=10.0, value_label="本線向き"),
            ],
        )
        sel = build_final_selection(pred, _input())
        assert sel.best_bets, "best_bets が空"
        assert any(b.market_odds is not None for b in sel.best_bets), (
            f"odds取得済みがあれば優先されるべき: {[b.combination for b in sel.best_bets]}"
        )

    def test_best_bets_empty_when_no_odds_available(self):
        """ルール1 厳密適用 (codex review): odds取得済みが1点も無いなら
        best_bets は空。代替は must_cover や warnings で提示する。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None, value_label=""),
                _bet("2-1-3", market_odds=None, value_label=""),
            ],
        )
        sel = build_final_selection(pred, _input())
        assert sel.best_bets == [], (
            f"全 odds=None なら best_bets は空にすべき (ルール1)。"
            f"実際: {[b.combination for b in sel.best_bets]}"
        )
        # 警告で「オッズ取得済みで買える候補なし」を通知する
        assert any(
            "オッズ取得済み" in w or "オッズ確認後" in w
            for w in sel.warnings
        ), (
            f"オッズ取得済みで買える候補なし警告が出るべき: {sel.warnings}"
        )


# ---------------------------------------------------------------------------
# ルール2: 見送り寄り を best_bets に入れない
# ---------------------------------------------------------------------------


class TestRule2ExcludeSayonara:
    def test_miokuri_yori_not_in_best_bets(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=3.5, value_label="見送り寄り"),
                _bet("2-1-3", market_odds=10.0, value_label="本線向き"),
            ],
        )
        sel = build_final_selection(pred, _input())
        combos = {b.combination for b in sel.best_bets}
        assert "1-2-3" not in combos, "見送り寄りは best_bets から除外"
        assert "2-1-3" in combos


# ---------------------------------------------------------------------------
# ルール3: gami_risk >= 0.8 を best_bets に入れない
# ---------------------------------------------------------------------------


class TestRule3ExcludeHighGami:
    def test_high_gami_not_in_best_bets(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=8.0, gami_risk=0.85, value_label="本線向き"),
                _bet("2-1-3", market_odds=10.0, gami_risk=0.3, value_label="本線向き"),
            ],
        )
        sel = build_final_selection(pred, _input())
        combos = {b.combination for b in sel.best_bets}
        assert "1-2-3" not in combos, "gami_risk>=0.8 は best_bets から除外"


# ---------------------------------------------------------------------------
# ルール4: honsen 全 odds=None → odds取得済みを must_cover_bets に昇格
# ---------------------------------------------------------------------------


class TestRule4PromoteToMustCover:
    def test_promote_odds_present_osae_when_honsen_all_no_odds(self):
        """honsen 全 odds=None → odds取得済み妙味は best_bets または
        must_cover_bets のいずれかに必ず昇格する。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None, value_label=""),
                _bet("2-1-3", market_odds=None, value_label=""),
            ],
            osae=[
                _bet("3-1-2", market_odds=15.0, value_label="妙味あり",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input())
        promoted = (
            {b.combination for b in sel.best_bets}
            | {b.combination for b in sel.must_cover_bets}
        )
        assert "3-1-2" in promoted, (
            f"odds取得済み妙味は best or must_cover に昇格すべき: {promoted}"
        )

    def test_no_promotion_when_honsen_has_odds(self):
        """honsen に odds取得済みがある場合は昇格不要。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=12.0, value_label="妙味あり"),
            ],
            osae=[
                _bet("3-1-2", market_odds=15.0, value_label="妙味あり",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input())
        # must_cover に 3-1-2 が「ルール4由来で」昇格する必要は無い
        # (ただし他ルールで入る可能性は許容)
        # best_bets には 1-2-3 が入る
        best_combos = {b.combination for b in sel.best_bets}
        assert "1-2-3" in best_combos


# ---------------------------------------------------------------------------
# ルール5: market_bias がある場合、偏りに合う odds取得済み買い目を最低1点残す
# ---------------------------------------------------------------------------


class TestRule5MarketBiasRetention:
    def test_focused_head_bet_retained(self):
        """3番頭4/5件集中 → 3番頭 odds取得済みを最低1点 final_selection に残す。"""
        # market_bias を発動させる odds 構成 (3番頭が4件)
        odds = [
            OddsEntry(bet_type="3連単", combination="3-1-2", odds=8.0),
            OddsEntry(bet_type="3連単", combination="3-2-1", odds=12.0),
            OddsEntry(bet_type="3連単", combination="3-4-5", odds=18.0),
            OddsEntry(bet_type="3連単", combination="3-1-5", odds=22.0),
            OddsEntry(bet_type="3連単", combination="1-3-4", odds=28.0),
        ]
        # honsen は別の買い目だけ
        pred = _pred(
            honsen=[
                _bet("4-5-1", market_odds=30.0, value_label="妙味あり"),
            ],
            osae=[
                _bet("3-1-2", market_odds=8.0, value_label="本線向き",
                     category="押さえ"),
                _bet("3-2-1", market_odds=12.0, value_label="本線向き",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input(odds=odds))
        # 3番頭 odds取得済みが best/must_cover のどこかに最低1点ある
        all_selected = sel.best_bets + sel.must_cover_bets
        has_bias_head_with_odds = any(
            b.combination and b.combination.startswith("3-")
            and b.market_odds is not None
            for b in all_selected
        )
        assert has_bias_head_with_odds, (
            f"市場偏り(3番頭) に合う odds取得済み買い目が残るべき: "
            f"best={[b.combination for b in sel.best_bets]}, "
            f"must_cover={[b.combination for b in sel.must_cover_bets]}"
        )


# ---------------------------------------------------------------------------
# ルール6: market_odds < 5 は cheap_popular_bets に分離
# ---------------------------------------------------------------------------


class TestRule6CheapSeparation:
    def test_cheap_odds_in_cheap_popular_not_best(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=3.2, value_label="本線向き"),
                _bet("2-1-3", market_odds=10.0, value_label="本線向き"),
            ],
        )
        sel = build_final_selection(pred, _input())
        cheap_combos = {b.combination for b in sel.cheap_popular_bets}
        best_combos = {b.combination for b in sel.best_bets}
        assert "1-2-3" in cheap_combos, "odds<5 は cheap_popular_bets に分離"
        assert "1-2-3" not in best_combos, "cheap は best_bets に含めない"


# ---------------------------------------------------------------------------
# ルール7: market_odds >= 20 はガミ注意 (cheap_popular_bets) にしない
# ---------------------------------------------------------------------------


class TestRule7HighOddsNotCheap:
    def test_high_odds_not_in_cheap_popular(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=25.0, value_label="妙味あり"),
                _bet("4-5-6", market_odds=80.0, value_label="妙味あり"),
            ],
        )
        sel = build_final_selection(pred, _input())
        cheap_combos = {b.combination for b in sel.cheap_popular_bets}
        assert "1-2-3" not in cheap_combos, "odds=25 はガミ注意ではない"
        assert "4-5-6" not in cheap_combos, "odds=80 はガミ注意ではない"


# ---------------------------------------------------------------------------
# ルール8: market_odds=None はガミ注意にしない
# ---------------------------------------------------------------------------


class TestRule8NoneOddsNotCheap:
    def test_none_odds_not_in_cheap_popular(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None, value_label=""),
            ],
        )
        sel = build_final_selection(pred, _input())
        cheap_combos = {b.combination for b in sel.cheap_popular_bets}
        assert "1-2-3" not in cheap_combos, "odds=None はガミ注意ではない"


# ---------------------------------------------------------------------------
# ルール9: ガールズ/新人戦は買い目を広げすぎない
# ---------------------------------------------------------------------------


class TestRule9GirlsRookieLimit:
    def test_girls_limits_best_bets_to_one(self):
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="本線向き"),
                _bet("2-1-3", market_odds=12.0, value_label="本線向き"),
                _bet("3-1-2", market_odds=15.0, value_label="本線向き"),
            ],
            is_girls=True,
        )
        ri = _input(class_name="ガールズ一般")
        sel = build_final_selection(pred, ri)
        assert len(sel.best_bets) <= BEST_BETS_MAX_RESTRICTED, (
            f"ガールズの best_bets は {BEST_BETS_MAX_RESTRICTED} 点まで。"
            f"実際: {len(sel.best_bets)}"
        )

    def test_normal_race_allows_two_best_bets(self):
        """ガールズ/新人戦でなければ通常上限 (2点)。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="本線向き"),
                _bet("2-1-3", market_odds=12.0, value_label="本線向き"),
            ],
            is_girls=False,
        )
        sel = build_final_selection(pred, _input())
        assert len(sel.best_bets) == 2


# ---------------------------------------------------------------------------
# ユーザー指定テスト: 統合シナリオ
# ---------------------------------------------------------------------------


class TestUserSpecScenarios:
    def test_zero_pct_honsen_odds_still_keeps_buyable_in_best_or_must_cover(self):
        """本線オッズ取得率0% でも、odds取得済み候補が best/must_cover に残る。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None),
                _bet("2-1-3", market_odds=None),
            ],
            osae=[
                _bet("3-1-2", market_odds=15.0, value_label="妙味あり",
                     category="押さえ"),
                _bet("3-2-1", market_odds=20.0, value_label="本線向き",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input())
        all_buyable = sel.best_bets + sel.must_cover_bets
        has_odds_bet = any(b.market_odds is not None for b in all_buyable)
        assert has_odds_bet, (
            f"本線odds=0%でも odds取得済み候補が best/must_cover に残るべき。"
            f"best={[b.combination for b in sel.best_bets]}, "
            f"must_cover={[b.combination for b in sel.must_cover_bets]}"
        )

    def test_market_bias_three_head_retention(self):
        """市場偏り3番頭 → 3番頭オッズ取得済み買い目が残る (詳細)。"""
        odds = [
            OddsEntry(bet_type="3連単", combination="3-1-2", odds=8.0),
            OddsEntry(bet_type="3連単", combination="3-2-1", odds=12.0),
            OddsEntry(bet_type="3連単", combination="3-4-5", odds=18.0),
            OddsEntry(bet_type="3連単", combination="3-1-5", odds=22.0),
            OddsEntry(bet_type="3連単", combination="1-3-4", odds=28.0),
        ]
        pred = _pred(
            honsen=[_bet("4-5-1", market_odds=40.0, value_label="妙味あり")],
            osae=[
                _bet("3-1-2", market_odds=8.0, value_label="本線向き",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input(odds=odds))
        all_selected = (
            sel.best_bets + sel.must_cover_bets + sel.cheap_popular_bets
        )
        # 3番頭のいずれかが選定されている (cheap でも OK)
        bias_combos = [
            b.combination for b in all_selected
            if b.combination and b.combination.startswith("3-")
        ]
        assert bias_combos, (
            f"市場偏り3番頭の買い目が final_selection に残るべき: "
            f"{[b.combination for b in all_selected]}"
        )

    def test_miokuri_yori_not_in_best_bets_strict(self):
        """見送り寄り is strictly excluded from best_bets."""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=3.5, value_label="見送り寄り"),
            ],
            osae=[
                _bet("4-5-1", market_odds=12.0, value_label="本線向き",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input())
        combos = {b.combination for b in sel.best_bets}
        assert "1-2-3" not in combos

    def test_none_odds_not_in_cheap_warning(self):
        """market_odds=None は cheap_popular_bets (ガミ注意枠) に入らない。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None, value_label=""),
                _bet("2-1-3", market_odds=4.0, value_label="本線向き"),
            ],
        )
        sel = build_final_selection(pred, _input())
        cheap_combos = {b.combination for b in sel.cheap_popular_bets}
        assert "1-2-3" not in cheap_combos
        assert "2-1-3" in cheap_combos  # こちらは odds=4<5 で cheap

    def test_honsen_and_purchase_judgement_no_contradiction(self):
        """display_honsen と best_bets が矛盾しない (重複/順序の整合)。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=10.0, value_label="本線向き"),
                _bet("2-1-3", market_odds=12.0, value_label="本線向き"),
                _bet("3-1-2", market_odds=15.0, value_label="本線向き"),
            ],
            osae=[
                _bet("4-5-1", market_odds=20.0, value_label="妙味あり",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input())
        # display_honsen の先頭は best_bets[0] と一致
        if sel.display_honsen and sel.best_bets:
            assert sel.display_honsen[0].combination == sel.best_bets[0].combination
        # display_osae は best_bets/must_cover_bets と重複しない
        osae_combos = {b.combination for b in sel.display_osae}
        purchase_combos = (
            {b.combination for b in sel.best_bets}
            | {b.combination for b in sel.must_cover_bets}
        )
        # display_osae は honsen と osae の差分 - 全選定済みを除外
        # best_bets/must_cover の combo が display_osae に出ない
        overlap = osae_combos & purchase_combos
        assert not overlap, (
            f"display_osae と best/must_cover の重複: {overlap}"
        )

    def test_girls_total_bet_count_limited(self):
        """ガールズ/新人戦は買い目数上限を守る。"""
        # 大量の honsen/osae を入れても、best_bets は 1点に制限
        pred = _pred(
            honsen=[
                _bet(f"1-2-{i}", market_odds=8.0 + i, value_label="本線向き")
                for i in range(3, 9)
            ],
            is_girls=True,
        )
        ri = _input(class_name="ガールズ一般")
        sel = build_final_selection(pred, ri)
        assert len(sel.best_bets) <= BEST_BETS_MAX_RESTRICTED


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


class TestWarnings:
    def test_low_odds_warning_emitted(self):
        """4点以上+odds<10 で低配当注意が warnings に入る。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=7.0, value_label="本線向き"),
                _bet("2-1-3", market_odds=9.0, value_label="本線向き"),
            ],
            osae=[
                _bet("3-1-2", market_odds=8.5, value_label="本線向き",
                     category="押さえ"),
                _bet("3-2-1", market_odds=11.0, value_label="本線向き",
                     category="押さえ"),
            ],
        )
        sel = build_final_selection(pred, _input())
        assert any("低配当注意" in w for w in sel.warnings), (
            f"低配当注意警告が出るべき: {sel.warnings}"
        )

    def test_all_no_odds_warning(self):
        """honsen 全 odds=None で best_bets が空なら警告が出る。"""
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=None),
                _bet("2-1-3", market_odds=None),
            ],
        )
        sel = build_final_selection(pred, _input())
        assert sel.best_bets == [], "ルール1 厳密適用で best_bets は空"
        assert any(
            "オッズ取得済み" in w or "オッズ確認後" in w
            for w in sel.warnings
        ), (
            f"オッズ取得済みで買える候補なし警告が出るべき: {sel.warnings}"
        )
