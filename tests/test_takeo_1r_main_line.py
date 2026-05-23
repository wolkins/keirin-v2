"""武雄1R 相当の fixture を使った一気通貫テスト。

仕様要件:
- 並び: 1-9-6 / 2-4 / 3-7 / 8-5
- 1番が win_score 最上位（◎）
- 9番が second、6番が third

検証:
A. build_candidate_bets 直後の本線・押さえに本命ライン構造が反映されている
   - honsen に 1-9-* が含まれる
   - honsen または osae に 9-1-* が含まれる
   - 1-2-3 が honsen の先頭でない
   - "本命ライン3番手" reason の買い目は本命ライン内の車番のみ

B. predict（mock LLM 経由）の最終出力 Markdown でも同じ構造を保つ
   - 本線セクションに 1-9-* が含まれる
   - 本線セクションの先頭が 1-2-3 ではない
   - LLM が買い目を勝手に書き換えていないこと
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli, render_prediction
from app.llm_client import build_default_client
from app.models import RaceInput
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
    resolve_rider_roles,
)
from app.value_analysis import annotate_prediction_with_value


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "takeo_1r_main_line.json"


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _full_pipeline_scores(ri: RaceInput):
    """予想本番と同じ補正順で scores を作る。"""
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    return scores


# ---------------------------------------------------------------------------
# A. build_candidate_bets 直後の検証
# ---------------------------------------------------------------------------


def test_roles_match_expected():
    """前提: roles が仕様通りに決まる。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    roles = resolve_rider_roles(ri, scores)
    assert roles[1] == "line_leader"
    assert roles[9] == "second"
    assert roles[6] == "third"
    assert roles[2] == "separate_leader"
    assert roles[4] == "separate_second"
    assert roles[3] == "separate_leader"
    assert roles[7] == "separate_second"
    assert roles[8] == "separate_leader"
    assert roles[5] == "separate_second"


def test_top1_is_car_1():
    """前提: ◎(top1) が 1番である。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    top1 = max(scores, key=lambda s: s.total())
    assert top1.car_no == 1


def test_honsen_contains_1_9_x():
    """honsen に 1-9-* が含まれる（仕様要件6.A）。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    has_1_9 = any(c.startswith("1-9-") for c in honsen_combos)
    assert has_1_9, (
        f"本線に 1-9-* が無い。仕様要件違反。\n  本線: {honsen_combos}"
    )


def test_honsen_or_osae_contains_9_1_x():
    """honsen または osae に 9-1-* が含まれる（仕様要件6.B）。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    bets = build_candidate_bets(ri, scores)
    honsen_combos = [b.combination for b in bets["本線"]]
    osae_combos = [b.combination for b in bets["押さえ"]]
    has_9_1 = (
        any(c.startswith("9-1-") for c in honsen_combos)
        or any(c.startswith("9-1-") for c in osae_combos)
    )
    assert has_9_1, (
        "honsen または osae に 9-1-* が無い。仕様要件違反。\n"
        f"  本線: {honsen_combos}\n  押さえ: {osae_combos}"
    )


def test_honsen_first_is_not_1_2_3():
    """1-2-3（別線同士のスコア上位フォーメーション）が honsen[0] でない（仕様要件6.C）。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    bets = build_candidate_bets(ri, scores)
    assert bets["本線"], "本線が空"
    first = bets["本線"][0].combination
    assert first != "1-2-3", (
        f"本線最上位が 1-2-3 になっている（別線混合フォーメーション）。仕様要件違反。\n"
        f"  全本線: {[b.combination for b in bets['本線']]}"
    )


def test_main_third_reason_is_main_line_only():
    """『本命ライン3番手』reason の買い目は本命ライン内の車番のみ（仕様要件6.D）。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    bets = build_candidate_bets(ri, scores)
    main_line_set = {1, 9, 6}
    violations = []
    for cat in ("本線", "押さえ", "穴", "大穴"):
        for b in bets[cat]:
            if "本命ライン3番手" not in b.reason:
                continue
            cars = set(int(c) for c in b.combination.split("-"))
            if not cars.issubset(main_line_set):
                violations.append((cat, b.combination, b.reason))
    assert not violations, (
        f"『本命ライン3番手』reason の買い目に別線混入: {violations}"
    )


def test_honsen_first_three_are_main_line_only():
    """本線の最初3点が本命ライン3車（1,9,6）のみで構成（厳格）。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    bets = build_candidate_bets(ri, scores)
    main_line_set = {1, 9, 6}
    honsen = bets["本線"]
    # 本命3番手2着上がりは押さえに降格されるため、本線が3点未満になることがある
    assert len(honsen) >= 1
    for i, b in enumerate(honsen[:3]):
        cars = set(int(c) for c in b.combination.split("-"))
        assert cars.issubset(main_line_set), (
            f"本線 {i+1}点目 '{b.combination}' に別線混入: {sorted(cars - main_line_set)}"
        )


# ---------------------------------------------------------------------------
# B. predict（mock LLM）最終出力での検証
# ---------------------------------------------------------------------------


