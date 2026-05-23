"""仕様レビュー（観点1〜5）の堅牢化テスト。

1. apply_market_signals が人気オッズに寄せすぎない（最大±0.5）
2. ズレ目候補（本線先頭-別線自力-本線番手 等）が雨/風で生成される
3. apply_trend_signals が着順パターンを認識（番手-先行-3番手 等）
4. ガールズで「番手」「追込」signal が無効化される
5. 3連単/3連複/2車単が買い目生成で混同されない
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput, Rider
from app.scoring import (
    TrendSignal,
    analyze_recent,
    apply_market_signals,
    apply_tospo_signals,
    build_candidate_bets,
    compute_scores,
)


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def _load_calm() -> RaceInput:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["weather"] = {
        "condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 0.0,
        "wind_direction": None, "wind_note": None, "temperature_c": None,
    }
    return RaceInput.model_validate(raw)


def _reasons(buckets: list) -> str:
    return " / ".join(b.reason for b in buckets)


# ---------------------------------------------------------------------------
# 1. apply_market_signals が人気オッズに寄せすぎない（最大±0.5）
# ---------------------------------------------------------------------------


def test_market_signals_max_boost_capped_at_0_5():
    """1番車が頭としてすべての人気上位に登場しても win 加点は 0.5 以下。"""
    ri = _load_calm()
    scores = compute_scores(ri)
    # 1番車が全レース頭の極端なオッズ
    odds = [
        {"bet_type": "3連単", "combination": f"1-{a}-{b}", "odds": 5.0 + i * 0.1}
        for i, (a, b) in enumerate([
            (2, 3), (2, 4), (3, 2), (3, 4), (4, 2), (4, 3),
            (5, 2), (5, 3), (6, 2), (6, 3),
            (7, 2), (7, 3), (2, 5), (2, 6), (3, 5), (3, 6),
            (4, 5), (4, 6), (5, 6), (6, 5),
        ])
    ]
    # 1番車の頭出現は 20 回（全件）
    before_1 = next(s.win_score for s in scores if s.car_no == 1)
    apply_market_signals(scores, odds)
    after_1 = next(s.win_score for s in scores if s.car_no == 1)
    delta = after_1 - before_1
    # 最大でも 0.5 まで
    assert delta <= 0.5 + 1e-6, f"市場補正が大きすぎる: +{delta:.3f}"


def test_market_signals_partial_appearance_proportional():
    """頻度1/4の場合、加点はもっと小さい。"""
    ri = _load_calm()
    scores = compute_scores(ri)
    # 1番頭が 5/20 = 25%
    odds = [
        {"bet_type": "3連単", "combination": "1-2-3", "odds": 5.0},
        {"bet_type": "3連単", "combination": "1-3-2", "odds": 6.0},
        {"bet_type": "3連単", "combination": "1-4-2", "odds": 7.0},
        {"bet_type": "3連単", "combination": "1-5-2", "odds": 8.0},
        {"bet_type": "3連単", "combination": "1-6-2", "odds": 9.0},
        {"bet_type": "3連単", "combination": "2-3-4", "odds": 10.0},
        {"bet_type": "3連単", "combination": "3-2-4", "odds": 11.0},
        {"bet_type": "3連単", "combination": "4-2-3", "odds": 12.0},
        {"bet_type": "3連単", "combination": "5-2-3", "odds": 13.0},
        {"bet_type": "3連単", "combination": "6-2-3", "odds": 14.0},
        {"bet_type": "3連単", "combination": "7-2-3", "odds": 15.0},
        {"bet_type": "3連単", "combination": "2-1-3", "odds": 16.0},
        {"bet_type": "3連単", "combination": "3-1-4", "odds": 17.0},
        {"bet_type": "3連単", "combination": "4-1-5", "odds": 18.0},
        {"bet_type": "3連単", "combination": "5-1-6", "odds": 19.0},
        {"bet_type": "3連単", "combination": "6-1-7", "odds": 20.0},
        {"bet_type": "3連単", "combination": "7-1-2", "odds": 21.0},
        {"bet_type": "3連単", "combination": "2-3-1", "odds": 22.0},
        {"bet_type": "3連単", "combination": "3-4-1", "odds": 23.0},
        {"bet_type": "3連単", "combination": "4-5-1", "odds": 24.0},
    ]
    before_1 = next(s.win_score for s in scores if s.car_no == 1)
    apply_market_signals(scores, odds)
    after_1 = next(s.win_score for s in scores if s.car_no == 1)
    delta = after_1 - before_1
    # 頻度 5/20 = 0.25 × weight 0.5 = 0.125
    assert delta <= 0.5
    assert 0.05 <= delta <= 0.2, f"想定範囲外: +{delta:.3f}"


# ---------------------------------------------------------------------------
# 2. ズレ目候補の漏れチェック
# ---------------------------------------------------------------------------


def test_rain_includes_leader_separate_lead_second():
    """雨補正に「本線先頭-別線自力-本線番手」(ll-sep_l-sec) が含まれる。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["weather"] = {
        "condition": "雨", "rain_mm_per_hour": 2.5, "wind_speed_mps": 0.0,
        "wind_direction": None, "wind_note": None, "temperature_c": None,
    }
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    assert "本線先頭-別線自力-本線番手" in all_reasons


