"""印・最終結論・表示の整合性テスト。

仕様要件:
1. 印が ◎3 ◯9 のとき、最終結論の「対抗」が 9番を指す
2. market_odds=None の買い目に「低配当注意」「ガミ注意」を表示しない
3. cheap_trio (3連複が極端に安い) による gami_risk 底上げが本線に限定
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import _format_bets, render_prediction
from app.llm_client import _build_final_conclusion, build_default_client
from app.models import BetRecommendation, RaceInput
from app.prompt_builder import build_full_prompt
from app.scoring import build_candidate_bets, build_marks, compute_scores
from app.value_analysis import annotate_prediction_with_value


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "takeo_1r_main_line.json"


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 1. 最終結論の「対抗」が印の◯と一致
# ---------------------------------------------------------------------------


def test_final_conclusion_taikou_matches_circle_mark():
    """印 ◎1 ◯9 のとき、最終結論の対抗は9番（印◯と一致）。

    武雄1R fixture: top1=1番(L1), main_second=9番(B9)
    """
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    marks = build_marks(scores, ri)
    # 前提: ◎=1, ◯=9
    assert marks.get("◎") == 1
    assert marks.get("◯") == 9

    msg = _build_final_conclusion(
        scores=scores, candidate_bets=bets, is_girls=False, marks=marks,
    )
    # 「対抗は9番」と書かれる
    assert "対抗は9番" in msg, (
        f"対抗が印◯(9番)と一致していない:\n{msg}"
    )
    # 「対抗は2番」「対抗は7番」など印と矛盾する番号は出ない
    for wrong in (2, 3, 4, 5, 6, 7, 8):
        assert f"対抗は{wrong}番" not in msg, (
            f"対抗が {wrong}番 になっている: {msg}"
        )


def test_final_conclusion_falls_back_to_score_when_no_marks():
    """marks が無いとき（古い呼び出し）はスコア2位にフォールバック。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    msg = _build_final_conclusion(
        scores=scores, candidate_bets=bets, is_girls=False, marks=None,
    )
    # 何らかの「対抗は○番」がある（フォールバック動作）
    assert "対抗は" in msg


