"""結果との突き合わせから反省カテゴリを推定する。

正規化:
- 結果文字列は "5-1-3" や "5=1=3" を許容し、1着/2着/3着の3整数に分解する
- 予想の本線は BetRecommendation.combination から同様に展開する
"""

from __future__ import annotations

from typing import Optional

from .models import (
    Prediction,
    RaceInput,
    Reflection,
    Rider,
)
from .scoring import build_line_position_map


def parse_result(result: str) -> Optional[tuple[int, int, int]]:
    """'5-1-3' / '5=1=3' を (5, 1, 3) に変換。失敗時は None。"""
    if not result:
        return None
    parts = result.replace("=", "-").split("-")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _combination_to_tuple(combo: str) -> Optional[tuple[int, int, int]]:
    return parse_result(combo)


def _combo_matches(predicted: str, actual: tuple[int, int, int]) -> bool:
    """予想買い目が実結果と一致するか。'=' はその位置の入れ替え可。"""
    parts = predicted.split("-")
    if len(parts) != 3:
        return False
    a_str = parts[0].split("=")
    b_str = parts[1].split("=")
    c_str = parts[2].split("=")
    try:
        allowed_1 = {int(x) for x in a_str}
        allowed_2 = {int(x) for x in b_str}
        allowed_3 = {int(x) for x in c_str}
    except ValueError:
        return False
    return (
        actual[0] in allowed_1
        and actual[1] in allowed_2
        and actual[2] in allowed_3
    )


