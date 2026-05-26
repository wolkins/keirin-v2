"""CoverageMetrics: candidate / market / honsen の各母集団のオッズ取得率を
1 オブジェクトに集約する.

Phase 16 (2026-05-26): 静岡6R の `2-7-1` 問題:
- 本文には 2-7-1 が「6.1倍 / 見送り寄り」と表示されている
- 末尾には「オッズ取得済み: 0/8点 (0%)」と表示される
これは「表示候補 coverage」と「実購入候補 coverage」を同じセクションで
混ぜていたため。CoverageMetrics は両者を別 Counts として保持する。

Phase 15 で導入した `市場人気オッズ取得状況` も market_popular として保持。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .candidate_lifecycle import CandidateLifecycle


@dataclass
class Counts:
    """母集団の総数と取得済み数."""

    total: int = 0
    with_odds: int = 0

    @property
    def ratio(self) -> float:
        return self.with_odds / self.total if self.total else 0.0

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    def __repr__(self) -> str:
        return f"Counts({self.with_odds}/{self.total}={self.ratio:.0%})"


@dataclass
class CoverageMetrics:
    """候補 / 購入 / 市場偏り / 市場人気 の取得率を 1 オブジェクトに集約.

    Phase 15 で分離した `候補買い目オッズ取得率` と `市場人気オッズ取得状況`
    の根拠データを統一する。Renderer は各 Counts を見て表示するだけ。
    """

    # 表示候補 (lifecycle.include_in_display_coverage=True)
    display: Counts = field(default_factory=Counts)

    # 実購入候補 (lifecycle.include_in_purchase_coverage=True)
    purchase: Counts = field(default_factory=Counts)

    # 市場偏りカバー (lifecycle.include_in_market_bias_coverage=True)
    market_bias: Counts = field(default_factory=Counts)

    # 市場人気オッズ (input_data.odds の件数, Phase 15 で導入)
    market_popular: Counts = field(default_factory=Counts)

    # 互換: 既存 OddsCoverage で持っていた honsen_real / honsen_cheap
    honsen_real: Counts = field(default_factory=Counts)
    honsen_cheap: Counts = field(default_factory=Counts)

    # Phase 16 Step 5A (2026-05-26): lifecycle.decision_state ベースで
    # 「参考候補」「ガミ注意候補」を集計。Renderer の新 layout 表示で
    # 「参考候補オッズ / ガミ注意候補オッズ」を出すために使う。
    watch_only: Counts = field(default_factory=Counts)
    gami_warning: Counts = field(default_factory=Counts)

    # market_popular の bet_type 内訳 (例: {"3連単": 5, "3連複": 3})
    market_popular_by_bet_type: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_lifecycles(
        cls,
        lifecycles: "Iterable[CandidateLifecycle]",
        *,
        market_popular_total: int = 0,
        market_popular_by_bet_type: dict[str, int] | None = None,
        honsen_real_lifecycles: "Iterable[CandidateLifecycle] | None" = None,
        honsen_cheap_lifecycles: "Iterable[CandidateLifecycle] | None" = None,
    ) -> "CoverageMetrics":
        """CandidateLifecycle のリストから CoverageMetrics を構築する.

        Args:
            lifecycles: 全 lifecycle (visible=True/False 両方)
            market_popular_total: input_data.odds の件数
            market_popular_by_bet_type: bet_type 別件数
            honsen_real_lifecycles: 「実購入本線」として集計する lifecycle
                (安い人気筋を除いた本線)。指定がなければ include_in_purchase
                の本線のみを使う
            honsen_cheap_lifecycles: 「安い人気筋」として集計する lifecycle
        """
        # Phase 16 Step 5A: lifecycle.decision_state を見て
        # watch_only / gami_warning を別 Counts に集計。Renderer の
        # 新 layout 表示で「参考候補オッズ / ガミ注意候補オッズ」に使う。
        from .candidate_lifecycle import (
            DECISION_STATE_GAMI_WARNING, DECISION_STATE_WATCH_ONLY,
        )
        m = cls()
        for lc in lifecycles:
            if lc.include_in_display_coverage:
                m.display.total += 1
                if lc.has_odds:
                    m.display.with_odds += 1
            if lc.include_in_purchase_coverage:
                m.purchase.total += 1
                if lc.has_odds:
                    m.purchase.with_odds += 1
            if lc.include_in_market_bias_coverage:
                m.market_bias.total += 1
                if lc.has_odds:
                    m.market_bias.with_odds += 1
            # state ベース集計 (visible のものだけ集計)
            if lc.visible:
                if lc.decision_state == DECISION_STATE_WATCH_ONLY:
                    m.watch_only.total += 1
                    if lc.has_odds:
                        m.watch_only.with_odds += 1
                elif lc.decision_state == DECISION_STATE_GAMI_WARNING:
                    m.gami_warning.total += 1
                    if lc.has_odds:
                        m.gami_warning.with_odds += 1

        m.market_popular = Counts(
            total=market_popular_total, with_odds=market_popular_total,
        )
        if market_popular_by_bet_type:
            m.market_popular_by_bet_type = dict(market_popular_by_bet_type)

        if honsen_real_lifecycles is not None:
            for lc in honsen_real_lifecycles:
                m.honsen_real.total += 1
                if lc.has_odds:
                    m.honsen_real.with_odds += 1
        if honsen_cheap_lifecycles is not None:
            for lc in honsen_cheap_lifecycles:
                m.honsen_cheap.total += 1
                if lc.has_odds:
                    m.honsen_cheap.with_odds += 1

        return m

    def has_low_purchase_coverage(self, threshold: float = 0.4) -> bool:
        """実購入候補のオッズ取得率が threshold 未満か (既存 has_warning と整合)."""
        if self.purchase.is_empty:
            return False
        return self.purchase.ratio < threshold

    def has_zero_purchase_with_market(self) -> bool:
        """購入候補オッズ 0/N かつ市場人気オッズあり: 矛盾に見えるケース.

        Phase 16: 静岡6R の `2-7-1` 問題. False (見える矛盾なし) なら、
        market_popular > 0 で「市場人気は取れている」と明示できる。
        """
        return (
            self.purchase.total > 0
            and self.purchase.with_odds == 0
            and self.market_popular.total > 0
        )
