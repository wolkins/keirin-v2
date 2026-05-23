"""仕様: 本命ライン優先の本線生成テスト。

ユーザー指摘の事例:
- 並びが 1-9-6 / 2-4 / 3-7 / 8-5 のとき
- 1番が win_score 最上位
- 9番が second
- 6番が third
このとき:
- honsen に 1-9-* または 9-1-* が **最低1点以上** 含まれる
- 1-2-3 (別線スコア上位フォーメーション) が **honsen 最上位に来ない**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import RaceInput
from app.scoring import build_candidate_bets, compute_scores


def _user_case_input() -> RaceInput:
    """ユーザー指摘の並び 1-9-6 / 2-4 / 3-7 / 8-5 を再現。

    1番に win_score 最上位を取らせるため、得点と先行型を強めに設定。
    """
    return RaceInput.model_validate({
        "race": {
            "race_id": "20260523-test-1",
            "date": "2026-05-23",
            "venue": "平塚",
            "race_no": 1,
            "class_name": "S級特選",
        },
        "weather": {
            "condition": "晴れ",
            "rain_mm_per_hour": 0.0,
            "wind_speed_mps": 1.0,  # 微風
        },
        "lines": [
            {"line_name": "ライン1", "cars": [1, 9, 6], "description": "1-9-6"},
            {"line_name": "ライン2", "cars": [2, 4], "description": "2-4"},
            {"line_name": "ライン3", "cars": [3, 7], "description": "3-7"},
            {"line_name": "ライン4", "cars": [8, 5], "description": "8-5"},
        ],
        "riders": [
            # 1番: 圧倒的1着候補（line_leader）
            {"car_no": 1, "name": "S1", "score": 105.0,
             "b_count": 8, "nige": 6, "makuri": 3, "sashi": 0, "mark": 0,
             "comment": "自力", "recent_summary": "GP級",
             "style_tags": ["先行", "自力", "捲り"]},
            # 2番: 別線先頭 (スコア2位)
            {"car_no": 2, "name": "S2", "score": 92.0,
             "b_count": 4, "nige": 3, "makuri": 1, "sashi": 0, "mark": 0,
             "comment": "自力", "recent_summary": "別線",
             "style_tags": ["先行", "自力"]},
            # 3番: 別線先頭 (スコア3位)
            {"car_no": 3, "name": "S3", "score": 90.5,
             "b_count": 3, "nige": 3, "makuri": 1, "sashi": 0, "mark": 0,
             "comment": "自力",
             "style_tags": ["先行", "自力"]},
            # 9番: 本命ライン番手（second）
            {"car_no": 9, "name": "B9", "score": 80.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 5, "mark": 3,
             "comment": "番手",
             "style_tags": ["番手", "差し"]},
            # 6番: 本命ライン3番手（third）
            {"car_no": 6, "name": "B6", "score": 75.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 1, "mark": 5,
             "comment": "3番手",
             "style_tags": ["3番手", "追込"]},
            # 4番: 別線番手 (separate_second)
            {"car_no": 4, "name": "S4", "score": 78.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 3, "mark": 3,
             "comment": "番手",
             "style_tags": ["番手", "差し"]},
            # 7番: 別線3番手
            {"car_no": 7, "name": "S7", "score": 72.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 1, "mark": 3,
             "comment": "3番手",
             "style_tags": ["3番手"]},
            # 8番: 別線先頭
            {"car_no": 8, "name": "S8", "score": 76.0,
             "b_count": 2, "nige": 2, "makuri": 0, "sashi": 0, "mark": 0,
             "comment": "自力",
             "style_tags": ["先行", "自力"]},
            # 5番: 別線番手
            {"car_no": 5, "name": "B5", "score": 73.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 2, "mark": 2,
             "comment": "番手",
             "style_tags": ["番手"]},
        ],
        "odds": [],
        "recent_results": [],
    })


# ---------------------------------------------------------------------------
# 仕様要件のテスト
# ---------------------------------------------------------------------------


def test_user_case_top1_is_main_leader():
    """前提確認: 1番が win_score 最上位、かつ line_leader である。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    top1 = max(scores, key=lambda s: s.total())
    assert top1.car_no == 1, (
        f"1番が win_score 最上位であるべき。実際: {top1.car_no}"
    )


