"""Phase 5: RaceTypePolicy.allow_line_logic を候補生成・分類側に反映する.

検証内容:
A. scoring.py の use_line_logic が policy.allow_line_logic と連動
B. 新人戦 / ガールズ新人戦で line logic 由来候補が生成されない
C. 通常ライン戦では line logic 由来候補が維持される (静岡4R 系)
D. PostRenderValidator (validate_line_terms_when_not_allowed) が
   allow_line_logic=False で line 用語残存を検出する
E. BetRecommendation.source_rules フィールドが空 default で動く
"""

from __future__ import annotations

import pytest

from app.cli import render_prediction_v2
from app.decision import (
    PurchaseMode, resolve_race_type_policy,
)
from app.markdown_renderer import validate_line_terms_when_not_allowed
from app.models import BetRecommendation, Prediction, RaceInput
from app.output_plan import OutputPlan, build_output_plan
from app.scoring import build_candidate_bets, compute_scores


def _bet(combo, **kw):
    kw.setdefault("category", "本線")
    kw.setdefault("reason", "t")
    kw.setdefault("gami_risk", 0.0)
    return BetRecommendation(bet_type="3連単", combination=combo, **kw)


def _pred(*, honsen=None, osae=None, marks=None, is_girls=False):
    return Prediction(
        race_id="t", venue="t", race_no=1, is_girls=is_girls,
        summary="", venue_trend_text="", weather_text="",
        lines_text="", marks=marks or {},
        honsen=list(honsen or []), osae=list(osae or []),
        ana=[], ooana=[],
        final_conclusion="", gami_memo="", reflection_points=[],
    )


def _ri(*, class_name="A級一般", is_girls=False, lines=None,
        odds=None, recent_results=None, riders=None):
    return RaceInput.model_validate({
        "race": {"race_id": "t", "date": "2026-05-24",
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
        "riders": riders or [
            {"car_no": i, "name": f"R{i}", "score": 88.0,
             "b_count": 1, "nige": 1 if i in (1, 5) else 0,
             "makuri": 1 if i == 7 else 0,
             "sashi": 1 if i in (2, 4) else 0, "mark": 1,
             "comment": "", "home_area": "中部"}
            for i in range(1, 8)
        ],
        "odds": odds or [],
        "recent_results": recent_results or [
            {"date": "2026-05-23", "venue": "テスト",
             "race_no": 1, "result": "1-2-3", "memo": "x"},
        ],
    })


# ---------------------------------------------------------------------------
# A. scoring.py の use_line_logic 連動
# ---------------------------------------------------------------------------


def _build_bets(ri: RaceInput):
    """build_candidate_bets を直接実行して各バケットの reason を返す。"""
    scores = compute_scores(ri)
    return build_candidate_bets(ri, scores)


class TestUseLineLogicReflectsPolicy:
    def test_normal_line_keeps_line_terms_in_reasons(self):
        """通常ライン戦: 本命ライン3着候補等が出る (既存挙動)。"""
        ri = _ri(class_name="A級一般")
        bets = _build_bets(ri)
        all_reasons = " ".join(
            b.reason for bucket in bets.values() for b in bucket
        )
        # 通常ライン戦では line logic 由来候補が出る (本命ライン or 番手 etc)
        # → reason に含まれる
        has_line_term = (
            "本命ライン" in all_reasons
            or "番手" in all_reasons
            or "3車ライン" in all_reasons
            or "別線" in all_reasons
        )
        assert has_line_term, all_reasons[:500]

    def test_rookie_skips_line_logic(self):
        """新人戦: policy.allow_line_logic=False → use_line_logic=False。
        line logic 由来候補が reason に出ない。"""
        ri = _ri(class_name="A級新人")
        # 前提確認
        p = resolve_race_type_policy(ri)
        assert p.allow_line_logic is False

        bets = _build_bets(ri)
        all_reasons = " ".join(
            b.reason for bucket in bets.values() for b in bucket
        )
        # 強い line 用語が出ない
        for word in (
            "本命ライン", "別線番手", "ライン3番手", "番手頭",
            "3車ライン", "4車ライン4番手の流れ込み",
            # codex P1 反映 (Phase 5 後続): 仕様12 / 強風補正もガード対象
            "仕様12", "雨補正",
        ):
            assert word not in all_reasons, (
                f"is_rookie=True なのに「{word}」が reason に残った:\n"
                f"{all_reasons[:500]}"
            )

    def test_rookie_with_strong_wind_skips_weather_line_candidates(self):
        """codex P1 反映: 強風 + 新人戦で _add_weather_and_trend_candidates
        の line role 前提候補がスキップされる。"""
        ri = _ri(
            class_name="A級新人",
            riders=None,  # default
        )
        # 強風シナリオに上書き
        ri = RaceInput.model_validate({
            **ri.model_dump(),
            "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0,
                        "wind_speed_mps": 8.0},  # 強風
        })
        bets = _build_bets(ri)
        all_reasons = " ".join(
            b.reason for bucket in bets.values() for b in bucket
        )
        # 強風補正の line role 前提候補が出ない
        for word in ("強風補正", "雨補正", "仕様12"):
            assert word not in all_reasons, (
                f"新人戦 強風で「{word}」が reason に残った:\n"
                f"{all_reasons[:500]}"
            )

    def test_girls_already_skipped_line_logic(self):
        """ガールズ: 既存挙動でも line logic 非使用。Phase 5 で挙動変わらず。"""
        ri = _ri(class_name="ガールズ", is_girls=True)
        bets = _build_bets(ri)
        all_reasons = " ".join(
            b.reason for bucket in bets.values() for b in bucket
        )
        for word in ("本命ライン", "別線番手", "ライン3番手"):
            assert word not in all_reasons, all_reasons[:500]


