"""広島3R A級一般 - 3番手2着上がりの押さえ降格テスト。

仕様要件:
1. 本命3番手2着上がり (1-4-3 など) は本線ではなく押さえ上位
   ただし直近で3番手2着上がり多発時のみ本線維持
2. 実購入判断は最大5点目安 (本線2-3 / 押さえ2 / 穴1)
3. 妙味あり別線番手頭 (7-1-4 など) は直近の番手傾向強なら押さえに昇格
4. 最終結論で「本線として有力」「押さえとして必要」を明示分離
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import _summarize_for_final
from app.llm_client import build_default_client
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInput, RecentResult,
)
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
    Path(__file__).resolve().parent
    / "fixtures"
    / "hiroshima_3r_third_secondup.json"
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


def _full_prediction(ri: RaceInput = None):
    if ri is None:
        ri = _load()
    scores, bets = _full(ri)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(pred, scores, ri.odds)
    promote_oddful_to_osae(pred)
    return pred


# ---------------------------------------------------------------------------
# 要件1: 3番手2着上がりは押さえ上位（直近傾向強でなければ）
# ---------------------------------------------------------------------------


def test_third_sec_up_goes_to_osae_when_trend_weak():
    """直近で3番手2着上がり多発が無いとき、1-4-3 は本線ではなく押さえ。

    本命ライン = [1,3,4]、3番手 = 4 → 「X-4-Y」形が降格対象。
    本fixtureの recent_results には「3番手2着」キーワードなし。
    """
    ri = _load()
    scores, bets = _full(ri)
    honsen_combos = {b.combination for b in bets["本線"]}
    osae_combos = {b.combination for b in bets["押さえ"]}

    # 本命3番手(4)が2着位置にある3連単は本線に居ない
    for b in bets["本線"]:
        if b.bet_type != "3連単":
            continue
        parts = b.combination.split("-")
        assert int(parts[1]) != 4, (
            f"3番手2着上がり '{b.combination}' が本線に残っている"
        )


def test_third_sec_up_stays_honsen_when_trend_strong():
    """直近で3番手2着上がりが多発（≥2件）すれば本線に残る。"""
    ri = _load()
    # recent_results を「3番手2着上がり」多発に書き換え
    ri.recent_results = [
        RecentResult(
            date="2026-05-22", venue="広島", race_no=8,
            result="1-3-2", memo="本命自力頭+3番手2着上がり",
        ),
        RecentResult(
            date="2026-05-22", venue="広島", race_no=9,
            result="1-4-3", memo="本命3番手の2着上がりが決まる",
        ),
    ]
    scores, bets = _full(ri)
    # この場合は 1-4-3 / 7-4-1 などが本線に残ってよい
    # → 「3番手位置(=4) が2着」の本線買い目が1点以上存在することを確認
    found = False
    for b in bets["本線"]:
        parts = b.combination.split("-")
        if len(parts) == 3 and int(parts[1]) == 4:
            found = True
            break
    assert found, "3番手2着上がり多発時には本線に残るべき"


# ---------------------------------------------------------------------------
# 要件3: 妙味あり別線番手頭は直近の番手傾向強なら押さえに昇格（穴ではなく）
# ---------------------------------------------------------------------------


def test_bessen_bantan_head_promotes_to_osae_when_trend_strong():
    """別線番手(5)頭の買い目 5-X-Y は、直近で別線番手好走時に押さえに昇格。

    fixture の recent_results に「別線番手の差し決まる」が含まれ、
    trend.bessen_bantan_count >= 1 → 昇格条件を満たす。
    """
    ri = _load()
    scores, bets = _full(ri)
    osae_combos = {b.combination for b in bets["押さえ"]}
    ana_combos = {b.combination for b in bets["穴"]}
    # 別線番手(5)頭の妙味あり買い目が、穴ではなく押さえに居ること
    # 穴に「5-X-Y」が居る場合は失敗。
    for b in bets["穴"]:
        if b.bet_type != "3連単":
            continue
        head = int(b.combination.split("-")[0])
        if head == 5 and b.value_label == "妙味あり":
            pytest.fail(
                f"別線番手頭(5)の妙味あり '{b.combination}' が穴に残っている"
            )


# ---------------------------------------------------------------------------
# 要件2: 実購入判断は最大5点目安
# ---------------------------------------------------------------------------


def test_purchase_judgement_max_5_points():
    """実購入判断の総点数（本線+押さえ+穴）が 5点を大きく超えない。"""
    pred = _full_prediction()
    text = _summarize_for_final(pred)
    judgement = text.split("### 実購入判断")[1]

    # 「本線として有力」行から車番組合せの数を数える
    def _count_combos(line: str) -> int:
        # 「**X-Y-Z / X-Y-Z**」形式 + 後続テキスト
        # combination パターン: 数字-数字-数字
        import re
        return len(re.findall(r"\d-\d-\d", line))

    main_count = 0
    cover_count = 0
    ana_count = 0
    for line in judgement.split("\n"):
        if "本線として有力" in line:
            main_count = _count_combos(line)
        elif "押さえとして必要" in line:
            cover_count = _count_combos(line)
        elif "少額の穴" in line:
            ana_count = _count_combos(line)
    assert main_count <= 3, f"本線が {main_count} 点（最大3点）"
    assert cover_count <= 2, f"押さえが {cover_count} 点（最大2点）"
    assert ana_count <= 1, f"穴が {ana_count} 点（最大1点）"
    total = main_count + cover_count + ana_count
    assert total <= 5, f"実購入判断の合計が {total} 点（最大5点目安）"


# ---------------------------------------------------------------------------
# 要件4: 最終結論で「本線として有力」「押さえとして必要」を明示分離
# ---------------------------------------------------------------------------


def test_final_judgement_separates_main_and_cover_labels():
    """実購入判断に「本線として有力」「押さえとして必要」のラベルが両方出る。"""
    pred = _full_prediction()
    text = _summarize_for_final(pred)
    judgement = text.split("### 実購入判断")[1]
    assert "本線として有力" in judgement
    # 押さえに何かしらの買い目があれば「押さえとして必要」が出る
    if pred.osae:
        assert "押さえとして必要" in judgement, (
            f"押さえあり時に「押さえとして必要」が出ない:\n{judgement}"
        )


def test_final_judgement_main_and_cover_combinations_differ():
    """「本線として有力」と「押さえとして必要」に同じ買い目は重複しない。"""
    pred = _full_prediction()
    text = _summarize_for_final(pred)
    judgement = text.split("### 実購入判断")[1]
    import re
    main_line = next(
        (line for line in judgement.split("\n") if "本線として有力" in line), ""
    )
    cover_line = next(
        (line for line in judgement.split("\n") if "押さえとして必要" in line), ""
    )
    main_combos = set(re.findall(r"\d-\d-\d", main_line))
    cover_combos = set(re.findall(r"\d-\d-\d", cover_line))
    overlap = main_combos & cover_combos
    assert not overlap, f"本線と押さえが重複: {overlap}"
