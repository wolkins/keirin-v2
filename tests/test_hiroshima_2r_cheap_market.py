"""広島2R ガールズ予選2 - 安すぎる市場人気の分離テスト。

市場の 1-2-3 (2.3倍) / 1-2-4 (4.2倍) が極端に安く、本線最上位にされてはいけない。

仕様要件:
- value_label="見送り寄り" が一番買いたいに出ない
- gami_risk >= 0.8 が一番買いたいに出ない
- market_odds < 5 が「ガミ注意」に分離
- ガールズ本線は最大3点
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import _summarize_for_final, render_prediction
from app.llm_client import build_default_client
from app.models import BetRecommendation, Prediction, RaceInput
from app.prompt_builder import build_full_prompt
from app.scoring import (
    apply_bank_signals,
    apply_market_signals,
    apply_reflection_signals,
    apply_tospo_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    compute_scores,
)
from app.value_analysis import annotate_prediction_with_value, promote_oddful_to_osae


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "hiroshima_2r_cheap_market.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _full(ri: RaceInput):
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    bets = build_candidate_bets(ri, scores)
    return scores, bets


def _full_prediction():
    ri = _load()
    scores, bets = _full(ri)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(pred, scores, ri.odds)
    promote_oddful_to_osae(pred)
    return pred


# ---------------------------------------------------------------------------
# ガールズ本線3点制限
# ---------------------------------------------------------------------------


def test_girls_honsen_max_3():
    """ガールズで本線が最大3点に絞られる。"""
    ri = _load()
    scores, bets = _full(ri)
    assert len(bets["本線"]) <= 3, f"ガールズ本線が3点超: {len(bets['本線'])}"


# ---------------------------------------------------------------------------
# 一番買いたい買い目から「見送り寄り」「高gami」「安すぎ」を除外
# ---------------------------------------------------------------------------


def test_top_pick_excludes_見送り寄り():
    """value_label='見送り寄り' は一番買いたいに出ない。"""
    pred = _full_prediction()
    text = _summarize_for_final(pred)
    top_section = text.split("### 押さえるべき")[0]
    assert "見送り寄り" not in top_section, (
        f"一番買いたい買い目に「見送り寄り」が出ている:\n{top_section}"
    )


def test_top_pick_excludes_high_gami():
    """gami_risk>=0.8 は一番買いたいに出ない（極小オッズ）。"""
    pred = _full_prediction()
    text = _summarize_for_final(pred)
    top_section = text.split("### 押さえるべき")[0]
    # 1-2-3 (gami=1.0) と 1-2-4 (gami=1.0) が出ていない
    assert "1-2-3" not in top_section
    assert "1-2-4" not in top_section


def test_top_pick_excludes_low_odds():
    """market_odds<5 が一番買いたいに出ない。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=True, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=0.5, market_odds=2.3,
                value_label="本線向き",  # 「本線向き」だが odds<5 で除外
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="5-4-6",
                reason="t", gami_risk=0.0, market_odds=35.0,
                value_label="妙味あり",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    top_section = text.split("### 押さえるべき")[0]
    assert "1-2-3" not in top_section  # 2.3倍 → 除外
    assert "5-4-6" in top_section


# ---------------------------------------------------------------------------
# ガミ警戒には残る（分離）
# ---------------------------------------------------------------------------


def test_cheap_odds_appear_in_gami_warning():
    """安すぎ + gami=1.0 はガミ警戒セクションに出る（分離）。"""
    pred = _full_prediction()
    text = _summarize_for_final(pred)
    gami_section = text.split("### ガミになりやすい")[1].split("### 実購入判断")[0]
    assert "1-2-3" in gami_section  # 2.3倍 / gami=1.0
    assert "1-2-4" in gami_section  # 4.2倍 / gami=1.0


# ---------------------------------------------------------------------------
# 押さえも「見送り寄り」を除外
# ---------------------------------------------------------------------------


def test_cover_pick_excludes_disqualified():
    """押さえるべき買い目からも「見送り寄り」「高gami」「安すぎ」を除外。"""
    pred = _full_prediction()
    text = _summarize_for_final(pred)
    cover_section = text.split("### 押さえるべき")[1].split("###")[0]
    assert "1-2-3" not in cover_section
    assert "1-2-4" not in cover_section
    assert "見送り寄り" not in cover_section


