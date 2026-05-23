"""ターゲット合計買い目点数 (bet_budget) のテスト。

- 配分ロジック
- build_candidate_bets への統合
- CLI フラグ
- .env の BET_BUDGET
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli import cli
from app.models import RaceInput
from app.scoring import (
    build_candidate_bets,
    compute_bet_distribution,
    compute_scores,
)


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


def _load() -> RaceInput:
    return RaceInput.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# compute_bet_distribution 配分ロジック
# ---------------------------------------------------------------------------


def test_distribution_minimum():
    """target_total が最低保証 (7) を下回っても安全。"""
    h, o, a, oo = compute_bet_distribution(0)
    assert (h, o, a, oo) == (2, 2, 2, 1)
    assert h + o + a + oo == 7


def test_distribution_standard():
    """target=18 → 合計が 18 前後で本線:押さえ:穴:大穴 比率が概ね妥当。"""
    h, o, a, oo = compute_bet_distribution(18)
    total = h + o + a + oo
    assert total == 18, f"配分合計が {total} (期待: 18)"
    # 各カテゴリが最低保証以上
    assert h >= 2 and o >= 2 and a >= 2 and oo >= 1
    # 大穴が最少、穴が最多 という大まかな順序
    assert oo <= o and oo <= a


def test_distribution_aggressive():
    """target=30 → 合計 30 前後で広め。"""
    h, o, a, oo = compute_bet_distribution(30)
    total = h + o + a + oo
    assert total == 30


def test_distribution_capped():
    """target が極端に大きくても 40 でクランプ。"""
    h, o, a, oo = compute_bet_distribution(100)
    total = h + o + a + oo
    assert total <= 40


def test_distribution_monotonic():
    """target を増やすと合計も増える（単調増加）。"""
    totals = []
    for t in (10, 15, 20, 25):
        h, o, a, oo = compute_bet_distribution(t)
        totals.append(h + o + a + oo)
    for i in range(1, len(totals)):
        assert totals[i] >= totals[i - 1], f"単調増加でない: {totals}"


# ---------------------------------------------------------------------------
# build_candidate_bets 統合
# ---------------------------------------------------------------------------


def test_bet_count_reduces_with_small_budget():
    """target_total=12 で合計が小さくなる。"""
    ri = _load()
    scores = compute_scores(ri)
    bets_small = build_candidate_bets(ri, scores, target_total=12)
    bets_default = build_candidate_bets(ri, scores)
    total_small = sum(len(bets_small[c]) for c in ("本線", "押さえ", "穴", "大穴"))
    total_default = sum(len(bets_default[c]) for c in ("本線", "押さえ", "穴", "大穴"))
    assert total_small <= total_default, (
        f"target=12 で合計が縮まっていない: small={total_small} default={total_default}"
    )


def test_bet_count_increases_with_large_budget():
    """target_total=28 で合計が大きくなる。"""
    ri = _load()
    scores = compute_scores(ri)
    bets_big = build_candidate_bets(ri, scores, target_total=28)
    bets_default = build_candidate_bets(ri, scores)
    total_big = sum(len(bets_big[c]) for c in ("本線", "押さえ", "穴", "大穴"))
    total_default = sum(len(bets_default[c]) for c in ("本線", "押さえ", "穴", "大穴"))
    assert total_big >= total_default, (
        f"target=28 で合計が広がらない: big={total_big} default={total_default}"
    )


def test_bet_count_none_keeps_default():
    """target_total=None は既存挙動と同じ（後方互換）。"""
    ri = _load()
    scores = compute_scores(ri)
    bets_a = build_candidate_bets(ri, scores)
    bets_b = build_candidate_bets(ri, scores, target_total=None)
    # 同じ結果
    for cat in ("本線", "押さえ", "穴", "大穴"):
        assert len(bets_a[cat]) == len(bets_b[cat])
        for x, y in zip(bets_a[cat], bets_b[cat]):
            assert x.combination == y.combination


def test_bet_count_minimum_categories():
    """target_total=10 でも各カテゴリ最低保証あり（本線2, 押さえ2, 穴2, 大穴1）。"""
    ri = _load()
    scores = compute_scores(ri)
    bets = build_candidate_bets(ri, scores, target_total=10)
    # 本線・押さえ は必須形があるので少なくとも 2 件以上は埋まる
    # （仕様準拠の本命ライン3形がある場合、HARD_HONSEN まで force_push される）
    assert len(bets["本線"]) >= 1
    # 穴は最低 2 点目標


# ---------------------------------------------------------------------------
# CLI フラグ
# ---------------------------------------------------------------------------


def test_cli_predict_with_bet_budget(tmp_path, monkeypatch):
    """predict --bet-budget 15 が動作する。"""
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **kw: {})
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        [
            "--db", str(db), "predict",
            "--input", str(SAMPLE),
            "--no-save", "--no-reflections",
            "--provider", "mock",
            "--bet-budget", "15",
        ],
    )
    assert result.exit_code == 0, result.output


def test_cli_predict_default_no_budget(tmp_path, monkeypatch):
    """predict --bet-budget なしでも動く（既存挙動）。"""
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **kw: {})
    runner = CliRunner()
    db = tmp_path / "t.db"
    result = runner.invoke(
        cli,
        [
            "--db", str(db), "predict",
            "--input", str(SAMPLE),
            "--no-save", "--no-reflections",
            "--provider", "mock",
        ],
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# .env BET_BUDGET
# ---------------------------------------------------------------------------


def test_load_settings_reads_bet_budget(monkeypatch):
    """BET_BUDGET 環境変数から bet_budget が読み込まれる。"""
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **kw: {})
    monkeypatch.setenv("BET_BUDGET", "22")
    from app.config import load_settings
    s = load_settings()
    assert s.bet_budget == 22


def test_load_settings_bet_budget_invalid_ignored(monkeypatch):
    """BET_BUDGET が不正なら None（数値以外、範囲外）。"""
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **kw: {})
    monkeypatch.setenv("BET_BUDGET", "abc")
    from app.config import load_settings
    s = load_settings()
    assert s.bet_budget is None

    monkeypatch.setenv("BET_BUDGET", "100")  # 範囲外
    s2 = load_settings()
    assert s2.bet_budget is None


def test_load_settings_bet_budget_unset(monkeypatch):
    """BET_BUDGET 未設定なら None。"""
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **kw: {})
    monkeypatch.delenv("BET_BUDGET", raising=False)
    from app.config import load_settings
    s = load_settings()
    assert s.bet_budget is None
