"""Phase 15 (2026-05-25): purchase_mode != BUYABLE の本文で禁止語が
出ないこと、オッズ取得率セクションが「候補買い目」と「市場人気」に
分離されていることを検証。

背景:
- 静岡6R で purchase_mode=SKIP のとき
  「purchase_mode=SKIP のため、final_* は実購入対象ではなく参考表示です。」
  が出力され、validator が「購入対象」を禁止語として検出した。
- 否定文 (「ではなく」) であっても禁止語が含まれていれば
  PURCHASE_MODE_VIOLATION 警告が出る方針。
- 修正: 非 BUYABLE 時は「購入対象」「実購入対象」「実購入候補」
  「買える候補」「本線向き」を一切出さない。

検証:
A. SKIP / WATCH_ONLY / TENTATIVE の本文に禁止語が含まれない
B. mark_alignment の補足文に「実購入対象」が含まれない (否定文でも)
C. 「### 候補買い目オッズ取得率」と「### 市場人気オッズ取得状況」が
   別セクションとして出る
D. 候補買い目オッズ 0/8 + 市場人気オッズ取得済み → 矛盾に見えない表示
"""

from __future__ import annotations

import pytest

from app.decision import PurchaseMode
from app.markdown_renderer import (
    render_output_plan, validate_purchase_mode_markdown,
)
from app.models import (
    BetRecommendation, OddsEntry, Prediction, RaceInfo, RaceInput, Rider,
)
from app.output_plan import OutputPlan, OutputPlanWarning


# 「購入対象」「実購入対象」「実購入候補」「買える候補」「本線向き」
# は purchase_mode != BUYABLE で本文に出てはいけない。
FORBIDDEN_BASIC = ("購入対象", "実購入対象", "実購入候補", "一番買いたい")
FORBIDDEN_STRICT = ("買える候補", "本線向き")


def _minimal_input() -> RaceInput:
    return RaceInput(
        race=RaceInfo(
            race_id="20260525-test-6",
            date="2026-05-25",
            venue="test",
            race_no=6,
            class_name="A級一般",
            start_time="12:00",
        ),
        riders=[
            Rider(
                car_no=i, name=f"R{i}", score=80.0, b_count=0,
                nige=0, makuri=0, sashi=0, mark=0, comment="",
                home_area="中部",
            )
            for i in range(1, 8)
        ],
        lines=[],
        odds=[
            # 市場人気オッズ (3連単上位 3点) → 市場人気オッズ取得状況
            # セクションで「取得済み: 3点」と表示される。
            OddsEntry(bet_type="3連単", combination="1-2-3", odds=5.5),
            OddsEntry(bet_type="3連単", combination="1-3-2", odds=8.2),
            OddsEntry(bet_type="3連単", combination="2-1-3", odds=12.0),
        ],
    )


def _minimal_prediction() -> Prediction:
    return Prediction(
        race_id="20260525-test-6",
        venue="test", race_no=6, is_girls=False, marks={},
        summary="t", weather_text="t", lines_text="t",
        venue_trend_text="t",
        honsen=[], osae=[], ana=[], ooana=[],
        final_conclusion="",
        gami_memo="",
        reflection_points=[],
    )


def _make_plan(mode: PurchaseMode) -> OutputPlan:
    """指定 mode の OutputPlan を最小構成で作る。"""
    plan = OutputPlan()
    plan.purchase_mode = mode
    # 候補側オッズは 0/N にする (final_best 空)。市場人気オッズは
    # _minimal_input() で 3 点取得済みに設定済み。
    return plan


# ---------------------------------------------------------------------------
# A. SKIP / WATCH_ONLY / TENTATIVE の本文に禁止語が出ない
# ---------------------------------------------------------------------------


class TestPurchaseModeNoForbiddenWords:
    @pytest.mark.parametrize("mode", [
        PurchaseMode.SKIP,
        PurchaseMode.WATCH_ONLY,
        PurchaseMode.TENTATIVE,
    ])
    def test_basic_forbidden_not_in_body(self, mode):
        """SKIP/WATCH_ONLY/TENTATIVE の本文に「購入対象」「実購入対象」
        「実購入候補」「一番買いたい」が出ない (否定文でも検出される)。"""
        plan = _make_plan(mode)
        md = render_output_plan(
            plan, _minimal_prediction(), _minimal_input(),
        )
        # 本文 = OutputPlan/Validation 警告セクション前まで
        # (警告 message には禁止語が含まれることがあるため除外する)
        body, _, _ = md.partition("### OutputPlan 警告")
        body, _, _ = body.partition("### 出力整合性チェック")
        for word in FORBIDDEN_BASIC:
            assert word not in body, (
                f"mode={mode.name} body に禁止語「{word}」が含まれている: "
                f"\n{body}"
            )

    @pytest.mark.parametrize("mode", [
        PurchaseMode.SKIP,
        PurchaseMode.WATCH_ONLY,
    ])
    def test_strict_forbidden_not_in_body(self, mode):
        """SKIP/WATCH_ONLY の本文に「買える候補」「本線向き」が出ない。"""
        plan = _make_plan(mode)
        md = render_output_plan(
            plan, _minimal_prediction(), _minimal_input(),
        )
        body, _, _ = md.partition("### OutputPlan 警告")
        body, _, _ = body.partition("### 出力整合性チェック")
        for word in FORBIDDEN_STRICT:
            assert word not in body, (
                f"mode={mode.name} body に禁止語「{word}」が含まれている: "
                f"\n{body}"
            )


