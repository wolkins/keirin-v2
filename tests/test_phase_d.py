"""フェーズD: 仕様の穴埋めテスト。

D-1: 結果列パターン認識（memo に依存しない）
D-2: ガールズ脚質タグ分類
D-3: 補正ルール網羅
D-4: 基本候補の漏れ補完
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput, Rider
from app.scoring import (
    analyze_recent,
    apply_market_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    classify_girls_role,
    compute_scores,
)


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def _load() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _calm_weather(raw: dict) -> dict:
    raw["weather"] = {
        "condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 0.0,
        "wind_direction": None, "wind_note": None, "temperature_c": None,
    }
    return raw


# ---------------------------------------------------------------------------
# D-1: 結果列パターン認識
# ---------------------------------------------------------------------------


def test_d1_head_concentration_detects_main_line_dominant():
    """同じ車番が頭になる結果が3件以上 → main_line_dominant_count >= 3。"""
    raw = _calm_weather(_load())
    raw["recent_results"] = [
        {"date": "2026-05-21", "venue": "大垣", "race_no": 1, "result": "5-1-3", "memo": ""},
        {"date": "2026-05-21", "venue": "大垣", "race_no": 2, "result": "5-2-3", "memo": ""},
        {"date": "2026-05-21", "venue": "大垣", "race_no": 3, "result": "5-4-6", "memo": ""},
    ]
    ri = RaceInput.model_validate(raw)
    trend = analyze_recent(ri.recent_results)
    # 車番5が3回頭 → 鉄板傾向
    assert trend.main_line_dominant_count >= 3
    assert trend.is_main_line_dominant is True


def test_d1_dispersion_detects_chaotic():
    """1着車番が散らばっている → chaotic_count >= 2 で is_chaotic。"""
    raw = _calm_weather(_load())
    raw["recent_results"] = [
        {"date": "2026-05-21", "venue": "大垣", "race_no": 1, "result": "1-2-3", "memo": ""},
        {"date": "2026-05-21", "venue": "大垣", "race_no": 2, "result": "4-5-6", "memo": ""},
        {"date": "2026-05-21", "venue": "大垣", "race_no": 3, "result": "7-1-3", "memo": ""},
        {"date": "2026-05-21", "venue": "大垣", "race_no": 4, "result": "6-2-4", "memo": ""},
    ]
    ri = RaceInput.model_validate(raw)
    trend = analyze_recent(ri.recent_results)
    # 4レースで4ユニークな1着 → 荒れ
    assert trend.is_chaotic is True


def test_d1_short_history_does_not_trigger():
    """結果が少ない場合は鉄板/荒れ判定は出ない。"""
    raw = _calm_weather(_load())
    raw["recent_results"] = [
        {"date": "2026-05-21", "venue": "大垣", "race_no": 1, "result": "5-1-3", "memo": ""},
        {"date": "2026-05-21", "venue": "大垣", "race_no": 2, "result": "5-2-3", "memo": ""},
    ]
    ri = RaceInput.model_validate(raw)
    trend = analyze_recent(ri.recent_results)
    # 2件しかないので main_line_dominant_count >= 3 にはならない
    assert trend.is_main_line_dominant is False
    assert trend.is_chaotic is False


# ---------------------------------------------------------------------------
# D-2: ガールズ脚質タグ分類
# ---------------------------------------------------------------------------


def _rider(car_no: int, tags=None, comment="") -> Rider:
    return Rider(
        car_no=car_no, name=f"選手{car_no}", score=0.0,
        b_count=0, nige=0, makuri=0, sashi=0, mark=0,
        comment=comment, recent_summary="",
        style_tags=tags or [],
    )


def test_d2_classify_maemae_by_tag():
    assert classify_girls_role(_rider(1, ["先行"])) == "前々型"
    assert classify_girls_role(_rider(2, ["自力"])) == "前々型"


def test_d2_classify_chase_by_tag():
    assert classify_girls_role(_rider(1, ["追走"])) == "追走型"
    assert classify_girls_role(_rider(2, ["差し"])) == "追走型"
    assert classify_girls_role(_rider(3, ["追込"])) == "追走型"


def test_d2_classify_jizai_by_tag():
    assert classify_girls_role(_rider(1, ["自在"])) == "自在型"


def test_d2_classify_by_comment_keyword():
    # comment に「逃」/「追」/「両」キーワードで補完
    assert classify_girls_role(_rider(1, comment="逃 神奈川")) == "前々型"
    assert classify_girls_role(_rider(2, comment="追 福岡")) == "追走型"
    assert classify_girls_role(_rider(3, comment="両 千葉")) == "自在型"


def test_d2_classify_unknown_when_no_info():
    assert classify_girls_role(_rider(1)) == "不明"


def test_d2_girls_includes_maemae_chase_form():
    """ガールズで前々型/追走型のタグが分布 → 仕様の必須形が含まれる。"""
    raw = _load()
    raw["race"]["class_name"] = "ガールズ"
    raw["race"]["is_girls"] = True
    raw["lines"] = []
    # 7車のうち、特定の車番に脚質タグを付与
    # car_no=1: 前々型、car_no=2: 追走型
    for r in raw["riders"]:
        if r["car_no"] == 1:
            r["style_tags"] = ["先行"]
        elif r["car_no"] == 2:
            r["style_tags"] = ["追走"]
        else:
            r["style_tags"] = []
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ") for b in bets[cat]
    )
    # 「本命頭-前々型-追走型」「対抗頭-本命-追走型」「本命頭-前々型-対抗」のいずれかが入る
    has_maemae_chase = any(
        kw in reasons
        for kw in ("本命頭-前々型-追走型", "対抗頭-本命-追走型", "本命頭-前々型-対抗")
    )
    assert has_maemae_chase


# ---------------------------------------------------------------------------
# D-3: 補正ルール網羅
# ---------------------------------------------------------------------------


def test_d3_trend_bantan_dominant_boosts_first_and_third():
    """番手差し決着多発時、line_leader の second_score と third の third_score が上がる。"""
    raw = _calm_weather(_load())
    raw["recent_results"] = [
        {"date": "2026-05-21", "venue": "大垣", "race_no": i + 1,
         "result": "1-2-3", "memo": "番手頭"}
        for i in range(3)
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)

    # 補正前の line_leader (5番) と third (3番) の score を保存
    by_car = {s.car_no: s for s in scores}
    before_5_second = by_car[5].second_score
    before_3_third = by_car[3].third_score

    apply_trend_signals(scores, ri)

    after_5_second = next(s.second_score for s in scores if s.car_no == 5)
    after_3_third = next(s.third_score for s in scores if s.car_no == 3)
    assert after_5_second > before_5_second
    assert after_3_third > before_3_third


def test_d3_trend_bessen_weakens_bantan_win():
    """別線番手絡み多発時、本命番手の win_score が下がる。"""
    raw = _calm_weather(_load())
    raw["recent_results"] = [
        {"date": "2026-05-21", "venue": "大垣", "race_no": i + 1,
         "result": "5-6-1", "memo": "別線番手"}
        for i in range(2)
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    before_1 = next(s.win_score for s in scores if s.car_no == 1)
    apply_trend_signals(scores, ri)
    after_1 = next(s.win_score for s in scores if s.car_no == 1)
    assert after_1 < before_1


def test_d3_wind_extra_short_line_leader_penalty():
    """強風(>=4m/s)で line_length<=2 の line_leader は win/second 弱め。"""
    raw = _calm_weather(_load())
    # 5番のラインを 1台 (単騎) に変える…のは難しいので、
    # 別線リーダー（2番）が 1車だけのラインになるよう lines を編集
    raw["lines"] = [
        {"line_name": "九州", "cars": [5, 1, 3], "description": "5-1-3"},
        {"line_name": "中部", "cars": [2, 6], "description": "2-6"},  # 2車だけ
        {"line_name": "単騎", "cars": [7], "description": "7"},
    ]
    # weather: 強風
    raw["weather"]["wind_speed_mps"] = 5.0
    raw["riders"] = [r for r in raw["riders"] if r["car_no"] != 4]  # 6車レース
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    before_2 = next(s.win_score for s in scores if s.car_no == 2)
    apply_wind_extra_signals(scores, ri)
    after_2 = next(s.win_score for s in scores if s.car_no == 2)
    # 別線リーダー 2番（ライン2車）は強風で win が下がる
    assert after_2 < before_2


def test_d3_wind_extra_skipped_when_no_wind():
    raw = _calm_weather(_load())
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    snapshot = [(s.car_no, s.win_score, s.second_score) for s in scores]
    apply_wind_extra_signals(scores, ri)
    after = [(s.car_no, s.win_score, s.second_score) for s in scores]
    assert snapshot == after


def test_d3_market_cheap_top_boosts_underdog():
    """人気1位が4倍未満 → 非人気車に win 微加点。"""
    raw = _calm_weather(_load())
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    # オッズリスト: 1人気が極端に安い
    odds = [
        {"bet_type": "3連単", "combination": "5-1-3", "odds": 2.5},
        {"bet_type": "3連単", "combination": "5-3-1", "odds": 5.0},
    ]
    before_7 = next(s.win_score for s in scores if s.car_no == 7)
    apply_market_signals(scores, odds)
    after_7 = next(s.win_score for s in scores if s.car_no == 7)
    # 7番は人気上位に登場していない非人気車 → 加点
    assert after_7 > before_7


def test_d3_build_gami_inflates_with_cheap_trio():
    """3連複が極端に安い (<5.0) と本線の gami_risk が底上げされる。"""
    raw = _calm_weather(_load())
    raw["odds"] = [
        {"bet_type": "3連複", "combination": "1=3=5", "odds": 3.0},
        {"bet_type": "3連単", "combination": "5-1-3", "odds": 10.0},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    # 本線の gami_risk が 0.2 以上ある（既存値 + 0.2 で底上げ）
    has_high_gami = any(b.gami_risk >= 0.2 for b in bets["本線"])
    assert has_high_gami
    # reason に「3連複安」マーカーが含まれる（新仕様: 該当組み合わせのみ）
    has_msg = any("3連複安" in b.reason for b in bets["本線"])
    assert has_msg


# ---------------------------------------------------------------------------
# D-4: 基本候補の漏れ補完
# ---------------------------------------------------------------------------


def test_d4_includes_second_third_leader_form():
    """穴に "second-third-line_leader" 形が追加されている。"""
    raw = _calm_weather(_load())
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(b.reason for b in bets["穴"])
    assert "番手-3番手-先行" in reasons


def test_d4_includes_separate_leader_combination():
    """『separate_leader-separate_second-main_leader』形が候補に追加されている。

    新仕様: 市場注目別線統合により、押さえに force_push されるケースもある
    （別線が3連単人気上位に頻出する場合）。本線・押さえ・穴のどこかに
    『別線自力-別線番手-本命』reason が含まれていれば OK。
    """
    raw = _calm_weather(_load())
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(
        b.reason for cat in ("本線", "押さえ", "穴", "大穴") for b in bets[cat]
    )
    assert "別線自力-別線番手-本命" in all_reasons


def test_d4_includes_solo_main_combination():
    """大穴に "solo-main_leader-main_second" 形が追加されている。"""
    raw = _calm_weather(_load())
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(b.reason for b in bets["大穴"])
    assert "単騎頭-本命-本命番手" in reasons