def test_final_conclusion_takes_honsen_2nd_when_circle_missing():
    """marks に ◯ が無いとき、本線1点目の2着車を対抗にする。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    # marks に ◯ を入れないが ◎ は入れる
    marks_no_circle = {"◎": 1}
    msg = _build_final_conclusion(
        scores=scores, candidate_bets=bets, is_girls=False, marks=marks_no_circle,
    )
    # 本線1点目は 1-9-6 なので、対抗は 9番
    assert "対抗は9番" in msg


# ---------------------------------------------------------------------------
# 2. market_odds=None で 低配当注意/ガミ注意 を表示しない
# ---------------------------------------------------------------------------


def _make_bet(combo: str, *, gami: float, market_odds=None) -> BetRecommendation:
    return BetRecommendation(
        category="穴",
        bet_type="3連単",
        combination=combo,
        reason="test",
        gami_risk=gami,
        market_odds=market_odds,
    )


def test_format_bets_hides_low_payout_when_odds_none():
    """market_odds=None の買い目に低配当注意が表示されない（仕様要件2）。"""
    bets = [_make_bet("1-2-3", gami=0.5, market_odds=None)]
    text = _format_bets(bets)
    assert "[低配当注意]" not in text, f"odds=Noneで低配当注意が表示: {text}"
    assert "[ガミ注意]" not in text


def test_format_bets_hides_gami_when_odds_none():
    """market_odds=None なら gami_risk が高くてもガミ注意を表示しない。"""
    bets = [_make_bet("1-2-3", gami=0.9, market_odds=None)]
    text = _format_bets(bets)
    assert "[ガミ注意]" not in text


def test_format_bets_shows_low_payout_when_odds_present():
    """市場オッズが取れている安い買い目には [低配当注意] を表示。"""
    bets = [_make_bet("1-2-3", gami=0.5, market_odds=5.0)]
    text = _format_bets(bets)
    assert "[低配当注意]" in text


def test_format_bets_shows_gami_when_odds_present():
    """市場オッズが取れている超安い買い目には [ガミ注意] を表示。"""
    bets = [_make_bet("1-2-3", gami=0.8, market_odds=3.0)]
    text = _format_bets(bets)
    assert "[ガミ注意]" in text


# ---------------------------------------------------------------------------
# 3. cheap_trio (3連複安) の gami_risk 底上げが本線に限定
# ---------------------------------------------------------------------------


def test_cheap_trio_gami_inflation_limited_to_honsen():
    """3連複が極端に安いとき、本線の gami_risk は底上げされるが、
    穴・大穴・押さえの **オッズ未取得** 買い目には影響しない（仕様要件3）。
    """
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # 3連複オッズだけ安く設定（3連単は無し）
    raw["odds"] = [
        {"bet_type": "3連複", "combination": "1=9=6", "odds": 3.0},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)

    # 本線: gami_risk が底上げされている
    for b in bets["本線"]:
        # 本線のうち少なくとも1点は cheap_trio 由来で gami_risk > 0
        pass
    has_inflated = any(b.gami_risk >= 0.2 for b in bets["本線"])
    assert has_inflated, (
        "本線の gami_risk が底上げされていない（cheap_trio 効果なし）"
    )

    # 穴・大穴のオッズ未取得買い目: gami_risk は元々 0 のはず（cheap_trio 影響なし）
    for cat in ("穴", "大穴"):
        for b in bets[cat]:
            if b.market_odds is None:
                # オッズ取れていない買い目は元々 gami=0
                assert b.gami_risk == 0.0, (
                    f"{cat}/{b.combination} (odds=None) の gami_risk が "
                    f"{b.gami_risk} に上がっている。cheap_trio が穴大穴に広がっている可能性。"
                )


def test_takeo_1r_predict_no_false_low_payout_warning():
    """武雄1R fixture の最終 Markdown で、オッズ未取得買い目に [低配当注意] が無い。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)
    md = render_prediction(prediction)

    # 武雄1R fixture は odds が空 → 全買い目で market_odds=None
    # → [低配当注意] / [ガミ注意] は1件も出ない
    assert "[低配当注意]" not in md, (
        f"武雄1R(オッズ未取得)に[低配当注意]が表示されている:\n{md[:2000]}"
    )
    assert "[ガミ注意]" not in md, (
        f"武雄1R(オッズ未取得)に[ガミ注意]が表示されている:\n{md[:2000]}"
    )


# ---------------------------------------------------------------------------
# 追加要件: 20倍以上に低配当注意が付かない
# ---------------------------------------------------------------------------


def test_no_low_payout_above_20x():
    """market_odds が 20倍以上の場合は [低配当注意]/[ガミ注意] を表示しない。"""
    # 20倍ピッタリ
    text20 = _format_bets([_make_bet("1-2-3", gami=0.5, market_odds=20.0)])
    assert "[低配当注意]" not in text20
    assert "[ガミ注意]" not in text20
    # 25倍
    text25 = _format_bets([_make_bet("1-2-3", gami=0.5, market_odds=25.0)])
    assert "[低配当注意]" not in text25
    # 50倍 + gami_risk 高 でも出ない
    text50 = _format_bets([_make_bet("1-2-3", gami=0.9, market_odds=50.0)])
    assert "[低配当注意]" not in text50
    assert "[ガミ注意]" not in text50


def test_low_payout_under_15x():
    """market_odds < 15倍 + gami_risk >= 0.4 で [低配当注意]。"""
    text = _format_bets([_make_bet("1-2-3", gami=0.4, market_odds=10.0)])
    assert "[低配当注意]" in text


def test_gami_warning_under_15x_with_high_gami():
    """market_odds < 15倍 + gami_risk >= 0.6 で [ガミ注意]。"""
    text = _format_bets([_make_bet("1-2-3", gami=0.7, market_odds=8.0)])
    assert "[ガミ注意]" in text