# ---------------------------------------------------------------------------
# 一番買いたい買い目が全部除外された場合の代替表示
# ---------------------------------------------------------------------------


def test_top_pick_empty_message():
    """全候補が除外された場合、『買うなら少額』案内を表示。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=True, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=1.0, market_odds=2.3,
                value_label="見送り寄り",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    top_section = text.split("### 押さえるべき")[0]
    assert "該当なし" in top_section or "少額" in top_section


# ---------------------------------------------------------------------------
# 本線セクションが「実購入候補」と「安い人気筋」に分離して表示される
# ---------------------------------------------------------------------------


def _pred_mixed_honsen() -> Prediction:
    """本線に「見送り寄り(安い人気)」と「妙味あり(実購入候補)」が混在するケース。"""
    return Prediction(
        race_id="t", venue="t", race_no=1, is_girls=True, marks={},
        honsen=[
            # 安い人気筋（除外対象）
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="人気上位", gami_risk=1.0, market_odds=2.3,
                value_label="見送り寄り",
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-4",
                reason="人気上位", gami_risk=1.0, market_odds=4.2,
                value_label="見送り寄り",
            ),
            # 実購入候補（妙味あり）
            BetRecommendation(
                category="本線", bet_type="3連単", combination="5-4-6",
                reason="スコア上位", gami_risk=0.0, market_odds=35.0,
                value_label="妙味あり",
            ),
        ],
        osae=[
            BetRecommendation(
                category="押さえ", bet_type="3連単", combination="5-3-4",
                reason="押さえ", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            ),
        ],
        ana=[], ooana=[],
        final_conclusion="",
    )


def test_honsen_section_separates_cheap_and_real_buys():
    """本線セクションが「実購入候補」と「安い人気筋(or 細分化ラベル)」に分離表示される。

    ガールズの場合、安い人気筋は「見送り寄り」「買うなら少額」「確認用」に
    3段階分離される (要件4)。
    """
    md = render_prediction(_pred_mixed_honsen())
    # 本線セクションを切り出し
    honsen_section = md.split("## 6. 本線")[1].split("## 7. 押さえ")[0]
    # 実購入候補ラベルがある
    assert "実購入候補" in honsen_section
    # 安い人気筋関連ラベル (通常 or ガールズ3段階) のいずれかが出る
    cheap_labels = [
        "安い人気筋", "見送り寄り（売れすぎ", "買うなら少額（人気だが",
        "確認用（参考",
    ]
    assert any(label in honsen_section for label in cheap_labels), (
        f"安い人気筋系のラベルが無い:\n{honsen_section}"
    )
    # 「実購入候補」が先、安い人気筋系が後（順序）
    real_idx = honsen_section.index("実購入候補")
    cheap_label_idx = min(
        honsen_section.index(lbl) for lbl in cheap_labels
        if lbl in honsen_section
    )
    assert real_idx < cheap_label_idx

    # 実購入候補に妙味あり買い目が入る
    real_part = honsen_section[:cheap_label_idx]
    cheap_part = honsen_section[cheap_label_idx:]
    assert "5-4-6" in real_part
    # 1-2-3 / 1-2-4 (見送り寄り) は cheap_part に
    assert "1-2-3" in cheap_part
    assert "1-2-4" in cheap_part
    assert "見送り寄り" not in real_part


def test_honsen_section_real_only_no_cheap_block():
    """安い人気筋が無いとき、安い人気筋セクションは表示されない。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=True, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="5-4-6",
                reason="t", gami_risk=0.0, market_odds=35.0,
                value_label="妙味あり",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    md = render_prediction(p)
    honsen_section = md.split("## 6. 本線")[1].split("## 7. 押さえ")[0]
    assert "安い人気筋" not in honsen_section


