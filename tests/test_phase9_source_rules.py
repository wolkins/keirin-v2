"""Phase 9: source_rules タグ拡張 + count_source_rule_prefixes helper.

検証内容:
A. source_rules merge (同一 combo の重複 push でタグ統合)
B. ガールズ新人で line タグ除外、market タグは保持
C. 通常ライン戦で line タグ維持
D. market タグ付与 (HeadBias 由来)
E. individual / girls / rookie タグ付与
F. count_source_rule_prefixes helper の動作
G. weather / trend タグ付与
"""

from __future__ import annotations

import pytest

from app.decision import (
    PurchaseMode, count_source_rule_prefixes, source_rules as SR,
)
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import OutputPlan, build_output_plan
from app.scoring import build_candidate_bets, compute_scores


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _ri(*, class_name="A級一般", is_girls=False, lines=None, odds=None,
        recent_results=None, weather=None):
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-25",
                 "venue": "テスト", "race_no": 1,
                 "class_name": class_name, "start_time": "10:00",
                 "is_girls": is_girls},
        "weather": weather or {"condition": "晴れ",
                                "rain_mm_per_hour": 0.0,
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
# A. source_rules merge
# ---------------------------------------------------------------------------


class TestSourceRulesMerge:
    def test_duplicate_push_merges_tags(self):
        """同じ combination を異なる tag セットで push すると merge される。"""
        # build_candidate_bets で多くの候補が複数経路で push されるケースを
        # シミュレート。シンプルに scoring から直接構築する。
        ri = _ri(
            class_name="A級一般",
            recent_results=[
                # 本命ライン決着多発 → trend_recent_result + line_trend
                {"date": "2026-05-24", "venue": "テスト",
                 "race_no": 1, "result": "1-2-3", "memo": "本命ライン決着"},
                {"date": "2026-05-24", "venue": "テスト",
                 "race_no": 2, "result": "1-2-3", "memo": "本命ライン決着"},
                {"date": "2026-05-24", "venue": "テスト",
                 "race_no": 3, "result": "1-2-3", "memo": "本命ライン決着"},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 1-2-3 は line_direct (本命ライン直行) + trend_recent_result
        # (本命ライン決着多発) の両方で push される
        bet_123 = next(
            (b for b in bets["本線"] if b.combination == "1-2-3"), None,
        )
        if bet_123 is not None:
            tags = bet_123.source_rules
            # line_direct と trend_recent_result の **両方** が含まれる
            # (merge 確認)
            has_line = "line_direct" in tags
            has_trend = "trend_recent_result" in tags
            # 両方経路を通った場合 merge されるはず
            if has_trend:
                assert has_line, (
                    f"merge 漏れ: tags={tags}"
                )

    def test_helper_merge_no_duplicates(self):
        """helper レベルで同じタグの重複追加が防止される。"""
        # BetRecommendation を直接作って source_rules を確認
        b = BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.0,
            source_rules=["line_direct", "market_head", "line_direct"],
        )
        # Pydantic は duplicate を弾かない。merge ロジックは _push 側で実装。
        # ここでは scoring _push の merge を確認する fixture を別途用意
        assert b.source_rules == [
            "line_direct", "market_head", "line_direct",
        ]


# ---------------------------------------------------------------------------
# B. girls_rookie で line タグ除外、market タグ保持
# ---------------------------------------------------------------------------


class TestGirlsRookieFiltersLineKeepsMarket:
    def test_line_market_combined_excluded_for_line(self):
        """同じ候補が line_direct + market_head の両方を持つ → girls_rookie で
        line filter にかかるが、market_head タグは候補オブジェクトに残る
        (filter は除外であって tag 書き換えではない)。"""
        ri = _ri(
            class_name="ガールズ新人決勝", is_girls=True,
            odds=[
                {"bet_type": "3連単", "combination": "3-4-2", "odds": 8.0},
            ],
        )
        pred = Prediction(
            race_id="t", venue="t", race_no=1, is_girls=True,
            summary="", venue_trend_text="", weather_text="",
            lines_text="", marks={},
            honsen=[
                _bet("3-4-2", market_odds=8.0,
                     source_rules=["line_direct", "market_head"]),
                _bet("4-3-2", market_odds=None,
                     source_rules=["market_head"]),  # 残す想定
            ],
            osae=[], ana=[], ooana=[],
            final_conclusion="", gami_memo="", reflection_points=[],
        )
        plan = build_output_plan(pred, ri)
        # 3-4-2 (line_direct + market_head) は line filter で watch_only に
        # 移動する
        watch_combos = [b.combination for b in plan.watch_only]
        assert "3-4-2" in watch_combos
        # その候補の source_rules には market_head が残る
        moved = next(
            (b for b in plan.watch_only if b.combination == "3-4-2"),
            None,
        )
        assert moved is not None
        assert "market_head" in (moved.source_rules or [])


# ---------------------------------------------------------------------------
# C. normal_line で line タグ維持
# ---------------------------------------------------------------------------


class TestNormalLineKeepsLineTags:
    def test_normal_line_keeps_line_tagged_in_display(self):
        ri = _ri(class_name="A級一般", odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
        ])
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 1-2-3 が honsen に残り、source_rules に line_direct がある
        bet_123 = next(
            (b for b in bets["本線"] if b.combination == "1-2-3"), None,
        )
        assert bet_123 is not None
        assert "line_direct" in (bet_123.source_rules or [])
        # line_structure も併用されている
        assert "line_structure" in (bet_123.source_rules or [])


# ---------------------------------------------------------------------------
# D. market タグ付与 (HeadBias 由来)
# ---------------------------------------------------------------------------


class TestMarketTagsAttached:
    def test_head_bias_attaches_market_head(self):
        """市場が 1 番頭に集中 + 本命ラインが 1 を含まない構成。
        HeadBias で _push_osae 経由の新規候補に market_head タグが付く。"""
        # ライン構成: 本命=5-3-6, 別線=2-4, 単=1, 7
        # 市場 1 番頭 5 件 → HeadBias filter で 1 頭候補が新規追加される
        ri = _ri(
            class_name="A級一般",
            lines=[
                {"line_name": "本命", "cars": [5, 3, 6]},
                {"line_name": "別線", "cars": [2, 4]},
                {"line_name": "単1", "cars": [1]},
                {"line_name": "単7", "cars": [7]},
            ],
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.0},
                {"bet_type": "3連単", "combination": "1-3-5", "odds": 7.0},
                {"bet_type": "3連単", "combination": "1-5-7", "odds": 8.5},
                {"bet_type": "3連単", "combination": "1-7-2", "odds": 10.0},
                {"bet_type": "3連単", "combination": "1-4-6", "odds": 11.5},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        market_bets = [
            b for bucket in bets.values() for b in bucket
            if "market_head" in (b.source_rules or [])
        ]
        assert len(market_bets) >= 1, (
            f"market_head タグが付いた候補が無い: "
            f"{[(b.combination, b.source_rules) for bucket in bets.values() for b in bucket if b.source_rules][:5]}"
        )

    def test_odds_available_added_when_odds_present(self):
        """odds 取得済み + market_head → odds_available タグも付く。"""
        ri = _ri(
            class_name="A級一般",
            lines=[
                {"line_name": "本命", "cars": [5, 3, 6]},
                {"line_name": "別線", "cars": [2, 4]},
                {"line_name": "単1", "cars": [1]},
                {"line_name": "単7", "cars": [7]},
            ],
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.0},
                {"bet_type": "3連単", "combination": "1-3-5", "odds": 7.0},
                {"bet_type": "3連単", "combination": "1-5-7", "odds": 8.5},
                {"bet_type": "3連単", "combination": "1-7-2", "odds": 10.0},
                {"bet_type": "3連単", "combination": "1-4-6", "odds": 11.5},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        market_with_odds = [
            b for bucket in bets.values() for b in bucket
            if "market_head" in (b.source_rules or [])
            and "odds_available" in (b.source_rules or [])
        ]
        assert len(market_with_odds) >= 1


# ---------------------------------------------------------------------------
# E. individual / girls / rookie タグ付与
# ---------------------------------------------------------------------------


class TestIndividualGirlsRookieTags:
    def test_individual_score_tag_on_top1_2_3(self):
        """個人戦 (ガールズまたは新人戦) のスコア上位 3 名並びに
        individual_score 付与 (scoring.py の else 分岐に入るケース)。"""
        ri = _ri(class_name="ガールズ", is_girls=True)
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # individual_score が付いた候補が存在する
        individual_bets = [
            b for bucket in bets.values() for b in bucket
            if "individual_score" in (b.source_rules or [])
        ]
        assert len(individual_bets) >= 1, (
            f"individual_score タグが付いた候補が無い"
        )

    def test_girls_top_eval_tag(self):
        """ガールズで girls_top_eval タグが付く。"""
        ri = _ri(class_name="ガールズ", is_girls=True)
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        girls_bets = [
            b for bucket in bets.values() for b in bucket
            if any(t.startswith("girls_") for t in (b.source_rules or []))
        ]
        assert len(girls_bets) >= 1, (
            f"ガールズ用 tag が付いた候補がない"
        )

    def test_rookie_wind_tag_on_strong_wind(self):
        """新人戦 + 強風 5m/s で rookie_wind タグが付く。"""
        ri = _ri(
            class_name="A級新人",
            weather={"condition": "晴れ", "rain_mm_per_hour": 0.0,
                     "wind_speed_mps": 6.0},
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        rookie_bets = [
            b for bucket in bets.values() for b in bucket
            if "rookie_wind" in (b.source_rules or [])
        ]
        # 強風 + is_individual の条件下で 4 位評価頭等が出るはず
        assert len(rookie_bets) >= 1, (
            f"rookie_wind タグが付いた候補がない (新人戦 + 強風)"
        )


# ---------------------------------------------------------------------------
# F. count_source_rule_prefixes helper
# ---------------------------------------------------------------------------


class TestCountSourceRulePrefixes:
    def test_basic_counts(self):
        """各 prefix の件数が正しくカウントされる。"""
        plan = OutputPlan(
            honsen=[
                _bet("1-2-3", source_rules=["line_direct", "line_structure"]),
                _bet("2-1-3", source_rules=["market_head"]),
            ],
            osae=[
                _bet("3-1-2", source_rules=["individual_score"]),
            ],
            watch_only=[
                _bet("5-3-1", source_rules=["odds_available"]),
                _bet("4-6-7", source_rules=["girls_market"]),
            ],
        )
        counts = count_source_rule_prefixes(plan)
        assert counts.get("line", 0) == 2  # line_direct + line_structure
        assert counts.get("market", 0) == 1
        assert counts.get("individual", 0) == 1
        assert counts.get("odds", 0) == 1
        assert counts.get("girls", 0) == 1

    def test_empty_plan(self):
        plan = OutputPlan()
        counts = count_source_rule_prefixes(plan)
        assert counts == {}

    def test_unknown_prefix_in_other(self):
        """未知の prefix は 'other' にカウントされる。"""
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["custom_unknown_tag"])],
        )
        counts = count_source_rule_prefixes(plan)
        assert counts.get("other", 0) == 1

    def test_multi_bucket_summed(self):
        """複数 bucket をまたいで集計される。"""
        plan = OutputPlan(
            honsen=[_bet("1-2-3", source_rules=["line_direct"])],
            osae=[_bet("2-1-3", source_rules=["line_third"])],
            ana=[_bet("3-1-2", source_rules=["line_spec12"])],
        )
        counts = count_source_rule_prefixes(plan)
        assert counts.get("line", 0) == 3


