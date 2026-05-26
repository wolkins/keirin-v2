"""Diagnostics: warnings / notes / groups を 1 個のコレクションに集約.

Phase 16 (2026-05-26): plan.warnings / plan.watch_only_reason_groups /
plan.mark_alignment_notes / plan.market_bias_notes /
plan.race_type_policy_notes が散在していたのを統一する。

Diagnostics は単純な dict ベースのコレクションで、各 category に entry を
ぶら下げる。Renderer は category ごとに表示順を制御する。

Step 1+2 (型定義 + populate) では既存フィールドと並走し、Step 3 で
Renderer を切り替え、Step 5 で旧フィールドを deprecate する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# DiagCategory
# ---------------------------------------------------------------------------


class DiagCategory:
    """診断情報のカテゴリ.

    Renderer が「どの section の補足か」を判断するのに使う。
    """

    # 警告 (severity=warning/error)
    WARNING = "warning"

    # 印 marks と final_* のズレ (Phase 2)
    MARK_ALIGNMENT = "mark_alignment"

    # 市場偏り (Phase 3)
    MARKET_BIAS = "market_bias"

    # レース種別 policy (Phase 4)
    RACE_TYPE_POLICY = "race_type_policy"

    # 参考候補の移動理由 (Phase 8)
    WATCH_ONLY_REASON = "watch_only_reason"

    # decision_context (purchase_mode の根拠)
    DECISION_CONTEXT = "decision_context"


# ---------------------------------------------------------------------------
# DiagEntry
# ---------------------------------------------------------------------------


@dataclass
class DiagEntry:
    """診断 1 件 (人間可読な文 + 任意の code/severity).

    既存 OutputPlanWarning と互換: code と message を持ち、severity は warning
    カテゴリのみ意味を持つ (他カテゴリでは "info" 固定)。
    """

    message: str
    code: Optional[str] = None
    severity: str = "info"   # "info" / "warning" / "error"


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@dataclass
class Diagnostics:
    """category → list[DiagEntry] のコレクション.

    Renderer は each_section() で順序付きで取り出す。空 category は
    表示しない。
    """

    entries: dict[str, list[DiagEntry]] = field(default_factory=dict)

    def add(
        self,
        category: str,
        message: str,
        *,
        code: Optional[str] = None,
        severity: str = "info",
    ) -> None:
        self.entries.setdefault(category, []).append(
            DiagEntry(message=message, code=code, severity=severity)
        )

    def get(self, category: str) -> list[DiagEntry]:
        return list(self.entries.get(category, []))

    def warnings_only(self) -> list[DiagEntry]:
        """severity=warning/error のものだけ抽出 (全 category 横断)."""
        out: list[DiagEntry] = []
        for entries in self.entries.values():
            for e in entries:
                if e.severity in ("warning", "error"):
                    out.append(e)
        return out

    def is_empty(self) -> bool:
        return not any(self.entries.values())
