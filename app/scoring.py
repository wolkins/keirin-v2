"""ルールベースのスコアリング。

LLMに渡す前段の数値補正をここで完結させる。
ガールズ判定、ライン内位置、天候/風/雨補正、直近結果からのトレンド補正、
オッズ妙味、ガミリスクなどを計算する。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .models import (
    BetRecommendation,
    Line,
    OddsEntry,
    RaceInput,
    RecentResult,
    Reflection,
    Rider,
    RiderScore,
)


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


# 会場→地区マッピング（docs/race_type_policy.md フェーズ C2）
# 競輪場の所在地と選手の所属地区を対応させる
VENUE_TO_AREA: dict[str, str] = {
    # 北日本
    "函館": "北日本", "青森": "北日本", "いわき平": "北日本",
    # 関東
    "取手": "関東", "宇都宮": "関東", "大宮": "関東",
    "西武園": "関東", "京王閣": "関東", "立川": "関東",
    "前橋": "関東",
    # 南関東
    "松戸": "南関東", "千葉": "南関東", "川崎": "南関東",
    "平塚": "南関東", "小田原": "南関東", "伊東": "南関東",
    "静岡": "南関東",
    # 中部
    "富山": "中部", "松阪": "中部", "名古屋": "中部",
    "岐阜": "中部", "大垣": "中部", "豊橋": "中部",
    "四日市": "中部", "弥彦": "中部", "京都向日町": "近畿",
    # 近畿
    "奈良": "近畿", "向日町": "近畿",
    "和歌山": "近畿", "岸和田": "近畿",
    # 中国
    "玉野": "中国", "広島": "中国", "防府": "中国",
    # 四国
    "高松": "四国", "小松島": "四国",
    "高知": "四国", "松山": "四国",
    # 九州
    "小倉": "九州", "久留米": "九州", "武雄": "九州",
    "佐世保": "九州", "別府": "九州", "熊本": "九州",
    "八代": "九州",
}


# 都道府県→地区マッピング（docs/race_type_policy.md フェーズ C 拡張）
# JKA 公式の地区分類に基づく。選手の所属都道府県（pref）から地区を導出する。
PREFECTURE_TO_AREA: dict[str, str] = {
    # 北日本
    "北海道": "北日本", "青森": "北日本", "岩手": "北日本",
    "宮城": "北日本", "秋田": "北日本", "山形": "北日本", "福島": "北日本",
    # 関東
    "茨城": "関東", "栃木": "関東", "群馬": "関東",
    "埼玉": "関東", "千葉": "関東",
    # 南関東
    "東京": "南関東", "神奈川": "南関東",
    "山梨": "南関東", "静岡": "南関東",
    # 中部
    "新潟": "中部", "富山": "中部", "石川": "中部", "福井": "中部",
    "長野": "中部", "岐阜": "中部", "愛知": "中部", "三重": "中部",
    # 近畿
    "滋賀": "近畿", "京都": "近畿", "大阪": "近畿",
    "兵庫": "近畿", "奈良": "近畿", "和歌山": "近畿",
    # 中国
    "鳥取": "中国", "島根": "中国", "岡山": "中国",
    "広島": "中国", "山口": "中国",
    # 四国
    "徳島": "四国", "香川": "四国", "愛媛": "四国", "高知": "四国",
    # 九州
    "福岡": "九州", "佐賀": "九州", "長崎": "九州",
    "熊本": "九州", "大分": "九州", "宮崎": "九州",
    "鹿児島": "九州", "沖縄": "九州",
}


def resolve_prefecture_area(pref: str) -> Optional[str]:
    """都道府県名から所属地区を導出する。

    Args:
        pref: 都道府県名（例: "広島", "東京都", "大阪府"）

    Returns:
        地区名（"北日本"/"関東"/"南関東"/"中部"/"近畿"/"中国"/"四国"/"九州"）。
        マップに無ければ None。
    """
    if not pref:
        return None
    # 完全一致
    if pref in PREFECTURE_TO_AREA:
        return PREFECTURE_TO_AREA[pref]
    # 「東京都」「大阪府」「広島県」等の接尾辞を除去して再試行
    for suffix in ("都", "府", "県"):
        if pref.endswith(suffix):
            base = pref[:-len(suffix)]
            if base in PREFECTURE_TO_AREA:
                return PREFECTURE_TO_AREA[base]
    # 部分一致（例: comment が「広島-A1-両」のように pref とその他を含む場合）
    for p, area in PREFECTURE_TO_AREA.items():
        if p in pref:
            return area
    return None


def resolve_venue_area(venue: str) -> Optional[str]:
    """会場名から所在地区を導出する。

    Args:
        venue: RaceInfo.venue (例: "広島", "高松", "京都向日町")

    Returns:
        地区名（"北日本"/"関東"/"南関東"/"中部"/"近畿"/"中国"/"四国"/"九州"）。
        マップに無ければ None。
    """
    if not venue:
        return None
    # 完全一致
    if venue in VENUE_TO_AREA:
        return VENUE_TO_AREA[venue]
    # 部分一致（例: "京都向日町記念" → "京都向日町"）
    for v, area in VENUE_TO_AREA.items():
        if v in venue:
            return area
    return None


# レース格ごとの「格上」判定閾値（競走得点）
# docs/race_type_policy.md Q1 のデフォルト案
KAKUJOU_THRESHOLD: dict[str, float] = {
    "F2": 85.0,
    "F1": 95.0,
    "G3": 100.0,
    "G2": 100.0,
    "G1": 100.0,
    "GP": 105.0,
}


def is_kakujou(rider: Rider, race_grade: str) -> bool:
    """選手がレース格に対して「格上」かを判定する。

    docs/race_type_policy.md Q1 のデフォルト案に基づき、競走得点ベースで判定する。
    将来 Rider.class_score (S級1/S級2/A級1/A級2) が取得できれば、それで置き換える。

    Args:
        rider: 対象選手
        race_grade: "GP" / "G1" / "G2" / "G3" / "F1" / "F2"

    Returns:
        True なら「格上」（番手・3番手・単騎の加点対象）
    """
    if rider.stats_missing or rider.score <= 0:
        return False
    threshold = KAKUJOU_THRESHOLD.get(race_grade, 85.0)
    return rider.score >= threshold


@dataclass(frozen=True)
class LinePosition:
    """ライン内での位置情報。ガールズや単騎では None / 単騎 となる。"""

    line_name: str
    index: int  # 0=先頭, 1=番手, 2=3番手, 3以降=後方
    line_length: int

    @property
    def is_head(self) -> bool:
        return self.index == 0

    @property
    def is_bantan(self) -> bool:  # 番手
        return self.index == 1

    @property
    def is_third(self) -> bool:
        return self.index == 2

    @property
    def is_tanki(self) -> bool:
        return self.line_length == 1


def build_line_position_map(lines: list[Line]) -> dict[int, LinePosition]:
    """車番 → LinePosition の辞書を返す。"""
    out: dict[int, LinePosition] = {}
    for line in lines:
        for idx, car in enumerate(line.cars):
            out[car] = LinePosition(
                line_name=line.line_name,
                index=idx,
                line_length=len(line.cars),
            )
    return out


def _wind_tier(wind_mps: float) -> int:
    """0:無風 1:中風 2:強風 3:強風大。"""
    if wind_mps >= 7.0:
        return 3
    if wind_mps >= 5.0:
        return 2
    if wind_mps >= 3.0:
        return 1
    return 0


def _rain_tier(rain_mm: float) -> int:
    """0:なし 1:小雨 2:雨。"""
    if rain_mm >= 1.0:
        return 2
    if rain_mm > 0.0:
        return 1
    return 0


# ---------------------------------------------------------------------------
# 直近結果からのトレンド分析
# ---------------------------------------------------------------------------


@dataclass
class TrendSignal:
    """直近結果から抽出した傾向。"""

    bessen_bantan_count: int = 0  # 別線番手が連対or3着に絡んだ回数
    third_in_3rd_count: int = 0  # 3番手が3着に絡んだ回数
    head_takes_count: int = 0  # 先行が1着の回数
    bantan_head_count: int = 0  # 番手が1着の回数
    # 仕様8章「直近結果による当日傾向補正」用フラグ
    main_line_dominant_count: int = 0  # 本命ライン決着 (1-2-3など) の回数
    chaotic_count: int = 0  # 荒れ傾向 (波乱・ズレ目・中穴)
    third_sec_up_count: int = 0  # 3番手の2着上がり
    # 仕様レビュー追加: 着順パターン認識
    senko_head_third_2nd_count: int = 0  # 先行頭+3番手2着 (例: 先行-3番手-番手)
    bantan_head_senko_2nd_count: int = 0  # 番手頭+先行2着 (例: 番手-先行-3番手)
    bessen_lead_dominant_count: int = 0  # 別線自力決着 (別線自力-別線番手-本線自力など)
    main_then_bessen_third_count: int = 0  # 本線先頭-番手-別線番手 (例: 1-9-5)
    main_with_bessen_lead_count: int = 0  # 本命先頭-別線自力-別線番手 (例: 3-2-8)

    @property
    def is_main_line_dominant(self) -> bool:
        return self.main_line_dominant_count >= 2

    @property
    def is_chaotic(self) -> bool:
        return self.chaotic_count >= 2

    @property
    def is_bantan_dominant(self) -> bool:
        return self.bantan_head_count >= 2

    @property
    def is_third_sec_up(self) -> bool:
        return self.third_sec_up_count >= 2

    @property
    def is_bessen_involved(self) -> bool:
        return self.bessen_bantan_count >= 2

    @property
    def is_senko_head_third_2nd(self) -> bool:
        """『先行-3番手-番手』など、先行頭で3番手が2着に上がる傾向。"""
        return self.senko_head_third_2nd_count >= 2

    @property
    def is_bantan_head_senko_2nd(self) -> bool:
        """『番手-先行-3番手』など、番手頭+先行2着+3番手3着の傾向。"""
        return self.bantan_head_senko_2nd_count >= 2

    @property
    def is_bessen_lead_dominant(self) -> bool:
        """『別線自力-別線番手-本線自力』など、別線決着の傾向。"""
        return self.bessen_lead_dominant_count >= 2

    @property
    def is_main_then_bessen_third(self) -> bool:
        """『本線先頭-番手-別線番手』の連発。1度でも具体的傾向あり。"""
        return self.main_then_bessen_third_count >= 1

    @property
    def is_main_with_bessen_lead(self) -> bool:
        """『本命先頭-別線自力-別線番手』の連発。1度でも具体的傾向あり。"""
        return self.main_with_bessen_lead_count >= 1


def analyze_recent(results: list[RecentResult]) -> TrendSignal:
    """直近結果から、トレンドシグナルを推定する。

    判定:
    1. memo（人間記述）の文字列マッチ
    2. result 文字列（例: '5-1-3'）からのパターン分析 (memo に依存しない)
       - 1着車番の集中度 → 鉄板/特定車頭傾向
       - 1着車番の分散度 → 荒れ傾向
    """
    sig = TrendSignal()
    head_counts: dict[int, int] = {}
    valid_results = 0

    for r in results:
        memo = (r.memo or "")
        if "別線番手" in memo:
            sig.bessen_bantan_count += 1
        if "3番手" in memo:
            sig.third_in_3rd_count += 1
        if "本命自力頭" in memo or "本線自力頭" in memo or "先行頭" in memo:
            sig.head_takes_count += 1
        if "本命番手頭" in memo or "番手頭" in memo:
            sig.bantan_head_count += 1
        # 荒れ傾向
        if any(k in memo for k in ("波乱", "ズレ目", "中穴", "大穴", "穴")):
            sig.chaotic_count += 1
        # 本命ライン決着
        if any(k in memo for k in ("本線ライン決着", "本命ライン決着", "順当")):
            sig.main_line_dominant_count += 1
        # 3番手の2着上がり
        if any(k in memo for k in ("3番手2着", "3番手の2着上がり", "3番手2着上がり")):
            sig.third_sec_up_count += 1
        # 仕様レビュー追加: 着順パターン
        # 「先行-3番手-番手」: 先行頭で3番手2着上がり
        if any(k in memo for k in (
            "先行-3番手", "先行頭-3番手", "自力-3番手",
        )):
            sig.senko_head_third_2nd_count += 1
        # 「番手-先行-3番手」: 番手頭、先行2着、3番手3着
        if any(k in memo for k in (
            "番手-先行", "番手頭-先行", "番手差し決着",
        )):
            sig.bantan_head_senko_2nd_count += 1
        # 「別線自力-別線番手」 / 「別線自力-本線」: 別線決着
        if any(k in memo for k in (
            "別線自力", "別線決着", "別線ライン決着",
        )):
            sig.bessen_lead_dominant_count += 1
        # 「本線先頭-番手-別線番手」: 本命ライン1-2着固定+別線番手3着
        if any(k in memo for k in (
            "本線先頭-番手-別線番手",
            "本命自力-本命番手-別線番手",
            "別線番手3着",
            "別線番手割り込み",
        )):
            sig.main_then_bessen_third_count += 1
        # 「本命先頭-別線自力-別線番手」: 本命頭+別線2着+別線3着
        if any(k in memo for k in (
            "本命先頭-別線自力",
            "本線先頭-別線自力",
            "本命自力-別線自力",
            "本命先頭-別線割り込み",
        )):
            sig.main_with_bessen_lead_count += 1

        # ---- 結果列パターン認識（memo に依存しない D-1）------------------
        parts = (r.result or "").replace("=", "-").split("-")
        if len(parts) == 3:
            try:
                head = int(parts[0])
                if 1 <= head <= 9:
                    head_counts[head] = head_counts.get(head, 0) + 1
                    valid_results += 1
            except ValueError:
                pass

    # 同じ車番が頭になる回数が多ければ「特定軸の鉄板傾向」→ main_line_dominant に寄せる
    if head_counts:
        max_head = max(head_counts.values())
        if valid_results >= 3 and max_head >= 3:
            sig.main_line_dominant_count = max(
                sig.main_line_dominant_count, max_head
            )
        # 1着車番のユニーク数が結果数とほぼ同じ = 散らばり = 荒れ
        unique_heads = len(head_counts)
        if valid_results >= 4 and unique_heads >= max(3, int(valid_results * 0.8)):
            # memo の chaotic_count とは独立に底上げ
            sig.chaotic_count = max(sig.chaotic_count, 2)

    return sig


# ---------------------------------------------------------------------------
# スコアリング本体
# ---------------------------------------------------------------------------


def promote_zero_score_to_missing(input_data: RaceInput) -> int:
    """全数値が 0 だが stats_missing=False の rider を検出して True に昇格。

    yenjoy 取得が失敗したのに stats_missing フラグだけ更新されていないケースや、
    Kドリームスのみで取得して数値が空のままのケースを検出する。

    Returns:
        昇格した選手数
    """
    promoted = 0
    for r in input_data.riders:
        if getattr(r, "stats_missing", False):
            continue
        all_zero = (
            (r.score is None or r.score == 0.0)
            and r.b_count == 0
            and r.nige == 0 and r.makuri == 0
            and r.sashi == 0 and r.mark == 0
        )
        if all_zero:
            r.stats_missing = True
            promoted += 1
    return promoted


def detect_score_data_insufficient(input_data: RaceInput) -> bool:
    """全選手の競走得点・B数・決まり手が未取得 (or 全部 0) か。

    判定基準:
      1. stats_missing フラグが立っている選手が **過半数** の場合 → True
      2. 全選手の score/b_count/nige/makuri/sashi/mark が全て 0 → True
      3. それ以外 → False

    True が返ったら **数値不足モード** で動作:
      - コメント (comment / style_tags) から脚質を補完
      - line 先頭でも追い込み型なら line_leader 評価を抑制
      - 市場オッズの参照を強める
      - 市場注目ラインを本命として上書き
    """
    if not input_data.riders:
        return False
    # (1) stats_missing が過半数なら数値不足モード
    missing_count = sum(
        1 for r in input_data.riders if getattr(r, "stats_missing", False)
    )
    if missing_count * 2 > len(input_data.riders):
        return True
    # (2) 全選手の数値が全部 0 なら数値不足モード（明示フラグ無しでも検出）
    for r in input_data.riders:
        if (
            (r.score is not None and r.score > 0)
            or r.b_count > 0
            or r.nige > 0
            or r.makuri > 0
            or r.sashi > 0
            or r.mark > 0
        ):
            return False
    return True


def _infer_role_tag(rider) -> str:
    """rider の comment / style_tags から脚質ヒントを推定。

    Returns:
        "leader": 逃 / 先行 / 自力
        "jizai":  両 / 自在
        "oikomi": 追 / 追込 / 番手 / 差し / マーク
        "unknown": 該当なし
    """
    text = (rider.comment or "") + " " + " ".join(rider.style_tags or [])
    # 「逃げ」「自力」「先行」: 主導権ライン候補
    if any(k in text for k in ("逃", "先行", "自力")):
        return "leader"
    # 「両」「自在」: 自力自在候補
    if any(k in text for k in ("両", "自在")):
        return "jizai"
    # 「追」「追込」「番手」: 追い込み候補
    if any(k in text for k in ("追込", "追", "番手", "差し", "マーク")):
        return "oikomi"
    return "unknown"


def compute_scores(input_data: RaceInput) -> list[RiderScore]:
    """各選手のスコアを計算して返す。

    数値不足モード（全選手の score/B/決まり手が0）のときは、
    コメント (comment / style_tags) ベースで脚質を補完する。
    """

    is_girls = input_data.race.resolved_is_girls()
    weather = input_data.weather
    wind_tier = _wind_tier(weather.wind_speed_mps) if weather else 0
    rain_tier = _rain_tier(weather.rain_mm_per_hour) if weather else 0
    pos_map = build_line_position_map(input_data.lines) if not is_girls else {}
    trend = analyze_recent(input_data.recent_results)
    favors = set(input_data.venue_trend.favors) if input_data.venue_trend else set()

    scores: list[RiderScore] = []
    for r in input_data.riders:
        score = _score_one(
            rider=r,
            is_girls=is_girls,
            position=pos_map.get(r.car_no),
            wind_tier=wind_tier,
            rain_tier=rain_tier,
            trend=trend,
            favors=favors,
        )
        scores.append(score)

    _apply_odds_value(scores, input_data.odds)

    # 数値不足モード: コメントベースの脚質補完
    if detect_score_data_insufficient(input_data):
        _apply_comment_based_role_boost(scores, input_data, pos_map)

    return scores


def _apply_comment_based_role_boost(
    scores: list[RiderScore],
    input_data: RaceInput,
    pos_map: dict,
) -> None:
    """数値不足モードのコメント脚質補完。

    - "逃/先行/自力" タグ: win_score +5、line 先頭なら line_strength +2 (主導権ライン)
    - "両/自在" タグ: win_score +2.5
    - "追/追込/番手" タグ: 2-3着候補に振り分け、line 先頭でも line_strength を上げない
    - line 先頭が "追/追込" の場合: line_strength を -1.5 して主導権ライン候補から除外
    """
    by_car = {s.car_no: s for s in scores}
    for s in scores:
        rider = input_data.rider_by_car(s.car_no)
        if not rider:
            continue
        role = _infer_role_tag(rider)
        pos = pos_map.get(s.car_no)
        is_head = bool(pos and not pos.is_tanki and pos.is_head)

        if role == "leader":
            s.win_score += 5.0
            if is_head:
                s.line_strength += 2.0
                s.reasons.append(
                    "数値不足モード: 脚質コメント[逃/先行/自力] → 自力評価+主導権ライン候補"
                )
            else:
                s.reasons.append(
                    "数値不足モード: 脚質コメント[逃/先行/自力] → 自力評価"
                )
        elif role == "jizai":
            s.win_score += 2.5
            s.second_score += 1.0
            s.third_score += 0.5
            s.reasons.append(
                "数値不足モード: 脚質コメント[両/自在] → 中位評価"
            )
        elif role == "oikomi":
            s.second_score += 2.0
            s.third_score += 2.0
            if is_head:
                # line 先頭だが追い込み型 → 主導権ライン候補から外す
                s.line_strength -= 1.5
                s.reasons.append(
                    "数値不足モード: line先頭が[追/追込/番手] → 主導権抑制+2-3着候補"
                )
            else:
                s.reasons.append(
                    "数値不足モード: 脚質コメント[追/追込/番手] → 2-3着候補"
                )
        else:
            s.reasons.append("数値不足モード: 脚質不明（コメントから推定できず）")


def _score_one(
    *,
    rider: Rider,
    is_girls: bool,
    position: Optional[LinePosition],
    wind_tier: int,
    rain_tier: int,
    trend: TrendSignal,
    favors: set[str],
) -> RiderScore:
    s = RiderScore(car_no=rider.car_no, name=rider.name)

    # ---- ベース（競走得点） ---------------------------------------------
    # 75点を基準に±でスコア化（線形）
    base = (rider.score - 75.0) * 0.6
    s.win_score += base
    s.second_score += base * 0.6
    s.third_score += base * 0.25
    if rider.score:
        s.reasons.append(f"競走得点{rider.score:.1f}に基づくベース補正 {base:+.2f}")

    # ---- ライン位置補正（ガールズ無効） ----------------------------------
    if not is_girls and position is not None:
        if position.is_tanki:
            s.line_strength += -0.5
            s.win_score += 0.3
            s.second_score += 0.5
            s.third_score += 0.6
            s.reasons.append("単騎: 位置取り次第で2-3着候補")
        elif position.is_head:
            s.line_strength += 1.0 + 0.5 * (position.line_length - 1)
            s.win_score += 1.5 + 0.5 * rider.b_count + 0.3 * rider.nige
            s.second_score += 0.4
            # 先行は3着固定にしにくい
            s.third_score -= 0.8
            s.reasons.append(
                f"{position.line_name}先頭(自力)。B{rider.b_count} 逃げ{rider.nige}を加味"
            )
        elif position.is_bantan:
            s.line_strength += 0.7
            s.win_score += 0.6 + 0.2 * rider.sashi
            s.second_score += 1.2 + 0.2 * rider.sashi
            s.third_score += 0.6
            s.reasons.append(f"{position.line_name}番手。差し{rider.sashi}回")
        elif position.is_third:
            s.line_strength += 0.4
            s.win_score += 0.1
            s.second_score += 0.7
            s.third_score += 1.1
            s.reasons.append(f"{position.line_name}3番手。3着候補として残す")
        else:
            s.line_strength += 0.1
            s.third_score += 0.3

    # ---- 風補正 ---------------------------------------------------------
    if wind_tier > 0:
        if position is not None and not is_girls:
            if position.is_bantan:
                s.wind_bonus += 0.4 * wind_tier
                s.reasons.append(f"風({wind_tier})により番手差しを加点")
            elif position.is_third:
                s.wind_bonus += 0.3 * wind_tier
                s.second_score += 0.2 * wind_tier
                s.third_score += 0.4 * wind_tier
                s.reasons.append(f"風({wind_tier})により3番手残りを加点")
            elif position.is_head:
                # 強風時の先行はリスク
                s.risk_score += 0.3 * wind_tier
                s.reasons.append(
                    f"風({wind_tier})により先行末脚リスクを加算"
                )
        # 追込・自在・単騎は風で加点
        if "追込" in rider.style_tags or "差し" in rider.style_tags:
            s.wind_bonus += 0.2 * wind_tier
        if "単騎" in rider.style_tags or "自在" in rider.style_tags:
            s.wind_bonus += 0.15 * wind_tier

    # ---- 雨補正 ---------------------------------------------------------
    if rain_tier > 0:
        if not is_girls and position is not None:
            if position.is_head:
                # 前々で踏める選手は雨でも残しやすい
                s.weather_bonus += 0.3 * rain_tier
                s.reasons.append(f"雨({rain_tier}): 前々に踏める先行を加点")
            elif position.is_bantan:
                s.weather_bonus += 0.5 * rain_tier
                s.second_score += 0.3 * rain_tier
                s.reasons.append(f"雨({rain_tier}): 番手差しを加点")
            elif position.is_third:
                s.weather_bonus += 0.4 * rain_tier
                s.third_score += 0.4 * rain_tier
                s.reasons.append(f"雨({rain_tier}): 3番手残りを加点")
        if "追走" in rider.style_tags or "差し" in rider.style_tags:
            s.weather_bonus += 0.2 * rain_tier
        if "内突き" in rider.style_tags:
            s.weather_bonus += 0.3 * rain_tier

    # ---- ガールズ補正 ---------------------------------------------------
    if is_girls:
        # 自力・位置取り・安定感を重視
        if "自力" in rider.style_tags:
            s.win_score += 0.6
            s.second_score += 0.4
            s.reasons.append("ガールズ: 自力寄りを加点")
        if "追走" in rider.style_tags:
            s.second_score += 0.4
            s.third_score += 0.5
            s.reasons.append("ガールズ: 追走力を2-3着候補に加点")
        # 風雨に強い位置取り型はさらに加点
        if (wind_tier > 0 or rain_tier > 0) and "位置取り" in rider.style_tags:
            s.weather_bonus += 0.4
            s.reasons.append("ガールズ: 悪天候で位置取り型を加点")

    # ---- 直近結果トレンド ----------------------------------------------
    if not is_girls and position is not None:
        if position.is_bantan and trend.bessen_bantan_count > 0:
            # 別線番手がよく来ているなら、自分が別線番手側でも加点
            s.trend_bonus += 0.3 * trend.bessen_bantan_count
            s.reasons.append(
                f"直近で別線番手好走{trend.bessen_bantan_count}回 → 番手を加点"
            )
        if position.is_third and trend.third_in_3rd_count > 0:
            s.trend_bonus += 0.3 * trend.third_in_3rd_count
            s.third_score += 0.3 * trend.third_in_3rd_count
            s.reasons.append(
                f"直近で3番手好走{trend.third_in_3rd_count}回 → 3着を加点"
            )
        if position.is_head and trend.head_takes_count > 0:
            s.trend_bonus += 0.2 * trend.head_takes_count
        if position.is_bantan and trend.bantan_head_count > 0:
            s.trend_bonus += 0.2 * trend.bantan_head_count
            s.win_score += 0.2 * trend.bantan_head_count

    # ---- 場の傾向（venue_trend.favors） --------------------------------
    if favors:
        tag_set = set(rider.style_tags)
        if position is not None and not is_girls:
            if position.is_bantan and "番手" in favors:
                s.trend_bonus += 0.4
            if position.is_third and "3番手" in favors:
                s.trend_bonus += 0.4
            if position.is_head and "先行" in favors:
                s.trend_bonus += 0.3
            if position.is_bantan and "別線番手" in favors:
                # この情報単体では別線か同線かを判定しきれないが番手なら寄与
                s.trend_bonus += 0.2
        if tag_set & favors:
            s.trend_bonus += 0.2

    return s


def _apply_odds_value(scores: list[RiderScore], odds: list[OddsEntry]) -> None:
    """単勝・複勝・3連単の頭フラグからオッズ妙味スコアを付ける。

    厳密な期待値計算はせず、頭固定オッズの最安/中位/高位で粗くスコア化する。
    """
    head_best: dict[int, float] = defaultdict(lambda: float("inf"))
    for o in odds:
        # 3連単 "5-1-3" の頭は 5
        first = o.combination.replace("=", "-").split("-")[0]
        try:
            car = int(first)
        except ValueError:
            continue
        if o.bet_type in ("3連単", "2車単"):
            head_best[car] = min(head_best[car], o.odds)
        elif o.bet_type == "単勝":
            head_best[car] = min(head_best[car], o.odds)

    if not head_best:
        return
    for s in scores:
        best = head_best.get(s.car_no)
        if best is None or best == float("inf"):
            continue
        if best <= 5.0:
            s.odds_value_score += -0.3  # 人気すぎ → 妙味薄
            s.gami_risk += 0.6
            s.reasons.append(f"頭オッズ{best:.1f}: 人気薄妙味+ガミリスク加算")
        elif best <= 15.0:
            s.odds_value_score += 0.4
            s.reasons.append(f"頭オッズ{best:.1f}: 中位妙味あり")
        elif best <= 40.0:
            s.odds_value_score += 0.8
            s.reasons.append(f"頭オッズ{best:.1f}: 中穴妙味")
        else:
            s.odds_value_score += 1.0
            s.reasons.append(f"頭オッズ{best:.1f}: 大穴妙味")


# ---------------------------------------------------------------------------
# 印・買い目候補
# ---------------------------------------------------------------------------


_MARK_ORDER = ["◎", "◯", "▲", "△", "×", "α"]


# ---------------------------------------------------------------------------
# 役割分類 (RiderRole)
# ---------------------------------------------------------------------------

# 仕様3章「通常ライン戦の役割分類」+ ガールズ
RIDER_ROLES = (
    "line_leader",      # 本命ライン先頭（自力）
    "second",           # 本命ライン番手
    "third",            # 本命ライン3番手
    "fourth",           # 4番手以降
    "separate_leader",  # 別線自力
    "separate_second",  # 別線番手
    "separate_third",   # 別線3番手
    "solo",             # 単騎
    "jizai",            # 自在型
    "girls",            # ガールズ
)


def resolve_rider_roles(
    input_data: RaceInput, scores: list[RiderScore]
) -> dict[int, str]:
    """各車番に役割タグ（RIDER_ROLES のいずれか）を割り当てる。

    判定基準:
      - ガールズレース → 全員 "girls"
      - 単騎ライン（cars=1） + 自在タグ → "jizai"、それ以外 → "solo"
      - 本命ライン（top1 のライン）の先頭/番手/3番手/4番手以降 → line_leader/second/third/fourth
      - 別線（top1 と違うライン）の先頭/番手/3番手 → separate_leader/separate_second/separate_third
      - ライン情報が無い場合は style_tags から推定
    """
    out: dict[int, str] = {}
    if not input_data.riders:
        return out

    if input_data.race.resolved_is_girls():
        for r in input_data.riders:
            out[r.car_no] = "girls"
        return out

    pos_map = build_line_position_map(input_data.lines)
    # 本命ライン特定（scores が空ならライン1を本命とみなす）
    top1_line: Optional[str] = None
    if scores:
        top1 = max(scores, key=lambda s: s.total())
        top1_pos = pos_map.get(top1.car_no)
        if top1_pos:
            top1_line = top1_pos.line_name
    if top1_line is None and input_data.lines:
        # スコア未確定 → 最も長いラインを本命扱い
        longest = max(input_data.lines, key=lambda l: len(l.cars))
        top1_line = longest.line_name

    riders_by_car = {r.car_no: r for r in input_data.riders}
    for car_no in (r.car_no for r in input_data.riders):
        rider = riders_by_car[car_no]
        pos = pos_map.get(car_no)
        if pos is None:
            # ライン情報なし → タグから推定
            tags = set(rider.style_tags)
            if "自在" in tags:
                out[car_no] = "jizai"
            else:
                out[car_no] = "solo"
            continue
        if pos.is_tanki:
            if "自在" in set(rider.style_tags):
                out[car_no] = "jizai"
            else:
                out[car_no] = "solo"
            continue
        same_line = top1_line is not None and pos.line_name == top1_line
        if pos.is_head:
            out[car_no] = "line_leader" if same_line else "separate_leader"
        elif pos.is_bantan:
            out[car_no] = "second" if same_line else "separate_second"
        elif pos.is_third:
            out[car_no] = "third" if same_line else "separate_third"
        else:
            out[car_no] = "fourth"
    return out


# ---------------------------------------------------------------------------
# 反省ログからの補正
# ---------------------------------------------------------------------------


def _category_counts(reflections: list[Reflection]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in reflections:
        for c in r.categories:
            counts[c] = counts.get(c, 0) + 1
    return counts


# カテゴリ → 加点ターゲットの簡易マッピング。
# 値は (適用先位置タグ, win_d, second_d, third_d, reason) のタプル列。
def _reflection_position_adjustments(
    counts: dict[str, int], top1_line_name: str | None
) -> list[tuple[str, float, float, float, str]]:
    """位置（同ラインのhead/bantan/third、別線bantan等）ごとに微補正を返す。

    各要素: (target_key, dwin, dsecond, dthird, reason)
    target_key は内部用識別子: 'head' | 'bantan_same' | 'bantan_other' | 'third_same' | 'third_other'
    """
    out: list[tuple[str, float, float, float, str]] = []

    # ---- 別線番手を軽視 / 別線番手の2着上がりを軽視した
    n = counts.get("別線番手を軽視", 0) + counts.get("別線番手の2着上がりを軽視した", 0)
    if n:
        delta = min(0.4 * n, 1.0)
        out.append(
            (
                "bantan_other",
                delta * 0.6,
                delta,
                delta * 0.5,
                f"過去の反省: 別線番手の軽視を{n}件検出 → 別線番手を加点",
            )
        )

    # ---- 3番手の伸びを軽視 / 3番手の2着上がりを軽視
    n = counts.get("3番手の伸びを軽視", 0) + counts.get("3番手の2着上がりを軽視した", 0)
    if n:
        delta = min(0.4 * n, 1.0)
        out.append(
            (
                "third_any",
                0.0,
                delta * 0.7,
                delta,
                f"過去の反省: 3番手軽視を{n}件検出 → 3番手の2-3着を加点",
            )
        )

    # ---- 本命自力の過信 → 先行のwin微減
    n = counts.get("本命自力の過信", 0)
    if n:
        delta = min(0.3 * n, 0.8)
        out.append(
            (
                "head",
                -delta,
                0.0,
                0.0,
                f"過去の反省: 本命自力の過信を{n}件検出 → 先行win微減",
            )
        )

    # ---- 本線番手を過信 → 同ライン番手のwin微減
    n = counts.get("本線番手を過信", 0)
    if n:
        delta = min(0.3 * n, 0.6)
        out.append(
            (
                "bantan_same",
                -delta,
                0.0,
                0.0,
                f"過去の反省: 本線番手の過信を{n}件検出 → 同ライン番手win微減",
            )
        )

    # ---- 本線ラインの3着を固定しすぎた → 別線3番手/別線番手の3着加点
    n = counts.get("本線ラインの3着を固定しすぎた", 0)
    if n:
        delta = min(0.3 * n, 0.7)
        out.append(
            (
                "third_other",
                0.0,
                0.0,
                delta,
                f"過去の反省: 本線3着固定を{n}件検出 → 別線3番手の3着を加点",
            )
        )
        out.append(
            (
                "bantan_other",
                0.0,
                0.0,
                delta * 0.5,
                "過去の反省: 別線番手も3着候補に残す",
            )
        )

    return out


def apply_reflection_signals(
    scores: list[RiderScore],
    reflections: list[Reflection],
    input_data: RaceInput,
) -> None:
    """関連 reflection から RiderScore.reflection_bonus を加減点する。

    破壊的に scores を書き換える。ガールズではライン位置が無いため、
    位置ベースの補正はスキップし、カテゴリベースのメモのみ理由欄に追加する。
    補正幅は控えめ（基本±1.0以内）で、本線決定を機械的に固定しない。
    """
    if not reflections:
        return
    counts = _category_counts(reflections)
    if not counts:
        return

    is_girls = input_data.race.resolved_is_girls()
    pos_map = build_line_position_map(input_data.lines) if not is_girls else {}

    # ◎を仮置きするためにスコア合計でトップラインを特定
    top1 = max(scores, key=lambda x: x.total())
    top1_line = pos_map.get(top1.car_no) if pos_map else None
    top1_line_name = top1_line.line_name if top1_line else None

    # ---- ガミ全体への警告 (穴広げすぎ) ---------------------------------
    n_gami = counts.get("穴を広げすぎてガミリスク増加", 0)
    if n_gami:
        # 加点ではなく、各スコアに記録だけ残す（買い目側で gami_risk を上げる）
        for s in scores:
            s.reasons.append(
                f"過去の反省: 穴広げすぎ{n_gami}件 → 大穴の点数を絞ること"
            )

    # ---- ガールズの位置取り評価不足 ------------------------------------
    n = counts.get("ガールズの位置取り評価不足", 0)
    if n and is_girls:
        for s in scores:
            rider_tags = next(
                (r.style_tags for r in input_data.riders if r.car_no == s.car_no), []
            )
            if "自力" in rider_tags or "追走" in rider_tags or "位置取り" in rider_tags:
                bonus = min(0.3 * n, 0.6)
                s.reflection_bonus += bonus
                s.reasons.append(
                    f"過去の反省: ガールズの位置取り軽視を{n}件 → +{bonus:.2f}"
                )

    if is_girls:
        return  # 以降はライン依存のため終了

    # ---- 風補正不足 / 雨補正不足（番手・3番手をさらに加点） -------------
    n_wind = counts.get("風補正不足", 0)
    n_rain = counts.get("雨補正不足", 0)
    for s in scores:
        pos = pos_map.get(s.car_no)
        if not pos:
            continue
        if pos.is_bantan:
            if n_wind:
                s.reflection_bonus += min(0.3 * n_wind, 0.6)
                s.reasons.append(f"過去の反省: 風補正不足{n_wind}件 → 番手加点")
            if n_rain:
                s.reflection_bonus += min(0.3 * n_rain, 0.6)
                s.reasons.append(f"過去の反省: 雨補正不足{n_rain}件 → 番手加点")
        elif pos.is_third:
            if n_wind:
                s.reflection_bonus += min(0.25 * n_wind, 0.5)
                s.reasons.append(f"過去の反省: 風補正不足{n_wind}件 → 3番手加点")
            if n_rain:
                s.reflection_bonus += min(0.25 * n_rain, 0.5)
                s.reasons.append(f"過去の反省: 雨補正不足{n_rain}件 → 3番手加点")

    # ---- 位置別の細かい補正 -------------------------------------------
    adjustments = _reflection_position_adjustments(counts, top1_line_name)
    for target, dwin, dsecond, dthird, reason in adjustments:
        for s in scores:
            pos = pos_map.get(s.car_no)
            if not pos:
                continue
            ok = False
            if target == "head" and pos.is_head:
                ok = True
            elif target == "bantan_same" and pos.is_bantan and top1_line_name and pos.line_name == top1_line_name:
                ok = True
            elif target == "bantan_other" and pos.is_bantan and (
                not top1_line_name or pos.line_name != top1_line_name
            ):
                ok = True
            elif target == "third_any" and pos.is_third:
                ok = True
            elif target == "third_other" and pos.is_third and (
                not top1_line_name or pos.line_name != top1_line_name
            ):
                ok = True
            if not ok:
                continue
            # win/second/third への加点を reflection_bonus と各位置スコアに按分
            s.reflection_bonus += dwin + dsecond * 0.6 + dthird * 0.4
            s.win_score += dwin
            s.second_score += dsecond
            s.third_score += dthird
            s.reasons.append(reason)


def gami_inflation_from_reflections(reflections: list[Reflection]) -> float:
    """穴広げすぎカテゴリ件数に応じた、大穴/穴 gami_risk への上乗せ係数。"""
    counts = _category_counts(reflections)
    return min(0.2 * counts.get("穴を広げすぎてガミリスク増加", 0), 0.6)


# ---------------------------------------------------------------------------
# 直近トレンドからのスコア補正（仕様8章 D-3）
# ---------------------------------------------------------------------------


def apply_trend_signals(
    scores: list[RiderScore],
    input_data: RaceInput,
) -> None:
    """直近結果トレンドに応じてスコアを破壊的に補正する。

    仕様8章:
    - 番手差し決着多発 → 番手 win + 先行 second + 3番手 third 加点
    - 別線番手絡み多発 → 別線番手 second/third 加点 + 本線番手 win 弱め
    - 3番手2着上がり多発 → 3番手 second 加点
    """
    if input_data.race.resolved_is_girls():
        return
    if not scores:
        return

    trend = analyze_recent(input_data.recent_results)
    roles = resolve_rider_roles(input_data, scores)

    for s in scores:
        role = roles.get(s.car_no)
        if role is None:
            continue

        # 番手差し決着が多発 → 番手 win 加点 / 先行 second / 3番手 third
        if trend.is_bantan_dominant:
            if role == "second":
                s.win_score += 0.4
                s.reasons.append("トレンド: 番手差し決着多発 → 本線番手winを加点")
            elif role == "line_leader":
                s.second_score += 0.3
                s.reasons.append("トレンド: 番手差し決着多発 → 先行secondを加点")
            elif role in ("third", "separate_third"):
                s.third_score += 0.3
                s.reasons.append("トレンド: 番手差し決着多発 → 3番手thirdを加点")

        # 3番手2着上がりが多発 → 3番手 second 加点
        if trend.is_third_sec_up and role in ("third", "separate_third"):
            s.second_score += 0.4
            s.reasons.append("トレンド: 3番手2着上がり多発 → secondを加点")

        # 別線番手絡みが多発 → 別線番手の second/third 加点・本線番手 win 弱め
        if trend.is_bessen_involved:
            if role == "separate_second":
                s.second_score += 0.5
                s.third_score += 0.3
                s.reasons.append("トレンド: 別線番手絡み多発 → 別線番手連対率up")
            elif role == "second":
                # 本線番手の win を少し弱める（仕様: 本線1・2着固定を弱める）
                s.win_score -= 0.2
                s.reasons.append("トレンド: 別線絡み多発 → 本線番手win弱め")


# ---------------------------------------------------------------------------
# 市場（オッズ人気）シグナルの反映
# ---------------------------------------------------------------------------


def apply_wind_extra_signals(
    scores: list[RiderScore],
    input_data: RaceInput,
) -> None:
    """仕様6章「強風時の減点」の補完（D-3）。

    強風 (wind >= 4 m/s) のとき:
      - ラインの短い自力（line_length <= 2 の line_leader）→ win_score 弱め
      - 単独で長く外を踏む選手（solo + style_tags に "捲り"）→ win_score 弱め
      - 既存の risk_score 加算と別系統で「2着固定減点」を実現するため second_score も
        わずかに下げる
    """
    if input_data.race.resolved_is_girls():
        return
    weather = input_data.weather
    if weather is None or weather.wind_speed_mps < 4.0:
        return

    wind_tier = _wind_tier(weather.wind_speed_mps)
    if wind_tier < 2:
        return

    pos_map = build_line_position_map(input_data.lines)
    riders_by_car = {r.car_no: r for r in input_data.riders}

    for s in scores:
        car = s.car_no
        pos = pos_map.get(car)
        rider = riders_by_car.get(car)
        if rider is None:
            continue
        tags = set(rider.style_tags or [])

        # ラインの短い自力（line_leader で line_length <= 2）の減点
        if pos is not None and pos.is_head and pos.line_length <= 2:
            s.win_score -= 0.3 * wind_tier
            s.second_score -= 0.2 * wind_tier
            s.reasons.append(
                f"強風: ラインの短い自力(line_length={pos.line_length}) → win/second 弱め"
            )

        # 単独で長く外を踏む選手 = solo + 捲り
        if pos is not None and pos.is_tanki and "捲り" in tags:
            s.win_score -= 0.3 * wind_tier
            s.reasons.append("強風: 単独外踏み(単騎+捲り) → win 弱め")

        # 風を長く受ける先行選手の 2着固定減点
        # （頭は消さないが 2着固定は不利 → second_score を弱める）
        if pos is not None and pos.is_head:
            s.second_score -= 0.2 * wind_tier
            s.reasons.append("強風: 先行選手の2着固定を弱める")


def apply_bank_signals(
    scores: list[RiderScore],
    input_data: RaceInput,
) -> None:
    """バンク特性（周長・差し/先行有利）に基づく補正を破壊的に適用する。

    仕様7章「バンク補正」準拠:
      - 500バンク: 番手差し・3番手残り・追い込み加点
      - 333バンク: 前々・先行・早めの自力加点。大捲り過信を弱める
      - 400バンク（既定）: 補正なし
      - 差し有利: 番手/3番手/別線番手/追い込み型を加点
      - 先行有利: 先行・前々を加点
    """
    if not scores:
        return
    bank_length = input_data.race.bank_length
    bank_style = (input_data.race.bank_style or "").strip()
    bank_note = (input_data.race.bank_note or "")
    # bank_note からのフォールバック判定
    is_sashi_favor = "差し有利" in (bank_style + " " + bank_note)
    is_senko_favor = "先行有利" in (bank_style + " " + bank_note)

    if bank_length is None and not (is_sashi_favor or is_senko_favor):
        return  # 補正対象なし

    roles = resolve_rider_roles(input_data, scores)
    riders_by_car = {r.car_no: r for r in input_data.riders}

    def _tags(car: int) -> set[str]:
        r = riders_by_car.get(car)
        return set(r.style_tags) if r else set()

    for s in scores:
        car = s.car_no
        role = roles.get(car)
        tags = _tags(car)

        # ---- 500バンク: 番手差し・3番手残り・追い込み ----
        if bank_length and bank_length >= 470:
            if role in ("second", "separate_second"):
                s.win_score += 0.5
                s.second_score += 0.4
                s.reasons.append("500バンク: 番手差しを加点")
            elif role in ("third", "separate_third"):
                s.second_score += 0.3
                s.third_score += 0.4
                s.reasons.append("500バンク: 3番手残りを加点")
            if "追込" in tags or "差し" in tags:
                s.win_score += 0.3
                s.second_score += 0.2
                s.reasons.append("500バンク: 追い込み型を加点")

        # ---- 333バンク: 前々・先行・番手・大捲り過信を弱める ----
        elif bank_length and bank_length <= 350:
            if role == "line_leader":
                s.win_score += 0.5
                s.reasons.append("333バンク: 前々/先行を加点")
            elif role == "separate_leader":
                s.win_score += 0.3
                s.reasons.append("333バンク: 別線自力を加点")
            elif role == "second":
                s.win_score += 0.3
                s.second_score += 0.3
                s.reasons.append("333バンク: 番手差しを加点")
            # 大捲り過信を弱める（後方からの大外捲りは過信しない）
            if "捲り" in tags and role in ("fourth", "separate_third"):
                s.win_score -= 0.3
                s.reasons.append("333バンク: 後方からの大捲りを軽減")

        # ---- 差し有利バンク: 番手・3番手・別線番手・追い込み ----
        if is_sashi_favor:
            if role in ("second", "separate_second"):
                s.win_score += 0.4
                s.reasons.append("差し有利バンク: 番手を加点")
            elif role in ("third", "separate_third"):
                s.second_score += 0.3
                s.third_score += 0.3
                s.reasons.append("差し有利バンク: 3番手を加点")
            if "追込" in tags or "差し" in tags:
                s.win_score += 0.3
                s.reasons.append("差し有利バンク: 追い込み型を加点")

        # ---- 先行有利バンク: 先行ライン全体を加点 ----
        if is_senko_favor:
            if role == "line_leader":
                s.win_score += 0.5
                s.reasons.append("先行有利バンク: 本命ライン先頭を加点")
            elif role == "second":
                s.win_score += 0.3
                s.reasons.append("先行有利バンク: 本命ライン番手を加点")
            elif role == "third":
                s.third_score += 0.3
                s.reasons.append("先行有利バンク: 本命ライン3番手を加点")


def apply_tospo_signals(
    scores: list[RiderScore],
    input_data: RaceInput,
) -> None:
    """東スポ signals (rider.style_tags に追加されたもの) でスコアを軽く補正する。

    補正は最大 ±0.5 程度に抑える（補助情報なので強くしすぎない）。

    ガールズでは **ライン系 signal（番手・追込）は無効化** する（仕様10章: ライン依存禁止）。
    ガールズで意味があるのは「自力」「前々」「自在」「状態良い」「不安/重/疲」のみ。

    | signal | 補正 | ガールズ |
    | --- | --- | --- |
    | 自力 | win +0.3 | ✓ |
    | 前々 | win +0.3 | ✓ |
    | 単騎 | win +0.2 | ✓ |
    | 番手 | second の win/second +0.2 | **× 無効** |
    | 追込 | second/third +0.2 | **× 無効** |
    | 自在 | win/second/third +0.1 | ✓ |
    | 状態良い / 好調 | win +0.2 / second +0.1 / third +0.1 | ✓ |
    | 不安 / 重い / 疲れ | win -0.3 | ✓ |
    """
    if not scores:
        return
    is_girls = input_data.race.resolved_is_girls()
    riders_by_car = {r.car_no: r for r in input_data.riders}
    for s in scores:
        rider = riders_by_car.get(s.car_no)
        if rider is None:
            continue
        tags = set(rider.style_tags or [])
        msgs: list[str] = []

        if "自力" in tags:
            s.win_score += 0.3
            msgs.append("自力")
        if "前々" in tags:
            s.win_score += 0.3
            msgs.append("前々")
        if "単騎" in tags:
            s.win_score += 0.2
            msgs.append("単騎")
        # 番手 / 追込 は **ガールズでは無視** （仕様10章: ライン依存禁止）
        if not is_girls:
            if "番手" in tags:
                s.win_score += 0.2
                s.second_score += 0.2
                msgs.append("番手")
            if "追込" in tags:
                s.second_score += 0.2
                s.third_score += 0.2
                msgs.append("追込")
        if "自在" in tags:
            s.win_score += 0.1
            s.second_score += 0.1
            s.third_score += 0.1
            msgs.append("自在")
        if any(t in tags for t in ("状態良い", "好調")):
            s.win_score += 0.2
            s.second_score += 0.1
            s.third_score += 0.1
            msgs.append("状態良い")
        if any(t in tags for t in ("不安", "重い", "疲れ")):
            s.win_score -= 0.3
            msgs.append("不安/重/疲")

        if msgs:
            s.reasons.append("東スポsignals: " + ", ".join(msgs))


# レース格ごとの加点係数（docs/race_type_policy.md フェーズ B4）
# 上位格ほど「番手の格」「3番手の格」の重みが大きい
GRADE_BOOST_MULTIPLIER: dict[str, float] = {
    "F2": 0.0,  # スキップ
    "F1": 1.0,
    "G3": 1.0,
    "G2": 1.1,
    "G1": 1.2,
    "GP": 1.3,
}


def apply_grade_signals(
    scores: list[RiderScore],
    input_data: RaceInput,
) -> None:
    """F1/グレードレース用の加点ロジック（破壊的）。

    docs/race_type_policy.md フェーズ A4 + B4。
    番手・3番手・別線番手・単騎が「格上」(competitive score が閾値以上）なら、
    対応する着順スコアを加点する。

    ガールズ・新人戦・F2 では呼び出されない前提（個人戦扱い、または点数差優先）。

    加点ルール（係数=1.0 の場合の基準値）:
        - 番手選手が格上     → win_score +0.4 / second_score +0.5
        - 本命3番手が格上    → second_score +0.4 / third_score +0.3
        - 別線番手が格上     → second_score +0.4 / third_score +0.3
        - 単騎自在型が格上   → second_score +0.2 / third_score +0.3

    係数:
        - レース格係数: F1/G3=1.0 / G2=1.1 / G1=1.2 / GP=1.3 (上位格ほど強い)
        - 決勝戦: ×1.3 (勝つ動きが重視される)
    """
    race_info = input_data.race
    if race_info.resolved_is_girls() or race_info.resolved_is_rookie():
        return

    race_grade = race_info.resolved_race_grade()
    grade_mult = GRADE_BOOST_MULTIPLIER.get(race_grade, 0.0)
    if grade_mult <= 0.0:
        # F2 ではグレード補正は控えめ（点数差が直接出るため別ロジックで対応）
        return

    riders_by_car = {r.car_no: r for r in input_data.riders}
    pos_map = build_line_position_map(input_data.lines)
    scores_by_car = {s.car_no: s for s in scores}

    ordered = sorted(scores, key=lambda x: x.total(), reverse=True)
    if not ordered:
        return
    top_car = ordered[0].car_no
    top_pos = pos_map.get(top_car)
    main_line_name = top_pos.line_name if top_pos else None

    final_mult = 1.3 if race_info.resolved_is_final() else 1.0
    boost_mult = grade_mult * final_mult

    for car, pos in pos_map.items():
        rider = riders_by_car.get(car)
        s = scores_by_car.get(car)
        if rider is None or s is None:
            continue
        if not is_kakujou(rider, race_grade):
            continue

        if pos.is_bantan:
            if pos.line_name == main_line_name:
                s.win_score += 0.4 * boost_mult
                s.second_score += 0.5 * boost_mult
                s.reasons.append(
                    f"グレード({race_grade}): 本命ライン番手が格上(得点{rider.score:.1f}) → "
                    f"番手頭/差し加点"
                )
            else:
                s.second_score += 0.4 * boost_mult
                s.third_score += 0.3 * boost_mult
                s.reasons.append(
                    f"グレード({race_grade}): 別線番手が格上(得点{rider.score:.1f}) → "
                    f"2着/3着加点"
                )
        elif pos.is_third and pos.line_name == main_line_name:
            s.second_score += 0.4 * boost_mult
            s.third_score += 0.3 * boost_mult
            s.reasons.append(
                f"グレード({race_grade}): 本命3番手が格上(得点{rider.score:.1f}) → "
                f"2着上がり/3着残り加点"
            )
        elif pos.is_tanki:
            s.second_score += 0.2 * boost_mult
            s.third_score += 0.3 * boost_mult
            s.reasons.append(
                f"グレード({race_grade}): 単騎格上(得点{rider.score:.1f}) → 3着以上加点"
            )


def apply_home_area_signals(
    scores: list[RiderScore],
    input_data: RaceInput,
) -> None:
    """地元選手・地元番手・地元3番手の加点（docs/race_type_policy.md フェーズ C3）。

    開催地の地区 (venue → resolve_venue_area) と選手の home_area が一致する場合、
    対応する着順スコアを加点する。グレードレースで効果が大きい。

    加点ルール（基準値）:
        - 地元番手 (本命ライン)  → win_score +0.2 / second_score +0.3
        - 地元番手 (別線)       → second_score +0.2 / third_score +0.2
        - 地元3番手 (本命ライン) → second_score +0.2 / third_score +0.3
        - 地元単騎              → second_score +0.1 / third_score +0.2

    係数:
        - レース格係数:
            F2: 0.5（控えめ）
            F1: 1.0
            G3: 1.2（記念は地元勢の勝負気配が強い）
            G2: 1.2
            G1: 1.3
            GP: 1.3
        - 決勝戦: ×1.2

    ガールズ・新人戦ではスキップ（ライン依存ロジックを使わない）。
    """
    race_info = input_data.race
    if race_info.resolved_is_girls() or race_info.resolved_is_rookie():
        return
    home_area = resolve_venue_area(race_info.venue)
    if home_area is None:
        return  # 会場マッピング不明

    race_grade = race_info.resolved_race_grade()
    grade_to_mult = {
        "F2": 0.5, "F1": 1.0, "G3": 1.2, "G2": 1.2,
        "G1": 1.3, "GP": 1.3,
    }
    grade_mult = grade_to_mult.get(race_grade, 0.5)
    final_mult = 1.2 if race_info.resolved_is_final() else 1.0
    boost_mult = grade_mult * final_mult

    riders_by_car = {r.car_no: r for r in input_data.riders}
    pos_map = build_line_position_map(input_data.lines)
    scores_by_car = {s.car_no: s for s in scores}

    ordered = sorted(scores, key=lambda x: x.total(), reverse=True)
    if not ordered:
        return
    top_car = ordered[0].car_no
    top_pos = pos_map.get(top_car)
    main_line_name = top_pos.line_name if top_pos else None

    boosted = 0
    for car, pos in pos_map.items():
        rider = riders_by_car.get(car)
        s = scores_by_car.get(car)
        if rider is None or s is None:
            continue
        if rider.home_area != home_area:
            continue  # 地元ではない

        if pos.is_bantan:
            if pos.line_name == main_line_name:
                s.win_score += 0.2 * boost_mult
                s.second_score += 0.3 * boost_mult
                s.reasons.append(
                    f"地元({home_area}): 本命ライン番手 → 番手頭/差し加点"
                )
            else:
                s.second_score += 0.2 * boost_mult
                s.third_score += 0.2 * boost_mult
                s.reasons.append(
                    f"地元({home_area}): 別線番手 → 2着/3着加点"
                )
            boosted += 1
        elif pos.is_third and pos.line_name == main_line_name:
            s.second_score += 0.2 * boost_mult
            s.third_score += 0.3 * boost_mult
            s.reasons.append(
                f"地元({home_area}): 本命3番手 → 2着上がり/3着加点"
            )
            boosted += 1
        elif pos.is_tanki:
            s.second_score += 0.1 * boost_mult
            s.third_score += 0.2 * boost_mult
            s.reasons.append(
                f"地元({home_area}): 単騎 → 3着以上加点"
            )
            boosted += 1


def apply_f2_signals(
    scores: list[RiderScore],
    input_data: RaceInput,
) -> None:
    """F2用の加点ロジック（docs/race_type_policy.md フェーズ B3）。

    F2 では選手間の力差が比較的大きく、点数上位の地力がそのまま出ることがある。
    一方、下位戦では脚力差・展開差・番手の技量差が大きく、荒れも出る。

    加点ルール:
        - 点数差が大きい (top1 - top2 >= 5.0点) → top1 の win_score を加点
        - チャレンジ (race_class="A級チャレンジ") + 先頭が若手自力タイプ (nige>=2 or makuri>=2)
          → 先頭 win_score 加点
        - ラインが長い (本命ライン 3車以上) → 3番手の third_score 加点
        - 番手選手が低得点 (score < 80) → 本線番手2着固定を抑制 (second_score を引かない)

    ガールズ・新人戦 / F1以上では呼び出されない。
    """
    race_info = input_data.race
    if race_info.resolved_is_girls() or race_info.resolved_is_rookie():
        return
    grade = race_info.resolved_race_grade()
    if grade != "F2":
        return

    race_class = race_info.resolved_race_class()
    riders_by_car = {r.car_no: r for r in input_data.riders}
    pos_map = build_line_position_map(input_data.lines)
    scores_by_car = {s.car_no: s for s in scores}

    ordered = sorted(scores, key=lambda x: x.total(), reverse=True)
    if len(ordered) < 2:
        return
    top1, top2 = ordered[0], ordered[1]
    top1_rider = riders_by_car.get(top1.car_no)
    top1_pos = pos_map.get(top1.car_no)
    main_line_name = top1_pos.line_name if top1_pos else None

    # 1. 点数差が大きい → 素直に力上位を頭で重視
    score_diff = top1.total() - top2.total()
    if top1_rider is not None and score_diff >= 5.0:
        top1.win_score += 0.4
        top1.reasons.append(
            f"F2: 点数差大({score_diff:.1f}) → 力上位({top1_rider.score:.1f})を頭重視"
        )

    # 2. チャレンジで先頭が若手自力 → 頭固定しやすい
    if race_class == "A級チャレンジ" and top1_rider is not None:
        is_jiriki = (top1_rider.nige >= 2) or (top1_rider.makuri >= 2)
        if is_jiriki:
            top1.win_score += 0.3
            top1.reasons.append(
                f"F2チャレンジ: 先頭若手自力(nige={top1_rider.nige}, "
                f"makuri={top1_rider.makuri}) → 頭固定加点"
            )

    # 3. ラインが長い場合は3番手の3着流し込みを加点
    if main_line_name:
        main_line_members = [
            (car, pos) for car, pos in pos_map.items()
            if pos.line_name == main_line_name
        ]
        if len(main_line_members) >= 3:
            # 3番手 (index=2) を加点
            third_candidates = [
                car for car, pos in main_line_members if pos.is_third
            ]
            for car in third_candidates:
                s = scores_by_car.get(car)
                if s is not None:
                    s.third_score += 0.3
                    s.reasons.append(
                        "F2: 本命ライン3車以上 → 3番手の3着流し込み加点"
                    )


def apply_market_signals(
    scores: list[RiderScore],
    odds_entries,
    *,
    top_n: int = 20,
    weight: float = 0.5,
    max_boost: float = 0.5,
    boost_multiplier: float = 1.0,
) -> None:
    """3連単オッズの人気上位 N 件から各車の頭/2着/3着出現頻度を集計し、
    score (win/second/third) に補正として加点する（破壊的）。

    出走表から score が取れない場面（ガールズや Kドリームスの初期出走表）でも、
    市場の人気を予想に反映させるための fallback。

    ※2車単/3連複は **使わない**（3連単オッズのみ集計）。市場補正は3連単市場の
      人気構造のみを反映する。3連複・2車単はガミリスク判定で別途使う。

    補正の強さ:
        - 仕様の他補正（バンク・トレンド・反省ログ・東スポ）と整合させ、
          最大 ±max_boost (=0.5) 程度に抑える。
        - 「市場に寄せすぎ」を防ぐため、上位常連車でも win_score 加点は 0.5 が上限。
    """
    # 3連単のオッズだけ使う
    trifecta: list[tuple[str, float]] = []
    for e in odds_entries:
        if isinstance(e, dict):
            bt = e.get("bet_type")
            combo = e.get("combination")
            odds_v = e.get("odds")
        else:
            bt = getattr(e, "bet_type", None)
            combo = getattr(e, "combination", None)
            odds_v = getattr(e, "odds", None)
        if bt != "3連単" or not combo or odds_v is None:
            continue
        try:
            o = float(odds_v)
        except (TypeError, ValueError):
            continue
        trifecta.append((str(combo), o))
    if not trifecta:
        return

    # オッズ昇順（人気順）に上位 N 件
    trifecta.sort(key=lambda x: x[1])
    top = trifecta[:top_n] if top_n > 0 else trifecta
    if not top:
        return

    # 頭/2着/3着の登場回数
    win_n: dict[int, int] = {}
    sec_n: dict[int, int] = {}
    third_n: dict[int, int] = {}
    for combo, _ in top:
        parts = combo.split("-")
        if len(parts) != 3:
            continue
        try:
            a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if not (1 <= a <= 9 and 1 <= b <= 9 and 1 <= c <= 9):
            continue
        if len({a, b, c}) != 3:
            continue
        win_n[a] = win_n.get(a, 0) + 1
        sec_n[b] = sec_n.get(b, 0) + 1
        third_n[c] = third_n.get(c, 0) + 1

    total = len(top)
    if total == 0:
        return
    # 加点幅: 頻度（0〜1） × weight を、最大 max_boost (=0.5) でクランプ
    # これにより市場に寄せすぎず、仕様の他補正と整合する
    for s in scores:
        car = s.car_no
        w1 = win_n.get(car, 0) / total
        w2 = sec_n.get(car, 0) / total
        w3 = third_n.get(car, 0) / total
        if w1 == 0 and w2 == 0 and w3 == 0:
            continue
        # 最大値でクランプ（市場依存を抑制）
        # 数値不足モードでは boost_multiplier を上げて市場参照を強める
        effective_max = max_boost * boost_multiplier
        add_w1 = min(w1 * weight * boost_multiplier, effective_max)
        add_w2 = min(w2 * weight * boost_multiplier, effective_max)
        add_w3 = min(w3 * weight * boost_multiplier, effective_max)
        s.win_score += add_w1
        s.second_score += add_w2
        s.third_score += add_w3
        s.reasons.append(
            f"市場人気上位{total}件: 頭{win_n.get(car,0)}/2着{sec_n.get(car,0)}/3着{third_n.get(car,0)}回 "
            f"(win+{add_w1:.2f})"
        )

    # ---- 仕様4章「人気本線が安すぎる」とき、非人気車に微加点 ----
    # 人気1位のオッズが極端に安い (< 4.0倍) → 番狂わせを期待して
    # 「市場の上位N件にまったく登場していない車番」に win 微加点（max 0.3）
    if top and top[0][1] < 4.0:
        for s in scores:
            if (
                win_n.get(s.car_no, 0) == 0
                and sec_n.get(s.car_no, 0) == 0
                and third_n.get(s.car_no, 0) == 0
            ):
                s.win_score += 0.3
                s.reasons.append(
                    "市場: 人気本線が安すぎる → 非人気車のwin微加点（番狂わせ期待）"
                )


def build_marks(
    scores: list[RiderScore],
    input_data: Optional[RaceInput] = None,
    candidate_bets: Optional[dict[str, list]] = None,
) -> dict[str, int]:
    """印を割り当てる。

    input_data があり、通常ライン戦の場合は **本命ライン構造を反映** する:
      - ◎: top1
      - ○: 本命ライン番手 (top1が番手なら ○ は本命先頭)
      - ▲: 本命ライン3番手 (main_third) または別線最強
      - △/×/α: 残り

    candidate_bets が渡された場合、△以下は **市場+本線/押さえ出現** で
    重み付けして決める（単騎が本線に出ないのに ▲化する等を防ぐ）。

    ガールズ・top1単騎・本命ライン2人未満ではスコア順位の既存ロジック。
    """
    ordered = sorted(scores, key=lambda x: x.total(), reverse=True)
    if not ordered:
        return {}

    # 旧挙動: input_data 無し / ガールズ / 通常ライン無効 → スコア順
    if input_data is None or input_data.race.resolved_is_girls():
        return {mark: s.car_no for mark, s in zip(_MARK_ORDER, ordered)}

    pos_map = build_line_position_map(input_data.lines)
    top1 = ordered[0]
    top1_pos = pos_map.get(top1.car_no)
    if top1_pos is None or top1_pos.is_tanki:
        # top1 が単騎/ライン無し → スコア順
        return {mark: s.car_no for mark, s in zip(_MARK_ORDER, ordered)}

    # 本命ライン特定
    main_line_name = top1_pos.line_name
    main_members = sorted(
        [(car, pos) for car, pos in pos_map.items() if pos.line_name == main_line_name],
        key=lambda kv: kv[1].index,
    )
    main_leader_car = main_members[0][0] if main_members else None
    main_second_car = main_members[1][0] if len(main_members) >= 2 else None
    main_third_car = main_members[2][0] if len(main_members) >= 3 else None

    # 本命ラインに2人未満ならスコア順
    if main_second_car is None:
        return {mark: s.car_no for mark, s in zip(_MARK_ORDER, ordered)}

    # 印割り当て
    out: dict[str, int] = {}
    used: set[int] = set()

    def _assign(mark: str, car: Optional[int]) -> None:
        if car is None or car in used:
            return
        if mark in out:
            return
        out[mark] = car
        used.add(car)

    # ◎: top1（line_leader でなくても OK、◎はあくまでスコア最上位）
    _assign("◎", top1.car_no)
    # ◯: 本命ライン構造を反映
    # - 通常（top1 が line_leader）: ◯ は main_second（本命番手）
    # - top1 が本命番手のとき: ◯ は本命ライン先頭（line_leader）
    if top1.car_no == main_second_car:
        _assign("◯", main_leader_car)
    else:
        _assign("◯", main_second_car)
    # ▲: 本命ライン3番手 (main_third) → 居なければ別線最強
    if main_third_car is not None:
        _assign("▲", main_third_car)
    else:
        # 別線最強（top1 のライン外の最上位）
        for s in ordered:
            if s.car_no not in used:
                pos = pos_map.get(s.car_no)
                if pos is None or pos.line_name != main_line_name:
                    _assign("▲", s.car_no)
                    break

    # △/×/α: 残りを「市場+本線/押さえ出現」スコアで決める
    # （candidate_bets があれば優先、無ければスコア順の既存挙動）
    remaining_marks = [m for m in _MARK_ORDER if m not in out]

    # 重み計算
    weights: dict[int, float] = {}
    for s in ordered:
        weights[s.car_no] = s.total()
    if candidate_bets:
        for cat, mult in (("本線", 5.0), ("押さえ", 3.0), ("穴", 1.0)):
            for b in candidate_bets.get(cat, []) or []:
                for part in str(b.combination).split("-"):
                    try:
                        weights[int(part)] = weights.get(int(part), 0.0) + mult
                    except (ValueError, TypeError):
                        pass
    # 市場上位（3連単 odds 安い順 10件まで）
    if input_data.odds:
        sorted_tanshou = sorted(
            [o for o in input_data.odds
             if o.bet_type == "3連単" and o.odds is not None],
            key=lambda o: o.odds,
        )[:10]
        for o in sorted_tanshou:
            for part in str(o.combination).split("-"):
                try:
                    weights[int(part)] = weights.get(int(part), 0.0) + 1.0
                except (ValueError, TypeError):
                    pass

    # 残り車を重み降順で並び替え
    remaining_cars = sorted(
        [s.car_no for s in ordered if s.car_no not in used],
        key=lambda c: -weights.get(c, 0.0),
    )
    for mark, car in zip(remaining_marks, remaining_cars):
        _assign(mark, car)

    return out


def _odds_for(odds: list[OddsEntry], combo: str, bet_type: str = "3連単") -> Optional[float]:
    for o in odds:
        if o.bet_type == bet_type and o.combination == combo:
            return o.odds
    return None


def _find_first_role(roles: dict[int, str], target: str) -> Optional[int]:
    """指定 role に該当する最初の車番を返す（同 role が複数なら最小車番）。"""
    matches = [car for car, r in roles.items() if r == target]
    return min(matches) if matches else None


def _all_role(roles: dict[int, str], target: str) -> list[int]:
    return sorted(car for car, r in roles.items() if r == target)


def _compute_market_line_counts(
    input_data: RaceInput,
    top_k_tanshou: int = 10,
) -> tuple[dict[str, int], dict]:
    """3連単上位 N 件から、各ラインの (1着, 2着) ペア頻出度を集計。

    Returns:
        (line_counts, pos_map) - line_name -> 出現回数、および position_map
    """
    pos_map = build_line_position_map(input_data.lines)
    if not input_data.lines or not input_data.odds:
        return {}, pos_map
    tanshou = sorted(
        [
            o for o in input_data.odds
            if o.bet_type == "3連単" and o.odds is not None
        ],
        key=lambda o: o.odds,
    )[:top_k_tanshou]
    line_counts: dict[str, int] = {}
    for o in tanshou:
        try:
            cars = [int(c) for c in o.combination.split("-")]
        except ValueError:
            continue
        if len(cars) < 2:
            continue
        p1 = pos_map.get(cars[0])
        p2 = pos_map.get(cars[1])
        if not p1 or not p2 or p1.is_tanki or p2.is_tanki:
            continue
        if p1.line_name == p2.line_name:
            line_counts[p1.line_name] = line_counts.get(p1.line_name, 0) + 1
    return line_counts, pos_map


def _line_info(
    line_name: str, pos_map: dict, count: int = 0,
) -> Optional[dict[str, int]]:
    """line_name から {leader, second, third, _count, _line_name} dict を返す。"""
    members = sorted(
        [(car, pos) for car, pos in pos_map.items() if pos.line_name == line_name],
        key=lambda kv: kv[1].index,
    )
    if not members:
        return None
    return {
        "leader": members[0][0],
        "second": members[1][0] if len(members) >= 2 else None,
        "third": members[2][0] if len(members) >= 3 else None,
        "_count": count,
        "_line_name": line_name,
    }


def _detect_market_focused_line(
    input_data: RaceInput,
    *,
    top_k_tanshou: int = 5,
    min_count: int = 2,
    dominance_gap: int = 2,
) -> Optional[dict[str, int]]:
    """3連単オッズ上位から、**圧倒的に支持されている** 単一ラインを判定。

    1位ラインの出現回数が、2位ラインより `dominance_gap` 件以上多い場合のみ
    「市場注目ライン」として返す。**拮抗（差<gap）なら None** を返す
    （本命ライン上書きには使わず、別途 _detect_market_focused_lines で
    押さえ強化用に複数取得する）。
    """
    line_counts, pos_map = _compute_market_line_counts(
        input_data, top_k_tanshou=top_k_tanshou,
    )
    if not line_counts:
        return None
    sorted_lines = sorted(line_counts.items(), key=lambda kv: -kv[1])
    best_line, best_count = sorted_lines[0]
    if best_count < min_count:
        return None
    # 2位ラインがあって、差が dominance_gap 未満なら「拮抗」→ 上書きしない
    if len(sorted_lines) >= 2:
        second_count = sorted_lines[1][1]
        if best_count - second_count < dominance_gap:
            return None
    return _line_info(best_line, pos_map, best_count)


def _detect_market_focused_pair_no_lines(
    input_data: RaceInput,
    *,
    top_k_tanshou: int = 5,
    min_count: int = 2,
) -> Optional[tuple[int, int]]:
    """ガールズ/個人戦（ライン無し）で、3連単上位の頻出車番ペアを検出。

    上位 top_k_tanshou 件の (1着, 2着) ペアを集計し、最頻出のペアを返す。
    ペアは順序問わず（(1,2)=(2,1)）。min_count 件以上で「市場注目」と判定。

    Returns:
        (car_a, car_b) のタプル（car_a < car_b）、または None
    """
    if not input_data.odds:
        return None
    tanshou = sorted(
        [o for o in input_data.odds
         if o.bet_type == "3連単" and o.odds is not None],
        key=lambda o: o.odds,
    )[:top_k_tanshou]
    pair_counts: dict[tuple[int, int], int] = {}
    for o in tanshou:
        try:
            cars = [int(c) for c in o.combination.split("-")]
        except ValueError:
            continue
        if len(cars) < 2:
            continue
        pair = tuple(sorted(cars[:2]))
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
    if not pair_counts:
        return None
    best_pair, best_count = max(pair_counts.items(), key=lambda kv: kv[1])
    if best_count < min_count:
        return None
    return best_pair


def _pick_market_top_third(
    input_data: RaceInput,
    ll_car: int,
    sec_car: int,
    m_lead: Optional[int],
    m_sec: Optional[int],
) -> Optional[int]:
    """市場上位3連単で「ll-sec-X」形の X として頻出する別線車を選ぶ。

    宇都宮11R 例: 4-1-2 (16.8), 4-1-7 (18.7), 4-1-5 (19.8) → 2 と 7 と 5。
    候補 (m_lead=7, m_sec=2) のうち、より上位人気で3着に出る方を返す。
    """
    if not input_data.odds:
        return m_sec or m_lead
    candidates: dict[int, float] = {}
    if m_lead:
        candidates[m_lead] = float("inf")
    if m_sec:
        candidates[m_sec] = float("inf")
    if not candidates:
        return None
    for o in sorted(input_data.odds, key=lambda x: x.odds or 999):
        if o.bet_type != "3連単" or o.odds is None:
            continue
        parts = o.combination.split("-")
        if len(parts) != 3:
            continue
        try:
            cars = [int(p) for p in parts]
        except ValueError:
            continue
        if cars[0] == ll_car and cars[1] == sec_car and cars[2] in candidates:
            # ll-sec-X 形を発見 → X の最良オッズ更新
            if o.odds < candidates[cars[2]]:
                candidates[cars[2]] = o.odds
    # 最良オッズが取れた方を優先（inf のものは取れなかった）
    found = {c: v for c, v in candidates.items() if v != float("inf")}
    if not found:
        return m_sec or m_lead
    # 一番安い（人気高い）車を返す
    return min(found.items(), key=lambda kv: kv[1])[0]


def _detect_market_focused_lines(
    input_data: RaceInput,
    *,
    top_k_tanshou: int = 10,
    min_count: int = 1,
    max_lines: int = 3,
) -> list[dict[str, int]]:
    """3連単上位から、市場注目度順に全ラインを返す（押さえ強化用）。

    `_detect_market_focused_line` と違い、拮抗・複数ラインでも全て返す。
    本命上書きには使わず、押さえ・本線の補強に使う。
    """
    line_counts, pos_map = _compute_market_line_counts(
        input_data, top_k_tanshou=top_k_tanshou,
    )
    if not line_counts:
        return []
    sorted_lines = sorted(line_counts.items(), key=lambda kv: -kv[1])
    out: list[dict[str, int]] = []
    for line_name, cnt in sorted_lines:
        if cnt < min_count:
            break
        info = _line_info(line_name, pos_map, cnt)
        if info is not None:
            out.append(info)
        if len(out) >= max_lines:
            break
    return out


def _resolve_separate_lines(
    input_data: RaceInput,
    scores: list[RiderScore],
) -> list[dict[str, int]]:
    """別線ラインを leader のスコア順で取得する。

    Returns:
        各要素は ``{"leader": car, "second": car, "third": car}`` の dict。
        単騎ラインは含めない。leader のスコアが高い順に並ぶ。
    """
    if input_data.race.resolved_is_girls():
        return []
    if not scores:
        return []
    pos_map = build_line_position_map(input_data.lines)
    by_car = {s.car_no: s for s in scores}
    top1 = max(scores, key=lambda s: s.total())
    top1_pos = pos_map.get(top1.car_no)
    top1_line_name = (
        top1_pos.line_name if top1_pos and not top1_pos.is_tanki else None
    )

    line_groups: dict[str, dict[str, int]] = {}
    for car, pos in pos_map.items():
        if pos.is_tanki:
            continue
        if pos.line_name == top1_line_name:
            continue
        d = line_groups.setdefault(pos.line_name, {})
        if pos.is_head:
            d["leader"] = car
        elif pos.is_bantan:
            d["second"] = car
        elif pos.is_third:
            d["third"] = car

    def _line_score(d: dict[str, int]) -> float:
        leader_car = d.get("leader")
        if leader_car and leader_car in by_car:
            return by_car[leader_car].total()
        return 0.0

    return sorted(line_groups.values(), key=lambda d: -_line_score(d))


def _add_weather_and_trend_candidates(
    *,
    input_data: RaceInput,
    scores: list[RiderScore],
    honsen: list,
    osae: list,
    ana: list,
    ooana: list,
    push_fn,
) -> None:
    """仕様5/6/8章の必須候補を、役割タグを使って強制的に追加する。

    天候(雨/強風)・直近トレンド(本命ライン決着/番手差し/3番手上がり/別線番手絡み/荒れ)
    のそれぞれで「必ず候補に入れる形」を push する。

    ガールズレースでは役割タグが全員 'girls' となるため、ここでは何もしない。
    （ガールズ専用形はフェーズCで対応予定）
    """
    if input_data.race.resolved_is_girls():
        return

    roles = resolve_rider_roles(input_data, scores)
    ll = _find_first_role(roles, "line_leader")
    sec = _find_first_role(roles, "second")
    thr = _find_first_role(roles, "third")
    sep_l = _find_first_role(roles, "separate_leader")
    sep_s = _find_first_role(roles, "separate_second")
    sep_t = _find_first_role(roles, "separate_third")
    tankis = _all_role(roles, "solo") + _all_role(roles, "jizai")

    weather = input_data.weather
    rain = weather.rain_mm_per_hour if weather else 0.0
    wind = weather.wind_speed_mps if weather else 0.0

    # ---- 雨補正：仕様5章「雨の場合、必ず候補に入れる」 ------------------
    if rain > 0.0:
        if ll and sec and sep_s:
            push_fn(osae, f"{ll}-{sec}-{sep_s}", "雨補正: 本命自力-本命番手-別線番手")
            push_fn(osae, f"{ll}-{sep_s}-{sec}", "雨補正: 本命自力-別線番手-本命番手")
        if ll and thr and sec:
            push_fn(osae, f"{ll}-{thr}-{sec}", "雨補正: 本命自力-3番手-本命番手")
        if sec and ll and thr:
            push_fn(ana, f"{sec}-{ll}-{thr}", "雨補正: 番手-自力-3番手")
        if sep_s and sep_l and ll:
            push_fn(ana, f"{sep_s}-{sep_l}-{ll}", "雨補正: 別線番手-別線自力-本命自力")
        # 別線が間に割って入る形（仕様レビューで追加）
        if ll and sep_l and sec:
            push_fn(ana, f"{ll}-{sep_l}-{sec}", "雨補正: 本線先頭-別線自力-本線番手")

    # ---- 強風補正：仕様6章「強風時に必ず残す形」(風速4m/s 以上) --------
    if wind >= 4.0:
        if ll and sep_s and sec:
            push_fn(osae, f"{ll}-{sep_s}-{sec}", "強風補正: 本線先頭-別線番手-本線番手")
        if ll and thr and sec:
            push_fn(osae, f"{ll}-{thr}-{sec}", "強風補正: 本線先頭-3番手-本線番手")
        if sec and ll and thr:
            push_fn(osae, f"{sec}-{ll}-{thr}", "強風補正: 番手-先行-3番手")
        if thr and sec and ll:
            push_fn(ana, f"{thr}-{sec}-{ll}", "強風補正: 3番手-番手-先行")
        if sep_s and sep_l and ll:
            push_fn(ana, f"{sep_s}-{sep_l}-{ll}", "強風補正: 別線番手-別線自力-本線自力")
        if ll and sep_s and sec:
            push_fn(osae, f"{ll}-{sep_s}-{sec}", "強風補正: 本命自力-別線番手-本線番手")
        # 別線が間に割って入る形（仕様レビューで追加）
        if ll and sep_l and sec:
            push_fn(ana, f"{ll}-{sep_l}-{sec}", "強風補正: 本線先頭-別線自力-本線番手")

    # ---- 直近結果トレンド（仕様8章）-----------------------------------
    trend = analyze_recent(input_data.recent_results)

    # 番手差し決着が多い → 番手頭を本線寄りに
    if trend.is_bantan_dominant and sec and ll and thr:
        push_fn(honsen, f"{sec}-{ll}-{thr}", "直近トレンド: 番手頭決着が多発")

    # 3番手2着上がりが多い → 自力-3番手-番手 を押さえに
    if trend.is_third_sec_up and ll and thr and sec:
        push_fn(osae, f"{ll}-{thr}-{sec}", "直近トレンド: 3番手2着上がり多発")

    # 別線番手絡みが多い → 必ず別線番手2着を入れる
    if trend.is_bessen_involved and ll and sep_s and sec:
        push_fn(osae, f"{ll}-{sep_s}-{sec}", "直近トレンド: 別線番手絡み多発")
        if sep_s and ll and thr:
            push_fn(ana, f"{sep_s}-{ll}-{thr}", "直近トレンド: 別線番手頭の波乱形")

    # 本命ライン決着が多い → 1-2-3 を再強化（既に本線に入っているはず）
    if trend.is_main_line_dominant and ll and sec and thr:
        push_fn(honsen, f"{ll}-{sec}-{thr}", "直近トレンド: 本命ライン決着多発")

    # 仕様レビュー追加: 着順パターン由来の必須形
    # 「先行-3番手-番手」: 先行頭で3番手2着上がりが多発
    if trend.is_senko_head_third_2nd and ll and thr and sec:
        push_fn(osae, f"{ll}-{thr}-{sec}", "直近トレンド: 先行-3番手-番手 多発")
    # 「番手-先行-3番手」: 番手頭+先行2着+3番手3着
    if trend.is_bantan_head_senko_2nd and sec and ll and thr:
        push_fn(osae, f"{sec}-{ll}-{thr}", "直近トレンド: 番手-先行-3番手 多発")
    # 別線自力決着が多発 → 別線自力-別線番手-本線自力 を穴に
    if trend.is_bessen_lead_dominant and sep_l and sep_s and ll:
        push_fn(ana, f"{sep_l}-{sep_s}-{ll}", "直近トレンド: 別線自力決着多発")
    # 別線自力決着の派生 → 別線番手-別線自力-本線自力
    if trend.is_bessen_lead_dominant and sep_s and sep_l and ll:
        push_fn(ana, f"{sep_s}-{sep_l}-{ll}", "直近トレンド: 別線番手-別線自力-本線自力")
    # 別線ラインを複数本（上位2本）取得（仕様: スコア上位2本までの別線を候補に）
    separate_lines = _resolve_separate_lines(input_data, scores)
    top_separate_lines = separate_lines[:2]

    # 「本線先頭-番手-別線番手」（1-9-5 形）が出た → 同形を押さえ上位に
    if trend.is_main_then_bessen_third and ll and sec:
        # 別線ラインごとに sep_s を入れて複数本展開
        for sline in top_separate_lines:
            s_sec = sline.get("second")
            if not s_sec:
                continue
            push_fn(
                osae, f"{ll}-{sec}-{s_sec}",
                "直近トレンド: 本線先頭-番手-別線番手の連発",
            )
            push_fn(
                osae, f"{ll}-{s_sec}-{sec}",
                "直近トレンド: 本命先頭-別線番手-本命番手の併用",
            )

    # 「本命先頭-別線自力-別線番手」（3-2-8 形）が出た → 同形を押さえ・穴に
    # 優先順位:
    #   (1) 各別線ラインの ll-sep_l-sep_s（押さえ）← 最優先
    #   (2) 各別線ラインの sep_l-sep_s-ll（穴）
    #   (3) 各別線ラインの ll-sep_l-sec（押さえ・波及）
    # この順で push することで、別線ライン2本目の必須形も上限に達する前に入る。
    if trend.is_main_with_bessen_lead and ll:
        # (1) ll-sep_l-sep_s （別線ラインの本線軸：押さえ最優先）
        for sline in top_separate_lines:
            s_lead = sline.get("leader")
            s_sec = sline.get("second")
            if s_lead and s_sec:
                push_fn(
                    osae, f"{ll}-{s_lead}-{s_sec}",
                    "直近トレンド: 本命先頭-別線自力-別線番手の連発",
                )
        # (2) sep_l-sep_s-ll （別線自力頭の波乱形：穴）
        for sline in top_separate_lines:
            s_lead = sline.get("leader")
            s_sec = sline.get("second")
            if s_lead and s_sec:
                push_fn(
                    ana, f"{s_lead}-{s_sec}-{ll}",
                    "直近トレンド: 別線自力頭-別線番手-本命の波乱形",
                )
        # (3) ll-sep_l-sec （本命番手3着の波及：押さえ）
        if sec:
            for sline in top_separate_lines:
                s_lead = sline.get("leader")
                if s_lead:
                    push_fn(
                        osae, f"{ll}-{s_lead}-{sec}",
                        "直近トレンド: 本命先頭-別線自力-本命番手の波及",
                    )

    # 荒れ傾向 → 単騎・自在頭、別線番手頭、3番手頭を穴/大穴に増やす
    if trend.is_chaotic:
        # 単騎・自在頭
        for car in tankis[:2]:
            if ll and sec:
                push_fn(ana, f"{car}-{ll}-{sec}", "直近トレンド: 荒れ傾向で単騎/自在頭")
        # 別線番手頭
        if sep_s and ll and sec:
            push_fn(ana, f"{sep_s}-{ll}-{sec}", "直近トレンド: 荒れ傾向で別線番手頭")
        # 3番手頭
        if thr and ll and sec:
            push_fn(ooana, f"{thr}-{ll}-{sec}", "直近トレンド: 荒れ傾向で3番手頭")


def classify_girls_role(rider: Rider) -> str:
    """ガールズ選手の脚質を「前々型 / 追走型 / 自在型 / 不明」に分類する。

    判定優先度:
      1. style_tags の明示
      2. comment の文字列マッチ（"逃" "追" "両" など Kドリームスの出走表の脚質列）
    """
    tags = set(rider.style_tags or [])
    comment = (rider.comment or "")

    # 前々型: 先行・自力・逃げタグ or comment に "逃"
    if "先行" in tags or "自力" in tags or "逃" in comment:
        return "前々型"
    # 追走型: 追走・差し・追込タグ or comment に "追"
    if "追走" in tags or "差し" in tags or "追込" in tags or "追" in comment:
        return "追走型"
    # 自在型: 自在タグ or comment に "両"
    if "自在" in tags or "両" in comment:
        return "自在型"
    return "不明"


def _add_girls_candidate_bets(
    *,
    ordered: list[RiderScore],
    input_data: RaceInput,
    honsen: list,
    osae: list,
    ana: list,
    ooana: list,
    push_fn,
) -> None:
    """ガールズ専用買い目（仕様10章）。

    ライン無しの個人戦扱い。スコア順を「本命/対抗/3位/中穴4位/追走5位」と仮定し、
    仕様の4形に対応する組み合わせを追加する。

    仕様の必須形:
      - 本命頭 - 対抗 - 追走型
      - 本命頭 - 中穴 - 対抗
      - 対抗頭 - 本命 - 追走型
      - 本命頭 - 前々型 - 追走型
    """
    if len(ordered) < 3:
        return
    top1, top2, top3 = ordered[0], ordered[1], ordered[2]
    top4 = ordered[3] if len(ordered) >= 4 else None
    top5 = ordered[4] if len(ordered) >= 5 else None

    # ---- 脚質タグ分類（仕様10章 D-2）---------------------------------
    riders_by_car = {r.car_no: r for r in input_data.riders}
    role_by_car: dict[int, str] = {
        s.car_no: classify_girls_role(riders_by_car[s.car_no])
        for s in ordered
        if s.car_no in riders_by_car
    }
    # スコア上位順に並んだ「前々型」「追走型」の車番リスト
    maemae_cars = [s.car_no for s in ordered if role_by_car.get(s.car_no) == "前々型"]
    chase_cars = [s.car_no for s in ordered if role_by_car.get(s.car_no) == "追走型"]

    # ---- 本線寄り（仕様: 本命頭中心）----
    # 本命頭 - 対抗 - 3位（既に基本で出ているはずだが念のため）
    push_fn(honsen, f"{top1.car_no}-{top2.car_no}-{top3.car_no}",
            "ガールズ: 本命-対抗-3位の素直な並び")
    # 本命頭 - 3位 - 対抗 (2-3着入替)
    push_fn(honsen, f"{top1.car_no}-{top3.car_no}-{top2.car_no}",
            "ガールズ: 本命-3位-対抗 (2-3着入替)")
    if top4:
        # 本命頭 - 対抗 - 中穴
        push_fn(honsen, f"{top1.car_no}-{top2.car_no}-{top4.car_no}",
                "ガールズ: 本命-対抗-中穴 (3着に中穴)")

    # ---- 押さえ（中穴2着パターン、対抗頭、追走型2着など）----
    if top4:
        # 本命頭 - 中穴 - 対抗
        push_fn(osae, f"{top1.car_no}-{top4.car_no}-{top2.car_no}",
                "ガールズ: 本命頭-中穴2着-対抗3着")
    # 対抗頭 - 本命 - 3位
    push_fn(osae, f"{top2.car_no}-{top1.car_no}-{top3.car_no}",
            "ガールズ: 対抗頭-本命-3位")
    if top4:
        # 対抗頭 - 本命 - 中穴
        push_fn(osae, f"{top2.car_no}-{top1.car_no}-{top4.car_no}",
                "ガールズ: 対抗頭-本命-中穴")
    if top5:
        # 本命頭 - 追走型(5位想定) - 対抗
        push_fn(osae, f"{top1.car_no}-{top5.car_no}-{top2.car_no}",
                "ガールズ: 本命-追走型-対抗")

    # ---- 穴（中穴頭、追走型絡みの波乱形）----
    if top4:
        # 中穴(4位) 頭の波乱形
        push_fn(ana, f"{top4.car_no}-{top1.car_no}-{top2.car_no}",
                "ガールズ: 中穴頭(4位)の波乱形")
    if top5:
        # 追走型(5位) が3着に伸びる
        push_fn(ana, f"{top1.car_no}-{top2.car_no}-{top5.car_no}",
                "ガールズ: 追走型(5位)の3着突っ込み")

    # ---- 大穴（5位以下頭）----
    if top5:
        push_fn(ooana, f"{top5.car_no}-{top1.car_no}-{top2.car_no}",
                "ガールズ: 5位頭の大波乱")

    # ---- 脚質タグベースの必須形（仕様10章 D-2）-----------------------
    # 本命頭 - 前々型 - 追走型
    if maemae_cars and chase_cars:
        maemae_car = next((c for c in maemae_cars if c != top1.car_no), None)
        chase_car = next(
            (c for c in chase_cars if c not in (top1.car_no, maemae_car)), None,
        )
        if maemae_car and chase_car:
            push_fn(
                honsen,
                f"{top1.car_no}-{maemae_car}-{chase_car}",
                "ガールズ: 本命頭-前々型-追走型",
            )
    # 対抗頭 - 本命 - 追走型
    if chase_cars:
        chase_car = next(
            (c for c in chase_cars if c not in (top1.car_no, top2.car_no)), None
        )
        if chase_car:
            push_fn(
                osae,
                f"{top2.car_no}-{top1.car_no}-{chase_car}",
                "ガールズ: 対抗頭-本命-追走型",
            )
    # 本命頭 - 中穴 - 対抗（既存 top1-top4-top2 と被るが、明示版を脚質ベースで補強）
    if maemae_cars:
        maemae_car = next(
            (c for c in maemae_cars if c not in (top1.car_no, top2.car_no)), None
        )
        if maemae_car:
            push_fn(
                osae,
                f"{top1.car_no}-{maemae_car}-{top2.car_no}",
                "ガールズ: 本命頭-前々型-対抗",
            )


_DEFAULT_BET_BUDGET = 18


_ROOKIE_REASON_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    # 順序重要: 長い表現から先に置換
    ("4番手評価", "4位評価"),
    ("3番手評価", "3位評価"),
    ("本命ライン3番手", "上位候補3位"),
    ("本命ライン", "上位候補"),
    ("別線自力-別線番手-本命", "別線2人-上位"),
    ("別線番手3着", "別線2位3着"),
    ("別線番手", "別線2位"),
    ("3番手", "3位"),
    ("4番手", "4位"),
    ("番手頭", "2位頭"),
    ("本命番手", "1位の2着候補"),
    ("番手", "2位"),
    ("本命3着", "1位3着"),
    ("本命", "1位評価"),
)


def _sanitize_reason_for_rookie(reason: Optional[str]) -> Optional[str]:
    """新人戦/個人戦向けに reason 内のライン用語を置換する。

    通常ライン戦の用語（本命ライン/番手/3番手/別線番手等）を、
    位置取り中心の表現（上位候補/2位/1位評価等）に置き換える。
    """
    if not reason:
        return reason
    out = reason
    for old, new in _ROOKIE_REASON_REPLACEMENTS:
        out = out.replace(old, new)
    return out


def compute_bet_distribution(
    target_total: int,
) -> tuple[int, int, int, int]:
    """ターゲット合計買い目点数を本線/押さえ/穴/大穴に配分する。

    配分比率: 本線 20% / 押さえ 30% / 穴 35% / 大穴 15%
    最低保証: (2, 2, 2, 1) = 合計7

    Args:
        target_total: 目標合計点数（推奨: 12〜30）

    Returns:
        (本線, 押さえ, 穴, 大穴) のターゲット点数
    """
    target_total = max(7, min(int(target_total), 40))
    # 最低保証
    base = (2, 2, 2, 1)
    remaining = target_total - sum(base)
    # 比率配分（残り分）
    extra_h = round(remaining * 0.20)
    extra_o = round(remaining * 0.30)
    extra_a = round(remaining * 0.35)
    extra_oo = remaining - extra_h - extra_o - extra_a
    return (
        base[0] + max(0, extra_h),
        base[1] + max(0, extra_o),
        base[2] + max(0, extra_a),
        base[3] + max(0, extra_oo),
    )


def build_candidate_bets(
    input_data: RaceInput,
    scores: list[RiderScore],
    *,
    gami_inflation: float = 0.0,
    target_total: Optional[int] = None,
) -> dict[str, list[BetRecommendation]]:
    """スコアとオッズから 本線/押さえ/穴/大穴 の買い目候補を構築する。

    LLMが利用できないときの決定論的フォールバックも兼ねる。

    Args:
        target_total: 目標合計買い目点数。指定すると MAX/HARD/TARGET が動的に
            計算され、合計を target_total 程度に絞る/広げる。
            None なら既定 (合計 ~13-20点) を使う。
    """
    is_girls = input_data.race.resolved_is_girls()
    is_rookie = input_data.race.resolved_is_rookie()
    # 新人戦・個人戦扱い: ライン情報があっても「番手/本命ライン/3番手」用語を避ける
    # use_line_logic も無効化してスコア優先 + 中穴重視に
    pos_map = build_line_position_map(input_data.lines) if not (is_girls or is_rookie) else {}
    ordered = sorted(scores, key=lambda x: x.total(), reverse=True)
    by_car = {s.car_no: s for s in scores}

    if len(ordered) < 3:
        return {"本線": [], "押さえ": [], "穴": [], "大穴": []}

    top1, top2, top3 = ordered[0], ordered[1], ordered[2]

    # 「本命ライン番手/3番手」「別線番手」「単騎・自在」のグルーピング
    bessen_bantan: list[int] = []
    same_line_third: list[int] = []
    tanki_jizai: list[int] = []
    if not is_girls:
        top1_line = pos_map.get(top1.car_no)
        for car, pos in pos_map.items():
            if pos.is_bantan and (
                not top1_line or pos.line_name != top1_line.line_name
            ):
                bessen_bantan.append(car)
            if pos.is_third and top1_line and pos.line_name == top1_line.line_name:
                same_line_third.append(car)
            if pos.is_tanki:
                tanki_jizai.append(car)

    # ---- 本命ライン情報の特定（仕様: 本命ライン優先で本線を作る）----
    # 通常は top1 の所属ライン。
    # 「市場注目ライン」（3連単上位人気で同一ラインが頻出）を常に検出する。
    # 用途は2段階:
    #   (1) 圧倒的支持の1本: 本命ライン上書き（数値不足モード時のみ）
    #   (2) 複数（拮抗含む）: 押さえ強化、本線への混合形追加
    main_leader_car: Optional[int] = None
    main_second_car: Optional[int] = None
    main_third_car: Optional[int] = None
    market_focused: Optional[dict] = None
    market_focused_lines: list[dict] = []
    # 新人戦・ガールズではライン情報を使わない
    if not (is_girls or is_rookie):
        market_focused = _detect_market_focused_line(input_data)
        market_focused_lines = _detect_market_focused_lines(input_data)

    # 本命ライン特定: 数値不足モード + 市場注目ライン → 本命を上書き
    # それ以外は top1 の所属ラインを本命に
    if market_focused is not None and detect_score_data_insufficient(input_data):
        main_leader_car = market_focused.get("leader")
        main_second_car = market_focused.get("second")
        main_third_car = market_focused.get("third")
    elif not (is_girls or is_rookie):
        top1_pos = pos_map.get(top1.car_no)
        if top1_pos is not None and not top1_pos.is_tanki:
            # top1 の所属ラインを本命ラインにする（先頭でなくてもOK）
            main_line_name = top1_pos.line_name
            members = sorted(
                [
                    (car, pos)
                    for car, pos in pos_map.items()
                    if pos.line_name == main_line_name
                ],
                key=lambda kv: kv[1].index,
            )
            if members:
                main_leader_car = members[0][0]
            if len(members) >= 2:
                main_second_car = members[1][0]
            if len(members) >= 3:
                main_third_car = members[2][0]

    # 本命ライン優先モードを使うか判定:
    #  - ガールズ・top1単騎・本命ライン2人未満なら使わない（スコア優先にフォールバック）
    use_line_logic = (
        not is_girls
        and main_leader_car is not None
        and main_second_car is not None
    )

    # 本線生成で中心に使う車番（本命ライン優先 / スコア優先）
    main_line_has_third = False
    if use_line_logic:
        ll_car = main_leader_car
        sec_car = main_second_car
        # 本命ライン3番手が居なければ「別線スコア上位」を補充
        if main_third_car is not None:
            thr_car = main_third_car
            main_line_has_third = True
        else:
            # ordered の中から本命ライン外（ll_car, sec_car 以外）で最高スコアを取る
            thr_car = next(
                (s.car_no for s in ordered if s.car_no not in (ll_car, sec_car)),
                top3.car_no,
            )
    else:
        ll_car = top1.car_no
        sec_car = top2.car_no
        thr_car = top3.car_no

    honsen: list[BetRecommendation] = []
    osae: list[BetRecommendation] = []
    ana: list[BetRecommendation] = []
    ooana: list[BetRecommendation] = []

    def _is_valid_combo(combo: str) -> bool:
        """3連単として有効か検証。'=' を含む or-表記は許可、純粋な重複車番は不可。"""
        if "=" in combo:
            return True  # 1-2=3 などは正当（2着3着の入れ替え表現）
        parts = combo.split("-")
        if len(parts) != 3:
            return False
        try:
            ints = [int(p) for p in parts]
        except ValueError:
            return False
        return len(set(ints)) == 3 and all(1 <= n <= 9 for n in ints)

    def _push(
        bucket: list[BetRecommendation],
        combo: str,
        reason: str,
        *,
        force: bool = False,
    ) -> None:
        """買い目を bucket に追加する。

        force=True のときは上限チェックをスキップする（仕様の必須形用）。
        """
        if not _is_valid_combo(combo):
            return  # 車番重複や不正フォーマットは弾く
        # 全カテゴリで既存の combination を検索（カテゴリ間で重複しないことを優先）
        for buc in (honsen, osae, ana, ooana):
            for b in buc:
                if b.combination == combo:
                    # 同一買い目は1回だけ提示。ただし複数根拠が出た場合は reason に追記して
                    # 「強風補正 + 直近トレンド」のように複合理由を可視化する。
                    if reason and reason not in b.reason:
                        b.reason = f"{b.reason} ＋ {reason}"
                    return
        # カテゴリ上限チェック（必須形追加が膨らみすぎない安全装置）
        # force=True なら通常上限は超えてOKだが、絶対上限（HARD）は超えない
        if not force:
            if bucket is honsen and len(honsen) >= MAX_HONSEN:
                return
            if bucket is osae and len(osae) >= MAX_OSAE:
                return
            if bucket is ana and len(ana) >= MAX_ANA:
                return
            if bucket is ooana and len(ooana) >= MAX_OOANA:
                return
        else:
            # 絶対上限（force でも超えない）
            # target_total 指定時は MAX 比例、未指定時は既定値（後方互換）
            if target_total is not None:
                HARD_HONSEN = MAX_HONSEN + 1
                HARD_OSAE = MAX_OSAE + 4
                HARD_ANA = MAX_ANA + 4
                HARD_OOANA = MAX_OOANA + 1
            else:
                HARD_HONSEN, HARD_OSAE, HARD_ANA, HARD_OOANA = 5, 9, 10, 5
            # ガールズは本線も最大3点に抑える
            if is_girls:
                HARD_HONSEN = 3
            if bucket is honsen and len(honsen) >= HARD_HONSEN:
                return
            if bucket is osae and len(osae) >= HARD_OSAE:
                return
            if bucket is ana and len(ana) >= HARD_ANA:
                return
            if bucket is ooana and len(ooana) >= HARD_OOANA:
                return
        odds = _odds_for(input_data.odds, combo)
        gami = 0.0
        if odds is not None and odds < 8.0:
            gami = 0.8
        elif odds is not None and odds < 15.0:
            gami = 0.4
        bucket.append(
            BetRecommendation(
                category=bucket_label(bucket),  # 後で書き換える
                bet_type="3連単",
                combination=combo,
                reason=reason + (f"（オッズ{odds:.1f}）" if odds else ""),
                gami_risk=gami,
            )
        )

    def _push_required(bucket, combo, reason):
        """仕様の必須形用ラッパー: 上限を無視して push。"""
        _push(bucket, combo, reason, force=True)

    def bucket_label(bucket: list[BetRecommendation]):
        # placeholder; we set category after pushing via finalize
        return "本線"

    # 目標件数（一律拡大）。'=' のような or-表記は使わず、
    # ストレート3連単で重複なく N 点を提示する。
    # target_total が指定されていれば配分計算、なければ既定値（後方互換）
    if target_total is not None:
        TARGET_HONSEN, TARGET_OSAE, TARGET_ANA, TARGET_OOANA = (
            compute_bet_distribution(target_total)
        )
        # 上限は TARGET + 余裕
        MAX_HONSEN = TARGET_HONSEN + 1
        MAX_OSAE = TARGET_OSAE + 2
        MAX_ANA = TARGET_ANA + 1
        MAX_OOANA = TARGET_OOANA + 1
    else:
        TARGET_HONSEN, TARGET_OSAE, TARGET_ANA, TARGET_OOANA = 3, 3, 4, 3
        MAX_HONSEN, MAX_OSAE, MAX_ANA, MAX_OOANA = 4, 6, 6, 4
    # 新人戦・ガールズでは穴/大穴を抑制（自動補充の過剰を防ぐ）
    if is_rookie or is_girls:
        TARGET_ANA = min(TARGET_ANA, 3)
        TARGET_OOANA = min(TARGET_OOANA, 2)
        MAX_ANA = min(MAX_ANA, 4)
        MAX_OOANA = min(MAX_OOANA, 3)
    # ガールズは本線も最大3点に抑える（市場人気と内部スコアの混在を避ける）
    if is_girls:
        TARGET_HONSEN = min(TARGET_HONSEN, 3)
        MAX_HONSEN = min(MAX_HONSEN, 3)

    # ---- 本線：本命ライン優先（仕様3,4,6 / ユーザー指示「ハードガード」）-
    if use_line_logic:
        # 本命ライン3形は force=True で push し、上限を超えても必ず本線に入る。
        # 本命ライン3番手が居る場合と居ない場合（2車ライン）で reason を変える
        # 市場注目ライン採用時は reason を明示
        _market_reason_suffix = (
            "・市場注目ライン採用" if market_focused else ""
        )
        if main_line_has_third:
            # 3車ライン: 本命ライン先頭-番手-3番手 系
            # 拮抗市場注目があれば、本命ライン3形のうち「先頭-3番手-番手」(2-3着入替)
            # を本線から外して、混合形 (本命+市場別線) を優先する
            _is_split_local = (
                market_focused is None and len(market_focused_lines) >= 1
            )
            _push(honsen, f"{ll_car}-{sec_car}-{thr_car}",
                  f"本命ライン: 先頭-番手-3番手（仕様準拠の本線軸）{_market_reason_suffix}",
                  force=True)
            if not _is_split_local:
                # 拮抗が無ければ通常通り3形すべて
                _push(honsen, f"{ll_car}-{thr_car}-{sec_car}",
                      "本命ライン: 先頭-3番手-番手（2-3着入替）",
                      force=True)
            _push(honsen, f"{sec_car}-{ll_car}-{thr_car}",
                  "本命ライン: 番手頭-先頭-3番手（番手差し）",
                  force=True)
            # 拮抗あり: 「本命+市場別線3着」混合形を本線に
            # 3着に置く別線車は、market_focused_lines[0] の中で市場人気高い方を選ぶ
            # （3連単上位人気で多く出る方を3着に → 4-1-2 形）
            if _is_split_local:
                for ml in market_focused_lines[:1]:
                    _m_lead = ml.get("leader")
                    _m_sec = ml.get("second")
                    # ll_car と同じラインの leader はスキップ
                    if _m_lead is None or _m_lead == ll_car:
                        continue
                    # 市場上位での3着出現を集計して、頻出する方を3着に
                    _better_third = _pick_market_top_third(
                        input_data, ll_car, sec_car, _m_lead, _m_sec,
                    )
                    if _better_third is None:
                        _better_third = _m_sec or _m_lead
                    _push(honsen, f"{ll_car}-{sec_car}-{_better_third}",
                          f"本命ライン+市場注目別線3着: {ll_car}-{sec_car}-{_better_third}",
                          force=True)
                    _push(honsen, f"{sec_car}-{ll_car}-{_better_third}",
                          f"本命ライン番手頭+市場注目別線3着: {sec_car}-{ll_car}-{_better_third}",
                          force=True)
        else:
            # 2車ライン: 3着は別線スコア上位（thr_car）。reason を区別。
            _push(honsen, f"{ll_car}-{sec_car}-{thr_car}",
                  f"本命ライン2車: 先頭-番手-別線スコア上位({thr_car})",
                  force=True)
            _push(honsen, f"{sec_car}-{ll_car}-{thr_car}",
                  f"本命ライン2車: 番手頭-先頭-別線スコア上位({thr_car})",
                  force=True)
            # ll_car-thr_car-sec_car（2-3着入替の派生）も追加
            _push(honsen, f"{ll_car}-{thr_car}-{sec_car}",
                  f"本命ライン2車: 先頭-別線({thr_car})-番手の2-3着入替",
                  force=True)

        # 別線市場注目ラインが拮抗している場合、本線にも「本命ライン+別線3着」を
        # 混合形で追加（4-1-2 / 1-4-2 系）。本命1本に寄せ過ぎを防ぐ。
        # 拮抗判定: market_focused が None なのに market_focused_lines が複数 ≒ 拮抗
        _is_split = (
            market_focused is None
            and len(market_focused_lines) >= 1
        )
        if _is_split:
            for ml in market_focused_lines[:1]:  # 拮抗時は1本のみ混合 (4形 force)
                _m_lead = ml.get("leader")
                _m_sec = ml.get("second")
                if _m_lead is None or _m_lead == ll_car:
                    continue
                # 4-1-2 (本命先頭-本命番手-市場別線leader)
                _push(honsen, f"{ll_car}-{sec_car}-{_m_lead}",
                      f"本命ライン+市場注目別線3着: {ll_car}-{sec_car}-{_m_lead}",
                      force=True)
                # 1-4-2 (本命番手頭-本命先頭-市場別線leader)
                _push(honsen, f"{sec_car}-{ll_car}-{_m_lead}",
                      f"本命ライン番手頭+市場注目別線3着: {sec_car}-{ll_car}-{_m_lead}",
                      force=True)
    else:
        # ガールズ / 新人戦 / 本命ライン無し / 単騎top1: スコア優先
        # 新人戦・個人戦扱いでは「番手/本命ライン」用語を使わない
        is_individual = is_girls or is_rookie
        _suffix = "（新人戦・個人戦）" if is_rookie else ""

        # 市場注目ペア（ガールズ/個人戦）: スコア優先より先に本線に入れる
        # 内部スコアと市場が乖離する場合、市場側を本線1点目に確実に入れる
        market_pair_added = 0
        if is_individual:
            pair = _detect_market_focused_pair_no_lines(input_data)
            if pair:
                _ca, _cb = pair
                tanshou_sorted = sorted(
                    [o for o in input_data.odds
                     if o.bet_type == "3連単" and o.odds is not None],
                    key=lambda o: o.odds,
                )
                for o in tanshou_sorted[:5]:
                    try:
                        cars = [int(c) for c in o.combination.split("-")]
                    except ValueError:
                        continue
                    if _ca in cars and _cb in cars:
                        _push(
                            honsen, o.combination,
                            f"市場注目ペア({_ca},{_cb}): 3連単人気上位({o.odds:.1f}倍){_suffix}",
                            force=True,
                        )
                        market_pair_added += 1
                        if market_pair_added >= 2:
                            break

        _push(
            honsen, f"{top1.car_no}-{top2.car_no}-{top3.car_no}",
            f"スコア上位3名の素直な並び{_suffix}",
        )
        _push(
            honsen, f"{top1.car_no}-{top3.car_no}-{top2.car_no}",
            f"上位2-3着の入替を想定{_suffix}",
        )
        if len(ordered) >= 4:
            _push(
                honsen,
                f"{top1.car_no}-{top2.car_no}-{ordered[3].car_no}",
                f"4位評価選手の3着差し込みまで本線でカバー{_suffix}",
            )
        # 強風時 (5m/s+) かつ新人戦/個人戦: 4番手評価の頭/2着候補を押さえに force_push
        # 中団確保・追走有利になる強風で、4位評価が頭差し・2着上がりも狙える
        weather_local = input_data.weather
        wind_strong = (
            weather_local is not None
            and weather_local.wind_speed_mps is not None
            and weather_local.wind_speed_mps >= 5.0
        )
        if is_individual and wind_strong and len(ordered) >= 4:
            _t4 = ordered[3]
            _push(
                osae, f"{_t4.car_no}-{top1.car_no}-{top3.car_no}",
                f"強風時の4番手評価頭差し: {_t4.car_no}-{top1.car_no}-{top3.car_no}{_suffix}",
                force=True,
            )
            _push(
                osae, f"{top1.car_no}-{_t4.car_no}-{top2.car_no}",
                f"強風時の4番手2着上がり: {top1.car_no}-{_t4.car_no}-{top2.car_no}{_suffix}",
                force=True,
            )
            _push(
                osae, f"{_t4.car_no}-{top1.car_no}-{top2.car_no}",
                f"強風時の4番手頭+本命2着: {_t4.car_no}-{top1.car_no}-{top2.car_no}{_suffix}",
                force=True,
            )

    # ---- 押さえ：本命ライン関連 + 別線番手割り込み ---------------------
    if use_line_logic:
        # 本命ライン2車（third 不在）の場合、別線高スコアラインを押さえ上位に
        # （ユーザー指示: スコア最上位が line_second でも別ラインを残す）
        if not main_line_has_third:
            _separate_lines_top = _resolve_separate_lines(input_data, scores)
            if _separate_lines_top:
                sl0 = _separate_lines_top[0]
                _s_lead = sl0.get("leader")
                _s_sec = sl0.get("second")
                if _s_lead and _s_sec:
                    # 別線高スコアライン×4形を押さえに force=True で push
                    # （本命ラインだけに寄せず、別線ラインの可能性も残す）
                    _push(osae, f"{_s_lead}-{_s_sec}-{ll_car}",
                          f"本命ライン2車: 別線{_s_lead}-{_s_sec}-本命先頭{ll_car}の押さえ",
                          force=True)
                    _push(osae, f"{_s_sec}-{_s_lead}-{ll_car}",
                          f"本命ライン2車: 別線番手頭{_s_sec}-{_s_lead}-本命先頭{ll_car}の押さえ",
                          force=True)
                    _push(osae, f"{_s_lead}-{_s_sec}-{sec_car}",
                          f"本命ライン2車: 別線{_s_lead}-{_s_sec}-本命番手{sec_car}の押さえ",
                          force=True)
                    _push(osae, f"{_s_sec}-{_s_lead}-{sec_car}",
                          f"本命ライン2車: 別線番手頭{_s_sec}-{_s_lead}-本命番手{sec_car}の押さえ",
                          force=True)

        # 市場注目ライン（圧倒的支持 or 拮抗）を統合して押さえ強化
        # `market_focused`: 圧倒的支持の1本
        # `market_focused_lines`: 拮抗含む全候補（上位3本まで）
        _all_market_lines: list[dict] = []
        if market_focused is not None:
            _all_market_lines.append(market_focused)
        for _ml in market_focused_lines:
            if _ml.get("leader") not in (
                m.get("leader") for m in _all_market_lines
            ):
                _all_market_lines.append(_ml)

        _seen_pushed: set[int] = set()
        for _ml in _all_market_lines[:2]:  # 上位2本まで押さえ強化
            _m_lead = _ml.get("leader")
            _m_sec = _ml.get("second")
            # 本命ラインと同じ leader はスキップ（本命ラインは本線軸で扱い済み）
            if _m_lead is None or _m_lead == ll_car or _m_lead in _seen_pushed:
                continue
            # 別線判定: leader が本命ライン外
            _is_separate = _m_lead not in (sec_car, thr_car)
            if not _is_separate or not _m_sec:
                continue
            _seen_pushed.add(_m_lead)
            # 市場注目別線 leader-second + 本命ライン車番
            _push(osae, f"{_m_lead}-{_m_sec}-{ll_car}",
                  f"市場注目別線: {_m_lead}-{_m_sec}-本命先頭{ll_car} (3連単上位人気)",
                  force=True)
            _push(osae, f"{_m_sec}-{_m_lead}-{ll_car}",
                  f"市場注目別線: 番手頭{_m_sec}-{_m_lead}-本命先頭{ll_car}",
                  force=True)
            _push(osae, f"{_m_lead}-{_m_sec}-{sec_car}",
                  f"市場注目別線: {_m_lead}-{_m_sec}-本命番手{sec_car}",
                  force=True)
            _push(osae, f"{_m_sec}-{_m_lead}-{sec_car}",
                  f"市場注目別線: 番手頭{_m_sec}-{_m_lead}-本命番手{sec_car}",
                  force=True)

        # 本命ライン先頭-番手-別線番手3着（割り込み形）
        for car in bessen_bantan[:1]:
            _push(
                osae, f"{ll_car}-{sec_car}-{car}",
                "本命ライン: 先頭-番手-別線番手3着の押さえ",
            )
        # 別線番手2着割り込み形
        for car in bessen_bantan[:1]:
            _push(
                osae, f"{ll_car}-{car}-{sec_car}",
                "本命ライン: 先頭-別線番手2着-本命番手3着の押さえ",
            )
        # スコア上位3名フォーメーション (本命ライン外を含む場合は押さえに)
        score_top_combo = f"{top1.car_no}-{top2.car_no}-{top3.car_no}"
        main_line_set = {ll_car, sec_car, thr_car}
        score_top_set = {top1.car_no, top2.car_no, top3.car_no}
        if not score_top_set.issubset(main_line_set):
            # スコア上位が本命ライン外を含む → ズレ目として押さえ
            _push(
                osae, score_top_combo,
                "スコア上位3名フォーメーション (本命ライン外を含むためズレ目扱い)",
            )
    else:
        # 個人戦扱い（ガールズ/新人戦/本命ライン無し）: 番手用語を抑制
        _suffix = "（新人戦・個人戦）" if is_rookie else ""
        # 新人戦・ガールズでは「番手」用語を使わず「2位頭」と表現
        _bantan_label = (
            "2位頭の捲られ展開を想定した押さえ"
            if is_girls or is_rookie
            else "番手頭の捲られ展開を想定した押さえ"
        )
        _bantan_4th_label = (
            "2位頭・4位3着の押さえ"
            if is_girls or is_rookie
            else "番手頭・4位3着の押さえ"
        )
        _push(
            osae, f"{top2.car_no}-{top1.car_no}-{top3.car_no}",
            f"{_bantan_label}{_suffix}",
        )
        if len(ordered) >= 4:
            _push(
                osae, f"{top1.car_no}-{ordered[3].car_no}-{top2.car_no}",
                f"本命頭・中位2着の捲り展開を想定{_suffix}",
            )
            _push(
                osae, f"{top2.car_no}-{top1.car_no}-{ordered[3].car_no}",
                f"{_bantan_4th_label}{_suffix}",
            )
    # 本命ライン3番手の用語は新人戦・ガールズでは抑制（pos_map=空なので same_line_third も空）
    for car in same_line_third[:1]:
        _push(
            osae, f"{ll_car}-{sec_car}-{car}",
            "本命ライン3番手を3着に固定した押さえ",
        )

    # ---- 穴：別線番手頭・3番手頭・単騎/自在絡み・4位頭 ------------------
    for car in bessen_bantan[:1]:
        _push(
            ana,
            f"{car}-{ll_car}-{sec_car}",
            "別線番手の頭を狙う中穴",
        )
        _push(
            ana,
            f"{ll_car}-{car}-{thr_car}",
            "別線番手の2着上がりを狙う",
        )
    for car in same_line_third[:1]:
        _push(
            ana,
            f"{car}-{ll_car}-{sec_car}",
            "本命ライン3番手の伸びを狙う中穴",
        )
    for car in tanki_jizai[:1]:
        s = by_car.get(car)
        if s and s.total() > 0:
            _push(
                ana,
                f"{ll_car}-{sec_car}-{car}",
                "単騎/自在の3着絡みを狙う",
            )
    # 4位選手の頭差しまで広げた中穴
    if len(ordered) >= 4:
        _push(
            ana,
            f"{ordered[3].car_no}-{ll_car}-{thr_car}",
            "4位評価の頭・本命3着の中穴",
        )

    # ---- 大穴：低評価頭、波乱形 ----------------------------------------
    if len(ordered) >= 4:
        wild = ordered[3]
        _push(
            ooana,
            f"{wild.car_no}-{ll_car}-{sec_car}",
            "4番手評価の頭差しまで広げた大穴",
        )
    for car in bessen_bantan[:1]:
        _push(
            ooana,
            f"{car}-{sec_car}-{ll_car}",
            "別線番手頭・本命番手2着・本命自力3着の波乱形",
        )
    if len(ordered) >= 5:
        _push(
            ooana,
            f"{ordered[4].car_no}-{ll_car}-{sec_car}",
            "5位評価の頭を狙う大穴",
        )

    # ---- 仕様12章「基本候補」の漏れ補完（D-4）-------------------------
    # second - third - line_leader : 番手頭-3番手2着-先行3着の波乱
    if not is_girls:
        # roles から実際の line_leader/second/third を取得して使う
        _roles = resolve_rider_roles(input_data, scores)
        _ll = _find_first_role(_roles, "line_leader")
        _sec = _find_first_role(_roles, "second")
        _thr = _find_first_role(_roles, "third")
        _sep_l = _find_first_role(_roles, "separate_leader")
        _sep_s = _find_first_role(_roles, "separate_second")
        # solo または jizai（仕様の「単騎」「自在型」の代表車）を1台拾う
        _solo_or_jizai = (
            _find_first_role(_roles, "solo") or _find_first_role(_roles, "jizai")
        )

        # 仕様12の基本候補も必須扱い（force=True で上限を無視）
        if _sec and _thr and _ll:
            _push_required(ana, f"{_sec}-{_thr}-{_ll}",
                  "仕様12: 番手-3番手-先行の崩れ形")
        if _sep_l and _sep_s and _ll:
            _push_required(ana, f"{_sep_l}-{_sep_s}-{_ll}",
                  "仕様12: 別線自力-別線番手-本命の別線決着")
        if _solo_or_jizai and _ll and _sec:
            _push_required(ooana, f"{_solo_or_jizai}-{_ll}-{_sec}",
                  "仕様12: 単騎頭-本命-本命番手の波乱形")

    # ---- 天候・トレンド別の必須候補追加（仕様5/6/8/12章）-----------------
    # 必須形は上限を無視して push する（_push_required 経由）
    _add_weather_and_trend_candidates(
        input_data=input_data,
        scores=scores,
        honsen=honsen,
        osae=osae,
        ana=ana,
        ooana=ooana,
        push_fn=_push_required,
    )

    # ---- ガールズ専用候補（仕様10章）----------------------------------
    if is_girls:
        _add_girls_candidate_bets(
            ordered=ordered,
            input_data=input_data,
            honsen=honsen,
            osae=osae,
            ana=ana,
            ooana=ooana,
            push_fn=_push_required,
        )

    # ---- 目標件数に満たない場合のフォールバック（ガールズ/単騎多数で発動） --
    def _all_existing_combos() -> set[str]:
        """全カテゴリの combination を集合で返す（カテゴリ間重複防止）。"""
        out: set[str] = set()
        for buc in (honsen, osae, ana, ooana):
            for b in buc:
                out.add(b.combination)
        return out

    def _pad(bucket: list[BetRecommendation], target: int, label: str) -> None:
        """各カテゴリで target 件に達していないとき、上位スコア順の組み合わせで補充。

        他カテゴリで既に出ている combination は採用しない（カテゴリ間重複防止）。
        """
        if len(bucket) >= target:
            return
        cars_top = [s.car_no for s in ordered[: min(7, len(ordered))]]

        if label in ("本線", "押さえ"):
            heads = [top1.car_no, top2.car_no]
            seconds = [top2.car_no, top3.car_no, top1.car_no]
            thirds = cars_top[:5]
        else:  # 穴 / 大穴
            heads = cars_top[2:6] if len(cars_top) >= 6 else cars_top[1:5]
            seconds = cars_top[:4]
            thirds = cars_top[:5]
        reason = {
            "本線": "上位スコア組み合わせ（自動補充）",
            "押さえ": "上位スコア組み合わせ（自動補充）",
            "穴": "下位頭+上位2-3着の組み合わせ（自動補充）",
            "大穴": "中位頭+上位2-3着の組み合わせ（自動補充）",
        }[label]
        existing = _all_existing_combos()
        for h in heads:
            if len(bucket) >= target:
                return
            for s2 in seconds:
                if h == s2:
                    continue
                for s3 in thirds:
                    if s3 in (h, s2):
                        continue
                    combo = f"{h}-{s2}-{s3}"
                    if combo in existing:
                        continue
                    _push(bucket, combo, reason)
                    existing.add(combo)
                    if len(bucket) >= target:
                        return

    _pad(honsen, TARGET_HONSEN, "本線")
    _pad(osae, TARGET_OSAE, "押さえ")
    _pad(ana, TARGET_ANA, "穴")
    _pad(ooana, TARGET_OOANA, "大穴")

    # finalize category labels
    def finalize(bucket: list[BetRecommendation], label: str):
        for b in bucket:
            b.category = label  # type: ignore[assignment]

    finalize(honsen, "本線")
    finalize(osae, "押さえ")
    finalize(ana, "穴")
    finalize(ooana, "大穴")

    # 反省「穴を広げすぎてガミリスク増加」が複数件あれば、穴/大穴の gami_risk を底上げ
    if gami_inflation > 0:
        for bucket in (ana, ooana):
            for b in bucket:
                b.gami_risk = min(b.gami_risk + gami_inflation, 1.5)

    # ---- 仕様11章「ガミリスク高」追加判定（D-3）-----------------------
    # 3連複が安い「組み合わせ車番セット」だけに gami_risk +0.2 を適用する。
    # 例: 1=2=3 が安い → 1,2,3 を含む3連単のみガミ警戒
    # 4-6-3 のように 3連複安と無関係な買い目には波及させない（一律加算しない）。
    cheap_trio_sets: list[frozenset[int]] = []
    for o in input_data.odds:
        if o.bet_type == "3連複" and o.odds is not None and o.odds < 5.0:
            try:
                cars = {int(c) for c in o.combination.replace("=", "-").split("-")}
            except ValueError:
                continue
            if len(cars) == 3:
                cheap_trio_sets.append(frozenset(cars))

    def _bet_matches_cheap_trio(combo: str) -> bool:
        try:
            cars = {int(c) for c in combo.split("-")}
        except ValueError:
            return False
        if len(cars) != 3:
            return False
        return any(cars == s for s in cheap_trio_sets)

    if cheap_trio_sets:
        for b in honsen:
            if _bet_matches_cheap_trio(b.combination):
                b.gami_risk = min(b.gami_risk + 0.2, 1.5)
                if "3連複安" not in b.reason:
                    b.reason = (
                        f"{b.reason} ＋ 3連複安: 該当組み合わせでガミ警戒"
                    )
        # 穴・大穴も該当組み合わせのみ「点数注意」
        for bucket in (ana, ooana):
            for b in bucket:
                if _bet_matches_cheap_trio(b.combination):
                    if "3連複安" not in b.reason:
                        b.reason = f"{b.reason} ＋ 3連複安・点数を絞る"
    # 本線を広げすぎ（3件以上、全部 gami_risk >= 0.6） → 「広げすぎ」警告
    high_gami_in_honsen = sum(1 for b in honsen if b.gami_risk >= 0.6)
    if len(honsen) >= 3 and high_gami_in_honsen >= len(honsen):
        for b in osae:
            b.gami_risk = max(b.gami_risk, 0.4)
            if "本線広げすぎ" not in b.reason:
                b.reason = f"{b.reason} ＋ 本線が全て安値 → 押さえに比重を移す"

    # ---- 数値不足モード: 3連単/3連複人気を本線・押さえに強制反映 -------
    # 競走得点・B数・決まり手が全部 0 のとき、score 計算は信頼性が低い。
    # 市場人気 (3連単上位 / 3連複上位) を強めに参照して本線・押さえに反映する。
    if detect_score_data_insufficient(input_data):
        # 3連単オッズ降順で上位3点を本線に強制追加
        trifecta = [
            o for o in input_data.odds
            if o.bet_type == "3連単" and o.odds is not None
        ]
        trifecta.sort(key=lambda o: o.odds)
        for rank, o in enumerate(trifecta[:3], start=1):
            _push(
                honsen, o.combination,
                f"数値不足モード: 3連単人気{rank}位({o.odds:.1f}倍)を本線採用",
                force=True,
            )
        # 3連複上位1〜2件の組み合わせから3連単派生を押さえに
        trio = [
            o for o in input_data.odds
            if o.bet_type == "3連複" and o.odds is not None
        ]
        trio.sort(key=lambda o: o.odds)
        for rank, o in enumerate(trio[:2], start=1):
            # "1=3=4" のような形式 → cars を取り出して3連単 6パターン
            try:
                cars = [int(c) for c in o.combination.replace("=", "-").split("-")]
            except ValueError:
                continue
            if len(cars) != 3:
                continue
            # 3連単 6 順列のうち、人気1位の3連単と被らない順列を 2 点だけ採用
            from itertools import permutations
            existing = {
                b.combination
                for b in honsen + osae
            }
            added = 0
            for perm in permutations(cars):
                combo = "-".join(str(c) for c in perm)
                if combo in existing:
                    continue
                _push(
                    osae, combo,
                    f"数値不足モード: 3連複人気{rank}位({o.odds:.1f}倍)から派生",
                    force=True,
                )
                added += 1
                if added >= 2:
                    break

    # 新人戦/個人戦の場合、reason 内のライン用語をサニタイズ
    if is_rookie:
        for bucket in (honsen, osae, ana, ooana):
            for b in bucket:
                b.reason = _sanitize_reason_for_rookie(b.reason)

    # 本命ライン戦のみ適用される後処理（ガールズ/新人戦/数値不足は除く）
    if not (is_girls or is_rookie):
        trend_sig = analyze_recent(input_data.recent_results)
        _demote_third_sec_up_from_honsen(
            honsen, osae,
            main_third_car=main_third_car,
            trend_strong=trend_sig.is_third_sec_up,
        )
        _promote_bessen_bantan_head_to_osae(
            osae, ana,
            bessen_bantan_cars=bessen_bantan,
            trend_strong=(
                trend_sig.is_bessen_involved
                or trend_sig.bessen_bantan_count >= 1
            ),
        )

    return {"本線": honsen, "押さえ": osae, "穴": ana, "大穴": ooana}


def _third_sec_up_head(combo: str, *, third_car: Optional[int]) -> bool:
    """3連単 combo の 2着位置が本命3番手か。"""
    if third_car is None or "-" not in combo:
        return False
    parts = combo.split("-")
    if len(parts) != 3:
        return False
    try:
        return int(parts[1]) == third_car
    except (ValueError, TypeError):
        return False


def _demote_third_sec_up_from_honsen(
    honsen: list[BetRecommendation],
    osae: list[BetRecommendation],
    *,
    main_third_car: Optional[int],
    trend_strong: bool,
) -> int:
    """3番手2着上がりの本線買い目を押さえ上位に移動する。

    直近傾向が強い (trend_strong=True) 場合は本線に残す。
    本命ライン戦のみ呼ばれる前提。
    """
    if main_third_car is None or trend_strong:
        return 0
    moved = 0
    kept: list[BetRecommendation] = []
    existing_osae = {b.combination for b in osae}
    for b in honsen:
        if b.bet_type == "3連単" and _third_sec_up_head(
            b.combination, third_car=main_third_car
        ):
            if b.combination not in existing_osae:
                # 押さえ「上位」= 先頭に挿入
                b.category = "押さえ"
                osae.insert(moved, b)
                existing_osae.add(b.combination)
                moved += 1
        else:
            kept.append(b)
    honsen[:] = kept
    return moved


def _promote_bessen_bantan_head_to_osae(
    osae: list[BetRecommendation],
    ana: list[BetRecommendation],
    *,
    bessen_bantan_cars: list[int],
    trend_strong: bool,
) -> int:
    """穴の「妙味あり別線番手頭」を押さえに昇格する。

    trend_strong (直近で別線番手好走あり) なときのみ昇格。
    """
    if not bessen_bantan_cars or not trend_strong:
        return 0
    moved = 0
    kept: list[BetRecommendation] = []
    existing_osae = {b.combination for b in osae}
    for b in ana:
        is_promotable = (
            b.bet_type == "3連単"
            and b.value_label == "妙味あり"
            and "-" in b.combination
        )
        if is_promotable:
            try:
                head = int(b.combination.split("-")[0])
            except (ValueError, TypeError):
                head = None
            if head is not None and head in bessen_bantan_cars:
                if b.combination not in existing_osae:
                    b.category = "押さえ"
                    osae.append(b)
                    existing_osae.add(b.combination)
                    moved += 1
                continue
        kept.append(b)
    ana[:] = kept
    return moved
