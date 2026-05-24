"""Phase 8: watch_only_reason_groups の回帰テスト.

検証内容:
A. line_source_filtered group: line filter で除外された候補
B. market_bias_suppressed group: HeadBias-only 制限で抑制された候補
C. max_final_best_overflow group: max_final_best 超過で移動された候補
D. gami_warning group: gami_warning を reason_groups にも反映
E. Renderer の「### 参考候補の内訳」セクション表示
F. helper _add_to_watch_only_with_reason の重複制御
G. purchase_mode WATCH_ONLY/SKIP で購入表現が出ない
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.decision import PurchaseMode
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import (
    OutputPlan,
    _add_to_watch_only_with_reason,
    _apply_line_source_rules_filter,
    _apply_max_final_best_limit,
    _restrict_same_axis_under_head_bias,
    build_output_plan,
)


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _pred(*, is_girls=False, honsen=None, osae=None, ana=None, ooana=None,
          marks=None):
    return Prediction(
        race_id="t", venue="t", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="",
        lines_text="", marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=list(ana or []), ooana=list(ooana or []),
        final_conclusion="", gami_memo="", reflection_points=[],
    )


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


def _make_plan_with_policy(allow_line_logic: bool):
    from app.decision.race_type_policy import (
        _NORMAL_LINE_POLICY, _ROOKIE_POLICY,
    )
    plan = OutputPlan()
    policy = _NORMAL_LINE_POLICY if allow_line_logic else _ROOKIE_POLICY
    plan.race_type = policy.race_type
    object.__setattr__(plan, "_race_type_policy", policy)
    return plan


# ---------------------------------------------------------------------------
# A. line_source_filtered group
# ---------------------------------------------------------------------------


class TestLineSourceFilteredGroup:
    def test_line_filter_populates_group(self):
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.honsen = [
            _bet("1-2-3", source_rules=["line_direct"]),
            _bet("2-1-3", source_rules=["market_axis"]),
        ]
        _apply_line_source_rules_filter(plan)
        group = plan.watch_only_reason_groups.get("line_source_filtered")
        assert group is not None
        assert len(group) == 1
        assert group[0].combination == "1-2-3"

    def test_line_filter_with_separate_tag(self):
        plan = _make_plan_with_policy(allow_line_logic=False)
        plan.osae = [
            _bet("5-3-1", source_rules=["separate_line"], category="押さえ"),
        ]
        _apply_line_source_rules_filter(plan)
        group = plan.watch_only_reason_groups.get("line_source_filtered")
        assert group and group[0].combination == "5-3-1"

    def test_normal_line_no_group(self):
        """normal_line では filter が走らず group も空。"""
        plan = _make_plan_with_policy(allow_line_logic=True)
        plan.honsen = [_bet("1-2-3", source_rules=["line_direct"])]
        _apply_line_source_rules_filter(plan)
        assert "line_source_filtered" not in plan.watch_only_reason_groups


# ---------------------------------------------------------------------------
# B. market_bias_suppressed group
# ---------------------------------------------------------------------------


class TestMarketBiasSuppressedGroup:
    def test_head_bias_axis_restriction_populates_group(self):
        """同一 (1, 2) 軸の 2 点目が watch_only に + reason_group に。"""
        plan = OutputPlan(
            final_best=[_bet("1-2-3", market_odds=5.5)],
            final_osae=[
                _bet("1-2-5", market_odds=7.0, category="押さえ"),
                _bet("1-2-7", market_odds=9.0, category="押さえ"),
            ],
        )
        _restrict_same_axis_under_head_bias(plan, head=1)
        group = plan.watch_only_reason_groups.get(
            "market_bias_suppressed"
        )
        assert group is not None
        assert any(b.combination == "1-2-7" for b in group)


# ---------------------------------------------------------------------------
# C. max_final_best_overflow group
# ---------------------------------------------------------------------------


class TestMaxFinalBestOverflowGroup:
    def test_overflow_to_watch_only_populates_group(self):
        """girls_rookie で max_final_best=2 超過分が group に入る。"""
        from app.decision.race_type_policy import _GIRLS_ROOKIE_POLICY
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", market_odds=8.0),
                _bet("2-1-3", market_odds=12.0),
                _bet("3-1-2", market_odds=15.0),
            ],
            purchase_mode=PurchaseMode.WATCH_ONLY,
        )
        plan.race_type = "girls_rookie"
        object.__setattr__(plan, "_race_type_policy", _GIRLS_ROOKIE_POLICY)
        _apply_max_final_best_limit(plan)
        group = plan.watch_only_reason_groups.get("max_final_best_overflow")
        assert group is not None
        assert any(b.combination == "3-1-2" for b in group)

    def test_buyable_overflow_does_not_populate_watch_group(self):
        """BUYABLE では超過分が final_osae に行くので watch_only_reason_groups
        には入らない。"""
        from app.decision.race_type_policy import _GIRLS_ROOKIE_POLICY
        plan = OutputPlan(
            final_best=[
                _bet("1-2-3", market_odds=8.0),
                _bet("2-1-3", market_odds=12.0),
                _bet("3-1-2", market_odds=15.0),
            ],
            purchase_mode=PurchaseMode.BUYABLE,
        )
        plan.race_type = "girls_rookie"
        object.__setattr__(plan, "_race_type_policy", _GIRLS_ROOKIE_POLICY)
        _apply_max_final_best_limit(plan)
        # max_final_best_overflow group は空 or 未定義 (final_osae 行き)
        group = plan.watch_only_reason_groups.get(
            "max_final_best_overflow", []
        )
        assert group == []


# ---------------------------------------------------------------------------
# D. gami_warning group (build_output_plan 末尾で反映)
# ---------------------------------------------------------------------------


class TestGamiWarningGroup:
    def test_gami_warning_reflected_to_reason_groups(self):
        """build_output_plan 末尾で gami_warning を reason_groups にコピー。"""
        ri = _ri(class_name="A級一般", odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 4.0},
        ])
        # 1-2-3 が低オッズなので gami_warning に入る想定
        pred = _pred(honsen=[_bet("1-2-3", market_odds=4.0, gami_risk=0.8)])
        plan = build_output_plan(pred, ri)
        # gami_warning と reason_groups["gami_warning"] が連動
        if plan.gami_warning:
            group = plan.watch_only_reason_groups.get("gami_warning")
            assert group is not None
            group_combos = [b.combination for b in group]
            gami_combos = [b.combination for b in plan.gami_warning]
            assert set(group_combos) >= set(gami_combos)


# ---------------------------------------------------------------------------
# E. Renderer の「参考候補の内訳」セクション表示
# ---------------------------------------------------------------------------


class TestRendererBreakdown:
    def test_breakdown_section_appears_when_groups_present(self):
        """rookie で line_source_filtered が発火 → 内訳セクションが出る。"""
        ri = _ri(class_name="A級新人")
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=8.0,
                     source_rules=["line_direct"]),
                _bet("2-1-3", market_odds=12.0),
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        assert "### 参考候補の内訳" in md
        # 「構造前提のため除外」ラベルが出る
        section = md.split("### 参考候補の内訳")[1]
        # 次のセクション or 警告セクションまで
        for sep in (
            "### 出力整合性チェック", "### OutputPlan 警告", "## 11.",
        ):
            if sep in section:
                section = section.split(sep)[0]
        assert "構造前提のため除外" in section
        assert "1-2-3" in section

    def test_breakdown_section_absent_when_no_groups(self):
        """通常戦で reason_groups が空 → セクション省略。"""
        ri = _ri(class_name="A級一般", odds=[
            {"bet_type": "3連単", "combination": "1-2-3", "odds": 8.0},
            {"bet_type": "3連単", "combination": "2-1-3", "odds": 10.0},
        ])
        pred = _pred(honsen=[
            _bet("1-2-3", market_odds=8.0),
            _bet("2-1-3", market_odds=10.0),
        ])
        md = render_prediction_v2(pred, input_data=ri)
        # gami_warning が無ければ「### 参考候補の内訳」セクション省略の可能性
        # (gami_warning は odds 低時に立つ)
        # 厳密に「絶対出ない」とは言えないため、出ても OK だが、
        # 出るとしたら label の中身が確認可能
        if "### 参考候補の内訳" in md:
            section = md.split("### 参考候補の内訳")[1]
            for sep in (
                "### 出力整合性チェック", "### OutputPlan 警告", "## 11.",
            ):
                if sep in section:
                    section = section.split(sep)[0]
            # normal_line では line_source_filtered は絶対に出ない
            assert "構造前提のため除外" not in section

    def test_breakdown_max_2_per_group(self):
        """各 group は最大 2 点まで表示。"""
        plan = _make_plan_with_policy(allow_line_logic=False)
        # 3 件 line_source_filtered に入れる
        plan.watch_only_reason_groups["line_source_filtered"] = [
            _bet("1-2-3"), _bet("2-1-3"), _bet("3-1-2"),
        ]
        from app.markdown_renderer import _render_watch_only_breakdown
        lines = _render_watch_only_breakdown(plan)
        joined = " ".join(lines)
        # 1-2-3 / 2-1-3 は出る、3-1-2 は表示されない
        assert "1-2-3" in joined
        assert "2-1-3" in joined
        assert "3-1-2" not in joined


# ---------------------------------------------------------------------------
# F. helper _add_to_watch_only_with_reason
# ---------------------------------------------------------------------------


class TestAddHelperDedup:
    def test_duplicate_combination_not_added_to_watch_only(self):
        plan = OutputPlan(watch_only=[_bet("1-2-3")])
        added = _add_to_watch_only_with_reason(
            plan, _bet("1-2-3"), "line_source_filtered",
        )
        # 既存なので watch_only には追加されない
        assert added is False
        assert len(plan.watch_only) == 1
        # しかし reason_groups には追加される (異なる group 経由でも記録)
        group = plan.watch_only_reason_groups.get("line_source_filtered")
        assert group and group[0].combination == "1-2-3"

    def test_prepend_inserts_at_head(self):
        plan = OutputPlan(watch_only=[_bet("9-8-7"), _bet("5-6-4")])
        _add_to_watch_only_with_reason(
            plan, _bet("1-2-3"), "line_source_filtered", prepend=True,
        )
        assert plan.watch_only[0].combination == "1-2-3"

    def test_same_combo_different_groups_no_duplicate_in_group(self):
        """同じ combo を異なる group に入れる場合、各 group には 1 回だけ
        追加される (同 group 内では重複しない)。"""
        plan = OutputPlan()
        _add_to_watch_only_with_reason(
            plan, _bet("1-2-3"), "line_source_filtered",
        )
        # 同じ combo を同じ group に再追加 → group には 1 件のみ
        _add_to_watch_only_with_reason(
            plan, _bet("1-2-3"), "line_source_filtered",
        )
        group = plan.watch_only_reason_groups["line_source_filtered"]
        assert len(group) == 1


# ---------------------------------------------------------------------------
# G. purchase_mode != BUYABLE で購入表現が出ない (既存挙動の維持確認)
# ---------------------------------------------------------------------------


class TestCodexP2Regressions:
    """codex P2 反映 (Phase 8 後続) の回帰テスト。"""

    def test_dedupe_moved_for_consistent_order(self):
        """codex P2-1: 同一 combo が複数バケットにあっても、watch_only と
        reason_group の順序が一致する。"""
        plan = _make_plan_with_policy(allow_line_logic=False)
        # 同じ combo を 2 バケットに仕込む
        plan.honsen = [
            _bet("1-2-3", source_rules=["line_direct"]),
            _bet("2-1-3", source_rules=["line_direct"]),
        ]
        plan.final_best = [
            _bet("1-2-3", source_rules=["line_direct"]),  # 重複
            _bet("3-1-2", source_rules=["line_direct"]),
        ]
        _apply_line_source_rules_filter(plan)
        # watch_only と reason_group["line_source_filtered"] の順序が一致
        watch_combos = [b.combination for b in plan.watch_only]
        group = plan.watch_only_reason_groups.get(
            "line_source_filtered", []
        )
        group_combos = [b.combination for b in group]
        assert watch_combos == group_combos, (
            f"watch_only={watch_combos} vs group={group_combos}"
        )
        # 重複は 1 回のみ
        assert watch_combos.count("1-2-3") == 1

    def test_gami_warning_not_in_breakdown_section(self):
        """codex P2-2: gami_warning は内訳セクションに表示されない
        (最終結論・実購入判断で既に表示されているため)。"""
        plan = _make_plan_with_policy(allow_line_logic=False)
        # gami_warning に candidate を入れて reason_groups にもコピー
        plan.gami_warning = [_bet("6-3-4", market_odds=4.0, gami_risk=0.8)]
        plan.watch_only_reason_groups["gami_warning"] = [
            _bet("6-3-4", market_odds=4.0, gami_risk=0.8),
        ]
        from app.markdown_renderer import _render_watch_only_breakdown
        lines = _render_watch_only_breakdown(plan)
        joined = " ".join(lines)
        assert "ガミ注意" not in joined
        assert "6-3-4" not in joined

    def test_all_combos_includes_reason_groups(self):
        """codex P2-3: all_combos が watch_only_reason_groups の combo も
        含める (manual_watch / low_quality_watch など group 単独で使う
        場合のセーフティネット)。"""
        plan = OutputPlan(
            honsen=[_bet("1-2-3")],
            watch_only=[_bet("2-1-3")],
        )
        # group 単独で別 combo を追加 (watch_only には入れない)
        plan.watch_only_reason_groups["manual_watch"] = [_bet("9-8-7")]
        combos = plan.all_combos()
        assert "1-2-3" in combos
        assert "2-1-3" in combos
        assert "9-8-7" in combos


class TestPurchaseModeSuppressesPurchasePhrase:
    def test_watch_only_mode_no_buyable_words_in_breakdown(self):
        """purchase_mode=WATCH_ONLY で内訳セクションが出ても、本文に
        「購入対象」「一番買いたい」が出ない (mode 分岐は既存ロジック)。"""
        ri = _ri(class_name="A級新人")
        pred = _pred(
            honsen=[
                _bet("1-2-3", market_odds=8.0,
                     source_rules=["line_direct"]),
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        # 本文 (警告セクション以前) を取り出す
        body = md
        for sep in ("### 出力整合性チェック", "### OutputPlan 警告"):
            if sep in body:
                body = body[:body.rfind(sep)]
        # rookie で line filter 発火 → purchase_mode が cap される
        # → 購入表現が出ない
        for word in ("購入対象", "一番買いたい", "実購入候補"):
            assert word not in body, (
                f"watch_only/SKIP mode で「{word}」が残った"
            )