def test_strong_wind_includes_leader_separate_lead_second():
    """強風補正にも「本線先頭-別線自力-本線番手」が含まれる。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["weather"] = {
        "condition": "曇り", "rain_mm_per_hour": 0.0, "wind_speed_mps": 5.0,
        "wind_direction": None, "wind_note": None, "temperature_c": None,
    }
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    assert "本線先頭-別線自力-本線番手" in all_reasons


def test_rain_required_specific_forms_all_present():
    """雨時に仕様5章の主要5形が全て含まれる（必須形のスモークテスト）。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["weather"] = {
        "condition": "雨", "rain_mm_per_hour": 2.5, "wind_speed_mps": 0.0,
        "wind_direction": None, "wind_note": None, "temperature_c": None,
    }
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    for required in (
        "本命自力-本命番手-別線番手",
        "本命自力-別線番手-本命番手",
        "本命自力-3番手-本命番手",
        "番手-自力-3番手",
        "別線番手-別線自力-本命自力",
    ):
        assert required in reasons, f"雨時の必須形が無い: {required}"


def test_strong_wind_required_specific_forms_all_present():
    """強風時に仕様6章の主要6形が全て含まれる。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["weather"] = {
        "condition": "曇り", "rain_mm_per_hour": 0.0, "wind_speed_mps": 5.0,
        "wind_direction": None, "wind_note": None, "temperature_c": None,
    }
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    for required in (
        "本線先頭-別線番手-本線番手",
        "本線先頭-3番手-本線番手",
        "番手-先行-3番手",
        "3番手-番手-先行",
        "別線番手-別線自力-本線自力",
    ):
        assert required in reasons, f"強風時の必須形が無い: {required}"


# ---------------------------------------------------------------------------
# 3. apply_trend_signals 着順パターン認識
# ---------------------------------------------------------------------------


def _input_with_memos(memos: list[str]) -> RaceInput:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["recent_results"] = [
        {"date": "2026-05-21", "venue": "大垣", "race_no": i + 1,
         "result": "5-1-3", "memo": m}
        for i, m in enumerate(memos)
    ]
    raw["weather"] = {
        "condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 0.0,
        "wind_direction": None, "wind_note": None, "temperature_c": None,
    }
    return RaceInput.model_validate(raw)


def test_trend_recognizes_senko_head_third_2nd():
    """memo に『先行-3番手』があれば is_senko_head_third_2nd が True。"""
    ri = _input_with_memos(["先行頭-3番手2着", "自力-3番手"])
    trend = analyze_recent(ri.recent_results)
    assert trend.is_senko_head_third_2nd is True


def test_trend_recognizes_bantan_head_senko_2nd():
    """memo に『番手-先行』があれば is_bantan_head_senko_2nd が True。"""
    ri = _input_with_memos(["番手-先行-3番手", "番手差し決着"])
    trend = analyze_recent(ri.recent_results)
    assert trend.is_bantan_head_senko_2nd is True


def test_trend_recognizes_bessen_lead_dominant():
    """memo に『別線自力』があれば is_bessen_lead_dominant が True。"""
    ri = _input_with_memos(["別線自力決着", "別線自力頭"])
    trend = analyze_recent(ri.recent_results)
    assert trend.is_bessen_lead_dominant is True


def test_trend_pattern_forms_added_to_candidates():
    """着順パターン由来の必須形が候補に追加される。"""
    ri = _input_with_memos([
        "先行-3番手-番手", "自力-3番手",
        "番手差し決着", "番手-先行-3番手",
        "別線自力決着", "別線自力頭",
    ])
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    assert "先行-3番手-番手 多発" in reasons
    assert "番手-先行-3番手 多発" in reasons
    assert "別線自力決着多発" in reasons


# ---------------------------------------------------------------------------
# 4. ガールズで「番手」「追込」signal が無効化される
# ---------------------------------------------------------------------------


def test_girls_disables_bantan_signal():
    """ガールズでは『番手』signal が東スポ補正で無視される。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"]["class_name"] = "ガールズ"
    raw["race"]["is_girls"] = True
    raw["lines"] = []
    # 1番に「番手」と「自力」両方タグ
    raw["riders"][0]["style_tags"] = ["番手", "自力"]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)

    car1 = ri.riders[0].car_no
    before = next(s.win_score for s in scores if s.car_no == car1)
    apply_tospo_signals(scores, ri)
    after = next(s.win_score for s in scores if s.car_no == car1)
    # ガールズでは「番手」+0.2 が無視され「自力」+0.3 のみ適用される
    delta = after - before
    assert 0.25 <= delta <= 0.35, f"想定 +0.3（自力のみ）、実 +{delta:.2f}"