def test_honsen_contains_1_9_or_9_1():
    """honsen に 1-9-* または 9-1-* が最低1点以上含まれる（仕様要件6,9）。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]

    has_1_9_x = any(
        c.startswith("1-9-") for c in honsen_combos
    )
    has_9_1_x = any(
        c.startswith("9-1-") for c in honsen_combos
    )
    assert has_1_9_x or has_9_1_x, (
        f"本線に 1-9-* または 9-1-* が無い: {honsen_combos}"
    )


def test_honsen_first_is_not_1_2_3():
    """1-2-3（別線スコア上位フォーメーション）が honsen の最上位に来ない（仕様要件5,9）。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen = bets["本線"]
    assert len(honsen) >= 1
    first = honsen[0].combination
    assert first != "1-2-3", (
        f"本線最上位が 1-2-3 になってはいけない（別線同士のスコア上位フォーメーション）。"
        f"実際: {first}"
    )


def test_main_line_third_in_top_buckets():
    """本命ライン3番手（6番）が本線・押さえに最低1回登場する（仕様要件4）。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    top_buckets = bets["本線"] + bets["押さえ"]
    # 6番が combination に登場するか
    found_6 = any("6" in b.combination.split("-") for b in top_buckets)
    assert found_6, (
        "本命ライン3番手 (6番) が本線・押さえに1度も登場していない。"
        f"本線: {[b.combination for b in bets['本線']]} / "
        f"押さえ: {[b.combination for b in bets['押さえ']]}"
    )


def test_main_line_second_in_top_buckets():
    """本命ライン番手（9番）が本線・押さえに必ず含まれる（仕様要件3）。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    top_buckets = bets["本線"] + bets["押さえ"]
    found_9 = any("9" in b.combination.split("-") for b in top_buckets)
    assert found_9, (
        "本命ライン番手 (9番) が本線・押さえに含まれていない。"
        f"本線: {[b.combination for b in bets['本線']]} / "
        f"押さえ: {[b.combination for b in bets['押さえ']]}"
    )


def test_score_top_3_combo_in_osae_or_穴_not_honsen():
    """スコア上位3名フォーメーション(1-2-3) が、本線ではなく押さえ or 穴に出る（仕様要件5）。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    osae_combos = [b.combination for b in bets["押さえ"]]
    ana_combos = [b.combination for b in bets["穴"]]
    # 1-2-3 が本線には無い
    assert "1-2-3" not in honsen_combos, (
        f"スコア上位フォーメーション 1-2-3 が本線にある: {honsen_combos}"
    )
    # 押さえ/穴のどこかに居る（reason が「ズレ目扱い」または既存ロジック由来）
    found = (
        "1-2-3" in osae_combos
        or "1-2-3" in ana_combos
    )
    assert found, (
        "スコア上位フォーメーション 1-2-3 が押さえ/穴のどこにも見当たらない: "
        f"押さえ={osae_combos}, 穴={ana_combos}"
    )


def test_main_line_three_form_appears():
    """仕様要件6: 本命ラインの先頭-番手-3番手 (1-9-6) が本線に入る。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    assert "1-9-6" in honsen_combos, (
        f"本命ライン先頭-番手-3番手 (1-9-6) が本線に無い: {honsen_combos}"
    )


# ---------------------------------------------------------------------------
# ガールズはこの制約を緩める（仕様要件8）
# ---------------------------------------------------------------------------


def test_girls_uses_score_priority_not_line_logic():
    """ガールズではライン無視・スコア優先（仕様10章）。"""
    raw = {
        "race": {
            "race_id": "20260523-girls-1",
            "date": "2026-05-23",
            "venue": "平塚",
            "race_no": 6,
            "class_name": "ガールズ予選",
            "is_girls": True,
        },
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 1.0},
        "lines": [],
        "riders": [
            {"car_no": i, "name": f"G{i}", "score": 60.0 - i,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 0, "mark": 0,
             "style_tags": ["自在"]}
            for i in range(1, 8)
        ],
        "odds": [], "recent_results": [],
    }
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(b.reason for b in bets["本線"])
    # ガールズでは「本命ライン」由来の reason が **本線には出ない**
    assert "本命ライン" not in reasons, (
        f"ガールズで本命ライン由来の本線 reason が出ている: {reasons}"
    )
    # 代わりに「スコア上位」または「ガールズ:」由来の reason が出る
    assert (
        "上位3" in reasons or "ガールズ" in reasons or "素直" in reasons
    ), f"ガールズでスコア優先の本線 reason が無い: {reasons}"


# ---------------------------------------------------------------------------
# 本命ライン無し（top1 が単騎）の場合はスコア優先（仕様要件8: 新人戦）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 仕様要件7の厳格テスト
# ---------------------------------------------------------------------------


