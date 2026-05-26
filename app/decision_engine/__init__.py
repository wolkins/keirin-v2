"""decision_engine: 候補の最終状態と coverage / diagnostics を一元管理.

Phase 16 (2026-05-26): OutputPlan は「最終状態のコレクション」だが、各候補
1 件が「どこから来てどこへ行ったか」「どの coverage 母集団に含まれるか」
「どの warning 判定に使われるか」が散在していて出力にブレが出ていた。
本モジュールでは:

- CandidateLifecycle: 1 combination = 1 ライフサイクル台帳
- CoverageMetrics: display / purchase / market_bias / market_popular の
  取得率を 1 オブジェクトに集約
- Diagnostics: warnings / notes / groups を統一スキーマに
- WarningEngine: lifecycle ベースで warning を生成 (3 段階に細分化)

エントリポイント: `build_decision_engine_data(plan, prediction, input_data)`
で OutputPlan に lifecycle / coverage_metrics / diagnostics を populate する。
出力 Markdown は変えず、内部データだけ追加する設計 (Phase 16 Step 1+2)。
"""

from .candidate_lifecycle import (
    CandidateLifecycle, Transition,
    DECISION_STATE_BUYABLE, DECISION_STATE_TENTATIVE,
    DECISION_STATE_WATCH_ONLY, DECISION_STATE_SKIP,
    DECISION_STATE_GAMI_WARNING,
    DISPLAY_BUCKET_HONSEN, DISPLAY_BUCKET_HONSEN_MIOKURI,
    DISPLAY_BUCKET_OSAE, DISPLAY_BUCKET_ANA, DISPLAY_BUCKET_OOANA,
    DISPLAY_BUCKET_GAMI_WARNING, DISPLAY_BUCKET_WATCH_ONLY,
    DISPLAY_BUCKET_DROPPED,
    merge_value_label,
)
from .coverage_metrics import Counts, CoverageMetrics
from .diagnostics import (
    DiagCategory, DiagEntry, Diagnostics,
)
from .engine import build_decision_engine_data
from .warning_engine import build_warnings_from_lifecycles

__all__ = [
    # candidate_lifecycle
    "CandidateLifecycle", "Transition",
    "DECISION_STATE_BUYABLE", "DECISION_STATE_TENTATIVE",
    "DECISION_STATE_WATCH_ONLY", "DECISION_STATE_SKIP",
    "DECISION_STATE_GAMI_WARNING",
    "DISPLAY_BUCKET_HONSEN", "DISPLAY_BUCKET_HONSEN_MIOKURI",
    "DISPLAY_BUCKET_OSAE", "DISPLAY_BUCKET_ANA", "DISPLAY_BUCKET_OOANA",
    "DISPLAY_BUCKET_GAMI_WARNING", "DISPLAY_BUCKET_WATCH_ONLY",
    "DISPLAY_BUCKET_DROPPED",
    "merge_value_label",
    # coverage_metrics
    "Counts", "CoverageMetrics",
    # diagnostics
    "DiagCategory", "DiagEntry", "Diagnostics",
    # engine
    "build_decision_engine_data",
    # warning_engine
    "build_warnings_from_lifecycles",
]