def test_honsen_section_cheap_only_message():
    """本線が全て安い人気筋のとき、実購入候補欄に案内文が出る。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=True, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="1-2-3",
                reason="t", gami_risk=1.0, market_odds=2.3,
                value_label="見送り寄り",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    md = render_prediction(p)
    honsen_section = md.split("## 6. 本線")[1].split("## 7. 押さえ")[0]
    # 実購入候補なし＋安い人気筋ありの状態
    assert "オッズ確認後" in honsen_section or "実購入候補なし" in honsen_section
    # ガールズなので「見送り寄り」(細分化ラベル) または「安い人気筋」
    assert (
        "安い人気筋" in honsen_section
        or "見送り寄り" in honsen_section
        or "買うなら少額" in honsen_section
        or "確認用" in honsen_section
    )


# ---------------------------------------------------------------------------
# 一番買いたい買い目がすべて market_odds=None の場合、確認メモを表示
# ---------------------------------------------------------------------------


def test_top_pick_all_odds_none_shows_check_memo():
    """top_pick の全候補が market_odds=None のとき、オッズ確認メモが出る。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="3-7-1",
                reason="本線最有力", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="7-3-1",
                reason="本線2点目", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    top_section = text.split("### 押さえるべき")[0]
    # honsen 全件 odds=None → 「オッズ確認後に判断する本線候補」セクションに切り替わる
    assert (
        "オッズ確認後に判断する本線候補" in top_section
        or "確定オッズを見てから購入判断" in top_section
    )


def test_top_pick_with_some_odds_no_check_memo():
    """top_pick に1つでも市場オッズあり買い目があれば確認メモは出ない。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="3-7-1",
                reason="本線", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            ),
            BetRecommendation(
                category="本線", bet_type="3連単", combination="7-3-1",
                reason="本線", gami_risk=0.0, market_odds=18.0,
                value_label="妙味あり",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    top_section = text.split("### 押さえるべき")[0]
    assert "オッズ取得後に購入判断" not in top_section


# ---------------------------------------------------------------------------
# 最終結論に「実購入判断」セクションが追加され、安い人気筋と実購入候補が分離
# ---------------------------------------------------------------------------


def test_final_judgement_section_present():
    """最終結論に「### 実購入判断」が出る。"""
    text = _summarize_for_final(_pred_mixed_honsen())
    assert "### 実購入判断" in text


def test_final_judgement_separates_buy_and_gami():
    """実購入判断で本線系ラベルと「ガミ注意」が両方出る。"""
    text = _summarize_for_final(_pred_mixed_honsen())
    judgement = text.split("### 実購入判断")[1]
    # 本線系ラベルが出る (本線として有力 / オッズ取得済みで買える候補 のいずれか)
    assert (
        "本線として有力" in judgement
        or "オッズ取得済みで買える候補" in judgement
    )
    assert "5-4-6" in judgement
    # ガミ注意 ⇒ 安い人気筋
    assert "売れすぎ" in judgement or "ガミ注意" in judgement
    assert "1-2-3" in judgement


def test_final_judgement_buy_excludes_cheap():
    """本線系ラベルの行に安い人気筋 (1-2-3 / 1-2-4) が含まれない。"""
    text = _summarize_for_final(_pred_mixed_honsen())
    judgement = text.split("### 実購入判断")[1]
    # 本線系ラベル行 (本線として有力 / オッズ取得済みで買える候補) を抜き出す
    buy_line = next(
        (line for line in judgement.split("\n")
         if ("本線として有力" in line
             or "オッズ取得済みで買える候補" in line)),
        "",
    )
    assert "1-2-3" not in buy_line
    assert "1-2-4" not in buy_line
    assert "5-4-6" in buy_line


def test_final_judgement_check_odds_when_top_all_none():
    """top_pick が全て odds=None のとき、確認メモが実購入判断に出る。"""
    p = Prediction(
        race_id="t", venue="t", race_no=1, is_girls=False, marks={},
        honsen=[
            BetRecommendation(
                category="本線", bet_type="3連単", combination="3-7-1",
                reason="本線", gami_risk=0.0, market_odds=None,
                value_label="オッズ未取得・要確認",
            ),
        ],
        osae=[], ana=[], ooana=[],
        final_conclusion="",
    )
    text = _summarize_for_final(p)
    judgement = text.split("### 実購入判断")[1]
    assert "オッズ" in judgement and ("確認" in judgement or "確定" in judgement)