def test_honsen_first_three_are_main_line_only():
    """本線の **最初の3点** が、本命ラインの3車（1,9,6）のみで構成される（仕様要件1,6）。

    別線の車番（2,3,4,5,7,8）が本線の最上位3点に **混じってはいけない**。
    """
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen = bets["本線"]
    # 3番手2着上がりは押さえに降格されるため、本線が3点未満になり得る。
    # ここで検証するのは「本線に本命ライン外の車番が混じらない」点。
    assert len(honsen) >= 1
    main_line_set = {1, 9, 6}
    for i, b in enumerate(honsen[:3]):
        cars = set(int(c) for c in b.combination.split("-"))
        assert cars.issubset(main_line_set), (
            f"本線 {i+1}点目 '{b.combination}' が本命ライン外を含む。\n"
            f"  別線車番: {sorted(cars - main_line_set)}\n"
            f"  全本線: {[h.combination for h in honsen]}"
        )


def test_main_line_three_form_is_first():
    """本線最上位は『1-9-6』（本命ライン: 先頭-番手-3番手）であるべき（仕様要件2）。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert bets["本線"], "本線が空"
    first = bets["本線"][0].combination
    assert first == "1-9-6", (
        f"本線最上位は 1-9-6（先頭-番手-3番手）であるべき。実際: {first}\n"
        f"全本線: {[b.combination for b in bets['本線']]}"
    )


def test_9_1_x_in_honsen_or_osae_top():
    """9-1-* （番手頭）が本線または押さえ上位に含まれる（仕様要件5）。"""
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    in_honsen = any(b.combination.startswith("9-1-") for b in bets["本線"])
    in_osae_top = any(
        b.combination.startswith("9-1-") for b in bets["押さえ"][:3]
    )
    assert in_honsen or in_osae_top, (
        "9-1-* が本線または押さえ上位3点に無い。\n"
        f"本線: {[b.combination for b in bets['本線']]}\n"
        f"押さえ: {[b.combination for b in bets['押さえ']]}"
    )


def test_main_line_third_reason_uses_main_line_cars_only():
    """『本命ライン3番手』という reason の買い目は、本命ライン内の車番だけを使う（仕様要件4）。

    例: 1-2-6 のように second が別線 (2番) になっているものを、この理由で説明しない。
    """
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    main_line_set = {1, 9, 6}
    all_bets = bets["本線"] + bets["押さえ"] + bets["穴"] + bets["大穴"]
    for b in all_bets:
        if "本命ライン3番手" not in b.reason:
            continue
        cars = set(int(c) for c in b.combination.split("-"))
        # 「本命ライン3番手」を語る買い目は、本命ライン内の車だけで構成
        assert cars.issubset(main_line_set), (
            f"買い目 '{b.combination}' は『本命ライン3番手』を理由にしているが、"
            f"本命ライン外を含む: {sorted(cars - main_line_set)}\n"
            f"  reason: {b.reason}"
        )


def test_pad_does_not_pollute_top_with_score_top_combos():
    """_pad（自動補充）でスコア上位フォーメーション 1-2-3 / 1-3-2 / 1-2-8 が
    本線に混入していないか確認（仕様要件3,6）。
    """
    ri = _user_case_input()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = {b.combination for b in bets["本線"]}
    # スコア上位の典型的な「ズレ目」が本線に混入していない
    forbidden = {"1-2-3", "1-3-2", "1-2-8"}
    intrusion = forbidden & honsen_combos
    assert not intrusion, (
        f"本線に別線混合のズレ目が混入: {intrusion}\n"
        f"全本線: {honsen_combos}"
    )


def test_top1_as_bantan_uses_same_main_line():
    """top1 が **second** であっても、所属ラインを本命にする（仕様要件1）。

    シナリオ:
        - 並び: 1-9-6 / 2-4 / 3-7 / 8-5
        - 9番が win_score 最上位（差し型で得点が高い）
        - 1番は line_leader だが得点で 9番に劣る

    期待:
        - 本命ライン=1-9-6 と判定される（9 が second なので 9 のライン）
        - 本線に 1-9-6 / 9-1-6 等が入る
    """
    raw = json.loads(
        json.dumps(json.loads(Path(__file__).read_text(encoding="utf-8")))
        if False else "{}"
    ) if False else None
    # 上記の dirty hack を避けて、直接構築する
    ri = RaceInput.model_validate({
        "race": {
            "race_id": "20260523-test-3",
            "date": "2026-05-23",
            "venue": "平塚",
            "race_no": 1,
            "class_name": "S級特選",
        },
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 1.0},
        "lines": [
            {"line_name": "ライン1", "cars": [1, 9, 6], "description": "1-9-6"},
            {"line_name": "ライン2", "cars": [2, 4], "description": "2-4"},
            {"line_name": "ライン3", "cars": [3, 7], "description": "3-7"},
            {"line_name": "ライン4", "cars": [8, 5], "description": "8-5"},
        ],
        "riders": [
            # 1番: line_leader（先行）。得点は中位
            {"car_no": 1, "name": "L1", "score": 88.0,
             "b_count": 4, "nige": 4, "makuri": 1, "sashi": 0, "mark": 0,
             "style_tags": ["先行", "自力"]},
            # 9番: second（差し型）。得点最上位
            {"car_no": 9, "name": "B9", "score": 105.0,
             "b_count": 1, "nige": 0, "makuri": 0, "sashi": 8, "mark": 4,
             "style_tags": ["番手", "差し"]},
            {"car_no": 6, "name": "B6", "score": 75.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 1, "mark": 5,
             "style_tags": ["3番手", "追込"]},
            {"car_no": 2, "name": "S2", "score": 92.0,
             "b_count": 3, "nige": 3, "makuri": 1, "sashi": 0, "mark": 0,
             "style_tags": ["先行", "自力"]},
            {"car_no": 3, "name": "S3", "score": 90.0,
             "b_count": 3, "nige": 3, "makuri": 1, "sashi": 0, "mark": 0,
             "style_tags": ["先行", "自力"]},
            {"car_no": 4, "name": "S4", "score": 78.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 3, "mark": 3,
             "style_tags": ["番手"]},
            {"car_no": 5, "name": "S5", "score": 73.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 2, "mark": 2,
             "style_tags": ["番手"]},
            {"car_no": 7, "name": "S7", "score": 72.0,
             "b_count": 0, "nige": 0, "makuri": 0, "sashi": 1, "mark": 3,
             "style_tags": ["3番手"]},
            {"car_no": 8, "name": "S8", "score": 76.0,
             "b_count": 2, "nige": 2, "makuri": 0, "sashi": 0, "mark": 0,
             "style_tags": ["先行", "自力"]},
        ],
        "odds": [], "recent_results": [],
    })
    scores = compute_scores(ri)
    top1 = max(scores, key=lambda s: s.total())
    assert top1.car_no == 9, f"前提: 9番がtop1。実際: {top1.car_no}"

    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]

    # 本命ライン (1-9-6) が本線最上位3点で表現されているか
    main_line_set = {1, 9, 6}
    for c in honsen_combos[:3]:
        cars = set(int(x) for x in c.split("-"))
        assert cars.issubset(main_line_set), (
            f"top1=9（second）でも本命ラインは 1-9-6 のはず。実際の本線: {honsen_combos}"
        )


def test_solo_top1_falls_back_to_score_priority():
    """top1 が単騎の場合は本命ラインが特定できず、スコア優先にフォールバック。"""
    raw = {
        "race": {
            "race_id": "20260523-test-2",
            "date": "2026-05-23",
            "venue": "平塚",
            "race_no": 1,
            "class_name": "A級予選",
        },
        "weather": {"condition": "晴れ", "rain_mm_per_hour": 0.0, "wind_speed_mps": 1.0},
        "lines": [
            {"line_name": "単騎", "cars": [1], "description": "1"},  # 1番単騎
            {"line_name": "ライン1", "cars": [2, 3, 4], "description": "2-3-4"},
            {"line_name": "ライン2", "cars": [5, 6, 7], "description": "5-6-7"},
        ],
        "riders": [
            # 1番が圧倒的トップだが単騎
            {"car_no": 1, "name": "T1", "score": 105.0,
             "b_count": 0, "nige": 0, "makuri": 3, "sashi": 0, "mark": 0,
             "style_tags": ["単騎", "自在", "捲り"]},
            *[
                {"car_no": i, "name": f"P{i}", "score": 80.0 - i,
                 "b_count": 2 if i in (2, 5) else 0,
                 "nige": 2 if i in (2, 5) else 0,
                 "makuri": 0, "sashi": 2 if i in (3, 6) else 0,
                 "mark": 1, "style_tags": ["先行"] if i in (2, 5) else ["番手"]}
                for i in range(2, 8)
            ],
        ],
        "odds": [], "recent_results": [],
    }
    ri = RaceInput.model_validate(raw)
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores)
    reasons = " / ".join(b.reason for b in bets["本線"])
    # 単騎top1なので「本命ライン」由来でない（スコア優先 or 単騎フォールバック）
    # ただし build_candidate_bets が本命ライン優先モードを skip → スコア優先で
    # "上位3" もしくは「素直」由来の reason
    assert (
        "上位3" in reasons or "素直" in reasons
    ), f"単騎top1 でスコア優先の reason が無い: {reasons}"
