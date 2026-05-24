"""CLIエントリポイント。

サブコマンド:
- predict        : 手入力JSONから予想を生成
- result         : レース結果を入力し、反省ログを保存
- reflections    : 反省ログを表示
- create-json    : 手入力JSONのテンプレートを書き出す

注意:
- 本ツールは予想支援目的のみ。自動投票/購入処理は一切持たない
- 的中保証/回収率保証の表現は出力に含めない
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import click

from .config import SUPPORTED_PROVIDERS, load_settings
from .enrichment import EnrichmentError, merge_recent_results
from .preparation import PreparationError, prepare_race_input
from .reporting import build_performance_report, render_report_text
from .value_analysis import (
    annotate_prediction_with_value,
    promote_oddful_to_honsen,
    promote_oddful_to_osae,
)
from .weather import (
    SUPPORTED_WEATHER_SOURCES,
    WeatherFetchError,
    build_weather_provider,
)
from .fetchers import (
    SUPPORTED_SOURCES,
    FetchError,
    FileCache,
    HttpClient,
    ManualFetcher,
    NotImplementedSource,
    RateLimiter,
    build_fetcher,
)
from .fetchers.cache import DEFAULT_CACHE_DIR, DEFAULT_TTL_SECONDS
from .llm_client import (
    LLMClient,
    UnknownProviderError,
    build_client,
    build_default_client,
)
from .models import Prediction, RaceInput, Reflection, Rider
from .prompt_builder import build_full_prompt
from .race_input_builder import (
    LinesParseError,
    build_placeholder_rider,
    build_quick_input,
    parse_lines,
)
from .reflection import build_reflection
from .scoring import (
    apply_bank_signals,
    apply_f2_signals,
    apply_grade_signals,
    apply_home_area_signals,
    apply_market_signals,
    apply_reflection_signals,
    apply_tospo_signals,
    apply_trend_signals,
    apply_wind_extra_signals,
    build_candidate_bets,
    compute_scores,
    gami_inflation_from_reflections,
)
from .storage import DEFAULT_DB_PATH, Storage


# ---------------------------------------------------------------------------
# 表示用ヘルパ
# ---------------------------------------------------------------------------


def _format_marks(marks: dict[str, int]) -> str:
    if not marks:
        return "(印なし)"
    return "  ".join(f"{m}: {c}" for m, c in marks.items())


def _format_bets(bets) -> str:
    if not bets:
        return "  （該当なし）"
    out = []
    for b in bets:
        risk = ""
        cat = getattr(b, "category", None)
        is_ana_bucket = cat in ("穴", "大穴")
        # 表示ラベルの優先順位:
        # (1) 穴/大穴 + 3連複安 reason  → [点数注意]（控えめ表示）
        # (2) market_odds が取れていて閾値満たす → [ガミ注意] / [低配当注意]
        # (3) market_odds=None や 20倍以上 → 何も表示しない
        if is_ana_bucket and "3連複安" in (b.reason or ""):
            risk = "  [点数注意]"
        elif b.market_odds is not None:
            # ガミ注意の条件:
            #   - gami_risk >= 0.8 かつ market_odds < 20倍
            #   - または gami_risk >= 0.6 かつ market_odds < 15倍
            if (
                (b.gami_risk >= 0.8 and b.market_odds < 20.0)
                or (b.gami_risk >= 0.6 and b.market_odds < 15.0)
            ):
                risk = "  [ガミ注意]"
            # 低配当注意:
            #   - gami_risk >= 0.4 かつ market_odds < 15倍
            elif b.gami_risk >= 0.4 and b.market_odds < 15.0:
                risk = "  [低配当注意]"
        value_bits: list[str] = []
        if b.market_odds is not None:
            value_bits.append(f"{b.market_odds:.1f}倍")
        if b.value_label:
            value_bits.append(b.value_label)
        value_str = f"  ({' / '.join(value_bits)})" if value_bits else ""
        out.append(f"  - {b.bet_type} {b.combination}  / {b.reason}{value_str}{risk}")
    return "\n".join(out)


def _top_pick_disqualified(b) -> bool:
    """「一番買いたい買い目」「押さえるべき買い目」から除外する判定。

    以下のいずれかに該当する場合は除外:
      - value_label == "見送り寄り"
      - gami_risk >= 0.8（高ガミ）
      - market_odds < 5.0（安すぎる人気・ガミ警戒）
    """
    if getattr(b, "value_label", None) == "見送り寄り":
        return True
    if getattr(b, "gami_risk", 0.0) >= 0.8:
        return True
    odds = getattr(b, "market_odds", None)
    if odds is not None and odds < 5.0:
        return True
    return False


def _line_natural_score(b) -> float:
    """買い目のライン自然度スコア。

    高いほど『一番買いたい買い目』に上位表示すべき。
    本命ライン: 先頭-番手-3番手 が最自然、2-3着入替は押さえ寄り。
    value_label の「本線向き」「妙味あり」も加点、「堅いが安い」は減点。
    """
    score = 0.0
    reason = b.reason or ""
    # ライン構造の自然度
    if "先頭-番手-3番手" in reason:
        score += 100.0
    elif "番手頭-先頭-3番手" in reason:
        score += 80.0
    elif "先頭-3番手-番手" in reason:
        # 2-3着入替は押さえ寄り（一番買いたい優先度を下げる）
        score += 30.0
    elif "本命ライン" in reason:
        score += 50.0
    # value_label
    label = b.value_label or ""
    if label == "本線向き":
        score += 30.0
    elif label == "妙味あり":
        score += 25.0
    elif label == "堅いが安い":
        score -= 10.0
    elif label == "見送り寄り":
        score -= 20.0
    # gami_risk は減点（同点ブレイクで使う）
    score -= b.gami_risk * 5.0
    return score


def _bet_line_strength(combo: Optional[str], lines: list) -> int:
    """3連単 combo の line整合度を返す。0=弱, 1=中, 2=強 (要件3)。

    docs/race_type_policy.md (2026-05-24 拡張):
      2 (強): 同ライン直行 (1着line頭 + 2着line番手 + 3着line3番手)
      1 (中): 同ライン番手 or 3番手のいずれか一致
      0 (弱): 単騎頭 + 別ライン2着 + さらに別ライン3着 等、ライン根拠が薄い

    line構造弱+オッズ未取得の買い目は押さえ上位に入れない判断に使う。
    """
    from .scoring import build_line_position_map
    if not combo or "-" not in combo:
        return 0
    parts = combo.split("-")
    if len(parts) != 3:
        return 0
    try:
        a, b_, c = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, TypeError):
        return 0
    line_map = build_line_position_map(lines or [])
    pa = line_map.get(a)
    pb = line_map.get(b_)
    pc = line_map.get(c)
    # 同ライン直行 (1着頭 + 2着番手 + 3着3番手)
    if (
        pa and pb and pc
        and pa.line_name == pb.line_name == pc.line_name
        and pa.is_head and pb.is_bantan and pc.is_third
    ):
        return 2
    # 同ライン番手頭 (2着頭 + 1着番手 + 3着3番手) も「強」
    if (
        pa and pb and pc
        and pa.line_name == pb.line_name == pc.line_name
        and pa.is_bantan and pb.is_head and pc.is_third
    ):
        return 2
    # 1着・2着が同ライン (頭+番手 or 頭+3番手)
    head_pair_same_line = (
        pa and pb
        and not pa.is_tanki and not pb.is_tanki
        and pa.line_name == pb.line_name
    )
    if head_pair_same_line:
        return 1
    # 1着・3着が同ライン
    head_third_same_line = (
        pa and pc
        and not pa.is_tanki and not pc.is_tanki
        and pa.line_name == pc.line_name
    )
    if head_third_same_line:
        return 1
    # 2着・3着が同ライン (codex review 反映: 5-1-7 のような
    # 単騎/別線頭 + 本命ライン頭-番手 もライン根拠ありとして中(1))
    second_third_same_line = (
        pb and pc
        and not pb.is_tanki and not pc.is_tanki
        and pb.line_name == pc.line_name
    )
    if second_third_same_line:
        return 1
    # 単騎頭 + 別ライン番手/3番手 ＝ 根拠薄い
    return 0


def _top_pick_score(b) -> float:
    """top_pick 候補の優先度スコア。

    強い優先順位 (高いほど上位):
      1. odds取得済み + 妙味あり/本線向き      (100点台)
      2. odds取得済み + その他ラベル           (60点台)
      3. odds未取得                            (0-15点 = ライン自然度のみ)
    """
    s = _line_natural_score(b)
    if b.market_odds is not None:
        s += 60.0
        if b.value_label in ("妙味あり", "本線向き"):
            s += 40.0
    return s


def _compute_top_pick(p: Prediction, *, max_picks: int = 2) -> list:
    """「一番買いたい買い目」候補を計算する (最大 max_picks 点)。

    武雄2R 要件1 (2026-05-24): final_conclusion 書き換えと
    `_summarize_for_final` 内の top_pick で同一ロジックを共有するため切り出し。

    `_top_pick_disqualified` (安い人気筋/見送り寄り/odds<5) は除外し、
    `_top_pick_score` 降順で最大 max_picks 点を返す。
    """
    pool = [
        b for b in (list(p.honsen) + list(p.osae))
        if not _top_pick_disqualified(b)
    ]
    pool_sorted = sorted(pool, key=lambda b: -_top_pick_score(b))
    seen: set[str] = set()
    out: list = []
    for b in pool_sorted:
        if b.combination in seen:
            continue
        seen.add(b.combination)
        out.append(b)
        if len(out) >= max_picks:
            break
    return out


def _summarize_for_final(
    p: Prediction, *, input_data=None, final_sel=None,
) -> str:
    """実購入候補として絞った最終結論を組み立てる。

    枠絞り（仕様16章 + ユーザー要件）:
    - 一番買いたい買い目: 最大2点（**オッズ取得済み優先** + ライン自然度）
    - 押さえるべき買い目: 最大4点（押さえ + 本線残り上位）
    - 少額で足す穴: 最大2点（value_label=妙味あり/穴として少額）
    - ガミになりやすい買い目: 全カテゴリから抽出

    input_data があれば line構造の弱い + オッズ未取得買い目を押さえ上位から
    降格する (要件3)。

    final_sel (FinalSelection) があれば top_pick として best_bets を直接使う
    (ルール9: ガールズ/新人戦上限が表示で破れないように)。
    """
    out: list[str] = []
    lines_list = (input_data.lines if input_data is not None else []) or []

    # final_sel があれば best_bets を top_pick として使う (ルール9上限を尊重)
    if final_sel is not None and final_sel.best_bets:
        top_pick = list(final_sel.best_bets)
    else:
        top_pick = _compute_top_pick(p, max_picks=2)
    seen_combos: set[str] = {b.combination for b in top_pick}

    # 押さえるべき買い目: osae カテゴリの順番をそのまま尊重（最大4点）
    # 「見送り寄り」「高 gami」「odds<5」は押さえからも除外
    # 武雄2R 要件2 (2026-05-24): top_pick の combo は cover_pick から **完全除外**
    # (旧ロジック `_keep_in_cover_despite_overlap` は撤廃 - 同一買い目を
    # 「一番買いたい」と「押さえるべき」両方に表示するのは混乱を招く)
    #
    # 要件3 (2026-05-24): line構造弱 + market_odds=None の買い目は cover の
    # 末尾扱い (上位ではなく overflow に逃がす)。最終的に cover の末尾 or
    # 「オッズ未取得だが展開上必要な候補」枠へ送る。
    cover_pick: list = []
    cover_combos: set[str] = set()
    weak_overflow: list = []  # line構造弱+odds=None の降格組

    def _is_weak_no_odds(b) -> bool:
        if b.market_odds is not None:
            return False
        # 市場偏り起因の派生候補は line構造が弱くても残す
        reason = b.reason or ""
        if "市場偏り" in reason:
            return False
        return _bet_line_strength(b.combination, lines_list) == 0

    # (a) osae を走査 (top_pick 重複は完全除外)
    for b in p.osae:
        if b.combination in cover_combos:
            continue
        if b.combination in seen_combos:
            continue
        if _top_pick_disqualified(b):
            continue
        if _is_weak_no_odds(b):
            weak_overflow.append(b)
            continue
        cover_combos.add(b.combination)
        cover_pick.append(b)
        if len(cover_pick) >= 4:
            break

    # (b) 残り枠を top_pick と重複しない honsen で埋める
    if len(cover_pick) < 4:
        honsen_extra = [
            b for b in p.honsen
            if b.combination not in seen_combos
            and b.combination not in cover_combos
            and not _top_pick_disqualified(b)
        ]
        honsen_extra.sort(key=lambda b: -_top_pick_score(b))
        for b in honsen_extra:
            if _is_weak_no_odds(b):
                weak_overflow.append(b)
                continue
            cover_pick.append(b)
            cover_combos.add(b.combination)
            if len(cover_pick) >= 4:
                break

    # (c) cover の枠がまだ余っていれば weak_overflow を末尾に補充
    if len(cover_pick) < 4 and weak_overflow:
        for b in weak_overflow:
            if b.combination in cover_combos:
                continue
            cover_pick.append(b)
            cover_combos.add(b.combination)
            if len(cover_pick) >= 4:
                break

    # 少額穴: 妙味あり/穴として少額（最大2点）
    small_longshot: list = []
    for b in list(p.ana) + list(p.ooana):
        if b.value_label in ("妙味あり", "穴として少額"):
            small_longshot.append(b)
    small_longshot = small_longshot[:2]
    gami_warn: list = []
    seen_gami: set[str] = set()
    for buc in (p.honsen, p.osae, p.ana, p.ooana):
        for b in buc:
            # ガミ警戒セクションの条件:
            # - market_odds が取得済み（オッズ未取得は対象外）
            # - market_odds < 15倍 + gami_risk >= 0.6  （安いオッズで一定のリスク）
            #   または market_odds < 20倍 + gami_risk >= 0.8  （ややオッズあっても極高リスク）
            # 20倍前後の穴候補は「点数注意」に留め、ガミ警戒には出さない
            if b.market_odds is None or b.combination in seen_gami:
                continue
            qualifies = (
                (b.market_odds < 15.0 and b.gami_risk >= 0.6)
                or (b.market_odds < 20.0 and b.gami_risk >= 0.8)
            )
            if qualifies:
                gami_warn.append(b)
                seen_gami.add(b.combination)

    def _line(b) -> str:
        bits = [b.combination]
        if b.market_odds is not None:
            bits.append(f"{b.market_odds:.1f}倍")
        if b.value_label:
            bits.append(b.value_label)
        return " / ".join(bits)

    # 本線がすべて market_odds=None の場合は「オッズ確認後に判断する本線候補」表示
    # 要件1,5: 「一番買いたい」ではなくオッズ確認待ち扱い
    honsen_all_no_odds = (
        bool(p.honsen)
        and all(b.market_odds is None for b in p.honsen)
    )
    top_pick_all_no_odds = (
        bool(top_pick)
        and all(b.market_odds is None for b in top_pick)
    )
    if honsen_all_no_odds and top_pick_all_no_odds:
        # 要件3: 強警告を最強レベルに (セクション名そのものに警告)
        out.append("### ⚠️ 主軸候補オッズ未取得（購入判断保留）")
        out.append(
            "> **本線がすべてオッズ未取得です。**"
            "確定オッズ取得後に再判断してください。"
        )
        out.append(
            "> 現時点では実購入推奨できる本線買い目はありません。"
        )
        out.append("")
        out.append("**参考: オッズ確認後に判断する本線候補**")
        for b in top_pick:
            out.append(f"- {_line(b)}")
        out.append(
            "- ⚠️ **主軸候補はオッズ未取得のため、実購入は直前オッズ確認後**"
        )
        out.append(
            "- ⚠️ 市場が安く売れている人気筋には厚く張らないこと"
        )
    else:
        out.append("### 一番買いたい買い目")
        if top_pick:
            for b in top_pick:
                out.append(f"- {_line(b)}")
            # 要件4: 一部オッズ未取得の場合は確認メモ
            #        全部オッズ未取得の場合はさらに強い警告
            if top_pick_all_no_odds:
                out.append(
                    "- ⚠️ **主軸候補はオッズ未取得のため、実購入は直前オッズ確認後**"
                )
                out.append(
                    "- ⚠️ 現時点では市場が安く売れている人気筋に厚く張らないこと"
                )
            elif any(b.market_odds is None for b in top_pick):
                out.append(
                    "- ※ オッズ未取得の買い目あり → 取得後に再確認してください"
                )
        else:
            out.append("（一番買いたい買い目は該当なし。本線が安すぎる人気・"
                       "ガミ警戒の場合は少額にとどめてください）")

    out.append("")
    out.append("### 押さえるべき買い目")
    if cover_pick:
        for b in cover_pick:
            out.append(f"- {_line(b)}")
    else:
        out.append("（該当なし）")

    out.append("")
    out.append("### 少額で足す穴")
    if small_longshot:
        for b in small_longshot:
            out.append(f"- {_line(b)}")
    else:
        out.append("（妙味のある穴は検出されませんでした）")

    out.append("")
    out.append("### ガミになりやすい買い目")
    if gami_warn:
        for b in gami_warn:
            out.append(f"- {_line(b)}  [gami_risk {b.gami_risk:.2f}]")
    else:
        out.append("（ガミリスク高の買い目は検出されませんでした）")

    # ---- 実購入判断サマリ ----
    # 候補羅列ではなく、買うべき/買わないべきを明示的にまとめる。
    # 合計5点を目安に絞る（本線2-3 / 押さえ2 / 穴1）。
    out.append("")
    out.append("### 実購入判断")
    judgement_lines = _build_purchase_judgement(
        top_pick, cover_pick, small_longshot, gami_warn,
        honsen=list(p.honsen),
        osae=list(p.osae),
        input_data=input_data,
        lines=lines_list,
    )
    out.extend(judgement_lines)

    return "\n".join(out)


def _build_purchase_judgement(
    top_pick, cover_pick, small_longshot, gami_warn,
    *,
    honsen: Optional[list] = None,
    osae: Optional[list] = None,
    input_data=None,
    lines: Optional[list] = None,
) -> list[str]:
    """実購入判断サマリ（要件3,4,武雄1R要件1-4 拡張）。

    枠 (2026-05-24 武雄1R 拡張):
        1. オッズ取得済みで買える候補 (top_pick で market_odds 取得済み)
        2. オッズ確認後の本線候補 (honsen の market_odds=None 全部 + top_pick)
        3. **オッズ未取得だが展開上必要な候補** (cover で odds=None かつ
           line構造強 or 市場偏り)
        4. **市場偏り集中頭の派生候補** (NEW; bias の集中頭が最終判断に
           最低2点出ない場合に補充)
        5. 押さえとして必要 (odds取得済みの cover)
        6. 少額の穴
        7. 安い人気筋 / ガミ警戒（参考、厚く張らない）
            - 集中頭の低配当は「市場偏り(集中頭): 売れすぎ」と区別表示

    odds 取得済みと未取得 + 展開根拠（line構造/市場偏り）を分けることで、
    購入判断の精度を上げる。
    """
    out: list[str] = []
    lines = lines or []
    honsen = honsen or []
    osae = osae or []
    # top_pick を odds の有無で分離
    odds_present_main = [b for b in top_pick if b.market_odds is not None]

    # 1. オッズ取得済みで買える候補（最優先）
    odds_present_combos: set[str] = set()
    if odds_present_main:
        buys = odds_present_main[:3]
        combos = " / ".join(b.combination for b in buys)
        out.append(
            f"- **オッズ取得済みで買える候補**: {combos}"
            f"（妙味/本線向き、購入対象）"
        )
        odds_present_combos = {b.combination for b in buys}

    # 2. オッズ確認後の本線候補 (武雄1R 要件2: honsen 全体の odds=None を対象)
    #    top_pick の odds=None も honsen に含まれるが、honsen 起点で集約
    odds_missing_honsen: list = []
    seen_missing: set[str] = set()
    # まず top_pick の odds=None を優先 (本線として推奨されている)
    for b in top_pick:
        if b.market_odds is None and b.combination not in seen_missing:
            odds_missing_honsen.append(b)
            seen_missing.add(b.combination)
    # 次に honsen の odds=None で _top_pick_disqualified=False を追加
    for b in honsen:
        if b.combination in seen_missing:
            continue
        if b.combination in odds_present_combos:
            continue
        if b.market_odds is None and not _top_pick_disqualified(b):
            odds_missing_honsen.append(b)
            seen_missing.add(b.combination)
    if odds_missing_honsen:
        combos = " / ".join(b.combination for b in odds_missing_honsen[:3])
        out.append(
            f"- **オッズ確認後の本線候補**: {combos}"
            f"（オッズ取得後に再判断）"
        )

    # 両方無ければ
    if not odds_present_main and not odds_missing_honsen:
        out.append(
            "- **本線として有力**: 該当なし → 見送り or 全体的に少額"
        )

    # 3. オッズ未取得だが展開上必要な候補 (要件4 NEW)
    #    cover_pick の中で market_odds=None かつ line構造強 or 市場偏り起因
    #    top_pick と重複するものは表示しない (top_pick が上位枠で既に表示済み)
    top_combos = {b.combination for b in top_pick}
    missing_combos = {b.combination for b in odds_missing_honsen[:3]}

    def _is_tenkai_needed(b) -> bool:
        if b.market_odds is not None:
            return False
        if b.combination in top_combos or b.combination in missing_combos:
            return False
        reason = b.reason or ""
        if "市場偏り" in reason:
            return True
        return _bet_line_strength(b.combination, lines) >= 1

    tenkai_needed = [b for b in cover_pick if _is_tenkai_needed(b)]
    tenkai_combos = {b.combination for b in tenkai_needed[:3]}
    if tenkai_needed:
        combos = " / ".join(b.combination for b in tenkai_needed[:3])
        out.append(
            f"- **オッズ未取得だが展開上必要な候補**: {combos}"
            f"（オッズ取得後に厚みを判断）"
        )

    # 4. 市場偏り集中頭の最低2点保持 (武雄1R 要件1)
    #    bias.has_head_focus なら、最終判断に集中頭買い目が最低2点表示される
    #    ことを保証。上記の枠 (top_pick / missing_honsen / tenkai_needed) に
    #    集中頭買い目が2点未満なら、補充表示する。
    supplement: list = []
    supplement_combos: set[str] = set()
    if input_data is not None:
        from .output_validation import detect_market_bias
        bias = detect_market_bias(input_data)
        if bias.has_head_focus and bias.focused_head is not None:
            head = bias.focused_head
            shown_combos = (
                odds_present_combos | missing_combos | tenkai_combos
            )
            head_shown_count = sum(
                1 for c in shown_combos
                if c and "-" in c and c.split("-")[0] == str(head)
            )
            if head_shown_count < 2:
                # honsen + osae から集中頭買い目を補充
                # codex review 反映: _top_pick_disqualified (安すぎ等) は
                # 「市場偏り(集中頭の低配当)」枠と重複するため除外
                head_pool = []
                seen_pool: set[str] = set(shown_combos)
                for b in (honsen + osae):
                    if not b.combination or "-" not in b.combination:
                        continue
                    if b.combination in seen_pool:
                        continue
                    if b.combination.split("-")[0] != str(head):
                        continue
                    if _top_pick_disqualified(b):
                        continue
                    head_pool.append(b)
                    seen_pool.add(b.combination)
                need = 2 - head_shown_count
                # オッズ取得済みを優先、その後 odds=None
                head_pool.sort(
                    key=lambda b: (
                        b.market_odds if b.market_odds is not None else 999.0
                    )
                )
                supplement = head_pool[:need]
                if supplement:
                    combos = " / ".join(b.combination for b in supplement)
                    out.append(
                        f"- **市場注目枠({head}番頭の派生候補)**: {combos}"
                        f"（市場偏りに合わせて最低2点残す）"
                    )
                    supplement_combos = {b.combination for b in supplement}

    # 5. 押さえとして必要（オッズ取得済みの cover を優先・最大2点）
    #    codex review 反映 + 武雄2R 重複除外:
    #    top_pick / tenkai_needed / missing_honsen / supplement と重複させない
    cover_with_odds = [
        b for b in cover_pick
        if b.market_odds is not None
        and b.combination not in top_combos
        and b.combination not in missing_combos
        and b.combination not in supplement_combos
    ]
    cover_remaining = [
        b for b in cover_pick
        if b.combination not in top_combos
        and b.combination not in tenkai_combos
        and b.combination not in missing_combos
        and b.combination not in supplement_combos
    ]
    buy_cover = (
        cover_with_odds[:2] if cover_with_odds else cover_remaining[:2]
    )
    if buy_cover:
        combos = " / ".join(b.combination for b in buy_cover)
        out.append(f"- **押さえとして必要**: {combos}（押さえ2点）")

    # 武雄2R 要件4 (2026-05-24): 実購入候補が4点以上 + market_odds<10 を含む
    # → 「低配当注意 / 点数を絞る」の警告を表示
    # 「実購入候補」は top_pick / cover / tenkai_needed / supplement /
    # odds_missing_honsen を含む (購入対象として表示される全枠)
    # codex review 反映: odds_missing_honsen も含めて集計の一貫性を確保
    purchase_bets = (
        list(odds_present_main) + list(buy_cover)
        + list(tenkai_needed[:3]) + list(supplement)
        + list(odds_missing_honsen[:3])
    )
    # 重複除外
    seen_purchase: set[str] = set()
    purchase_unique = []
    for b in purchase_bets:
        if b.combination in seen_purchase:
            continue
        seen_purchase.add(b.combination)
        purchase_unique.append(b)
    LOW_ODDS_THRESHOLD = 10.0
    low_odds_picks = [
        b for b in purchase_unique
        if b.market_odds is not None and b.market_odds < LOW_ODDS_THRESHOLD
    ]
    if len(purchase_unique) >= 4 and low_odds_picks:
        combos = " / ".join(
            f"{b.combination}({b.market_odds:.1f}倍)"
            for b in low_odds_picks[:3]
        )
        out.append(
            f"- ⚠️ **低配当注意**: 実購入候補 {len(purchase_unique)}点中、"
            f"{combos} は10倍未満 → 点数を絞ることを推奨"
        )

    # 6. 少額穴（最大1点）
    if small_longshot:
        combo = small_longshot[0].combination
        out.append(f"- **少額の穴**: {combo}（1点までを目安に）")

    # 7. 安い人気筋 / ガミ警戒（参考） - 武雄1R 要件4: 市場偏り起因を区別
    if gami_warn:
        # bias の focused_head 判定 (reason 文字列に依存せず head 一致でも区別)
        focused_head_str: Optional[str] = None
        if input_data is not None:
            from .output_validation import detect_market_bias
            bias_for_label = detect_market_bias(input_data)
            if (
                bias_for_label.has_head_focus
                and bias_for_label.focused_head is not None
            ):
                focused_head_str = str(bias_for_label.focused_head)

        def _is_market_focused_cheap(b) -> bool:
            if b.market_odds is None or b.market_odds >= 5.0:
                return False
            if "市場偏り" in (b.reason or ""):
                return True
            if (
                focused_head_str is not None
                and b.combination
                and "-" in b.combination
                and b.combination.split("-")[0] == focused_head_str
            ):
                return True
            return False

        market_cheap = [b for b in gami_warn[:5] if _is_market_focused_cheap(b)]
        other_cheap = [b for b in gami_warn[:5] if b not in market_cheap]
        if market_cheap:
            combos = " / ".join(b.combination for b in market_cheap[:3])
            out.append(
                f"- **市場偏り(集中頭の低配当)**: {combos} は売れすぎ → "
                f"厚く買わない（一番買いたいには入れない）"
            )
        if other_cheap:
            combos = " / ".join(b.combination for b in other_cheap[:3])
            out.append(
                f"- **安い人気筋**: {combos} は売れすぎ / ガミ注意 → 厚く買わない"
                f"（確認程度）"
            )
    return out


def render_prediction_v2(
    p: Prediction,
    *,
    input_data=None,
) -> str:
    """OutputPlan + MarkdownRenderer ベースの新 renderer (2026-05-24)。

    LLM が返す final_conclusion / honsen / osae / ana / ooana は完全に無視し、
    build_output_plan が生成した OutputPlan のみから Markdown を生成する。
    最終的に Markdown 内の 3連単 combo が OutputPlan に存在しなければ
    フォールバック (テンプレート再生成 + 警告追記)。

    既存 render_prediction との互換性のため、別関数として共存。
    """
    if input_data is None:
        # OutputPlan を作るには input_data が必須。fallback として既存 renderer。
        return render_prediction(p, input_data=None)

    from .markdown_renderer import render_output_plan, verify_markdown_combos
    from .output_plan import build_output_plan, OutputPlanWarning
    from .output_validation import sanitize_prediction

    # codex review 反映: sanitize → build_output_plan の順 (旧 renderer と挙動を合わせる)
    # 元の Prediction を保護するため、まず copy して sanitize を適用
    # 2026-05-24: 新人戦用語サニタイズも適用 (is_rookie を渡す)
    # 8b56ba2 後続レビュー反映: sanitize_prediction が
    # BetRecommendation.reason / gami_risk を破壊的に書き換えるため、
    # model_copy(deep=True) でネスト含めて深く複製しないと元の Prediction も
    # 巻き込まれて変更される (例: pred.honsen[0].reason の line 用語が
    # サニタイズで本命候補に書き換わる)。
    p_for_plan = p.model_copy(deep=True)
    is_rookie = bool(input_data.race.resolved_is_rookie())
    sanitize_prediction(p_for_plan, is_rookie=is_rookie)

    # LLM の final_conclusion は OutputPlan で完全に無視するため、
    # 検証段階で validate_prediction_output が再混入させないよう先に消す
    p_for_plan.final_conclusion = ""

    plan = build_output_plan(p_for_plan, input_data)
    md = render_output_plan(plan, p_for_plan, input_data)
    unregistered = verify_markdown_combos(md, plan)
    if unregistered:
        # OutputPlan に存在しない combo が Markdown に混入 → フォールバック
        plan.warnings.append(OutputPlanWarning(
            code="MARKDOWN_COMBO_UNREGISTERED",
            severity="error",
            message=(
                f"Markdown 中に OutputPlan 未登録の combo が検出されました "
                f"({len(unregistered)}件)。テンプレート再生成を強制します。"
            ),
        ))
        # LLM 装飾文を全て安全側のテンプレートに置換
        # codex review 反映: final_conclusion も明示的に空にする
        # (validate が再検出して警告文中に未登録 combo を入れる副作用を防ぐ)
        p_safe = p_for_plan.model_copy(deep=False)
        p_safe.summary = (
            "[整合性フォールバック] LLM出力に未登録買い目が混入していたため、"
            "テンプレート出力に切り替えました。"
        )
        p_safe.venue_trend_text = "(テンプレートフォールバック中)"
        p_safe.weather_text = "(テンプレートフォールバック中)"
        p_safe.lines_text = "(テンプレートフォールバック中)"
        p_safe.final_conclusion = ""
        p_safe.gami_memo = ""
        p_safe.reflection_points = []
        md = render_output_plan(plan, p_safe, input_data)
        # codex review 反映: 再検証で確実に未登録 combo を排除
        still_unregistered = verify_markdown_combos(md, plan)
        if still_unregistered:
            # フォールバック後もまだ未登録なら警告のみ追記 (実害最小化)
            plan.warnings.append(OutputPlanWarning(
                code="MARKDOWN_FALLBACK_LEAKED",
                severity="error",
                message=(
                    f"フォールバック後も未登録 combo が残存 "
                    f"({len(still_unregistered)}件)。手動確認が必要です。"
                ),
            ))
            # codex review 反映 (方針B): renderer_selector が文字列マッチで
            # fallback 検出できるよう、Markdown 末尾にも明示マーカーを残す
            md += (
                "\n<!-- MARKDOWN_FALLBACK_LEAKED: フォールバック後も未登録 "
                "combo が残存しています -->"
            )
    return md


def render_prediction(
    p: Prediction,
    *,
    input_data=None,
) -> str:
    """予想を人間可読な日本語Markdownに整形して返す。

    input_data を渡すとオッズ取得率/データ品質/市場偏り/整合性警告も付与。

    2026-05-24 (final_selection 統合):
    - LLM が出した honsen/osae/ana/ooana を、deterministic な
      `build_final_selection` で再分類して表示する (ルール11)
    - final_conclusion は best_bets から再生成 (ルール10)
    - warnings は出力末尾の「### final_selection 警告」に表示
    """
    # サニタイズ: 「穴馬」→「穴目」等を破壊的に置換 (要件6)
    # 2026-05-24: v1 経路でも新人戦の line 用語をサニタイズ (input_data
    # から is_rookie 判定)
    from .output_validation import sanitize_prediction
    is_rookie_v1 = bool(
        input_data is not None
        and input_data.race.resolved_is_rookie()
    )
    sanitize_prediction(p, is_rookie=is_rookie_v1)

    # ---- final_selection レイヤー (deterministic 再分類) ----
    # LLM の出力は装飾文 (summary 等) として尊重し、買い目の最終分類は
    # 本レイヤーが決定する。
    # codex review 反映: in-place 上書きは DB 保存内容も書き換えてしまうため、
    # 表示用に **コピー** して扱う (元の p.honsen/osae/ana/ooana は保護)。
    final_sel = None
    if input_data is not None:
        from .final_selection import build_final_selection
        final_sel = build_final_selection(p, input_data)
        # 表示用にだけ display_* を反映 (元の Prediction は変更しない)
        # 以降の render ロジックは p.honsen 等を読むため、Prediction の copy で対応
        p = p.model_copy(deep=False)
        p.honsen = list(final_sel.display_honsen)
        p.osae = list(final_sel.display_osae)
        p.ana = list(final_sel.display_ana)
        p.ooana = list(final_sel.display_ooana)

    # 最終結論文中「本線は X, Y を中心に据える」を best_bets の順序で書き換え
    # (ルール10: final_conclusion は final_selection の内容だけから生成)
    if p.final_conclusion:
        import re
        if final_sel is not None and final_sel.best_bets:
            new_honsen_str = ", ".join(
                b.combination for b in final_sel.best_bets
            )
        else:
            # input_data が無い旧パスは _compute_top_pick で代替
            top_pick_for_conclusion = _compute_top_pick(p, max_picks=2)
            new_honsen_str = (
                ", ".join(b.combination for b in top_pick_for_conclusion)
                if top_pick_for_conclusion else ""
            )
        if new_honsen_str:
            p.final_conclusion = re.sub(
                r"本線は\s*[\d\- ,]+を中心に据える。",
                f"本線は {new_honsen_str} を中心に据える。",
                p.final_conclusion,
            )

        # 静岡4R 修正方針2 (2026-05-24): final_conclusion 内に未登録の
        # 3連単買い目がある場合は、LLM 出力を採用せずテンプレート再生成
        registered = set()
        for bucket in (p.honsen, p.osae, p.ana, p.ooana):
            for b in bucket:
                if b.combination:
                    registered.add(b.combination)
        fc_combos = set(re.findall(r"\b(\d-\d-\d)\b", p.final_conclusion))
        unregistered = fc_combos - registered
        if unregistered:
            # テンプレート: final_sel があれば best_bets/small_longshots、
            # 無ければ _compute_top_pick で fallback (codex review 反映)
            if final_sel is not None and final_sel.best_bets:
                best_list = final_sel.best_bets
                longshot_list = final_sel.small_longshots
            else:
                best_list = _compute_top_pick(p, max_picks=2)
                longshot_list = [
                    b for b in (list(p.ana) + list(p.ooana))
                    if b.value_label in ("妙味あり", "穴として少額")
                ][:1]
            best_str = (
                ", ".join(b.combination for b in best_list)
                if best_list else "（該当なし）"
            )
            longshot_str = (
                ", ".join(b.combination for b in longshot_list)
                if longshot_list else ""
            )
            template = f"本線は {best_str} を中心に据える。"
            if longshot_str:
                template += f" 配当狙いとして {longshot_str} を少額で残す。"
            # codex review 反映: 未登録 combo を理由文に埋め込まない
            # (validate が再検出する副作用を防ぐ)
            template += (
                f"\n\n[整合性フォールバック] LLM出力に未登録買い目が"
                f"含まれていたため、テンプレート生成に切り替えました。"
            )
            p.final_conclusion = template

    lines = []
    lines.append(f"# 予想結果  {p.race_id}")
    lines.append("")
    lines.append("## 1. レース概要")
    lines.append(p.summary)
    lines.append("")
    lines.append("## 2. 直近結果からの場の傾向")
    lines.append(p.venue_trend_text)
    lines.append("")
    lines.append("## 3. 天候・雨・風補正")
    lines.append(p.weather_text)
    lines.append("")
    lines.append("## 4. 並び")
    lines.append(p.lines_text)
    lines.append("")
    lines.append("## 5. 印")
    lines.append(_format_marks(p.marks))
    lines.append("")
    lines.append("## 6. 本線")
    # 本線を「実購入候補」「オッズ確認後の本線候補」「安い人気筋」に分離。
    # 安い人気筋: value_label="見送り寄り" / gami_risk>=0.8 / market_odds<5.0
    # 武雄2R 要件3 (2026-05-24): 本線は最大3点。odds=None は「オッズ確認後の
    # 本線候補」に分離。
    real_buys = [b for b in p.honsen if not _top_pick_disqualified(b)]
    cheap_pops = [b for b in p.honsen if _top_pick_disqualified(b)]
    real_buys_with_odds = [b for b in real_buys if b.market_odds is not None]
    real_buys_no_odds = [b for b in real_buys if b.market_odds is None]
    # market_odds 取得済みは「妙味あり/本線向き → その他」順で最大3点
    def _honsen_with_odds_order(b) -> int:
        return 0 if (b.value_label or "") in ("妙味あり", "本線向き") else 1
    real_buys_with_odds.sort(key=_honsen_with_odds_order)
    HONSEN_MAX = 3
    real_buys_with_odds_top = real_buys_with_odds[:HONSEN_MAX]
    if real_buys_with_odds_top:
        lines.append("**実購入候補** (最大3点):")
        lines.append(_format_bets(real_buys_with_odds_top))
    elif not real_buys_no_odds:
        lines.append(
            "（本線にオッズ取得済みの実購入候補なし。オッズ確認後に判断してください）"
        )
    # odds=None は「オッズ確認後の本線候補」として分離表示
    if real_buys_no_odds:
        lines.append("")
        lines.append("**オッズ確認後の本線候補** (オッズ取得後に再判断):")
        lines.append(_format_bets(real_buys_no_odds))
    if cheap_pops:
        lines.append("")
        # 要件4: ガールズ時は odds 帯で 3段階分離
        # - 見送り寄り (odds<3 または value_label="見送り寄り" または gami>=0.9)
        # - 買うなら少額 (3 <= odds < 5)
        # - 確認用 (5 <= odds < 8 + gami)
        # ガールズ以外は従来の「安い人気筋・ガミ注意」として一括表示
        if p.is_girls:
            sayonara_tier: list = []   # 見送り寄り
            shogaku_tier: list = []    # 買うなら少額
            kakunin_tier: list = []    # 確認用
            for b in cheap_pops:
                odds = b.market_odds
                if (
                    b.value_label == "見送り寄り"
                    or b.gami_risk >= 0.9
                    or (odds is not None and odds < 3.0)
                ):
                    sayonara_tier.append(b)
                elif odds is not None and odds < 5.0:
                    shogaku_tier.append(b)
                else:
                    kakunin_tier.append(b)
            if sayonara_tier:
                lines.append("**見送り寄り（売れすぎ・買わない候補）**:")
                lines.append(_format_bets(sayonara_tier))
                lines.append("")
            if shogaku_tier:
                lines.append("**買うなら少額（人気だが妙味薄め）**:")
                lines.append(_format_bets(shogaku_tier))
                lines.append("")
            if kakunin_tier:
                lines.append("**確認用（参考表示・厚く張らない）**:")
                lines.append(_format_bets(kakunin_tier))
        else:
            lines.append("**安い人気筋・ガミ注意（買うなら少額）**:")
            lines.append(_format_bets(cheap_pops))
    lines.append("")
    lines.append("## 7. 押さえ")
    lines.append(_format_bets(p.osae))
    lines.append("")
    lines.append("## 8. 穴")
    lines.append(_format_bets(p.ana))
    lines.append("")
    lines.append("## 9. 大穴")
    lines.append(_format_bets(p.ooana))
    lines.append("")
    lines.append("## 10. 最終結論")
    if p.final_conclusion:
        lines.append(p.final_conclusion)
        lines.append("")
    lines.append(_summarize_for_final(p, input_data=input_data, final_sel=final_sel))
    # final_selection の cheap_popular_bets を「### 実購入判断」末尾に補強表示
    # (display_honsen/osae 上書きで gami_warn 計算が cheap を拾えなくなった分を補う)
    if final_sel is not None and final_sel.cheap_popular_bets:
        combos = " / ".join(
            f"{b.combination}({b.market_odds:.1f}倍)"
            for b in final_sel.cheap_popular_bets[:3]
        )
        lines.append(
            f"- **安い人気筋**: {combos} は売れすぎ / ガミ注意 → "
            f"厚く買わない（確認程度）"
        )
    lines.append("")
    lines.append("## 11. ガミ回避メモ")
    lines.append(p.gami_memo)
    lines.append("")
    lines.append("## 12. 結果入力後に保存すべき反省ポイント")
    for pt in p.reflection_points:
        lines.append(f"- {pt}")
    lines.append("")
    lines.append("---")
    # オッズ取得率 / データ品質 / 市場偏り / 整合性警告 (input_data 必須)
    if input_data is not None:
        from .output_validation import (
            assess_data_quality,
            compute_odds_coverage,
            render_odds_coverage_section,
            summarize_market_bias,
            validate_prediction_output,
        )
        lines.append("")
        coverage = compute_odds_coverage(p)
        lines.append(render_odds_coverage_section(coverage))
        # データ品質
        quality = assess_data_quality(input_data)
        lines.append("")
        lines.append(f"### データ品質: **{quality}**")
        if quality in ("low", "very_low"):
            lines.append(
                "- データ不足のため買い目を広げすぎず、オッズ取得済み買い目を優先してください"
            )
        # 市場偏り
        bias = summarize_market_bias(input_data)
        if bias:
            lines.append("")
            lines.append(f"### 市場の偏り")
            lines.append(f"- {bias}")
        # 整合性警告
        warnings = validate_prediction_output(input_data, p)
        if warnings:
            lines.append("")
            lines.append("### 出力整合性チェック")
            for w in warnings:
                lines.append(f"- ⚠️ [{w.code}] {w.message}")
        # final_selection レイヤーの警告 (低配当注意 / オッズ未取得 等)
        if final_sel is not None and final_sel.warnings:
            lines.append("")
            lines.append("### final_selection 警告")
            for w in final_sel.warnings:
                lines.append(f"- ⚠️ {w}")
        lines.append("---")
    lines.append("（本ツールは予想支援目的のみ。自動投票・購入処理は持ちません）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 予想ロジックの実行
# ---------------------------------------------------------------------------


def run_prediction(
    input_data: RaceInput,
    llm: Optional[LLMClient] = None,
    *,
    reflections: Optional[list[Reflection]] = None,
    value_analysis: bool = True,
    bet_budget: Optional[int] = None,
) -> Prediction:
    """与えられた RaceInput から Prediction を組み立てる（CLI/テスト両方から呼ぶ）。

    reflections が渡された場合は scoring/buildに反映し、実LLMプロンプトにも注入する。
    value_analysis=True のとき、Prediction 生成後に各買い目へ妙味分析を反映する。
    bet_budget が指定されれば、買い目合計をその点数に近づけるよう自動配分する。
    """
    client = llm or build_default_client("mock")
    refs = reflections or []
    scores = compute_scores(input_data)
    apply_reflection_signals(scores, refs, input_data)
    apply_bank_signals(scores, input_data)
    apply_wind_extra_signals(scores, input_data)
    apply_trend_signals(scores, input_data)
    apply_tospo_signals(scores, input_data)
    # F1/グレードレースの「格上」加点（番手・3番手・別線番手・単騎）
    apply_grade_signals(scores, input_data)
    # F2 用の点数差/チャレンジ自力/ライン3車加点
    apply_f2_signals(scores, input_data)
    # 地元選手の加点（地区が会場と一致する選手）
    apply_home_area_signals(scores, input_data)
    # 市場（オッズ人気）からのシグナルを反映。出走表に score が無いガールズや
    # 初期出走表でも、市場の人気を予想に反映できる。
    # 数値不足モードでは市場参照を強める（boost_multiplier=3 で最大±1.5）
    from .scoring import detect_score_data_insufficient
    _insufficient = detect_score_data_insufficient(input_data)
    apply_market_signals(
        scores, input_data.odds,
        boost_multiplier=3.0 if _insufficient else 1.0,
    )
    bets = build_candidate_bets(
        input_data, scores,
        gami_inflation=gami_inflation_from_reflections(refs),
        target_total=bet_budget,
    )
    # Mock も実LLMも同じ拡張プロンプトでOK（Mockはプロンプトを使わない）
    prompt = build_full_prompt(
        input_data, scores, bets, reflections=refs, value_analysis=value_analysis
    )
    prediction = client.generate_prediction(input_data, scores, bets, prompt)
    if value_analysis:
        annotate_prediction_with_value(prediction, scores, input_data.odds)
        # 本線がオッズ未取得ばかりなら、穴のオッズ取得済み中穴を押さえに昇格
        promote_oddful_to_osae(prediction)
        # さらに本線が全件オッズ未取得なら、押さえの妙味ありを本線に昇格
        # （本線セクションと「一番買いたい買い目」の整合性確保）
        promote_oddful_to_honsen(prediction)
    return prediction


def load_race_input(path: Path) -> RaceInput:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return RaceInput.model_validate(raw)


# ---------------------------------------------------------------------------
# Click コマンド
# ---------------------------------------------------------------------------


@click.group(help="競輪予想支援CLI（予想支援目的のみ・自動投票なし）")
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_DB_PATH,
    show_default=True,
    help="SQLite DBの保存先",
)
@click.pass_context
def cli(ctx: click.Context, db_path: Path) -> None:
    ctx.ensure_object(dict)
    ctx.obj["storage"] = Storage(db_path)


def _cli_warn(msg: str) -> None:
    """LLMクライアントからの警告をstderrに日本語で表示する。"""
    click.echo(msg, err=True)


@cli.command("predict", help="手入力JSONから予想を生成して表示する")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="手入力JSONのパス",
)
@click.option(
    "--provider",
    type=str,
    default=None,
    help="LLMプロバイダ。mock / openai / anthropic。未指定時は .env の LLM_PROVIDER を使用（既定: openai）",
)
@click.option(
    "--save/--no-save",
    default=True,
    show_default=True,
    help="予想をDBに保存するか",
)
@click.option(
    "--prompt-out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="生成したLLMプロンプトをファイル保存（任意）",
)
@click.option(
    "--use-reflections/--no-reflections",
    "use_reflections",
    default=True,
    show_default=True,
    help="関連する過去の反省ログを scoring/プロンプトに自動注入するか",
)
@click.option(
    "--reflection-limit",
    type=int,
    default=5,
    show_default=True,
    help="注入する反省ログの最大件数",
)
@click.option(
    "--value-analysis/--no-value-analysis",
    "value_analysis",
    default=True,
    show_default=True,
    help="買い目ごとにオッズ妙味分析（value_label）を付与するか",
)
@click.option(
    "--bet-budget",
    type=int,
    default=None,
    help=(
        "目標合計買い目点数（10〜30程度を推奨）。"
        "本線/押さえ/穴/大穴 に自動配分される。"
        "未指定なら既定（合計13〜20点）。"
        ".env の BET_BUDGET も参照可。"
    ),
)
@click.option(
    "--renderer",
    type=click.Choice(["v1", "v2", "auto"], case_sensitive=False),
    default="auto",
    show_default=True,
    help=(
        "出力 renderer の選択 (2026-05-24 v2 デフォルト化)。"
        "v2=OutputPlan+MarkdownRenderer (デフォルト, deterministic, "
        "LLM 捏造 combo を排除)、"
        "v1=legacy render_prediction (互換用)、"
        "auto=環境変数 KEIRIN_USE_OUTPUT_PLAN を参照 "
        "(0/false/no で v1、それ以外は v2)。"
    ),
)
@click.pass_context
def predict_cmd(
    ctx: click.Context,
    input_path: Path,
    provider: Optional[str],
    save: bool,
    prompt_out: Optional[Path],
    use_reflections: bool,
    reflection_limit: int,
    value_analysis: bool,
    bet_budget: Optional[int],
    renderer: str,
) -> None:
    settings = load_settings(override_provider=provider)
    if settings.provider not in SUPPORTED_PROVIDERS:
        raise click.ClickException(
            f"未知のLLMプロバイダ: '{settings.provider}'。"
            f"サポート対象: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    input_data = load_race_input(input_path)
    try:
        client = build_client(settings.provider, settings=settings, warn=_cli_warn)
    except UnknownProviderError as e:
        raise click.ClickException(str(e))

    storage: Storage = ctx.obj["storage"]
    reflections: list[Reflection] = []
    if use_reflections and reflection_limit > 0:
        reflections = storage.get_relevant_reflections(
            input_data, limit=reflection_limit
        )
        if reflections:
            click.echo(
                f"過去の反省を{len(reflections)}件参照します (limit={reflection_limit})",
                err=True,
            )
        else:
            click.echo("関連する過去の反省はありません。", err=True)
    else:
        click.echo("反省ログ参照は無効化されています。", err=True)

    scores = compute_scores(input_data)
    apply_reflection_signals(scores, reflections, input_data)
    apply_bank_signals(scores, input_data)
    apply_wind_extra_signals(scores, input_data)
    apply_trend_signals(scores, input_data)
    apply_tospo_signals(scores, input_data)
    # F1/グレードレースの「格上」加点
    apply_grade_signals(scores, input_data)
    apply_f2_signals(scores, input_data)
    apply_home_area_signals(scores, input_data)
    # 数値不足モードでは市場参照を強める（boost_multiplier=3 で最大±1.5）
    from .scoring import detect_score_data_insufficient
    _insufficient = detect_score_data_insufficient(input_data)
    apply_market_signals(
        scores, input_data.odds,
        boost_multiplier=3.0 if _insufficient else 1.0,
    )
    # bet_budget: CLI フラグ > .env > 既定（None）
    effective_budget = bet_budget if bet_budget is not None else settings.bet_budget
    bets = build_candidate_bets(
        input_data,
        scores,
        gami_inflation=gami_inflation_from_reflections(reflections),
        target_total=effective_budget,
    )
    prompt = build_full_prompt(
        input_data,
        scores,
        bets,
        reflections=reflections,
        value_analysis=value_analysis,
    )
    if prompt_out:
        prompt_out.write_text(prompt, encoding="utf-8")
        click.echo(f"LLMプロンプトを書き出しました: {prompt_out}")
    click.echo(f"使用プロバイダ: {settings.provider}", err=True)
    prediction = client.generate_prediction(input_data, scores, bets, prompt)
    if value_analysis:
        annotate_prediction_with_value(prediction, scores, input_data.odds)
        promote_oddful_to_osae(prediction)
        promote_oddful_to_honsen(prediction)
    from .renderer_selector import render_prediction_auto
    click.echo(render_prediction_auto(
        prediction, input_data=input_data, renderer=renderer,
    ))
    if save:
        storage.save_prediction(prediction)
        click.echo(f"\n予想を保存しました: race_id={prediction.race_id}")


@cli.command(
    "result",
    help=(
        "レース結果を入力し、反省ログを保存する。"
        "race_id は --input から自動抽出、未指定なら直近の予想を使用。"
        "結果は positional または --result で指定可能。"
    ),
)
@click.argument("result_arg", required=False)
@click.option("--race-id", default=None, help="対象の race_id（省略時は --input か直近の予想から決定）")
@click.option(
    "--result",
    "result_flag",
    default=None,
    help="結果。例: 5-1-3 （同着は `3-5-1 / 3-5-9` のように `/` または `,` 区切り）",
)
@click.option("--note", default="", help="自由メモ")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="該当レースの手入力JSON（位置取り判定の精度向上用。race_id 未指定時はここから抽出）",
)
@click.pass_context
def result_cmd(
    ctx: click.Context,
    result_arg: Optional[str],
    race_id: Optional[str],
    result_flag: Optional[str],
    note: str,
    input_path: Optional[Path],
) -> None:
    # ---- 結果 (positional 優先 / --result も互換維持) ----
    result_str = result_arg or result_flag
    if not result_str:
        raise click.ClickException(
            "結果が指定されていません。例: `result 5-1-3` または `result --result 5-1-3`"
            " （同着は `3-5-1 / 3-5-9`）"
        )

    storage: Storage = ctx.obj["storage"]

    # ---- race_id の決定 ----
    # 優先度: 明示 --race-id > --input から抽出 > 直近の予想
    if race_id is None and input_path is not None:
        try:
            data = json.loads(input_path.read_text(encoding="utf-8"))
            race_id = (data.get("race") or {}).get("race_id")
        except Exception as e:
            raise click.ClickException(
                f"--input から race_id を読み取れませんでした: {e}"
            )
        if not race_id:
            raise click.ClickException(
                f"--input に race.race_id が含まれていません: {input_path}"
            )
        click.echo(f"[案内] --input から race_id を抽出: {race_id}", err=True)
    if race_id is None:
        latest = storage.get_latest_prediction()
        if latest is None:
            raise click.ClickException(
                "race_id が特定できません。--race-id か --input を指定するか、先に predict を実行してください。"
            )
        race_id = latest.race_id
        click.echo(f"[案内] 直近の予想を使用します: race_id={race_id}", err=True)

    # ---- prediction の取得 ----
    prediction = storage.get_prediction(race_id)
    if prediction is None:
        raise click.ClickException(
            f"予想が見つかりません: race_id={race_id} 先に predict を実行してください"
        )

    input_data = load_race_input(input_path) if input_path else None
    storage.save_result(race_id, result_str)
    reflection = build_reflection(
        prediction=prediction,
        actual_result=result_str,
        input_data=input_data,
        note=note,
    )
    rid = storage.save_reflection(reflection)
    click.echo(f"結果を保存しました: {race_id} → {result_str}")
    click.echo(f"反省ログID: {rid}")
    click.echo("分類:")
    for cat in reflection.categories:
        click.echo(f"  - {cat}")
    if note:
        click.echo(f"メモ: {note}")


@cli.command("reflections", help="反省ログを表示する")
@click.option("--venue", default=None, help="場名で絞り込み")
@click.option("--weather", default=None, help="天候で絞り込み（例: 雨）")
@click.option("--limit", type=int, default=20, show_default=True)
@click.pass_context
def reflections_cmd(
    ctx: click.Context, venue: Optional[str], weather: Optional[str], limit: int
) -> None:
    storage: Storage = ctx.obj["storage"]
    items = storage.list_reflections(venue=venue, weather_condition=weather, limit=limit)
    if not items:
        click.echo("該当する反省ログはありません。")
        return
    for r in items:
        click.echo("---")
        click.echo(f"race_id: {r.race_id}  /  {r.venue} {r.race_no}R")
        click.echo(
            f"天候: {r.weather_condition or '不明'}  風 {r.wind_speed_mps:.1f}m/s"
            f"  雨 {r.rain_mm_per_hour:.1f}mm/h"
        )
        click.echo(f"予想本線: {', '.join(r.predicted_honsen) or '(なし)'}")
        click.echo(f"実結果: {r.actual_result}")
        click.echo("分類:")
        for cat in r.categories:
            click.echo(f"  - {cat}")
        if r.note:
            click.echo(f"メモ: {r.note}")


@cli.command("create-json", help="手入力JSONのテンプレートを書き出す（--interactive で対話モード）")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="出力先パス",
)
@click.option(
    "--girls/--no-girls",
    default=False,
    help="ガールズ用テンプレートにする",
)
@click.option(
    "--interactive",
    is_flag=True,
    default=False,
    help="対話形式でJSONを作成する",
)
def create_json_cmd(out_path: Path, girls: bool, interactive: bool) -> None:
    if interactive:
        data = _interactive_build()
    else:
        data = _template(girls=girls)
    # 書き出す前に必ず RaceInput としてバリデーション
    try:
        RaceInput.model_validate(data)
    except Exception as e:
        raise click.ClickException(
            f"生成したJSONがバリデーションに失敗しました: {e}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    click.echo(f"テンプレートを書き出しました: {out_path}")


# ---------------------------------------------------------------------------
# quick-json: フラグだけで RaceInput を組み立てるショートカット
# ---------------------------------------------------------------------------


@cli.command(
    "quick-json",
    help="フラグだけで予想用JSONを素早く生成する（最小限の出走表は placeholder）",
)
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--venue", required=True, help="場名。例: 大宮")
@click.option("--race-no", type=int, required=True, help="レース番号 1〜12")
@click.option(
    "--class-name",
    default="A級一般",
    show_default=True,
    help="クラス。例: A級特選 / S級 / ガールズ",
)
@click.option("--date", "date_str", default=None, help="日付 YYYY-MM-DD（既定: 今日）")
@click.option("--start-time", default=None, help="発走時刻。例: 10:53")
@click.option("--race-id", default=None, help="任意のrace_id（未指定時は自動生成）")
@click.option("--bank-note", default=None, help="バンク特性メモ")
@click.option("--weather", default=None, help="天候。例: 晴れ / 曇り / 雨")
@click.option("--wind-direction", default=None, help="風向。例: 北 / 南西")
@click.option(
    "--wind-speed", type=float, default=0.0, show_default=True, help="風速 m/s"
)
@click.option(
    "--rain", type=float, default=0.0, show_default=True, help="降雨量 mm/h"
)
@click.option("--wind-note", default=None, help="風メモ")
@click.option(
    "--lines",
    "lines_text",
    default=None,
    help="並び。例: '3-7-2 / 1-5 / 4-6'（ガールズ時は無視）",
)
@click.option("--girls", is_flag=True, default=False, help="ガールズ競輪扱い（lines無効）")
@click.option(
    "--cars",
    "car_count",
    type=int,
    default=7,
    show_default=True,
    help="lines未指定時に作るplaceholder rider の頭数",
)
def quick_json_cmd(
    out_path: Path,
    venue: str,
    race_no: int,
    class_name: str,
    date_str: Optional[str],
    start_time: Optional[str],
    race_id: Optional[str],
    bank_note: Optional[str],
    weather: Optional[str],
    wind_direction: Optional[str],
    wind_speed: float,
    rain: float,
    wind_note: Optional[str],
    lines_text: Optional[str],
    girls: bool,
    car_count: int,
) -> None:
    if girls and lines_text:
        click.echo("[案内] --girls 指定のため --lines は無視します。", err=True)
    try:
        race_input = build_quick_input(
            venue=venue,
            race_no=race_no,
            class_name=class_name,
            date_str=date_str,
            start_time=start_time,
            race_id=race_id,
            bank_note=bank_note,
            weather=weather,
            wind_direction=wind_direction,
            wind_speed=wind_speed,
            rain=rain,
            wind_note=wind_note,
            lines_text=lines_text,
            girls=girls,
            car_count=car_count,
        )
    except LinesParseError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(f"JSON生成に失敗しました: {e}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(race_input.model_dump_json())
    out_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    click.echo(f"JSONを書き出しました: {out_path}")
    if not lines_text and not girls:
        click.echo("[案内] --lines 未指定のため、車番1〜{n}のplaceholder riderを作成しました。".format(n=car_count), err=True)
    click.echo("[案内] 出走表(score / b_count / コメント等)はファイルを開いて編集してください。", err=True)


# ---------------------------------------------------------------------------
# 対話ビルダー
# ---------------------------------------------------------------------------


def _prompt_text(label: str, default: str = "", *, allow_empty: bool = True) -> str:
    """空文字を許容する click.prompt のラッパ。"""
    val = click.prompt(label, default=default, show_default=bool(default))
    val = (val or "").strip()
    if not allow_empty and not val:
        click.echo("値が必要です。再入力してください。", err=True)
        return _prompt_text(label, default, allow_empty=allow_empty)
    return val


def _prompt_float(label: str, default: float = 0.0) -> float:
    while True:
        raw = click.prompt(label, default=str(default), show_default=True)
        try:
            return float(raw)
        except ValueError:
            click.echo("数値で入力してください。", err=True)


def _prompt_int(label: str, default: int, *, minimum: int, maximum: int) -> int:
    while True:
        raw = click.prompt(label, default=str(default), show_default=True)
        try:
            n = int(raw)
        except ValueError:
            click.echo("整数で入力してください。", err=True)
            continue
        if not minimum <= n <= maximum:
            click.echo(f"{minimum}〜{maximum}の範囲で入力してください。", err=True)
            continue
        return n


def _prompt_bool(label: str, default: bool = False) -> bool:
    return click.confirm(label, default=default)


def _interactive_build() -> dict:
    """対話形式でRaceInput相当のdictを組み立てる。"""
    click.echo("=== 対話形式での予想用JSON作成 ===")
    click.echo("（空欄のままEnterで既定値・None を採用します）")

    venue = _prompt_text("場名 (例: 大垣)", "場名", allow_empty=False)
    race_no = _prompt_int("レース番号 (1-12)", 1, minimum=1, maximum=12)
    class_name = _prompt_text("クラス (例: A級一般 / S級 / ガールズ)", "A級一般", allow_empty=False)
    date_str = _prompt_text("日付 YYYY-MM-DD", "")
    start_time = _prompt_text("発走時刻 hh:mm（任意）", "")
    bank_note = _prompt_text("バンク特性メモ（任意）", "")

    girls = _prompt_bool("ガールズ競輪ですか？", default="ガールズ" in class_name)

    # 天候
    has_weather = _prompt_bool("天候情報を入力しますか？", default=True)
    weather: Optional[str] = None
    wind_dir: Optional[str] = None
    wind_speed = 0.0
    rain = 0.0
    wind_note: Optional[str] = None
    if has_weather:
        weather = _prompt_text("天候 (晴れ/曇り/雨/小雨)", "曇り")
        wind_dir = _prompt_text("風向（任意）", "")
        wind_speed = _prompt_float("風速 m/s", 0.0)
        rain = _prompt_float("降雨量 mm/h", 0.0)
        wind_note = _prompt_text("風メモ（任意）", "")

    # ライン
    lines_text: Optional[str] = None
    car_count = 7
    if girls:
        car_count = _prompt_int("出走頭数", 7, minimum=1, maximum=9)
    else:
        while True:
            lines_text = _prompt_text("並び (例: 5-1-3 / 2-6-4 / 7)", "")
            if not lines_text:
                # ライン未入力なら頭数を聞く
                car_count = _prompt_int("並び未指定。出走頭数を入れてください", 7, minimum=1, maximum=9)
                lines_text = None
                break
            try:
                parse_lines(lines_text)
                break
            except LinesParseError as e:
                click.echo(f"並びを解釈できません: {e}", err=True)

    # 出走表
    riders_input: list[Rider] = []
    cars_from_lines: list[int] = []
    if lines_text:
        for line in parse_lines(lines_text):
            cars_from_lines.extend(line.cars)
    car_list = sorted(set(cars_from_lines)) if cars_from_lines else list(range(1, car_count + 1))

    if _prompt_bool("各車の選手情報を入力しますか？（No なら placeholder で埋めます）", default=True):
        for car in car_list:
            click.echo(f"--- 車番 {car} ---")
            name = _prompt_text("選手名", f"選手{car}", allow_empty=False)
            score = _prompt_float("競走得点", 0.0)
            b_count = _prompt_int("B数", 0, minimum=0, maximum=99)
            nige = _prompt_int("逃げ回数", 0, minimum=0, maximum=99)
            makuri = _prompt_int("捲り回数", 0, minimum=0, maximum=99)
            sashi = _prompt_int("差し回数", 0, minimum=0, maximum=99)
            mark = _prompt_int("マーク回数", 0, minimum=0, maximum=99)
            comment = _prompt_text("脚質コメント（例: 自力/番手/3番手）", "")
            recent = _prompt_text("直近内容まとめ（任意）", "")
            tags_raw = _prompt_text(
                "脚質タグ（カンマ区切り。例: 先行,自力 / 番手,差し / 単騎,自在）", ""
            )
            tags = [t.strip() for t in re.split(r"[、,，]+", tags_raw) if t.strip()] if tags_raw else []
            riders_input.append(
                Rider(
                    car_no=car,
                    name=name,
                    score=score,
                    b_count=b_count,
                    nige=nige,
                    makuri=makuri,
                    sashi=sashi,
                    mark=mark,
                    comment=comment,
                    recent_summary=recent,
                    style_tags=tags,
                )
            )
    else:
        riders_input = [build_placeholder_rider(c) for c in car_list]

    race_input = build_quick_input(
        venue=venue,
        race_no=race_no,
        class_name=class_name,
        date_str=date_str or None,
        start_time=start_time or None,
        bank_note=bank_note or None,
        weather=weather,
        wind_direction=wind_dir or None,
        wind_speed=wind_speed,
        rain=rain,
        wind_note=wind_note or None,
        lines_text=lines_text,
        girls=girls,
        car_count=car_count,
        extra_riders=riders_input,
    )
    return json.loads(race_input.model_dump_json())


def _template(*, girls: bool) -> dict:
    base: dict = {
        "race": {
            "race_id": "YYYYMMDD-venue-N",
            "date": "2026-01-01",
            "venue": "場名",
            "race_no": 1,
            "class_name": "ガールズ" if girls else "A級一般",
            "start_time": "00:00",
            "bank_note": "",
        },
        "weather": {
            "condition": "晴れ",
            "rain_mm_per_hour": 0.0,
            "wind_direction": "北",
            "wind_speed_mps": 0.0,
            "wind_note": "",
        },
        "lines": []
        if girls
        else [
            {"line_name": "ライン名", "cars": [1, 2, 3], "description": "①-②-③"}
        ],
        "riders": [
            {
                "car_no": i,
                "name": f"選手{i}",
                "score": 0.0,
                "b_count": 0,
                "nige": 0,
                "makuri": 0,
                "sashi": 0,
                "mark": 0,
                "comment": "",
                "recent_summary": "",
                "style_tags": [],
            }
            for i in range(1, 8)
        ],
        "odds": [],
        "recent_results": [],
        "venue_trend": None,
        "user_note": "",
    }
    return base


# ---------------------------------------------------------------------------
# fetch-json: 外部ソースから手入力JSON互換ファイルを取得して保存
# ---------------------------------------------------------------------------


SUPPORTED_KINDS = ("race_card", "results", "odds", "race_notes")
_RACE_NOTES_SOURCES = (
    "tospo", "winticket", "netkeirin", "oddspark", "yenjoy", "manual_text", "generic",
)
SUPPORTED_BET_TYPES = ("trifecta", "trio", "exacta")


@cli.command(
    "fetch-json",
    help="外部データソースから構造化JSONを取得する（自動投票なし）",
)
@click.option(
    "--source",
    type=str,
    required=True,
    help=f"ソース名。サポート: {', '.join(SUPPORTED_SOURCES)}",
)
@click.option(
    "--kind",
    type=str,
    default="race_card",
    show_default=True,
    help=f"取得種別。サポート: {', '.join(SUPPORTED_KINDS)}",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="出力先パス",
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="manual ソース時の入力JSONパス",
)
@click.option("--venue", default=None, help="場名（外部ソース用）")
@click.option("--race-no", type=int, default=None, help="レース番号（外部ソース用）")
@click.option("--date", "date_str", default=None, help="日付 YYYY-MM-DD（外部ソース用）")
@click.option(
    "--no-cache",
    "no_cache",
    is_flag=True,
    default=False,
    help="HTTPキャッシュを使わない（外部ソース時）",
)
@click.option(
    "--refresh-cache",
    "refresh_cache",
    is_flag=True,
    default=False,
    help="既存キャッシュを無視して再取得し、新結果でキャッシュを上書きする",
)
@click.option(
    "--cache-ttl",
    type=int,
    default=DEFAULT_TTL_SECONDS,
    show_default=True,
    help="キャッシュTTL秒",
)
@click.option(
    "--rate-limit-seconds",
    type=float,
    default=1.0,
    show_default=True,
    help="同一ドメインへのアクセス間隔（秒）",
)
@click.option(
    "--fallback-input",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="外部取得失敗時のフォールバック手入力JSON",
)
@click.option(
    "--bet-type",
    type=str,
    default=None,
    help=f"--kind odds 用。{', '.join(SUPPORTED_BET_TYPES)} を指定。未指定なら全種別",
)
@click.option(
    "--limit",
    type=int,
    default=20,
    show_default=True,
    help="--kind odds の人気上位件数上限",
)
@click.option(
    "--session-no",
    type=int,
    default=1,
    show_default=True,
    help="開催日番号（初日=1）。Kドリームスの URL 生成に使う",
)
@click.option(
    "--url",
    "direct_url",
    type=str,
    default=None,
    help="--kind race_notes 用。東スポ予想ページURLを直接指定",
)
def fetch_json_cmd(
    source: str,
    kind: str,
    out_path: Path,
    input_path: Optional[Path],
    venue: Optional[str],
    race_no: Optional[int],
    date_str: Optional[str],
    no_cache: bool,
    refresh_cache: bool,
    cache_ttl: int,
    rate_limit_seconds: float,
    fallback_input: Optional[Path],
    bet_type: Optional[str],
    limit: int,
    session_no: int,
    direct_url: Optional[str],
) -> None:
    src = (source or "").strip().lower()
    if src not in SUPPORTED_SOURCES:
        raise click.ClickException(
            f"未知のソース: '{source}'。サポート対象: {', '.join(SUPPORTED_SOURCES)}"
        )
    target_kind = (kind or "race_card").strip().lower()
    if target_kind not in SUPPORTED_KINDS:
        raise click.ClickException(
            f"未知の取得種別: '{kind}'。サポート対象: {', '.join(SUPPORTED_KINDS)}"
        )

    # 外部ソース用の共有HTTPクライアント
    cache = FileCache(
        cache_dir=DEFAULT_CACHE_DIR, ttl_seconds=cache_ttl, enabled=not no_cache
    )
    rate_limiter = RateLimiter(min_interval_seconds=rate_limit_seconds)
    http_client = HttpClient(
        cache=cache, rate_limiter=rate_limiter, force_refresh=refresh_cache,
    )

    parsed_date = None
    if date_str:
        try:
            from datetime import datetime
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise click.ClickException(
                f"日付は YYYY-MM-DD で指定してください: '{date_str}'"
            )

    try:
        fetcher = build_fetcher(
            src,
            http_client=http_client,
            manual_input_path=str(input_path) if input_path else None,
        )
    except FetchError as e:
        raise click.ClickException(str(e))

    primary_err: Optional[Exception] = None
    payload: Optional[dict] = None

    def _try_with(f) -> Optional[dict]:
        # session_no は kdreams のみが解釈する。ManualFetcher 等は **kwargs で無視される。
        if target_kind == "race_card":
            try:
                return f.fetch_race_card(
                    venue=venue, race_no=race_no, date=parsed_date, session_no=session_no
                )
            except TypeError:
                return f.fetch_race_card(venue=venue, race_no=race_no, date=parsed_date)
        if target_kind == "results":
            try:
                items = f.fetch_results(
                    venue=venue, race_no=race_no, date=parsed_date, session_no=session_no
                )
            except TypeError:
                items = f.fetch_results(
                    venue=venue, race_no=race_no, date=parsed_date
                )
            return {
                "source": f.source_name,
                "kind": "results",
                "venue": venue,
                "date": date_str,
                "results": items,
            }
        if target_kind == "odds":
            try:
                payload = f.fetch_odds(
                    venue=venue,
                    race_no=race_no,
                    date=parsed_date,
                    bet_type=bet_type,
                    limit=limit,
                    session_no=session_no,
                )
            except TypeError:
                payload = f.fetch_odds(
                    venue=venue,
                    race_no=race_no,
                    date=parsed_date,
                    bet_type=bet_type,
                    limit=limit,
                )
            return {
                "source": f.source_name,
                "kind": "odds",
                "venue": venue,
                "date": date_str,
                "race_no": race_no,
                "odds": payload,
            }
        if target_kind == "race_notes":
            return f.fetch_race_notes(
                venue=venue,
                race_no=race_no,
                date=parsed_date,
                url=direct_url,
            )
        return None

    try:
        payload = _try_with(fetcher)
    except NotImplementedSource as e:
        primary_err = e
        click.echo(f"[案内] {e}", err=True)
    except FetchError as e:
        primary_err = e
        click.echo(f"[警告] 外部取得に失敗しました: {e}", err=True)

    if payload is None:
        if fallback_input is None:
            raise click.ClickException(
                "取得に失敗しました。--fallback-input <手入力JSON> を指定するとフォールバックできます。"
            )
        click.echo(
            f"[案内] フォールバック手入力JSONを読み込みます: {fallback_input}",
            err=True,
        )
        try:
            fallback_fetcher = ManualFetcher(input_path=fallback_input)
            payload = _try_with(fallback_fetcher)
        except FetchError as e:
            raise click.ClickException(
                f"フォールバックも失敗: {e} (一次原因: {primary_err})"
            )

    # 構造化バリデーション
    if target_kind == "race_card":
        try:
            RaceInput.model_validate(payload)
        except Exception as e:
            raise click.ClickException(
                f"取得結果が RaceInput スキーマに合致しません: {e}"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    click.echo(f"JSONを書き出しました: {out_path}")


# ---------------------------------------------------------------------------
# enrich-json: 既存 RaceInput JSON に外部取得結果を取り込む
# ---------------------------------------------------------------------------


_ENRICH_RESULTS_SOURCES = ("kdreams", "manual")


def _load_results_json(path: Path):
    if not path.exists():
        raise click.ClickException(f"results-json が見つかりません: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise click.ClickException(
            f"results-json の JSON パースに失敗しました: {path} ({e})"
        )


def _fetch_results_from_source(
    *,
    source: str,
    venue: Optional[str],
    date_str: Optional[str],
    race_no: Optional[int],
    input_path: Optional[Path],
    no_cache: bool,
    cache_ttl: int,
    rate_limit_seconds: float,
) -> dict:
    """指定ソースから fetch_results を呼び、envelope dict を返す。"""
    src = (source or "").strip().lower()
    if src not in _ENRICH_RESULTS_SOURCES:
        raise click.ClickException(
            f"未対応の results-source: '{source}'。"
            f"サポート対象: {', '.join(_ENRICH_RESULTS_SOURCES)}"
        )
    if not venue:
        raise click.ClickException(
            "--results-source 利用時は --venue が必須です。"
        )
    if not date_str:
        raise click.ClickException(
            "--results-source 利用時は --date が必須です。"
        )
    try:
        from datetime import datetime
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise click.ClickException(
            f"日付は YYYY-MM-DD で指定してください: '{date_str}'"
        )

    cache = FileCache(
        cache_dir=DEFAULT_CACHE_DIR, ttl_seconds=cache_ttl, enabled=not no_cache
    )
    rate_limiter = RateLimiter(min_interval_seconds=rate_limit_seconds)
    http_client = HttpClient(cache=cache, rate_limiter=rate_limiter)

    try:
        fetcher = build_fetcher(
            src,
            http_client=http_client,
            manual_input_path=str(input_path) if input_path else None,
        )
    except FetchError as e:
        raise click.ClickException(str(e))

    try:
        items = fetcher.fetch_results(
            venue=venue, race_no=race_no, date=parsed_date
        )
    except NotImplementedSource as e:
        raise click.ClickException(str(e))
    except FetchError as e:
        raise click.ClickException(f"外部取得に失敗しました: {e}")

    return {
        "source": fetcher.source_name,
        "kind": "results",
        "venue": venue,
        "date": date_str,
        "results": items,
    }


@cli.command(
    "enrich-json",
    help="既存の RaceInput JSON に外部取得結果を recent_results として取り込む",
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    required=True,
    help="既存の RaceInput JSON のパス",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="出力先パス",
)
@click.option(
    "--results-json",
    "results_json_path",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="既に取得済みの結果JSON（envelope または list）",
)
@click.option(
    "--results-source",
    type=str,
    default=None,
    help=f"結果取得ソース。サポート: {', '.join(_ENRICH_RESULTS_SOURCES)}",
)
@click.option(
    "--results-input",
    "results_input_path",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="results-source=manual のときの入力JSON",
)
@click.option("--venue", default=None, help="results-source 用の場名")
@click.option("--race-no", type=int, default=None, help="特定レース番号のみ取得")
@click.option("--date", "date_str", default=None, help="results-source 用の日付 YYYY-MM-DD")
@click.option(
    "--max-results",
    type=int,
    default=None,
    help="最終 recent_results の件数上限（date降順→race_no降順）",
)
@click.option(
    "--no-dedupe",
    is_flag=True,
    default=False,
    help="同一(venue/date/race_no/result)の重複を除去しない",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="HTTPキャッシュを使わない（results-source 利用時）",
)
@click.option(
    "--cache-ttl",
    type=int,
    default=DEFAULT_TTL_SECONDS,
    show_default=True,
    help="キャッシュTTL秒（results-source 利用時）",
)
@click.option(
    "--rate-limit-seconds",
    type=float,
    default=1.0,
    show_default=True,
    help="同一ドメインへのアクセス間隔（秒）",
)
def enrich_json_cmd(
    input_path: Path,
    out_path: Path,
    results_json_path: Optional[Path],
    results_source: Optional[str],
    results_input_path: Optional[Path],
    venue: Optional[str],
    race_no: Optional[int],
    date_str: Optional[str],
    max_results: Optional[int],
    no_dedupe: bool,
    no_cache: bool,
    cache_ttl: int,
    rate_limit_seconds: float,
) -> None:
    # --- 入力ファイル ---
    if not input_path.exists():
        raise click.ClickException(f"--input が見つかりません: {input_path}")
    try:
        base_raw = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise click.ClickException(
            f"--input の JSON パースに失敗しました: {input_path} ({e})"
        )

    # --- 結果データの入手元を決定 ---
    if results_json_path is None and not results_source:
        raise click.ClickException(
            "--results-json か --results-source のいずれかを指定してください。"
        )
    if results_json_path is not None and results_source:
        click.echo(
            "[案内] --results-json と --results-source が両方指定されています。"
            "--results-json を優先します。",
            err=True,
        )

    if results_json_path is not None:
        results_data = _load_results_json(results_json_path)
    else:
        results_data = _fetch_results_from_source(
            source=results_source or "",
            venue=venue,
            date_str=date_str,
            race_no=race_no,
            input_path=results_input_path,
            no_cache=no_cache,
            cache_ttl=cache_ttl,
            rate_limit_seconds=rate_limit_seconds,
        )

    # --- マージ ---
    try:
        enriched = merge_recent_results(
            base_raw,
            results_data,
            max_results=max_results,
            dedupe=not no_dedupe,
        )
    except EnrichmentError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(f"取り込みに失敗しました: {e}")

    # --- 書き出し ---
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(enriched.model_dump_json())
    out_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    click.echo(f"JSONを書き出しました: {out_path}")
    click.echo(
        f"[案内] recent_results: {len(enriched.recent_results)} 件",
        err=True,
    )


# ---------------------------------------------------------------------------
# prepare-json: 外部取得 + 天候マージ + recent_results 取り込みを一発で
# ---------------------------------------------------------------------------


_PREPARE_SOURCES = ("kdreams", "manual")


def _auto_out_path(venue: str, date_str: str, race_no: int) -> Path:
    """venue+date+race_no から tmp/{venue}_{date}_{NN}r.json を自動生成。"""
    safe_venue = re.sub(r"[^\w぀-ヿ一-鿿]+", "", venue) or "race"
    safe_date = re.sub(r"[^0-9\-]", "", date_str) or "unknown"
    return Path("tmp") / f"{safe_venue}_{safe_date}_{race_no:02d}r.json"


@cli.command(
    "prepare-json",
    help=(
        "外部取得+天候+recent_resultsをまとめて予想用RaceInput JSONを作る。"
        " --race-no 未指定なら 1〜12R を一括生成。"
        " --out 未指定なら tmp/{venue}_{date}_{NN}r.json に自動保存。"
    ),
)
@click.option(
    "--source",
    type=str,
    default="kdreams",
    show_default=True,
    help=f"取得ソース。サポート: {', '.join(_PREPARE_SOURCES)}",
)
@click.option("--venue", required=True, help="場名")
@click.option("--date", "date_str", required=True, help="日付 YYYY-MM-DD")
@click.option(
    "--race-no",
    type=int,
    default=None,
    help="レース番号 1〜12。未指定なら全12レースを一括生成",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "出力先パス。未指定なら tmp/{venue}_{date}_{NN}r.json"
        "（--race-no も未指定なら必須でなく、各レースごとに自動命名）"
    ),
)
# 天候系
@click.option("--weather", default=None, help="天候 例: 曇り/雨")
@click.option("--rain", type=float, default=None, help="降雨量 mm/h")
@click.option("--wind-direction", default=None, help="風向 例: 北/西")
@click.option("--wind-speed", type=float, default=None, help="風速 m/s")
@click.option("--wind-note", default=None, help="風メモ")
# 天候API
@click.option(
    "--weather-source",
    type=str,
    default="open-meteo",
    show_default=True,
    help=f"天候の取得元。サポート: {', '.join(SUPPORTED_WEATHER_SOURCES)}",
)
@click.option(
    "--start-time",
    default=None,
    help="天候の代表時刻 HH:MM（未指定なら正午）",
)
@click.option(
    "--session-no",
    type=int,
    default=1,
    show_default=True,
    help="開催日番号（初日=1, 2日目=2, ...）。Kドリームスの URL 生成に使う",
)
# バンク情報
@click.option("--bank-note", default=None, help="バンク特性メモ（自由文）")
@click.option(
    "--bank-length",
    type=int,
    default=None,
    help="バンク周長(m)。race.bank_length に格納、bank_note にも '周長Nm' を追記",
)
@click.option(
    "--bank-style",
    type=str,
    default=None,
    help="バンク特性。'差し有利' / '先行有利' / '中立' などを指定",
)
# results 取り込み
@click.option(
    "--results/--no-results",
    "include_results",
    default=True,
    show_default=True,
    help="同日の結果を recent_results に取り込むか",
)
@click.option(
    "--results-race-no",
    type=int,
    default=None,
    help="特定レースの結果のみ取り込む。未指定なら race-no より前のレースだけ",
)
@click.option(
    "--max-results",
    type=int,
    default=None,
    help="最終 recent_results の件数上限（date降順→race_no降順）",
)
# odds 取り込み
@click.option(
    "--odds/--no-odds",
    "include_odds",
    default=True,
    show_default=True,
    help="人気上位オッズを取得して RaceInput.odds にマージするか",
)
@click.option(
    "--odds-bet-type",
    type=str,
    default=None,
    help=f"取得するオッズ種別。未指定なら全種別。サポート: {', '.join(SUPPORTED_BET_TYPES)}",
)
@click.option(
    "--odds-limit",
    type=int,
    default=20,
    show_default=True,
    help="オッズ取得時の人気上位件数",
)
@click.option(
    "--odds-source",
    type=str,
    default="oddspark",
    show_default=True,
    help="オッズ取得元。サポート: kdreams / oddspark（既定 oddspark）",
)
# 東スポ補助情報（補助データ・任意）
@click.option(
    "--tospo-notes/--no-tospo-notes",
    "include_tospo_notes",
    default=False,
    show_default=True,
    help="東スポ予想ページから補助情報（コメント要約・signals）を取り込む",
)
@click.option(
    "--tospo-url",
    type=str,
    default=None,
    help=(
        "東スポ予想ページURL（直接指定）。--tospo-notes 有効時に必須。"
        "URL自動生成は未対応。失敗しても処理は続行する"
    ),
)
# 汎用 RaceNotes 取り込み（任意・複数ソース対応）
@click.option(
    "--race-notes-json",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="事前に作成した RaceNotes JSON をマージする",
)
@click.option(
    "--race-notes-text",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "手入力テキストファイルから RaceNotes をパースしてマージする。"
        "--race-notes-source で情報源を指定可能（既定: manual_text）"
    ),
)
@click.option(
    "--race-notes-source",
    type=click.Choice(_RACE_NOTES_SOURCES),
    default="manual_text",
    show_default=True,
    help="--race-notes-text で使う情報源（東スポ等の貼り付けでも使える）",
)
# fallback / HTTP
@click.option(
    "--fallback-input",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="出走表取得失敗時のフォールバック手入力JSON",
)
@click.option("--no-cache", is_flag=True, default=False, help="HTTPキャッシュを使わない")
@click.option(
    "--refresh-cache",
    "refresh_cache",
    is_flag=True,
    default=False,
    help="既存キャッシュを無視して再取得し、新しい結果でキャッシュを上書きする",
)
@click.option(
    "--cache-ttl",
    type=int,
    default=DEFAULT_TTL_SECONDS,
    show_default=True,
    help="キャッシュTTL秒",
)
@click.option(
    "--rate-limit-seconds",
    type=float,
    default=1.0,
    show_default=True,
    help="同一ドメインへのアクセス間隔（秒）",
)
def prepare_json_cmd(
    source: str,
    venue: str,
    date_str: str,
    race_no: Optional[int],
    out_path: Optional[Path],
    weather: Optional[str],
    rain: Optional[float],
    wind_direction: Optional[str],
    wind_speed: Optional[float],
    wind_note: Optional[str],
    weather_source: str,
    start_time: Optional[str],
    session_no: int,
    bank_note: Optional[str],
    bank_length: Optional[int],
    bank_style: Optional[str],
    include_results: bool,
    results_race_no: Optional[int],
    max_results: Optional[int],
    include_odds: bool,
    odds_bet_type: Optional[str],
    odds_limit: int,
    odds_source: Optional[str],
    include_tospo_notes: bool,
    tospo_url: Optional[str],
    race_notes_json: Optional[Path],
    race_notes_text: Optional[Path],
    race_notes_source: str,
    fallback_input: Optional[Path],
    no_cache: bool,
    refresh_cache: bool,
    cache_ttl: int,
    rate_limit_seconds: float,
) -> None:
    src = (source or "").strip().lower()
    if src not in _PREPARE_SOURCES:
        raise click.ClickException(
            f"未対応のソース: '{source}'。サポート対象: {', '.join(_PREPARE_SOURCES)}"
        )
    if odds_bet_type is not None and odds_bet_type.lower() not in SUPPORTED_BET_TYPES:
        raise click.ClickException(
            f"未対応のオッズ種別: '{odds_bet_type}'。"
            f"サポート対象: {', '.join(SUPPORTED_BET_TYPES)}"
        )
    ws = (weather_source or "manual").strip().lower()
    if ws not in SUPPORTED_WEATHER_SOURCES:
        raise click.ClickException(
            f"未対応の weather-source: '{weather_source}'。"
            f"サポート対象: {', '.join(SUPPORTED_WEATHER_SOURCES)}"
        )

    # race-no 未指定で out 明示はNG（複数レースで同じファイルを上書きしてしまう）
    if race_no is None and out_path is not None:
        raise click.ClickException(
            "--race-no を省略する全レース一括モードでは --out は指定できません。"
            "（複数レースが同じファイルを上書きするため）"
        )

    # bank_note と bank_length を統合
    final_bank_note = bank_note
    if bank_length is not None:
        if bank_length <= 0:
            raise click.ClickException(
                f"--bank-length は正の整数で指定してください: {bank_length}"
            )
        bank_length_str = f"周長{bank_length}m"
        if final_bank_note:
            final_bank_note = f"{final_bank_note} / {bank_length_str}"
        else:
            final_bank_note = bank_length_str
    # bank-style バリデーション（任意・自由文OKだが想定値の案内）
    if bank_style:
        known = {"差し有利", "先行有利", "中立"}
        if bank_style not in known:
            click.echo(
                f"[案内] --bank-style='{bank_style}' は自由文として保存します"
                f"（想定値: {', '.join(known)}）",
                err=True,
            )

    # HTTPクライアント（複数レースで共有）
    cache = FileCache(
        cache_dir=DEFAULT_CACHE_DIR, ttl_seconds=cache_ttl, enabled=not no_cache
    )
    rate_limiter = RateLimiter(min_interval_seconds=rate_limit_seconds)
    http_client = HttpClient(
        cache=cache, rate_limiter=rate_limiter, force_refresh=refresh_cache,
    )

    # ---- 開催なしインデックス確認（キャッシュ有効時のみ） ----
    from .no_meet_index import NoMeetIndex
    no_meet = NoMeetIndex(DEFAULT_CACHE_DIR)
    if (
        not refresh_cache
        and not no_cache
        and no_meet.is_known_no_meet(venue, date_str, session_no)
    ):
        click.echo(
            f"[案内] 「開催なし」が記録済み: {venue} {date_str} (session_no={session_no})。"
            "強制再取得するには --refresh-cache を指定してください。",
            err=True,
        )
        return

    # ---- レース対象のリスト ----
    if race_no is None:
        targets = list(range(1, 13))
        click.echo(
            f"[案内] race-no 未指定のため 1〜12R を一括生成します（出力: tmp/{venue}_{date_str}_NNr.json）",
            err=True,
        )
    else:
        targets = [race_no]

    success_count = 0
    failure_count = 0
    for r in targets:
        # 出力パスを決定
        if out_path is not None:
            current_out = out_path
        else:
            current_out = _auto_out_path(venue, date_str, r)

        try:
            ri = prepare_race_input(
                source=src,
                venue=venue,
                date_str=date_str,
                race_no=r,
                http_client=http_client,
                weather=weather,
                rain=rain,
                wind_direction=wind_direction,
                wind_speed=wind_speed,
                wind_note=wind_note,
                bank_note=final_bank_note,
                include_results=include_results,
                results_race_no=results_race_no,
                max_results=max_results,
                include_odds=include_odds,
                odds_bet_type=odds_bet_type,
                odds_limit=odds_limit,
                odds_source=odds_source,
                weather_source=ws if ws != "manual" else None,
                start_time=start_time,
                session_no=session_no,
                bank_length=bank_length,
                bank_style=bank_style,
                tospo_url=tospo_url if include_tospo_notes else None,
                include_tospo_notes=include_tospo_notes,
                fallback_input=fallback_input,
                warn=_cli_warn,
            )
        except PreparationError as e:
            err_msg = str(e)
            # 「開催なし」エラーの場合はインデックスに記録（次回早期終了用）
            if "SYSTEM_ERROR" in err_msg or "開催が無い" in err_msg:
                no_meet.record_no_meet(venue, date_str, session_no)
            if len(targets) > 1:
                click.echo(f"[警告] {r}R をスキップ: {e}", err=True)
                failure_count += 1
                # 「開催なし」エラーは全レースで同じ結果になるため早期終了
                if "SYSTEM_ERROR" in err_msg or "開催が無い" in err_msg:
                    click.echo(
                        "[案内] 「開催なし」エラーが検出されたため、残りのレースの取得は省略します。"
                        f"場名「{venue}」の {date_str} の開催を確認してください。"
                        " インデックスに記録したため、次回以降は通信無しで即時スキップします。",
                        err=True,
                    )
                    break
                continue
            raise click.ClickException(err_msg)

        # 汎用 RaceNotes マージ（任意・複数ソース対応）
        if race_notes_json or race_notes_text:
            from .enrichment import merge_race_notes
            from .models import RaceNotes
            from .race_notes import ManualTextParseError, parse_race_notes_text

            try:
                if race_notes_json:
                    raw_notes = json.loads(
                        race_notes_json.read_text(encoding="utf-8")
                    )
                    notes_obj = RaceNotes.model_validate(raw_notes)
                else:
                    text_body = race_notes_text.read_text(encoding="utf-8")
                    notes_obj = parse_race_notes_text(
                        text_body,
                        source=race_notes_source,
                        venue=venue,
                        date=date_str,
                        race_no=r,
                    )
                ri = merge_race_notes(ri, notes_obj)
            except (ManualTextParseError, json.JSONDecodeError) as e:
                click.echo(
                    f"[警告] RaceNotes の読み込み/パースに失敗（出走表は使用継続）: {e}",
                    err=True,
                )
            except Exception as e:
                click.echo(
                    f"[警告] RaceNotes の取り込みに失敗（出走表は使用継続）: "
                    f"{type(e).__name__}: {e}",
                    err=True,
                )

        current_out.parent.mkdir(parents=True, exist_ok=True)
        raw = json.loads(ri.model_dump_json())
        current_out.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        click.echo(
            f"R{r}: {current_out}  (recent_results: {len(ri.recent_results)}件)"
        )
        success_count += 1

    if len(targets) > 1:
        click.echo(
            f"[案内] 完了: {success_count}件 成功 / {failure_count}件 スキップ",
            err=True,
        )


# ---------------------------------------------------------------------------
# fetch-weather: 天候API単独取得
# ---------------------------------------------------------------------------


@cli.command(
    "fetch-weather",
    help="天候APIから Weather を取得して envelope JSON を書き出す",
)
@click.option(
    "--provider",
    type=str,
    default="open-meteo",
    show_default=True,
    help=f"天候プロバイダ。サポート: open-meteo",
)
@click.option("--venue", required=True, help="場名（緯度経度に変換）")
@click.option("--date", "date_str", required=True, help="日付 YYYY-MM-DD")
@click.option("--start-time", default=None, help="代表時刻 HH:MM（任意）")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="出力先パス",
)
@click.option("--no-cache", is_flag=True, default=False)
@click.option(
    "--cache-ttl",
    type=int,
    default=DEFAULT_TTL_SECONDS,
    show_default=True,
)
@click.option(
    "--rate-limit-seconds",
    type=float,
    default=1.0,
    show_default=True,
)
def fetch_weather_cmd(
    provider: str,
    venue: str,
    date_str: str,
    start_time: Optional[str],
    out_path: Path,
    no_cache: bool,
    cache_ttl: int,
    rate_limit_seconds: float,
) -> None:
    cache = FileCache(
        cache_dir=DEFAULT_CACHE_DIR, ttl_seconds=cache_ttl, enabled=not no_cache
    )
    rate_limiter = RateLimiter(min_interval_seconds=rate_limit_seconds)
    http_client = HttpClient(cache=cache, rate_limiter=rate_limiter)

    try:
        wp = build_weather_provider(provider, http_client=http_client)
    except WeatherFetchError as e:
        raise click.ClickException(str(e))

    try:
        weather = wp.fetch_weather(
            venue=venue, date=date_str, start_time=start_time
        )
    except WeatherFetchError as e:
        raise click.ClickException(str(e))

    envelope = {
        "source": wp.source_name,
        "venue": venue,
        "date": date_str,
        "start_time": start_time,
        "weather": json.loads(weather.model_dump_json()),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    click.echo(f"JSONを書き出しました: {out_path}")
    click.echo(
        f"[案内] {wp.source_name} 由来の Weather を取得しました: "
        f"{weather.condition} / 風 {weather.wind_direction or '-'} {weather.wind_speed_mps:.1f}m/s / "
        f"雨 {weather.rain_mm_per_hour:.1f}mm/h",
        err=True,
    )


# ---------------------------------------------------------------------------
# reports: 予想・結果・反省を集計する成績レポート
# ---------------------------------------------------------------------------


_REPORT_FORMATS = ("text", "json")


def _validate_date_str(s: Optional[str], *, label: str) -> Optional[str]:
    if s is None:
        return None
    try:
        from datetime import datetime as _dt
        _dt.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise click.ClickException(
            f"{label} は YYYY-MM-DD で指定してください: '{s}'"
        )
    return s


@cli.command("reports", help="保存済みの予想・結果・反省ログを集計する成績レポート")
@click.option("--venue", default=None, help="場名でフィルタ")
@click.option("--from-date", "from_date", default=None, help="開始日 YYYY-MM-DD")
@click.option("--to-date", "to_date", default=None, help="終了日 YYYY-MM-DD")
@click.option("--weather", "weather", default=None, help="天候でフィルタ（例: 雨）")
@click.option(
    "--format",
    "fmt",
    type=str,
    default="text",
    show_default=True,
    help=f"出力形式。サポート: {', '.join(_REPORT_FORMATS)}",
)
@click.option(
    "--limit-reflections",
    type=int,
    default=10,
    show_default=True,
    help="反省カテゴリ上位の表示件数",
)
@click.pass_context
def reports_cmd(
    ctx: click.Context,
    venue: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    weather: Optional[str],
    fmt: str,
    limit_reflections: int,
) -> None:
    if fmt not in _REPORT_FORMATS:
        raise click.ClickException(
            f"未対応の format: '{fmt}'。サポート対象: {', '.join(_REPORT_FORMATS)}"
        )
    from_date = _validate_date_str(from_date, label="--from-date")
    to_date = _validate_date_str(to_date, label="--to-date")
    if limit_reflections < 0:
        raise click.ClickException("--limit-reflections は0以上の整数で指定してください。")

    storage: Storage = ctx.obj["storage"]
    report = build_performance_report(
        storage,
        venue=venue,
        from_date=from_date,
        to_date=to_date,
        weather_condition=weather,
        limit_reflections=limit_reflections,
    )

    if fmt == "json":
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        click.echo(render_report_text(report))


# ---------------------------------------------------------------------------
# parse-race-notes / merge-notes: 補助情報 (RaceNotes) 関連
# ---------------------------------------------------------------------------


@cli.command(
    "parse-race-notes",
    help=(
        "手入力テキストから RaceNotes JSON を生成する。"
        "東スポ/WINTICKET/netkeirin等のコメントをコピペで貼り付けたファイルを"
        "正規化して保存する。著作権配慮で短い要約と signals のみを保存する。"
    ),
)
@click.option(
    "--source",
    type=click.Choice(_RACE_NOTES_SOURCES),
    default="manual_text",
    show_default=True,
    help="情報源の種類",
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="入力テキストファイル",
)
@click.option("--venue", default=None, help="場名（テキスト内のヘッダより優先）")
@click.option("--date", "date_str", default=None, help="日付 YYYY-MM-DD")
@click.option("--race-no", type=int, default=None, help="レース番号")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="出力先 RaceNotes JSON",
)
def parse_race_notes_cmd(
    source: str,
    input_path: Path,
    venue: Optional[str],
    date_str: Optional[str],
    race_no: Optional[int],
    out_path: Path,
) -> None:
    from .race_notes import ManualTextParseError, parse_race_notes_text

    text = input_path.read_text(encoding="utf-8")
    try:
        notes = parse_race_notes_text(
            text, source=source, venue=venue, date=date_str, race_no=race_no,
        )
    except ManualTextParseError as e:
        raise click.ClickException(str(e))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(notes.model_dump_json())
    out_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    click.echo(f"RaceNotes JSON を書き出しました: {out_path}")
    click.echo(
        f"[案内] source={notes.source} / 選手コメント {len(notes.rider_notes)} 件",
        err=True,
    )


@cli.command(
    "merge-notes",
    help=(
        "既存の RaceInput JSON に RaceNotes JSON を取り込む（出走表/オッズ等は維持）。"
        "Rider.comment / style_tags に短い要約と signals を追記、user_note に記者見解を追記。"
    ),
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="既存の RaceInput JSON",
)
@click.option(
    "--notes",
    "notes_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="RaceNotes JSON",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="マージ後の RaceInput JSON 出力先",
)
def merge_notes_cmd(
    input_path: Path,
    notes_path: Path,
    out_path: Path,
) -> None:
    from .enrichment import EnrichmentError, merge_race_notes
    from .models import RaceInput, RaceNotes

    try:
        raw_input = json.loads(input_path.read_text(encoding="utf-8"))
        ri = RaceInput.model_validate(raw_input)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"--input の JSON パース失敗: {e}")
    except Exception as e:
        raise click.ClickException(f"--input が RaceInput スキーマに合致しません: {e}")

    try:
        raw_notes = json.loads(notes_path.read_text(encoding="utf-8"))
        notes = RaceNotes.model_validate(raw_notes)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"--notes の JSON パース失敗: {e}")
    except Exception as e:
        raise click.ClickException(f"--notes が RaceNotes スキーマに合致しません: {e}")

    try:
        merged = merge_race_notes(ri, notes)
    except EnrichmentError as e:
        raise click.ClickException(str(e))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_out = json.loads(merged.model_dump_json())
    out_path.write_text(
        json.dumps(raw_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    click.echo(f"マージ済み RaceInput を書き出しました: {out_path}")
    click.echo(
        f"[案内] {notes.source} の rider_notes {len(notes.rider_notes)} 件を取り込みました",
        err=True,
    )


# ---------------------------------------------------------------------------
# fetch-rider-stats: rider 統計情報（競走得点・B数・決まり手）の取得検証
# ---------------------------------------------------------------------------


@cli.command(
    "fetch-rider-stats",
    help=(
        "選手の競走得点・B数・決まり手を取得して JSON で出力する検証用コマンド。"
        "実数値(actual) / 推定値(estimated) / 未取得(missing) を区別する。"
        "本番の prepare-json には組み込まれていない（独立検証用）。"
    ),
)
@click.option(
    "--source",
    type=click.Choice(("yenjoy", "yenjoy_dynamic", "manual")),
    default="yenjoy",
    show_default=True,
    help=(
        "取得元。yenjoy=静的取得(推定値) / "
        "yenjoy_dynamic=Playwright経由(実験的・現状未安定) / "
        "manual=ローカルJSONから"
    ),
)
@click.option("--venue", required=True, help="場名")
@click.option("--date", "date_str", required=True, help="予想したい日 YYYY-MM-DD")
@click.option("--race-no", type=int, required=True, help="レース番号 (1〜12)")
@click.option(
    "--session-no", type=int, default=1, show_default=True,
    help="開催日番号 (連戦の何日目か)",
)
@click.option(
    "--auto-session-search/--no-auto-session-search",
    default=True, show_default=True,
    help="連戦初日を逆算して自動探索する",
)
@click.option(
    "--manual-path",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="manual ソース時の入力 JSON ファイル",
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="出力先 JSON",
)
@click.option(
    "--rate-limit-seconds", type=float, default=1.0,
    help="HTTP レート制限間隔（秒）",
)
@click.option(
    "--no-cache", is_flag=True, default=False,
    help="HTTP キャッシュを無効化",
)
def fetch_rider_stats_cmd(
    source: str,
    venue: str,
    date_str: str,
    race_no: int,
    session_no: int,
    auto_session_search: bool,
    manual_path: Optional[Path],
    out: Path,
    rate_limit_seconds: float,
    no_cache: bool,
) -> None:
    from datetime import date as Date
    from datetime import datetime, timedelta

    from app.fetchers import HttpClient
    from app.fetchers.cache import DEFAULT_CACHE_DIR, DEFAULT_TTL_SECONDS, FileCache
    from app.fetchers.rate_limit import RateLimiter
    from app.rider_stats import compute_quality_summary, fetch_rider_stats

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise click.ClickException(
            f"--date は YYYY-MM-DD で指定してください: '{date_str}'"
        )

    # HttpClient（manual ソースでも不要だが共通化）
    cache = FileCache(
        cache_dir=DEFAULT_CACHE_DIR,
        ttl_seconds=DEFAULT_TTL_SECONDS,
        enabled=not no_cache,
    )
    rl = RateLimiter(min_interval_seconds=float(rate_limit_seconds))
    client = HttpClient(cache=cache, rate_limiter=rl)

    # auto_session_search: yenjoy 経路では複数 URL 候補を試行する仕組みが
    # 既に YenJoyStaticSource 内にあるので、ここでは入力された session_no を渡す。
    # その上で、HTTP 失敗時は session_no を 1..5 でリトライ
    bundles: list = []
    tried_sessions: list[int] = []
    if auto_session_search and source.startswith("yenjoy"):
        # 予想したい日（target_date）を起点に session_no を逆算
        candidates_session = [session_no] + [
            s for s in (1, 2, 3, 4, 5) if s != session_no
        ]
        for s in candidates_session:
            initial_date = target_date - timedelta(days=s - 1)
            tried_sessions.append(s)
            bundle = fetch_rider_stats(
                source=source,
                venue=venue,
                date=initial_date,
                race_no=race_no,
                session_no=s,
                http_client=client,
                manual_path=manual_path,
            )
            # 成功（actual or estimated が1人以上）なら採用
            ok = (
                bundle.quality_summary.actual_count
                + bundle.quality_summary.estimated_count
            ) > 0
            if ok:
                # 表示用 race.date は予想したい日
                bundle.date = target_date
                break
            bundles.append(bundle)
        else:
            # 全部失敗 → 最後の bundle を返す
            bundle = bundles[-1] if bundles else bundle
            bundle.warnings.append(
                f"全 session_no {tried_sessions} で取得失敗"
            )
    else:
        bundle = fetch_rider_stats(
            source=source, venue=venue, date=target_date,
            race_no=race_no, session_no=session_no,
            http_client=client, manual_path=manual_path,
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(bundle.model_dump_json())
    out.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    qs = bundle.quality_summary
    click.echo(f"RiderStatsBundle を書き出しました: {out}")
    click.echo(
        f"[品質] actual={qs.actual_count} / estimated={qs.estimated_count} / "
        f"missing={qs.missing_count} / total={qs.total}",
        err=True,
    )
    for w in bundle.warnings:
        click.echo(f"[警告] {w}", err=True)


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