def test_gami_warning_15_to_20x_only_when_gami_very_high():
    """15〜20倍は gami_risk >= 0.8 のときのみ [ガミ注意]。"""
    # gami=0.7, odds=17倍 → 表示なし
    text_no = _format_bets([_make_bet("1-2-3", gami=0.7, market_odds=17.0)])
    assert "[低配当注意]" not in text_no
    assert "[ガミ注意]" not in text_no
    # gami=0.85, odds=17倍 → [ガミ注意]
    text_yes = _format_bets([_make_bet("1-2-3", gami=0.85, market_odds=17.0)])
    assert "[ガミ注意]" in text_yes


# ---------------------------------------------------------------------------
# 追加要件: cheap_trio が穴・大穴で「点数注意」程度
# ---------------------------------------------------------------------------


def test_cheap_trio_marks_only_matching_combo():
    """cheap_trio 時、**該当組み合わせのみ** 「3連複安」が追記される。
    無関係な組み合わせには波及しない（仕様: 3連複安を一律加算しない）。
    """
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["odds"] = [{"bet_type": "3連複", "combination": "1=9=6", "odds": 3.0}]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    # 本命ライン形 (1,9,6) は cheap_trio セットに含まれるので reason 追記
    matched_honsen = [
        b for b in bets["本線"]
        if set(b.combination.split("-")) == {"1", "9", "6"}
    ]
    if matched_honsen:
        assert any("3連複安" in b.reason for b in matched_honsen), (
            f"3連複安 (1=9=6) に該当する本線買い目に reason が無い"
        )
    # 該当しない買い目 (例: 4-9-1 / 6-1-9 など) には reason 追記されない
    non_matched = [
        b for b in (bets["本線"] + bets["押さえ"] + bets["穴"] + bets["大穴"])
        if set(b.combination.split("-")) != {"1", "9", "6"}
        and "3連複安" in b.reason
    ]
    assert not non_matched, (
        f"無関係な買い目に 3連複安 reason が波及している: "
        f"{[(b.combination, b.reason) for b in non_matched]}"
    )


def test_cheap_trio_ana_display_is_tensuu_chuui_not_gami():
    """3連複安由来の穴買い目は [点数注意] であって [ガミ注意]/[低配当注意] ではない。"""
    from app.models import BetRecommendation
    bet = BetRecommendation(
        category="穴",
        bet_type="3連単",
        combination="1-2-3",
        reason="別線番手の頭を狙う中穴 ＋ 3連複安・点数を絞る",
        gami_risk=0.0,
        market_odds=None,  # 穴はオッズ未取得が多い
    )
    text = _format_bets([bet])
    assert "[点数注意]" in text
    assert "[ガミ注意]" not in text
    assert "[低配当注意]" not in text


def test_cheap_trio_honsen_uses_strong_warning():
    """本線は 3連複安 でも [点数注意] ではなく通常の[ガミ注意]/[低配当注意]ロジック。"""
    from app.models import BetRecommendation
    bet = BetRecommendation(
        category="本線",
        bet_type="3連単",
        combination="1-2-3",
        reason="スコア上位 ＋ 3連複が極端に安いためガミ警戒",
        gami_risk=0.8,
        market_odds=4.0,
    )
    text = _format_bets([bet])
    # 本線は通常表示（ガミ注意）
    assert "[ガミ注意]" in text
    assert "[点数注意]" not in text


# ---------------------------------------------------------------------------
# 追加要件: 直近結果が 本線先頭-番手-別線番手 のとき、次レース押さえ上位
# ---------------------------------------------------------------------------