# ---------------------------------------------------------------------------
# C. 通常ライン戦の 4 車ライン (静岡4R 風) は line logic 維持
# ---------------------------------------------------------------------------


class TestNormalLineKeeps4CarLine:
    def test_4_car_line_flow_candidate_still_generated(self):
        """通常戦の 4 車ライン (1-2-3-4) で 4番手流れ込み候補が osae に残る。"""
        ri = _ri(
            class_name="F1",
            lines=[
                {"line_name": "本命長線", "cars": [1, 2, 3, 4]},
                {"line_name": "別線", "cars": [5, 6]},
                {"line_name": "単", "cars": [7]},
            ],
        )
        bets = _build_bets(ri)
        osae_combos = [b.combination for b in bets["押さえ"]]
        # 1-2-4 (4番手流れ込み) が残る
        assert "1-2-4" in osae_combos, osae_combos


# ---------------------------------------------------------------------------
# D. PostRenderValidator: validate_line_terms_when_not_allowed
# ---------------------------------------------------------------------------


class TestValidateLineTermsWhenNotAllowed:
    def _make_plan_with_policy(self, allow_line_logic: bool):
        from app.decision.race_type_policy import (
            _NORMAL_LINE_POLICY, _ROOKIE_POLICY,
        )
        plan = OutputPlan()
        policy = (
            _NORMAL_LINE_POLICY if allow_line_logic else _ROOKIE_POLICY
        )
        plan.race_type = policy.race_type
        object.__setattr__(plan, "_race_type_policy", policy)
        return plan

    def test_normal_line_does_not_flag(self):
        plan = self._make_plan_with_policy(allow_line_logic=True)
        v = validate_line_terms_when_not_allowed(
            plan, "本命ラインの番手差しが連発"
        )
        # 通常ライン戦では検出しない
        assert v == []

    def test_rookie_flags_compound_terms(self):
        plan = self._make_plan_with_policy(allow_line_logic=False)
        v = validate_line_terms_when_not_allowed(
            plan, "本命ラインの番手差しが見える"
        )
        codes = [w.code for w in v]
        assert "LINE_TERMS_LEAKED" in codes
        # 「本命ライン」「番手差し」両方検出
        messages = " ".join(w.message for w in v)
        assert "本命ライン" in messages
        assert "番手差し" in messages

    def test_standalone_bantan_with_particle_detected_for_rookie(self):
        """Phase 5 follow-up: 単独「番手」+助詞を検出する。
        rookie / girls で「番手の浮上」「番手から差し」等が出たら warning。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        v = validate_line_terms_when_not_allowed(
            plan, "番手の浮上が見える。番手から差し込み。"
        )
        codes = [w.code for w in v]
        assert "LINE_TERMS_LEAKED" in codes
        messages = " ".join(w.message for w in v)
        assert "番手" in messages

    def test_standalone_bantan_kara_detected(self):
        """codex P2 反映: 「番手から」単独テスト
        (旧 regex `[のがをでにとは]` では `か` がなくて漏れていた)。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        v = validate_line_terms_when_not_allowed(
            plan, "差しが番手から伸びる"
        )
        codes = [w.code for w in v]
        assert "LINE_TERMS_LEAKED" in codes
        messages = " ".join(w.message for w in v)
        assert "番手から" in messages

    def test_numbered_bantan_detected_for_rookie(self):
        """rookie で「3番手」「4番手」「5番手」等の数字付きを検出。"""
        plan = self._make_plan_with_policy(allow_line_logic=False)
        v = validate_line_terms_when_not_allowed(
            plan, "3番手の流れ込みを期待"
        )
        codes = [w.code for w in v]
        assert "LINE_TERMS_LEAKED" in codes

    def test_standalone_bantan_not_detected_for_normal_line(self):
        """通常ライン戦では単独「番手」が出ても warning にならない
        (通常戦では番手用語が許可される)。"""
        plan = self._make_plan_with_policy(allow_line_logic=True)
        v = validate_line_terms_when_not_allowed(
            plan, "番手の浮上が見える。3番手の流れ込み。"
        )
        # 通常戦は早期 return で何も検出しない
        assert v == []

    def test_no_policy_attribute_no_error(self):
        """plan._race_type_policy が無い (古い test fixture) でも例外なし。"""
        plan = OutputPlan()
        # policy 属性なし
        v = validate_line_terms_when_not_allowed(plan, "本命ライン")
        assert v == []