def test_normal_race_uses_bantan_signal():
    """通常戦では『番手』signal が有効。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    # 1番(楢原)のタグに「番手」明示（既に番手, 差し が入っている可能性も）
    for r in raw["riders"]:
        if r["car_no"] == 1:
            if "番手" not in r["style_tags"]:
                r["style_tags"].append("番手")
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    before = next(s.win_score for s in scores if s.car_no == 1)
    apply_tospo_signals(scores, ri)
    after = next(s.win_score for s in scores if s.car_no == 1)
    # 通常戦では 番手 +0.2 が入る
    assert (after - before) >= 0.2


def test_girls_disables_oikomi_signal():
    """ガールズでは『追込』signal も無視される。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"]["class_name"] = "ガールズ"
    raw["race"]["is_girls"] = True
    raw["lines"] = []
    raw["riders"][0]["style_tags"] = ["追込"]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)

    car1 = ri.riders[0].car_no
    before_sec = next(s.second_score for s in scores if s.car_no == car1)
    apply_tospo_signals(scores, ri)
    after_sec = next(s.second_score for s in scores if s.car_no == car1)
    # ガールズでは「追込」+0.2 が second に入らない
    assert after_sec == before_sec


# ---------------------------------------------------------------------------
# 5. 3連単/3連複/2車単の分離
# ---------------------------------------------------------------------------


def test_market_signals_ignores_trio_and_exacta():
    """apply_market_signals は 3連複/2車単 を集計対象にしない。"""
    ri = _load_calm()
    scores = compute_scores(ri)
    # 1番頭の 3連複 と 2車単 だけ（3連単は空）
    odds = [
        {"bet_type": "3連複", "combination": "1=2=3", "odds": 5.0},
        {"bet_type": "3連複", "combination": "1=3=4", "odds": 6.0},
        {"bet_type": "2車単", "combination": "1-2", "odds": 3.0},
        {"bet_type": "2車単", "combination": "1-3", "odds": 4.0},
    ]
    snapshot = [(s.car_no, s.win_score, s.second_score, s.third_score) for s in scores]
    apply_market_signals(scores, odds)
    after = [(s.car_no, s.win_score, s.second_score, s.third_score) for s in scores]
    # 3連単が無いので何も変わらない
    assert snapshot == after


def test_build_candidate_bets_only_generates_trifecta():
    """build_candidate_bets の生成買い目はすべて 3連単。"""
    ri = _load_calm()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    for cat in ("本線", "押さえ", "穴", "大穴"):
        for b in bets[cat]:
            assert b.bet_type == "3連単", (
                f"{cat} に非3連単が混入: {b.bet_type} {b.combination}"
            )


def test_cheap_trio_inflates_gami_but_does_not_change_combinations():
    """3連複が安い → 本線の gami_risk が底上げされるが、生成される combination は変わらない。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["weather"] = {
        "condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 0.0,
        "wind_direction": None, "wind_note": None, "temperature_c": None,
    }
    # 3連単オッズなし、3連複だけ
    raw["odds"] = [
        {"bet_type": "3連複", "combination": "1=3=5", "odds": 3.0},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    # 本線の gami_risk が底上げされる
    assert any(b.gami_risk > 0 for b in bets["本線"])
    # ただし 3連複の "1=3=5" が買い目に混入していない
    all_combos = [b.combination for cat in bets.values() for b in cat]
    assert "1=3=5" not in all_combos
    # 全部 「N-N-N」形式（3連単）
    import re
    pattern = re.compile(r"^\d-\d-\d$")
    for c in all_combos:
        assert pattern.match(c), f"非3連単形式: {c}"
