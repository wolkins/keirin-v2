from __future__ import annotations

from app.prompt_builder import build_prompt
from app.scoring import build_candidate_bets, compute_scores


def test_prompt_contains_sections(sample_input):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    prompt = build_prompt(sample_input, scores, bets)
    # 主要なセクション見出しが含まれていること
    for header in [
        "競輪予想プロンプト",
        "1. レース概要",
        "12. 結果入力後に保存すべき反省ポイント",
        "race_json",
    ]:
        # 'race_json' は本来テンプレ内では消えるが、見出しは残る
        # 一方、レンダリング後は {race_json} が JSON に置き換わるので
        # ここでは見出しのみ確認
        if header == "race_json":
            assert "{race_json}" not in prompt
        else:
            assert header in prompt


def test_prompt_does_not_contain_html(sample_input):
    """生HTMLが混入しないこと。"""
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    prompt = build_prompt(sample_input, scores, bets)
    assert "<html" not in prompt.lower()
    assert "<div" not in prompt.lower()


def test_prompt_includes_scores_and_bets(sample_input):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    prompt = build_prompt(sample_input, scores, bets)
    # スコアJSONに含まれるキー
    assert "win_score" in prompt
    assert "本線" in prompt