def test_predict_markdown_keeps_main_line():
    """mock provider で predict した最終 Markdown でも本命ライン構造が保たれる。

    （LLM が買い目を勝手に書き換えていないこと）
    """
    ri = _load()
    scores = _full_pipeline_scores(ri)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)

    md = render_prediction(prediction)

    # 本線セクションを抽出
    lines = md.splitlines()
    honsen_section_lines: list[str] = []
    in_honsen = False
    for line in lines:
        if line.startswith("## 6. 本線"):
            in_honsen = True
            continue
        if in_honsen:
            if line.startswith("## "):
                break
            honsen_section_lines.append(line)

    honsen_text = "\n".join(honsen_section_lines)
    # 本線セクションに 1-9-* が必ず含まれる
    assert "1-9-" in honsen_text, (
        f"最終 Markdown の本線セクションに 1-9-* が無い。\n本線:\n{honsen_text}"
    )
    # 本線セクションの先頭は 1-2-3 でない
    first_line = next(
        (l for l in honsen_section_lines if "3連単" in l), ""
    )
    assert "1-2-3" not in first_line, (
        f"本線セクション先頭が 1-2-3 になっている: {first_line}"
    )


# ---------------------------------------------------------------------------
# C. 印と一番買いたいの整合性（仕様: 本線が 1-9 系中心なら 9 が ◯ または ▲）
# ---------------------------------------------------------------------------


def test_marks_second_is_circle_or_triangle():
    """本命ライン 1-9-6 で、9番が ◯ または ▲ に入る（仕様要件3,5）。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    from app.scoring import build_marks
    marks = build_marks(scores, ri)
    # 9 が ◯ (○) または ▲ に入る
    second_or_third = (marks.get("◯") == 9) or (marks.get("▲") == 9)
    assert second_or_third, (
        f"9番（本命ライン番手）が ◯ または ▲ に入っていない。"
        f"印: {marks}"
    )


def test_marks_top1_is_double_circle():
    """1番（top1 = line_leader）が ◎ に入る。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    from app.scoring import build_marks
    marks = build_marks(scores, ri)
    assert marks.get("◎") == 1


def test_marks_main_line_third_in_top3():
    """6番（本命ライン3番手）が ◎ ◯ ▲ のいずれかに入る（仕様要件5）。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    from app.scoring import build_marks
    marks = build_marks(scores, ri)
    top3_cars = {marks.get("◎"), marks.get("◯"), marks.get("▲")}
    assert 6 in top3_cars, (
        f"6番（本命ライン3番手）が top3印 に入っていない。印: {marks}"
    )


def test_top_pick_includes_9_1_6():
    """9-1-6（番手頭・本線向き）が「一番買いたい買い目」に含まれる（仕様要件6）。"""
    ri = _load()
    scores = _full_pipeline_scores(ri)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)

    from app.cli import _line_natural_score, _summarize_for_final
    text = _summarize_for_final(prediction)
    # 一番買いたい買い目セクションを抽出
    top_pick_section = text.split("### 押さえるべき")[0]
    assert "9-1-6" in top_pick_section, (
        f"9-1-6 が「一番買いたい」に含まれない:\n{top_pick_section}"
    )


def test_top_pick_does_not_include_1_6_9_when_better_exists():
    """1-6-9（2-3着入替）が「一番買いたい」上位2点を独占しない（仕様要件6）。

    1-9-6 / 9-1-6 がライン構造として自然なため、1-6-9 はそれより下位に位置する。
    """
    ri = _load()
    scores = _full_pipeline_scores(ri)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    prediction = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(prediction, scores, ri.odds)

    from app.cli import _summarize_for_final
    text = _summarize_for_final(prediction)
    top_pick_section = text.split("### 押さえるべき")[0]
    # 1-9-6 と 9-1-6 が一番買いたい2点に居る（1-6-9 は除外）
    has_1_9_6 = "1-9-6" in top_pick_section
    has_9_1_6 = "9-1-6" in top_pick_section
    has_1_6_9 = "1-6-9" in top_pick_section
    assert has_1_9_6 and has_9_1_6, (
        f"1-9-6 と 9-1-6 が一番買いたい2点に揃っていない:\n{top_pick_section}"
    )
    assert not has_1_6_9, (
        f"1-6-9（2-3着入替・押さえ寄り）が一番買いたいに混入:\n{top_pick_section}"
    )


def test_predict_cli_end_to_end(tmp_path: Path):
    """CLI predict 経由でも本命ライン構造が保たれる。"""
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        ["--db", str(db), "predict",
         "--input", str(FIXTURE),
         "--no-save", "--no-reflections", "--provider", "mock"],
    )
    assert result.exit_code == 0, result.output
    # 本線セクションに 1-9-* が含まれる
    assert "1-9-6" in result.output or "1-9-" in result.output, (
        f"CLI predict 出力に 1-9-* が無い。\n{result.output[:2000]}"
    )
    # 本線セクション抽出
    lines = result.output.splitlines()
    in_honsen = False
    honsen_lines: list[str] = []
    for line in lines:
        if line.startswith("## 6. 本線"):
            in_honsen = True
            continue
        if in_honsen:
            if line.startswith("## "):
                break
            honsen_lines.append(line)
    # 先頭の3連単行が 1-2-3 ではない
    first_bet_line = next((l for l in honsen_lines if "3連単" in l), "")
    assert "1-2-3" not in first_bet_line, (
        f"本線セクション先頭が 1-2-3 になっている: {first_bet_line}\n"
        f"全本線セクション:\n{chr(10).join(honsen_lines)}"
    )
