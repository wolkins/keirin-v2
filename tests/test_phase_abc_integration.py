"""フェーズ A/B/C の統合検証テスト。

3つの実レース型 fixture で、レース種別ごとの加点が期待通り効くかを確認:
- G3広島記念 準決勝: フェーズ A (グレード格上) + フェーズ C (地元中国地区)
- F1名古屋S級特選: フェーズ B (F1強化) + フェーズ C (中部地区)
- F2武雄チャレンジ: フェーズ B (F2点数差 + チャレンジ自力) + フェーズ C (F2:0.5係数)

スコア差分とパイプライン全体での挙動を検証する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import render_prediction
from app.llm_client import build_default_client
from app.models import RaceInput
from app.prompt_builder import build_full_prompt
from app.scoring import (
    apply_bank_signals,
    apply_f2_signals,
    apply_grade_signals,
    apply_home_area_signals,
    apply_market_signals,
    apply_reflection_signals,
    apply_tospo_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    compute_scores,
)
from app.value_analysis import (
    annotate_prediction_with_value,
    promote_oddful_to_honsen,
    promote_oddful_to_osae,
)


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> RaceInput:
    return RaceInput.model_validate(
        json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    )


def _full_pipeline(ri: RaceInput):
    """フェーズA/B/C を含む完全パイプライン。"""
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_grade_signals(scores, ri)
    apply_f2_signals(scores, ri)
    apply_home_area_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    bets = build_candidate_bets(ri, scores)
    return scores, bets


def _prediction(ri: RaceInput, scores, bets):
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(pred, scores, ri.odds)
    promote_oddful_to_osae(pred)
    promote_oddful_to_honsen(pred)
    return pred


def _signals_only(ri: RaceInput, *, with_phase_abc: bool):
    """フェーズ A/B/C を on/off で比較するための分割パイプライン。"""
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    if with_phase_abc:
        apply_grade_signals(scores, ri)
        apply_f2_signals(scores, ri)
        apply_home_area_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    return scores


# ---------------------------------------------------------------------------
# G3広島記念 準決勝: 地元中国 + 格上番手・3番手・別線番手・単騎
# ---------------------------------------------------------------------------


class TestG3HiroshimaKinen:
    """G3 準決勝。グレード加点 + 地元加点が同時に効くケース。

    fixture:
      - 1番(中国, 自力, 108.5pt) - 本命先頭
      - 3番(中国, 番手, 102.3pt) - 格上 + 地元
      - 4番(中国, 3番手, 85.0pt) - 地元のみ
      - 5番(九州, 別線番手, 96.8pt) - 別線番手
      - 6番(近畿, 単騎, 100.2pt) - 単騎格上
      - 7番(九州, 別線自力, 98.5pt) - 別線先頭
    """

    def test_pipeline_runs_without_error(self):
        ri = _load("g3_hiroshima_kinen_semifinal.json")
        scores, bets = _full_pipeline(ri)
        assert len(scores) == 7
        assert len(bets["本線"]) >= 1

    def test_local_kakujou_bantan_scoring_boost(self):
        """3番(地元 + 格上番手) は phase ABC で win_score 加点。"""
        ri = _load("g3_hiroshima_kinen_semifinal.json")
        before = _signals_only(ri, with_phase_abc=False)
        after = _signals_only(ri, with_phase_abc=True)
        before_win = next(s.win_score for s in before if s.car_no == 3)
        after_win = next(s.win_score for s in after if s.car_no == 3)
        # グレード加点(0.4*1.0) + 地元加点(0.2*1.2) が入る
        assert after_win > before_win + 0.5, (
            f"3番(地元格上番手) の win 加点不足: {before_win:.2f} → {after_win:.2f}"
        )

    def test_local_third_scoring_boost(self):
        """4番(地元 + 本命3番手) は地元加点のみ (得点85→格上ではない)。"""
        ri = _load("g3_hiroshima_kinen_semifinal.json")
        before = _signals_only(ri, with_phase_abc=False)
        after = _signals_only(ri, with_phase_abc=True)
        before_second = next(s.second_score for s in before if s.car_no == 4)
        after_second = next(s.second_score for s in after if s.car_no == 4)
        # 4番は score=85 で G3閾値=100 未満 → グレード加点なし。地元加点のみ。
        assert after_second > before_second

    def test_local_tanki_kakujou_boost(self):
        """6番(近畿単騎, 100.2pt, 地元ではない) はグレード加点のみ。"""
        ri = _load("g3_hiroshima_kinen_semifinal.json")
        before = _signals_only(ri, with_phase_abc=False)
        after = _signals_only(ri, with_phase_abc=True)
        before_third = next(s.third_score for s in before if s.car_no == 6)
        after_third = next(s.third_score for s in after if s.car_no == 6)
        # 6番 100.2pt → G3閾値=100 で格上判定 → グレード単騎加点 0.3
        # ただし地元(近畿)≠会場地区(中国) なので地元加点なし
        assert after_third > before_third

    def test_separate_bantan_kakujou(self):
        """5番(九州別線番手, 96.8pt) は G3閾値=100 未満なのでグレード加点なし。"""
        ri = _load("g3_hiroshima_kinen_semifinal.json")
        before = _signals_only(ri, with_phase_abc=False)
        after = _signals_only(ri, with_phase_abc=True)
        before_third = next(s.third_score for s in before if s.car_no == 5)
        after_third = next(s.third_score for s in after if s.car_no == 5)
        # 5番は地元ではない(九州≠中国)+格上ではない(96.8<100)
        assert after_third == before_third

    def test_predict_markdown_contains_main_line(self):
        ri = _load("g3_hiroshima_kinen_semifinal.json")
        scores, bets = _full_pipeline(ri)
        pred = _prediction(ri, scores, bets)
        md = render_prediction(pred)
        # 本命ライン中心の本線が出ている
        assert "1-3" in md or "3-1" in md


# ---------------------------------------------------------------------------
# F1名古屋S級特選: F1係数 + 中部地区
# ---------------------------------------------------------------------------


class TestF1NagoyaSTokusen:
    """F1 S級特選。F1格上閾値=95 で番手・別線番手が加点される。

    fixture:
      - 1番(中部, 自力, 105.0pt)
      - 3番(中部, 番手, 99.5pt) - F1格上(95+) + 地元
      - 5番(近畿, 別線番手, 97.8pt) - F1格上
      - 7番(近畿, 別線自力, 94.0pt) - F1格上ぎりぎり下
    """

    def test_pipeline_runs(self):
        ri = _load("f1_nagoya_s_tokusen.json")
        scores, bets = _full_pipeline(ri)
        assert len(scores) == 8

    def test_f1_kakujou_bantan_boost(self):
        """3番(中部F1格上番手) は phase ABC で win_score 加点。"""
        ri = _load("f1_nagoya_s_tokusen.json")
        before = _signals_only(ri, with_phase_abc=False)
        after = _signals_only(ri, with_phase_abc=True)
        before_win = next(s.win_score for s in before if s.car_no == 3)
        after_win = next(s.win_score for s in after if s.car_no == 3)
        # F1グレード加点 0.4*1.0 + 地元加点 0.2*1.0 = 0.6 程度
        assert after_win > before_win + 0.4

    def test_f1_separate_bantan_boost(self):
        """5番(近畿別線番手, 97.8pt) は F1閾値=95 で格上判定。"""
        ri = _load("f1_nagoya_s_tokusen.json")
        before = _signals_only(ri, with_phase_abc=False)
        after = _signals_only(ri, with_phase_abc=True)
        before_third = next(s.third_score for s in before if s.car_no == 5)
        after_third = next(s.third_score for s in after if s.car_no == 5)
        # 別線番手格上で third 加点 0.3
        assert after_third > before_third

    def test_no_f2_signal_for_f1(self):
        """F1 では apply_f2_signals は加点しない (点数差大でも)。"""
        ri = _load("f1_nagoya_s_tokusen.json")
        # 1番 vs 2番の score 差 = 105 - 92 = 13点 (大きい)
        # F2 加点が入るなら 1番 win_score が増えるが、F1なので入らない
        # → F1 グレード加点も 1番(自力先頭)には入らない（先頭は加点対象外）
        scores = compute_scores(ri)
        before_win = next(s.win_score for s in scores if s.car_no == 1)
        # apply_f2_signals 単独実行
        apply_f2_signals(scores, ri)
        after_win = next(s.win_score for s in scores if s.car_no == 1)
        assert after_win == before_win

    def test_predict_works(self):
        ri = _load("f1_nagoya_s_tokusen.json")
        scores, bets = _full_pipeline(ri)
        pred = _prediction(ri, scores, bets)
        md = render_prediction(pred)
        assert "## 10. 最終結論" in md


# ---------------------------------------------------------------------------
# F2武雄チャレンジ: F2点数差大 + チャレンジ若手自力
# ---------------------------------------------------------------------------


class TestF2TakeoChallenge:
    """F2 チャレンジ。点数差 + 自力タイプ + ライン3車加点。

    fixture:
      - 1番(九州, 自力若手, 88.5pt, nige=5, makuri=2) - top1, 自力
      - 3番(九州, 番手, 78.0pt) - top2
      - 4番(九州, 3番手, 70.0pt)
      - point diff (top1-top2) = 10.5 (大)
    """

    def test_pipeline_runs(self):
        ri = _load("f2_takeo_challenge.json")
        scores, bets = _full_pipeline(ri)
        assert len(scores) == 8

    def test_f2_large_score_diff_boost(self):
        """1番 top1 で点数差大(10pt+) → win_score 加点 0.4。"""
        ri = _load("f2_takeo_challenge.json")
        # apply_f2_signals 単独で検証
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 1)
        apply_f2_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 1)
        # 点数差大 0.4 + チャレンジ自力 0.3 = 0.7
        assert after >= before + 0.6, (
            f"F2チャレンジ加点不足: {before:.2f} → {after:.2f}"
        )

    def test_f2_three_car_line_third_boost(self):
        """ライン3車(九州 1-3-4) なら 4番の third_score 加点。"""
        ri = _load("f2_takeo_challenge.json")
        scores = compute_scores(ri)
        before = next(s.third_score for s in scores if s.car_no == 4)
        apply_f2_signals(scores, ri)
        after = next(s.third_score for s in scores if s.car_no == 4)
        assert after > before

    def test_no_grade_signal_for_f2(self):
        """F2 では apply_grade_signals は加点しない (number=99pt以下が複数いても)。"""
        ri = _load("f2_takeo_challenge.json")
        scores = compute_scores(ri)
        before_all = [(s.car_no, s.win_score) for s in scores]
        apply_grade_signals(scores, ri)
        after_all = [(s.car_no, s.win_score) for s in scores]
        assert before_all == after_all

    def test_f2_home_area_milder_than_grade(self):
        """F2 の地元加点は係数 0.5 で控えめ (G3より弱い)。

        武雄=九州、1番が地元九州自力先頭(88.5pt)。
        ただし apply_home_area_signals は「先頭」を加点しない (番手/3番手/単騎のみ)。
        → 3番(九州番手) を見るが、F2閾値=85で78pt は格上ではないため、
           home_area加点だけが入る (grade加点はスキップ)。
        """
        ri = _load("f2_takeo_challenge.json")
        scores = compute_scores(ri)
        before = next(s.win_score for s in scores if s.car_no == 3)
        apply_home_area_signals(scores, ri)
        after = next(s.win_score for s in scores if s.car_no == 3)
        # F2係数 0.5 で 0.2 * 0.5 = 0.1 の控えめ加点
        assert after > before
        delta = after - before
        assert delta < 0.2, f"F2の地元加点が大きすぎる: +{delta:.2f}"

    def test_predict_markdown_no_line_term_violation(self):
        """F2 では「ライン」「番手」用語が出てOK（チャレンジでも個人戦扱いではない）。"""
        ri = _load("f2_takeo_challenge.json")
        scores, bets = _full_pipeline(ri)
        pred = _prediction(ri, scores, bets)
        md = render_prediction(pred)
        # 何らかの予想が出ている
        assert "## 6. 本線" in md


# ---------------------------------------------------------------------------
# 全 fixture を通したスモーク・テスト
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", [
    "g3_hiroshima_kinen_semifinal.json",
    "f1_nagoya_s_tokusen.json",
    "f2_takeo_challenge.json",
])
def test_all_fixtures_complete_pipeline(fixture_name):
    """3 fixture すべてが完全パイプラインを通る (例外なし)。"""
    ri = _load(fixture_name)
    scores, bets = _full_pipeline(ri)
    pred = _prediction(ri, scores, bets)
    md = render_prediction(pred)
    # 必須セクションが揃う
    assert "## 6. 本線" in md
    assert "## 7. 押さえ" in md
    assert "## 10. 最終結論" in md
    assert "### 実購入判断" in md