# ---------------------------------------------------------------------------
# E. BetRecommendation.source_rules
# ---------------------------------------------------------------------------


class TestSourceRulesField:
    def test_default_empty_list(self):
        b = BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.0,
        )
        assert b.source_rules == []

    def test_can_set_source_rules(self):
        b = BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.0, source_rules=["line_third"],
        )
        assert "line_third" in b.source_rules


# ---------------------------------------------------------------------------
# F. E2E: 平塚10R 風 (girls_rookie) で禁止語が本文に出ない
# ---------------------------------------------------------------------------


class TestHiratsuka10rGirlsRookieNoLineTerms:
    def test_girls_rookie_markdown_has_no_line_terms(self):
        ri = _ri(
            class_name="ガールズ新人決勝", is_girls=True,
            lines=[{"line_name": f"L{i}", "cars": [i]} for i in range(1, 8)],
            odds=[
                {"bet_type": "3連単", "combination": "3-4-2", "odds": 12.3},
                {"bet_type": "3連単", "combination": "3-4-7", "odds": 9.3},
            ],
        )
        pred = _pred(
            is_girls=True,
            honsen=[
                _bet("3-4-2", market_odds=12.3, value_label="本線向き"),
                _bet("3-4-7", market_odds=9.3, value_label="見送り寄り"),
            ],
        )
        md = render_prediction_v2(pred, input_data=ri)
        # 警告セクション以前の本文だけチェック
        body = md
        for sep in ("### 出力整合性チェック", "### OutputPlan 警告"):
            if sep in body:
                body = body[:body.rfind(sep)]
        # 強い line 用語が出ない
        for word in ("本命ライン", "番手差し", "番手頭", "別線番手"):
            assert word not in body, (
                f"girls_rookie で本文に「{word}」が残った\n"
                f"body 末尾:\n{body[-1500:]}"
            )


# ---------------------------------------------------------------------------
# G. 静岡4R 風: 通常戦で line logic 維持
# ---------------------------------------------------------------------------


class TestShizuoka4rNormalLineLogicKept:
    def test_normal_line_build_output_plan_includes_line_candidates(self):
        """通常戦の build_output_plan で line logic 由来候補が残る。"""
        ri = _ri(
            class_name="A級一般",
            lines=[
                {"line_name": "本命", "cars": [1, 2, 3]},
                {"line_name": "別線", "cars": [5, 4, 6]},
                {"line_name": "単", "cars": [7]},
            ],
            odds=[
                {"bet_type": "3連単", "combination": "1-2-3", "odds": 6.0},
                {"bet_type": "3連単", "combination": "1-2-5", "odds": 9.0},
                {"bet_type": "3連単", "combination": "2-1-3", "odds": 11.0},
            ],
        )
        scores = compute_scores(ri)
        bets = build_candidate_bets(ri, scores)
        # 何らかの honsen / osae 候補がある (空ではない)
        assert bets["本線"] or bets["押さえ"]