def test_main_then_bessen_third_trend_adds_to_osae():
    """直近結果メモに『別線番手3着』『本線先頭-番手-別線番手』が含まれる場合、
    次レースで line_leader-second-separate_second（例: 3-9-5）が押さえに追加。
    """
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["recent_results"] = [
        {"date": "2026-05-23", "venue": "武雄", "race_no": 1,
         "result": "1-9-5", "memo": "本線先頭-番手-別線番手"},
        {"date": "2026-05-23", "venue": "武雄", "race_no": 2,
         "result": "3-9-1", "memo": "別線番手3着"},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_reasons = " / ".join(b.reason for b in bets["押さえ"])
    # トレンド由来の reason が押さえにある
    assert "本線先頭-番手-別線番手の連発" in osae_reasons, (
        f"押さえに本線先頭-番手-別線番手 トレンド形が無い:\n"
        f"押さえ: {[b.combination + ': ' + b.reason for b in bets['押さえ']]}"
    )


def test_main_with_bessen_lead_trend_detection():
    """memo に『本命先頭-別線自力』が含まれる場合 main_with_bessen_lead が True。"""
    from app.scoring import analyze_recent
    from app.models import RecentResult
    from datetime import date

    results = [
        RecentResult(date=date(2026, 5, 23), venue="武雄", race_no=2,
                     result="3-2-8", memo="本命先頭-別線自力-別線3着"),
    ]
    trend = analyze_recent(results)
    assert trend.is_main_with_bessen_lead is True


def test_main_with_bessen_lead_adds_to_osae():
    """直近結果に『本命先頭-別線自力』があれば、次レースで line_leader-separate_leader-separate_second
    系が押さえに追加される（仕様要件1）。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["recent_results"] = [
        {"date": "2026-05-23", "venue": "武雄", "race_no": 1,
         "result": "1-9-5", "memo": "本線先頭-番手-別線番手"},
        {"date": "2026-05-23", "venue": "武雄", "race_no": 2,
         "result": "3-2-8", "memo": "本命先頭-別線自力-別線3着"},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_reasons = " / ".join(b.reason for b in bets["押さえ"])
    # 本命先頭-別線自力-別線番手 トレンド形 が押さえに
    assert "本命先頭-別線自力-別線番手" in osae_reasons, (
        f"押さえに本命先頭-別線自力 トレンド形が無い:\n"
        f"押さえ: {[b.combination + ' / ' + b.reason for b in bets['押さえ']]}"
    )


def test_main_then_bessen_third_adds_swap_pair():
    """『本線先頭-番手-別線番手』トレンド時、ll-sec-sep_s だけでなく
    ll-sep_s-sec（別線番手2着-本命番手3着の派生）も押さえに追加される（仕様要件1）。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["recent_results"] = [
        {"date": "2026-05-23", "venue": "武雄", "race_no": 1,
         "result": "1-9-5", "memo": "本線先頭-番手-別線番手"},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    osae_reasons = " / ".join(b.reason for b in bets["押さえ"])
    # 本命先頭-別線番手-本命番手 形が押さえに
    assert "本命先頭-別線番手-本命番手の併用" in osae_reasons, (
        f"押さえに本命先頭-別線番手-本命番手 形が無い:\n{osae_reasons}"
    )


# ---------------------------------------------------------------------------
# 追加: 「ガミになりやすい買い目」からの market_odds=None 除外
# ---------------------------------------------------------------------------


def test_main_with_bessen_lead_adds_anaclassic_form():
    """『本命先頭-別線自力-別線番手』トレンド時、別線自力頭の波乱形 sep_l-sep_s-ll が穴に。

    武雄3R 例: 2R 3-2-8 → 次レースで 別線自力(2)-別線番手(8)-本命先頭(3) のような形が穴に。
    """
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["recent_results"] = [
        {"date": "2026-05-23", "venue": "武雄", "race_no": 2,
         "result": "3-2-8", "memo": "本命先頭-別線自力-別線番手"},
    ]
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    ana_reasons = " / ".join(b.reason for b in bets["穴"])
    assert "別線自力頭-別線番手-本命の波乱形" in ana_reasons, (
        f"穴に別線自力頭の波乱形 (sep_l-sep_s-ll) が無い:\n"
        f"穴: {[b.combination + ': ' + b.reason for b in bets['穴']]}"
    )


# ---------------------------------------------------------------------------
# 追加要件: ガミ警戒に 20倍以上を出さない
# ---------------------------------------------------------------------------


def test_gami_section_excludes_high_odds():
    """ガミ警戒セクションから market_odds >= 20 の買い目を除外（仕様要件2）。"""
    from app.cli import _summarize_for_final
    from app.models import BetRecommendation, Prediction

    p = Prediction(
        race_id="t-1", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.8, market_odds=4.0,  # 安オッズ
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="9-4-2",
                reason="t", gami_risk=0.6, market_odds=41.1,  # 高オッズ
            ),
        ],
        osae=[],
        ana=[],
        ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    gami_section = text.split("### ガミになりやすい")[1].split("### 実購入判断")[0]
    # 安オッズはガミ警戒に出る
    assert "1-2-3" in gami_section
    # 高オッズ (41.1倍) はガミ警戒から除外
    assert "9-4-2" not in gami_section, (
        f"market_odds=41.1 がガミ警戒に出ている: {gami_section}"
    )


def test_gami_section_threshold_exactly_20():
    """20.0倍ピッタリはガミ警戒に出ない（< 20 が条件）。"""
    from app.cli import _summarize_for_final
    from app.models import BetRecommendation, Prediction

    p = Prediction(
        race_id="t-1", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.8, market_odds=20.0,  # 境界値
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    gami_section = text.split("### ガミになりやすい")[1].split("### 実購入判断")[0]
    assert "1-2-3" not in gami_section


def test_gami_section_excludes_odds_none():
    """『ガミになりやすい買い目』に market_odds=None の買い目を含めない（仕様要件3）。"""
    from app.cli import _summarize_for_final
    from app.models import BetRecommendation, Prediction

    p = Prediction(
        race_id="t-1", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[BetRecommendation(
            category="本線", bet_type="3連単", combination="1-2-3",
            reason="t", gami_risk=0.8, market_odds=3.0,  # オッズあり
        )],
        osae=[],
        ana=[BetRecommendation(
            category="穴", bet_type="3連単", combination="9-1-2",
            reason="t", gami_risk=0.8, market_odds=None,  # オッズ未取得
        )],
        ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    gami_section = text.split("### ガミになりやすい")[1].split("### 実購入判断")[0]
    assert "1-2-3" in gami_section  # オッズあり → 表示
    assert "9-1-2" not in gami_section  # オッズ未取得 → 除外


def test_main_then_bessen_third_trend_signal_detection():
    """memo の各種フレーズで main_then_bessen_third_count がカウントされる。"""
    from app.scoring import analyze_recent
    from app.models import RecentResult
    from datetime import date

    results = [
        RecentResult(date=date(2026, 5, 23), venue="武雄", race_no=1,
                     result="1-9-5", memo="本線先頭-番手-別線番手"),
    ]
    trend = analyze_recent(results)
    assert trend.is_main_then_bessen_third is True

    results2 = [
        RecentResult(date=date(2026, 5, 23), venue="武雄", race_no=1,
                     result="1-9-5", memo="本命自力-本命番手-別線番手"),
    ]
    trend2 = analyze_recent(results2)
    assert trend2.is_main_then_bessen_third is True

    # キーワード無しなら False
    results3 = [
        RecentResult(date=date(2026, 5, 23), venue="武雄", race_no=1,
                     result="1-9-3", memo="本命ライン決着"),
    ]
    trend3 = analyze_recent(results3)
    assert trend3.is_main_then_bessen_third is False


def test_takeo_1r_predict_taikou_is_9():
    """武雄1R fixture の最終結論の対抗が 9番（B9）になっている。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    md = render_prediction(prediction)
    assert "対抗は9番" in md, (
        f"対抗が9番でない（最終結論と印の整合性違反）:\n{md[:2000]}"
    )