def classify(
    *,
    prediction: Prediction,
    actual_result: str,
    input_data: Optional[RaceInput] = None,
) -> list[str]:
    """結果と予想から反省カテゴリのリストを返す。

    複数該当しうるので list で返す。
    """
    categories: list[str] = []
    actual = parse_result(actual_result)
    if actual is None:
        return ["結果フォーマット不正"]

    honsen = prediction.honsen
    osae = prediction.osae
    ana = prediction.ana
    ooana = prediction.ooana
    all_bets = honsen + osae + ana + ooana

    # ---- 的中 / 部分的中 ----
    hit_honsen = any(_combo_matches(b.combination, actual) for b in honsen)
    hit_any = any(_combo_matches(b.combination, actual) for b in all_bets)
    if hit_honsen:
        categories.append("的中")
    elif hit_any:
        categories.append("買い目にはあったが本線ではなかった")

    # 以降は外しの分析（的中時でも気づきとして残してOKだが、ここでは外し時のみ）
    if hit_honsen:
        return categories

    riders_by_car: dict[int, Rider] = {}
    pos_map = {}
    if input_data is not None:
        riders_by_car = {r.car_no: r for r in input_data.riders}
        if not input_data.race.resolved_is_girls():
            pos_map = build_line_position_map(input_data.lines)
        is_girls = input_data.race.resolved_is_girls()
        weather = input_data.weather
    else:
        is_girls = prediction.is_girls
        weather = None

    win_car, second_car, third_car = actual

    # ---- 本線番手を過信 ----
    if pos_map:
        # 本線(◎)番手の予想車番を取り出す
        marks = prediction.marks
        honmei = marks.get("◎")
        honmei_line = pos_map.get(honmei) if honmei else None
        # 本線番手 = 本命と同じラインの番手
        if honmei_line:
            same_line_bantan = [
                car
                for car, p in pos_map.items()
                if p.line_name == honmei_line.line_name and p.is_bantan
            ]
            in_top3 = {win_car, second_car, third_car}
            if same_line_bantan and not (set(same_line_bantan) & in_top3):
                if any(
                    b.category == "本線" and str(c) in b.combination.split("-")[0]
                    for b in honsen
                    for c in same_line_bantan
                ):
                    categories.append("本線番手を過信")

        # ---- 別線番手を軽視 / 3番手の伸びを軽視 ----
        winner_pos = pos_map.get(win_car)
        second_pos = pos_map.get(second_car)
        third_pos = pos_map.get(third_car)
        # 別線番手が絡んだ
        bessen_bantan_hit = any(
            p is not None and p.is_bantan and (not honmei_line or p.line_name != honmei_line.line_name)
            for p in (winner_pos, second_pos, third_pos)
        )
        if bessen_bantan_hit and not any(
            b for b in all_bets if any(
                pos_map.get(int(x), None) and pos_map[int(x)].is_bantan and (not honmei_line or pos_map[int(x)].line_name != honmei_line.line_name)
                for x in _flatten_combo(b.combination)
            )
        ):
            categories.append("別線番手を軽視")

        # 3番手の伸びを軽視 / 2着上がりを軽視
        third_anywhere = [winner_pos, second_pos, third_pos]
        if any(p is not None and p.is_third for p in third_anywhere):
            # 予想に3番手が含まれていない場合
            covered = any(
                pos_map.get(int(x), None) and pos_map[int(x)].is_third
                for b in all_bets
                for x in _flatten_combo(b.combination)
            )
            if not covered:
                categories.append("3番手の伸びを軽視")
            else:
                # 2着の場合の専用カテゴリ
                if second_pos is not None and second_pos.is_third:
                    if not any(
                        pos_map.get(int(parts[1]) if (parts := b.combination.split("-"))[1].isdigit() else -1, None)
                        and pos_map[int(parts[1])].is_third
                        for b in all_bets
                        if len(b.combination.split("-")) == 3 and b.combination.split("-")[1].isdigit()
                    ):
                        categories.append("3番手の2着上がりを軽視した")

        # 別線番手の2着上がり
        if second_pos is not None and second_pos.is_bantan and honmei_line and second_pos.line_name != honmei_line.line_name:
            if not any(
                _combination_to_tuple(b.combination) and _combination_to_tuple(b.combination)[1] == second_car  # type: ignore[index]
                for b in all_bets
            ):
                categories.append("別線番手の2着上がりを軽視した")

        # 本命ラインの3着を固定しすぎた
        if honmei_line:
            same_line_cars = {
                car for car, p in pos_map.items() if p.line_name == honmei_line.line_name
            }
            third_party_in_3rd = third_car not in same_line_cars
            if third_party_in_3rd:
                # 全本線買いの3着が同ラインのみだった
                honsen_thirds = []
                for b in honsen:
                    t = _combination_to_tuple(b.combination)
                    if t:
                        honsen_thirds.append(t[2])
                if honsen_thirds and all(c in same_line_cars for c in honsen_thirds):
                    categories.append("本線ラインの3着を固定しすぎた")

    # ---- 風補正不足 / 雨補正不足 ----
    if weather is not None:
        if weather.wind_speed_mps >= 5.0:
            # 強風時に番手/3番手/追込が来たのに拾えてないなら風補正不足
            if pos_map:
                hit_pos = [pos_map.get(c) for c in (win_car, second_car, third_car)]
                wind_favored_hit = any(p and (p.is_bantan or p.is_third) for p in hit_pos)
                if wind_favored_hit and not hit_any:
                    categories.append("風補正不足")
        if weather.rain_mm_per_hour >= 1.0:
            if pos_map:
                hit_pos = [pos_map.get(c) for c in (win_car, second_car, third_car)]
                rain_favored_hit = any(p and (p.is_bantan or p.is_third or p.is_head) for p in hit_pos)
                if rain_favored_hit and not hit_any:
                    categories.append("雨補正不足")

    # ---- 本命自力の過信 ----
    if pos_map:
        marks = prediction.marks
        honmei = marks.get("◎")
        if honmei and pos_map.get(honmei) and pos_map[honmei].is_head:
            if win_car != honmei and not hit_any:
                # 本命自力を頭固定にしていたか
                head_fixed_honsen = sum(
                    1 for b in honsen if (t := _combination_to_tuple(b.combination)) and t[0] == honmei
                )
                if head_fixed_honsen >= max(1, len(honsen) // 2):
                    categories.append("本命自力の過信")

    # ---- 穴を広げすぎてガミリスク増加 ----
    if not hit_any:
        total = len(all_bets)
        if total >= 10:
            categories.append("穴を広げすぎてガミリスク増加")

    # ---- ガールズの位置取り評価不足 ----
    if is_girls and not hit_any:
        # 自力寄り選手の好走を拾えていない、などのざっくり判定
        if riders_by_car:
            winner = riders_by_car.get(win_car)
            if winner and ("自力" in winner.style_tags or "追走" in winner.style_tags):
                categories.append("ガールズの位置取り評価不足")

    if not categories:
        categories.append("外れ・主要カテゴリには該当なし（個別メモ推奨）")
    return categories


def _flatten_combo(combo: str) -> list[str]:
    parts = []
    for p in combo.split("-"):
        parts.extend(p.split("="))
    return parts


def build_reflection(
    *,
    prediction: Prediction,
    actual_result: str,
    input_data: Optional[RaceInput] = None,
    note: str = "",
) -> Reflection:
    categories = classify(
        prediction=prediction, actual_result=actual_result, input_data=input_data
    )
    weather = input_data.weather if input_data else None
    class_name = input_data.race.class_name if input_data else None
    return Reflection(
        race_id=prediction.race_id,
        venue=prediction.venue,
        race_no=prediction.race_no,
        is_girls=prediction.is_girls,
        weather_condition=(weather.condition if weather else None),
        wind_speed_mps=(weather.wind_speed_mps if weather else 0.0),
        rain_mm_per_hour=(weather.rain_mm_per_hour if weather else 0.0),
        class_name=class_name,
        predicted_honsen=[b.combination for b in prediction.honsen],
        actual_result=actual_result,
        categories=categories,
        note=note,
    )