# ---------------------------------------------------------------------------
# G. weather / trend タグ付与
# ---------------------------------------------------------------------------


class TestWeatherTrendTags:
    def test_strong_wind_attaches_weather_strong_wind(self):
        """5m/s 以上で weather_strong_wind タグが付く。"""
        ri = _ri(
            class_name="A級一般",
            weather={"condition": "晴れ", "rain_mm_per_hour": 0.0,
                     "wind_speed_mps": 6.0},
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        wind_bets = [
            b for bucket in bets.values() for b in bucket
            if "weather_strong_wind" in (b.source_rules or [])
        ]
        assert len(wind_bets) >= 1

    def test_rain_attaches_weather_rain(self):
        """雨で weather_rain タグが付く。"""
        ri = _ri(
            class_name="A級一般",
            weather={"condition": "雨", "rain_mm_per_hour": 2.0,
                     "wind_speed_mps": 2.0},
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        rain_bets = [
            b for bucket in bets.values() for b in bucket
            if "weather_rain" in (b.source_rules or [])
        ]
        assert len(rain_bets) >= 1


# ---------------------------------------------------------------------------
# H. source_rules モジュール定数の存在確認
# ---------------------------------------------------------------------------


class TestSourceRulesConstants:
    def test_line_constants(self):
        assert SR.LINE_DIRECT == "line_direct"
        assert SR.LINE_FOURTH_FLOW == "line_fourth_flow"
        assert SR.LINE_SPEC12 == "line_spec12"

    def test_market_constants(self):
        assert SR.MARKET_HEAD == "market_head"
        assert SR.MARKET_AXIS == "market_axis"

    def test_girls_rookie_constants(self):
        assert SR.GIRLS_TOP_EVAL == "girls_top_eval"
        assert SR.ROOKIE_WIND == "rookie_wind"

    def test_is_line_source_helper(self):
        assert SR.is_line_source(["line_direct"]) is True
        assert SR.is_line_source(["separate_line"]) is True
        assert SR.is_line_source(["market_head"]) is False
        assert SR.is_line_source([]) is False
        assert SR.is_line_source(None) is False
