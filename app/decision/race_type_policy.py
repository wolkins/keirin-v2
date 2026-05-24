"""RaceTypePolicy (Phase 4): レース種別ごとの一貫した方針.

レース種別:
- normal_line: 通常ライン戦 (A級/S級の通常戦、F1/F2 含む)
- girls: ガールズ (固定ラインなし、市場オッズ重視)
- rookie: 新人戦 (男子)、ライン用語禁止
- girls_rookie: ガールズ新人戦 (girls + rookie の厳しい方を採用)

Phase 4 のスコープ:
- policy を定義して plan に露出する
- decision_context / renderer / sanitizer 等が policy を参照できる
- 候補生成 (scoring.py) や sanitize ルール本体の改修は範囲外
  (既存 _GIRLS_TERM_REPLACEMENTS / _ROOKIE_TERM_REPLACEMENTS は維持)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .context import PurchaseMode

if TYPE_CHECKING:
    from ..models import RaceInput


RaceType = Literal["normal_line", "girls", "rookie", "girls_rookie"]


@dataclass
class RaceTypePolicy:
    """レース種別ごとの policy.

    Fields:
        race_type: normal_line / girls / rookie / girls_rookie
        allow_line_logic: ライン候補生成を許可するか
        allow_line_terms: 「本命ライン」「番手」等の用語を許可するか
        market_weight: 0.0-1.0、市場オッズ重視度
        line_weight: 0.0-1.0、ライン構造重視度
        max_final_best: final_best の最大点数
        low_quality_max_purchase_mode: data_quality=low/very_low 時の
            purchase_mode 上限
        low_coverage_threshold: WATCH_ONLY cap の odds_overall_coverage 閾値。
            coverage < この値 で WATCH_ONLY 以下にキャップされる。
            (なお SKIP は derive_purchase_mode 本体の固定閾値 0.20 で判定。
             policy 経由では SKIP 判定はしない)
        force_watch_only_when_low_quality: data_quality=low で必ず
            WATCH_ONLY 以下にするか
        notes: 人間可読な説明
    """

    race_type: RaceType
    allow_line_logic: bool
    allow_line_terms: bool
    market_weight: float
    line_weight: float
    max_final_best: int
    low_quality_max_purchase_mode: PurchaseMode
    low_coverage_threshold: float
    force_watch_only_when_low_quality: bool
    notes: list[str] = field(default_factory=list)


# --- 種別ごとのデフォルト policy ---------------------------------------------

# policy notes は Renderer に直接表示される。ガールズ/新人戦サニタイズの
# 影響を受けないよう、ライン前提の用語 (本命ライン / 番手 / ライン3番手 等)
# は **使わない**。説明したい場合は「位置取り」「追走」「上位評価」等の
# 中立的な表現で書く。
_NORMAL_LINE_POLICY = RaceTypePolicy(
    race_type="normal_line",
    allow_line_logic=True,
    allow_line_terms=True,
    market_weight=0.5,
    line_weight=0.5,
    max_final_best=3,
    low_quality_max_purchase_mode=PurchaseMode.TENTATIVE,
    low_coverage_threshold=0.20,
    force_watch_only_when_low_quality=False,
    notes=[
        "通常ライン戦: 固定ライン構造を前提にした候補生成を許可"
    ],
)

_GIRLS_POLICY = RaceTypePolicy(
    race_type="girls",
    allow_line_logic=False,
    allow_line_terms=False,
    market_weight=0.7,
    line_weight=0.0,
    max_final_best=3,
    low_quality_max_purchase_mode=PurchaseMode.WATCH_ONLY,
    low_coverage_threshold=0.25,
    force_watch_only_when_low_quality=True,
    notes=[
        "ガールズ: 固定構成を前提にせず、市場オッズを重視。"
        "低品質時は見送り寄り。"
    ],
)

_ROOKIE_POLICY = RaceTypePolicy(
    race_type="rookie",
    allow_line_logic=False,
    allow_line_terms=False,
    market_weight=0.5,
    line_weight=0.0,
    max_final_best=3,
    low_quality_max_purchase_mode=PurchaseMode.WATCH_ONLY,
    low_coverage_threshold=0.25,
    force_watch_only_when_low_quality=True,
    notes=[
        # 「ライン」文字列を含めない (新人戦サニタイズ対象のため Markdown
        # 表示時にチェックが走る)
        "新人戦: 固定構成を前提にせず、用語は中立的に表現する"
        " (位置取り / 追走 / 4位評価 など)",
        "強風5m/s以上は4位評価を押さえ上位へ (scoring 側で対応)",
    ],
)

# girls + rookie の **厳しい方** を採用 (重複適用)
_GIRLS_ROOKIE_POLICY = RaceTypePolicy(
    race_type="girls_rookie",
    allow_line_logic=False,
    allow_line_terms=False,
    market_weight=0.7,                       # girls 寄り
    line_weight=0.0,
    max_final_best=2,                        # 厳しめ
    low_quality_max_purchase_mode=PurchaseMode.WATCH_ONLY,
    # codex P1 反映 (2026-05-24): low_coverage_threshold が girls (0.25)
    # より小さいと「coverage<threshold で WATCH_ONLY」の意味で逆に **弱く**
    # なるため、girls と同じ 0.25 に揃える。「厳しい方を採用」は max_final_best=2
    # / force_watch_only_when_low_quality=True で表現。
    low_coverage_threshold=0.25,
    force_watch_only_when_low_quality=True,
    notes=[
        "ガールズ新人戦: girls / rookie 両方の厳しい閾値を採用",
        "data_quality=low → WATCH_ONLY、"
        "odds_overall_coverage<0.20 → SKIP",
    ],
)


def resolve_race_type_policy(input_data: "RaceInput") -> RaceTypePolicy:
    """RaceInput から RaceTypePolicy を導出する.

    判定優先順位 (排他的):
    1. is_girls and is_rookie → girls_rookie
    2. is_girls (and not is_rookie) → girls
    3. is_rookie (and not is_girls) → rookie
    4. その他 → normal_line

    `resolved_is_girls()` / `resolved_is_rookie()` は class_name / フラグから
    判定済み。
    """
    if input_data is None:
        return _NORMAL_LINE_POLICY

    race = input_data.race
    is_girls = bool(race.resolved_is_girls())
    is_rookie = bool(race.resolved_is_rookie())

    if is_girls and is_rookie:
        return _GIRLS_ROOKIE_POLICY
    if is_girls:
        return _GIRLS_POLICY
    if is_rookie:
        return _ROOKIE_POLICY
    return _NORMAL_LINE_POLICY
