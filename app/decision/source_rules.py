"""source_rules タグの定数定義 (Phase 9, 2026-05-25).

scoring.py の _push / _push_required に渡す source_rules タグを
中央管理する。typo 防止 + 将来の rename を一箇所で完結。

タグ命名ルール:
- line_* / separate_*  : allow_line_logic=False のとき除外対象
- market_*             : 市場由来 (HeadBias / AxisBias / 人気 / オッズ取得済)
- individual_*         : 個人戦・スコア由来
- girls_*              : ガールズ用
- rookie_*             : 新人戦用
- weather_* / trend_*  : 補正由来 (天候 / 直近結果)
- odds_* / gami_*      : オッズ・ガミ由来 / value_label

OutputPlan の _is_line_source_tag は `line_` / `separate_` で始まるタグを
line 由来として扱う (Phase 7)。新規 line タグは必ずこの prefix で始める。
"""

from __future__ import annotations

# --- line 系 (固定ライン構造前提) ----------------------------------------
LINE_DIRECT = "line_direct"                # 本命ライン直行 (先頭-番手-3番手)
LINE_SECOND_HEAD = "line_second_head"      # 番手頭 (番手-先頭-3番手)
LINE_THIRD = "line_third"                  # 3 番手絡み (3 着固定)
LINE_FOURTH = "line_fourth"                # 4 番手 (一般)
LINE_FOURTH_FLOW = "line_fourth_flow"      # 4 車ライン4 番手流れ込み
LINE_SPEC12 = "line_spec12"                # 仕様12「基本候補」
LINE_WEATHER = "line_weather"              # 雨補正 / 強風補正の line role 由来
LINE_TREND = "line_trend"                  # 直近トレンド (本命ライン決着 等)
LINE_STRUCTURE = "line_structure"          # ライン構造前提の補助タグ
                                           # (他 line_* と併用、単独でも line 扱い)

# --- separate 系 (別線系) ------------------------------------------------
SEPARATE_LINE = "separate_line"            # 別線関連 (3 着絡み等)
SEPARATE_LEADER = "separate_leader"        # 別線先頭
SEPARATE_SECOND = "separate_second"        # 別線番手
SEPARATE_THIRD = "separate_third"          # 別線3 番手
SEPARATE_MIXED = "separate_mixed"          # 別線決着 (混合)

# --- market 系 (市場オッズ由来) ------------------------------------------
MARKET_HEAD = "market_head"                # HeadBias 由来
MARKET_AXIS = "market_axis"                # AxisBias 由来
MARKET_STRONG_AXIS = "market_strong_axis"  # StrongAxisBias 由来
MARKET_POPULAR = "market_popular"          # 市場上位人気保持
MARKET_PAIR = "market_pair"                # 市場上位ペア
MARKET_ODDS_AVAILABLE = "market_odds_available"  # オッズ取得済みで本線昇格

# --- individual 系 (個人戦/スコア由来) ----------------------------------
INDIVIDUAL_SCORE = "individual_score"      # スコア上位3名の並び
INDIVIDUAL_TOP = "individual_top"          # 上位車番
INDIVIDUAL_RANK_SWAP = "individual_rank_swap"  # 上位2-3着入替
INDIVIDUAL_MID = "individual_mid"          # 4 位評価頭
INDIVIDUAL_LONGSHOT = "individual_longshot"  # 5 位評価頭
INDIVIDUAL_AUTO_FILL = "individual_auto_fill"  # 自動補充

# --- girls 系 ------------------------------------------------------------
GIRLS_MARKET = "girls_market"              # ガールズ市場由来
GIRLS_POSITION = "girls_position"          # ガールズ位置取り
GIRLS_TOP_EVAL = "girls_top_eval"          # ガールズ上位評価
GIRLS_FOLLOW = "girls_follow"              # ガールズ追走
GIRLS_LONGSHOT = "girls_longshot"          # ガールズ大波乱

# --- rookie 系 -----------------------------------------------------------
ROOKIE_POSITION = "rookie_position"        # 新人戦位置取り
ROOKIE_WIND = "rookie_wind"                # 新人戦強風4 位評価
ROOKIE_TOP_EVAL = "rookie_top_eval"        # 新人戦上位評価
ROOKIE_FOLLOW = "rookie_follow"            # 新人戦追走
ROOKIE_LONGSHOT = "rookie_longshot"        # 新人戦大波乱

# --- weather / trend 系 -------------------------------------------------
WEATHER_WIND = "weather_wind"
WEATHER_RAIN = "weather_rain"
WEATHER_STRONG_WIND = "weather_strong_wind"  # 5m/s 以上
TREND_RECENT_RESULT = "trend_recent_result"
TREND_VENUE = "trend_venue"

# --- odds / gami 系 -----------------------------------------------------
ODDS_AVAILABLE = "odds_available"          # market_odds 取得済み
ODDS_MISSING = "odds_missing"              # market_odds=None
GAMI_WARNING = "gami_warning"              # gami_risk>=0.6 / 「ガミ注意」
LOW_ODDS = "low_odds"                      # market_odds<5
VALUE_CANDIDATE = "value_candidate"        # value_label="妙味あり" 等


def is_line_source(tags: list[str] | None) -> bool:
    """source_rules タグが line/separate 由来かどうかを判定する.

    OutputPlan._is_line_source_tag と同じロジック (allow_line_logic=False
    で除外対象になるかの判定)。
    """
    if not tags:
        return False
    return any(
        t.startswith("line_") or t.startswith("separate_") for t in tags
    )


def count_source_rule_prefixes(plan) -> dict[str, int]:
    """OutputPlan 内の全候補について、source_rules の prefix ごとの件数を返す.

    prefix 候補: line / separate / market / individual / girls / rookie /
                 weather / trend / odds / gami / other

    Renderer の常時表示用ではなく、テスト/デバッグ用。
    """
    counts: dict[str, int] = {}
    known_prefixes = (
        "line", "separate", "market", "individual",
        "girls", "rookie", "weather", "trend", "odds", "gami",
    )
    target_buckets = (
        "honsen", "osae", "ana", "ooana", "honsen_miokuri",
        "final_best", "final_osae", "final_ana",
        "gami_warning", "watch_only",
    )
    for bucket_name in target_buckets:
        bucket = getattr(plan, bucket_name, None)
        if not bucket:
            continue
        for b in bucket:
            for tag in (b.source_rules or []):
                matched = False
                for prefix in known_prefixes:
                    if tag.startswith(f"{prefix}_") or tag == prefix:
                        counts[prefix] = counts.get(prefix, 0) + 1
                        matched = True
                        break
                if not matched:
                    counts["other"] = counts.get("other", 0) + 1
    return counts