# ---------------------------------------------------------------------------
# B. validator: 否定文 / 「実購入対象」も検出する
# ---------------------------------------------------------------------------


class TestValidatorDetectsForbiddenInNegations:
    def test_jitsupurchase_target_detected(self):
        """「実購入対象ではなく参考表示です」のような否定文でも
        validator は「実購入対象」と「購入対象」を両方検出する。"""
        plan = _make_plan(PurchaseMode.SKIP)
        body = (
            "## 10. 最終結論\n"
            "purchase_mode=SKIP のため、final_* は実購入対象ではなく"
            "参考表示です。\n"
        )
        violations = validate_purchase_mode_markdown(plan, body)
        codes = [v.code for v in violations]
        # 「実購入対象」と「購入対象」の両方が検出される
        messages = [v.message for v in violations]
        assert any("実購入対象" in m for m in messages), (
            f"「実購入対象」が検出されていない: {messages}"
        )
        assert any("購入対象" in m for m in messages), (
            f"「購入対象」が検出されていない: {messages}"
        )
        assert "PURCHASE_MODE_VIOLATION" in codes

    def test_negation_does_not_excuse_forbidden(self):
        """否定文 (「ではなく」) であっても禁止語が含まれていれば
        必ず警告が出る (validator は意味解析しない)。"""
        plan = _make_plan(PurchaseMode.WATCH_ONLY)
        # 「購入対象ではありません」も同じく検出される
        body = "本候補は購入対象ではありません。"
        violations = validate_purchase_mode_markdown(plan, body)
        assert len(violations) >= 1
        assert all(v.code == "PURCHASE_MODE_VIOLATION" for v in violations)

    def test_buyable_skips_check(self):
        """BUYABLE のときは禁止語チェックをスキップする (warning なし)。"""
        plan = _make_plan(PurchaseMode.BUYABLE)
        body = "オッズ取得済みで買える候補: 1-2-3 (購入対象)"
        violations = validate_purchase_mode_markdown(plan, body)
        assert violations == []


# ---------------------------------------------------------------------------
# C. オッズ取得率セクションが分離されている
# ---------------------------------------------------------------------------


class TestOddsCoverageSectionSeparation:
    def test_two_separate_sections_in_md(self):
        """render_output_plan の出力に「候補買い目オッズ取得率」と
        「市場人気オッズ取得状況」が別セクションとして出る。"""
        plan = _make_plan(PurchaseMode.SKIP)
        md = render_output_plan(
            plan, _minimal_prediction(), _minimal_input(),
        )
        assert "### 候補買い目オッズ取得率" in md
        assert "### 市場人気オッズ取得状況" in md

    def test_market_odds_section_shows_total(self):
        """市場人気オッズ取得状況に「取得済み: 3点」が出る
        (_minimal_input は 3連単 3点を持っている)。"""
        plan = _make_plan(PurchaseMode.SKIP)
        md = render_output_plan(
            plan, _minimal_prediction(), _minimal_input(),
        )
        # 市場人気オッズ取得状況セクションを抽出
        idx = md.find("### 市場人気オッズ取得状況")
        assert idx >= 0
        next_idx = md.find("###", idx + 1)
        section = md[idx:next_idx] if next_idx > 0 else md[idx:]
        assert "取得済み: 3点" in section
        assert "3連単 3点" in section


# ---------------------------------------------------------------------------
# D. 候補買い目オッズ 0/N でも、市場人気オッズが取得済みなら
#    矛盾に見えない表示 (= 別セクションで明示される)
# ---------------------------------------------------------------------------


class TestOddsCoverageDoesNotContradictMarketOdds:
    def test_candidate_zero_but_market_present(self):
        """候補側オッズが 0/0 でも、市場人気オッズが取得済みなら
        「市場人気オッズは取得済み」と明示され、矛盾に見えない。"""
        plan = _make_plan(PurchaseMode.SKIP)
        # final_best が空 → 候補買い目は 0/0 になる
        md = render_output_plan(
            plan, _minimal_prediction(), _minimal_input(),
        )
        # 候補買い目セクションは「取得済み: 0/0点」または「0%」
        # 市場人気セクションは「取得済み: 3点」と分離されて表示される
        assert "### 候補買い目オッズ取得率" in md
        assert "### 市場人気オッズ取得状況" in md
        market_idx = md.find("### 市場人気オッズ取得状況")
        next_idx = md.find("###", market_idx + 1)
        market_section = md[market_idx:next_idx] if next_idx > 0 else md[market_idx:]
        # 「未取得」ではなく取得済み件数が出ること
        assert "未取得" not in market_section
        assert "取得済み: 3点" in market_section

    def test_market_odds_empty_shows_unfetched(self):
        """input_data.odds が空なら「未取得」と明示される。"""
        plan = _make_plan(PurchaseMode.SKIP)
        ri = _minimal_input()
        ri.odds = []
        md = render_output_plan(plan, _minimal_prediction(), ri)
        market_idx = md.find("### 市場人気オッズ取得状況")
        next_idx = md.find("###", market_idx + 1)
        market_section = md[market_idx:next_idx] if next_idx > 0 else md[market_idx:]
        assert "未取得" in market_section
