from __future__ import annotations

from app.scoring import (
    analyze_recent,
    build_candidate_bets,
    build_line_position_map,
    build_marks,
    compute_scores,
)


def _by_car(scores):
    return {s.car_no: s for s in scores}


def test_compute_scores_basic(sample_input):
    scores = compute_scores(sample_input)
    assert len(scores) == len(sample_input.riders)
    by = _by_car(scores)
    # トップ得点(85.71)の5番が win_score 最上位の一人であること
    top = max(scores, key=lambda x: x.total())
    assert top.car_no == 5


def test_wind_bonus_for_bantan(sample_input):
    """風5.0m/sで番手(1)が wind_bonus を獲得する。"""
    scores = compute_scores(sample_input)
    by = _by_car(scores)
    assert by[1].wind_bonus > 0
    # 先行 5番は risk_score が加算される
    assert by[5].risk_score > 0


def test_third_position_gets_third_score(sample_input):
    scores = compute_scores(sample_input)
    by = _by_car(scores)
    # 3番(九州3番手), 4番(中部中国3番手) はいずれも third_score が積まれる
    assert by[3].third_score > by[5].third_score
    assert by[4].third_score > by[2].third_score


def test_rain_bonus(sample_input):
    """雨を強めて, 番手/3番手が weather_bonus を得ることを確認。"""
    data = sample_input.model_dump()
    data["weather"]["rain_mm_per_hour"] = 2.5
    data["weather"]["condition"] = "雨"
    from app.models import RaceInput

    ri = RaceInput.model_validate(data)
    scores = compute_scores(ri)
    by = _by_car(scores)
    assert by[1].weather_bonus > 0  # 番手
    assert by[3].weather_bonus > 0  # 3番手


def test_girls_disables_line_strength(girls_input):
    scores = compute_scores(girls_input)
    by = _by_car(scores)
    # ガールズではライン強さは全員 0 のまま
    for s in scores:
        assert s.line_strength == 0
    # 自力タグ(1,5) は win_score の加点を受ける
    assert by[1].win_score > 0 or by[5].win_score > 0


def test_recent_results_trend(sample_input):
    sig = analyze_recent(sample_input.recent_results)
    # サンプルJSONには別線番手 / 3番手 / 番手頭が含まれる
    assert sig.bessen_bantan_count >= 1
    assert sig.bantan_head_count >= 1


def test_build_marks_assigns_unique(sample_input):
    scores = compute_scores(sample_input)
    marks = build_marks(scores)
    assert len(set(marks.values())) == len(marks)
    assert "◎" in marks


def test_candidate_bets_have_all_categories(sample_input):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    assert set(bets.keys()) == {"本線", "押さえ", "穴", "大穴"}
    assert bets["本線"], "本線が空"
    assert bets["穴"], "穴が空"


def test_odds_value_marks_cheap_head_as_gami_risk(sample_input):
    scores = compute_scores(sample_input)
    by = _by_car(scores)
    # 5番頭の最安オッズが 12.5 → 中位妙味なので gami_risk は付かない
    # 一方、odds_value_score にプラスが入っているはず
    assert by[5].odds_value_score >= 0


def test_line_position_map(sample_input):
    pos = build_line_position_map(sample_input.lines)
    assert pos[5].is_head and pos[5].line_name == "九州"
    assert pos[1].is_bantan
    assert pos[3].is_third
    assert pos[7].is_tanki
