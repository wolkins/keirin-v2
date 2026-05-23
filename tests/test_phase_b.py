"""フェーズB: 雨・強風・直近トレンド別の必須候補追加 のテスト。

仕様5/6/8/12章をカバー。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput
from app.scoring import (
    TrendSignal,
    analyze_recent,
    build_candidate_bets,
    compute_scores,
    resolve_rider_roles,
)


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def _input(**race_overrides) -> RaceInput:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"].update(race_overrides)
    return RaceInput.model_validate(raw)


def _input_with_weather(**weather) -> RaceInput:
    """weather を完全置換する（既定 weather からの上書きでなく全置換）。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    base = {"condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 0.0,
            "wind_direction": None, "wind_note": None, "temperature_c": None}
    base.update(weather)
    raw["weather"] = base
    return RaceInput.model_validate(raw)


def _input_with_results(memos: list[str], *, calm_weather: bool = True) -> RaceInput:
    """recent_results を差し替える。calm_weather=True なら晴れ・無風で他補正が混ざらない。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["recent_results"] = [
        {"date": "2026-05-21", "venue": "大垣", "race_no": i + 1, "result": "5-1-3", "memo": m}
        for i, m in enumerate(memos)
    ]
    if calm_weather:
        raw["weather"] = {
            "condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 0.0,
            "wind_direction": None, "wind_note": None, "temperature_c": None,
        }
    return RaceInput.model_validate(raw)


def _combos(bets: list) -> list[str]:
    return [b.combination for b in bets]


def _reasons(bets: list) -> list[str]:
    return [b.reason for b in bets]


# ---------------------------------------------------------------------------
# TrendSignal 拡張
# ---------------------------------------------------------------------------


def test_trend_chaotic_detection():
    ri = _input_with_results(["波乱の中穴", "ズレ目決着"])
    trend = analyze_recent(ri.recent_results)
    assert trend.chaotic_count == 2
    assert trend.is_chaotic is True


def test_trend_main_line_dominant():
    ri = _input_with_results(["本命ライン決着", "順当決着"])
    trend = analyze_recent(ri.recent_results)
    assert trend.main_line_dominant_count == 2
    assert trend.is_main_line_dominant is True


def test_trend_bantan_dominant():
    ri = _input_with_results(["本命番手頭", "番手頭"])
    trend = analyze_recent(ri.recent_results)
    assert trend.is_bantan_dominant is True


def test_trend_third_sec_up():
    ri = _input_with_results(["3番手の2着上がり", "3番手2着"])
    trend = analyze_recent(ri.recent_results)
    assert trend.is_third_sec_up is True


def test_trend_bessen_involved():
    ri = _input_with_results(["別線番手絡み", "別線番手2着"])
    trend = analyze_recent(ri.recent_results)
    assert trend.is_bessen_involved is True


# ---------------------------------------------------------------------------
# 天候別必須候補（雨）
# ---------------------------------------------------------------------------


def test_rain_required_forms_added():
    ri = _input_with_weather(rain_mm_per_hour=2.5, condition="雨", wind_speed_mps=0.0)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    # 雨補正の必須形が含まれるか reason ベースで確認
    all_reasons = (
        _reasons(bets["本線"]) + _reasons(bets["押さえ"]) +
        _reasons(bets["穴"]) + _reasons(bets["大穴"])
    )
    joined = " / ".join(all_reasons)
    assert "雨補正" in joined
    # 「本命自力-本命番手-別線番手」「本命自力-別線番手-本命番手」
    assert "本命自力-本命番手-別線番手" in joined
    assert "本命自力-別線番手-本命番手" in joined
    # 「番手-自力-3番手」「別線番手-別線自力-本命自力」
    assert "番手-自力-3番手" in joined
    assert "別線番手-別線自力-本命自力" in joined


def test_no_rain_no_rain_forms():
    """雨量0のとき雨補正は追加されない。"""
    ri = _input_with_weather(rain_mm_per_hour=0.0, condition="曇り")
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(
        _reasons(bets["本線"]) + _reasons(bets["押さえ"]) +
        _reasons(bets["穴"]) + _reasons(bets["大穴"])
    )
    assert "雨補正" not in all_reasons


# ---------------------------------------------------------------------------
# 天候別必須候補（強風）
# ---------------------------------------------------------------------------


def test_strong_wind_required_forms_added():
    ri = _input_with_weather(wind_speed_mps=5.0, condition="曇り")
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(
        _reasons(bets["本線"]) + _reasons(bets["押さえ"]) +
        _reasons(bets["穴"]) + _reasons(bets["大穴"])
    )
    assert "強風補正" in all_reasons
    # 「本線先頭-別線番手-本線番手」「本線先頭-3番手-本線番手」「番手-先行-3番手」
    assert "本線先頭-別線番手-本線番手" in all_reasons
    assert "本線先頭-3番手-本線番手" in all_reasons
    assert "番手-先行-3番手" in all_reasons
    # 「3番手-番手-先行」「別線番手-別線自力-本線自力」
    assert "3番手-番手-先行" in all_reasons
    assert "別線番手-別線自力-本線自力" in all_reasons


def test_no_wind_no_strong_wind_forms():
    """風速が4m/s未満のとき強風補正は追加されない。"""
    ri = _input_with_weather(wind_speed_mps=2.0)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(
        _reasons(bets["本線"]) + _reasons(bets["押さえ"]) +
        _reasons(bets["穴"]) + _reasons(bets["大穴"])
    )
    assert "強風補正" not in all_reasons


# ---------------------------------------------------------------------------
# トレンド別必須候補
# ---------------------------------------------------------------------------


def test_trend_bantan_dominant_adds_bantan_head_to_honsen():
    """番手差し決着多発時、番手頭の本線が追加される。"""
    ri = _input_with_results(["本命番手頭", "番手頭"])
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_reasons = " / ".join(_reasons(bets["本線"]))
    assert "番手頭決着が多発" in honsen_reasons


def test_trend_third_sec_up_adds_to_osae():
    """3番手2着上がり多発時、自力-3番手-番手 が候補に追加される。

    本命ライン優先モードでは本線に「先頭-3番手-番手（2-3着入替）」が
    既に入っているため、3番手2着上がりトレンドの push は本線への
    複合理由追記になることがある（本線/押さえどちらかで言及されればOK）。
    """
    ri = _input_with_results(["3番手2着", "3番手の2着上がり"])
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(_reasons(bets["本線"]) + _reasons(bets["押さえ"]))
    assert "3番手2着上がり多発" in all_reasons


def test_trend_bessen_involved_adds_bessen_bantan():
    """別線番手絡み多発時、本命自力-別線番手-本命番手 が押さえに、別線番手頭が穴に追加。"""
    ri = _input_with_results(["別線番手", "別線番手2着"])
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(
        _reasons(bets["押さえ"]) + _reasons(bets["穴"])
    )
    assert "別線番手絡み多発" in all_reasons


def test_trend_chaotic_adds_to_ana_ooana():
    """荒れ傾向時、単騎/自在頭・別線番手頭・3番手頭が穴/大穴に追加。"""
    ri = _input_with_results(["波乱", "ズレ目", "中穴決着"])
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(_reasons(bets["穴"]) + _reasons(bets["大穴"]))
    assert "荒れ傾向" in all_reasons


# ---------------------------------------------------------------------------
# ガールズではスキップされる
# ---------------------------------------------------------------------------


def test_girls_skips_role_based_additions():
    """ガールズでは役割ベースの追加が動かない（全員 girls role）。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["race"]["class_name"] = "ガールズ"
    raw["race"]["is_girls"] = True
    raw["lines"] = []
    raw["weather"]["rain_mm_per_hour"] = 2.5
    raw["weather"]["wind_speed_mps"] = 5.0
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_reasons = " / ".join(
        _reasons(bets["本線"]) + _reasons(bets["押さえ"]) +
        _reasons(bets["穴"]) + _reasons(bets["大穴"])
    )
    # ガールズなので雨補正・強風補正は適用されない
    assert "雨補正" not in all_reasons
    assert "強風補正" not in all_reasons


# ---------------------------------------------------------------------------
# カテゴリ重複なし
# ---------------------------------------------------------------------------


def test_added_candidates_no_duplicate_across_categories():
    """雨+強風+トレンド全部入りでもカテゴリ間で combination が重複しない。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["weather"]["rain_mm_per_hour"] = 2.5
    raw["weather"]["wind_speed_mps"] = 6.0
    raw["recent_results"] = [
        {"date": "2026-05-21", "venue": "大垣", "race_no": 10,
         "result": "1-2-3", "memo": "本命番手頭 / 3番手の2着上がり / 別線番手絡み / 波乱"},
        {"date": "2026-05-21", "venue": "大垣", "race_no": 11,
         "result": "2-1-3", "memo": "番手頭 / 3番手2着 / 別線番手2着 / ズレ目"},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    all_combos = (
        _combos(bets["本線"]) + _combos(bets["押さえ"]) +
        _combos(bets["穴"]) + _combos(bets["大穴"])
    )
    assert len(all_combos) == len(set(all_combos)), f"重複あり: {all_combos}"
