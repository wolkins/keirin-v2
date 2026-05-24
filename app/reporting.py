"""予想・結果・反省ログから成績レポートを集計する。

責務:
- SQLite に保存された predictions / results / reflections を読み取り、
  全体／場別／天候別／風速別／レース種別ごとに集計する
- 改善メモをルールベースで生成する
- 予想ロジックや HTTP には依存しない（storage.py のみに依存）

これは予想支援・検証機能であり、自動投票や購入処理は一切行わない。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import BetRecommendation, Prediction, Reflection
from .reflection import _combo_matches, parse_result, parse_results
from .storage import Storage


# 風速バケットの定義（順序固定）
WIND_BUCKETS = ("0-2m/s", "2-4m/s", "4-6m/s", "6m/s以上", "不明")


def _wind_bucket(wind_speed: Optional[float]) -> str:
    if wind_speed is None:
        return "不明"
    s = float(wind_speed)
    if s < 0:
        return "不明"
    if s < 2:
        return "0-2m/s"
    if s < 4:
        return "2-4m/s"
    if s < 6:
        return "4-6m/s"
    return "6m/s以上"


# 的中分類のキー（仕様準拠）
HitClass = str  # "main_hit"/"backup_hit"/"longshot_hit"/"big_longshot_hit"/"miss"


def classify_hit(prediction: Prediction, actual_result: str) -> HitClass:
    """予想と結果から的中区分を返す。

    優先順位: 本線 → 押さえ → 穴 → 大穴 → miss
    同着対応 (2026-05-24): 複数結果のいずれかにマッチすれば的中扱い。
    """
    parsed_list = parse_results(actual_result)
    if not parsed_list:
        return "miss"
    for category, items in (
        ("main_hit", prediction.honsen),
        ("backup_hit", prediction.osae),
        ("longshot_hit", prediction.ana),
        ("big_longshot_hit", prediction.ooana),
    ):
        if any(
            _combo_matches(b.combination, p)
            for b in items for p in parsed_list
        ):
            return category
    return "miss"


def total_bet_count(prediction: Prediction) -> int:
    return (
        len(prediction.honsen)
        + len(prediction.osae)
        + len(prediction.ana)
        + len(prediction.ooana)
    )


def _high_gami_count(prediction: Prediction, *, threshold: float = 0.6) -> int:
    n = 0
    for bucket in (prediction.honsen, prediction.osae, prediction.ana, prediction.ooana):
        for b in bucket:
            if b.gami_risk >= threshold:
                n += 1
    return n


@dataclass
class BucketStats:
    """1つのバケットの集計結果。"""

    label: str
    total: int = 0
    with_result: int = 0
    main_hit: int = 0
    backup_hit: int = 0
    longshot_hit: int = 0
    big_longshot_hit: int = 0
    miss: int = 0
    listed_but_not_main: int = 0
    bet_count_sum: int = 0
    high_gami_count: int = 0
    categories: Counter = field(default_factory=Counter)

    def record_prediction(self, prediction: Prediction) -> None:
        self.total += 1
        self.bet_count_sum += total_bet_count(prediction)
        self.high_gami_count += _high_gami_count(prediction)

    def record_outcome(self, hit_class: HitClass) -> None:
        self.with_result += 1
        setattr(self, hit_class, getattr(self, hit_class) + 1)
        if hit_class in ("backup_hit", "longshot_hit", "big_longshot_hit"):
            self.listed_but_not_main += 1

    def record_reflection(self, reflection: Reflection) -> None:
        for c in reflection.categories:
            self.categories[c] += 1

    @property
    def hit_rate(self) -> float:
        if self.with_result == 0:
            return 0.0
        hits = self.main_hit + self.backup_hit + self.longshot_hit + self.big_longshot_hit
        return hits / self.with_result

    @property
    def avg_bet_count(self) -> float:
        if self.total == 0:
            return 0.0
        return self.bet_count_sum / self.total

    def top_categories(self, n: int = 10) -> list[tuple[str, int]]:
        return self.categories.most_common(n)

    def to_dict(self, *, top_n: int = 10) -> dict[str, Any]:
        return {
            "label": self.label,
            "total": self.total,
            "with_result": self.with_result,
            "main_hit": self.main_hit,
            "backup_hit": self.backup_hit,
            "longshot_hit": self.longshot_hit,
            "big_longshot_hit": self.big_longshot_hit,
            "listed_but_not_main": self.listed_but_not_main,
            "miss": self.miss,
            "hit_rate": round(self.hit_rate, 4),
            "avg_bet_count": round(self.avg_bet_count, 2),
            "high_gami_count": self.high_gami_count,
            "top_categories": self.top_categories(top_n),
        }


# ---------------------------------------------------------------------------
# データ抽出
# ---------------------------------------------------------------------------


def _prediction_weather(prediction: Prediction) -> tuple[Optional[str], Optional[float], Optional[float]]:
    """Prediction.weather_text から条件抽出は難しいため、reflections の値を使う。

    storage には Reflection が保存されており、その weather_condition/wind_speed_mps を
    集計のキーに使う。Prediction 自体には weather は保存されていない設計のため。
    """
    return (None, None, None)


def _gather_reflections_for(
    storage: Storage, race_id: str
) -> list[Reflection]:
    """race_id 単位で Reflection を引く。"""
    # 既存の list_reflections は venue/weather フィルタのみなので、
    # ここでは race_id で絞り込む簡易クエリを直接書く。
    items: list[Reflection] = []
    import sqlite3 as _sqlite3
    with _sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT payload_json, created_at FROM reflections WHERE race_id = ? ORDER BY id DESC",
            (race_id,),
        ).fetchall()
    for r in rows:
        ref = Reflection.model_validate_json(r["payload_json"])
        try:
            ref.created_at = r["created_at"]
        except (IndexError, KeyError):
            pass
        items.append(ref)
    return items


# ---------------------------------------------------------------------------
# レポート構築
# ---------------------------------------------------------------------------


def _ensure_bucket(d: dict[str, BucketStats], key: str) -> BucketStats:
    if key not in d:
        d[key] = BucketStats(label=key)
    return d[key]


def build_performance_report(
    storage: Storage,
    *,
    venue: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    weather_condition: Optional[str] = None,
    limit_reflections: int = 10,
) -> dict[str, Any]:
    """集計結果を辞書で返す（CLI が text/json にレンダリングする前提）。"""
    predictions = storage.list_predictions(
        venue=venue, from_date=from_date, to_date=to_date
    )
    results = storage.list_results_raw(
        venue=venue, from_date=from_date, to_date=to_date
    )

    summary = BucketStats(label="全体")
    by_venue: dict[str, BucketStats] = {}
    by_weather: dict[str, BucketStats] = {}
    by_wind: dict[str, BucketStats] = {b: BucketStats(label=b) for b in WIND_BUCKETS}
    by_race_class: dict[str, BucketStats] = {
        "girls": BucketStats(label="ガールズ"),
        "regular": BucketStats(label="通常戦"),
    }
    all_categories: Counter = Counter()

    for prediction in predictions:
        reflections = _gather_reflections_for(storage, prediction.race_id)
        # 代表 reflection（最新）から天候キー（無ければ None）
        ref_weather: Optional[str] = None
        ref_wind: Optional[float] = None
        ref_rain: Optional[float] = None
        if reflections:
            head = reflections[0]
            ref_weather = head.weather_condition
            ref_wind = head.wind_speed_mps
            ref_rain = head.rain_mm_per_hour

        # weather_condition フィルタ（指定があれば、reflection の値で絞り込む）
        if weather_condition and ref_weather != weather_condition:
            continue

        # 各バケットに prediction を計上
        summary.record_prediction(prediction)
        venue_bucket = _ensure_bucket(by_venue, prediction.venue or "不明")
        venue_bucket.record_prediction(prediction)

        weather_key = ref_weather or "不明"
        weather_bucket = _ensure_bucket(by_weather, weather_key)
        weather_bucket.record_prediction(prediction)

        wind_key = _wind_bucket(ref_wind)
        by_wind[wind_key].record_prediction(prediction)

        race_class_key = "girls" if prediction.is_girls else "regular"
        by_race_class[race_class_key].record_prediction(prediction)

        # 結果がある場合は的中分類
        if prediction.race_id in results:
            hit = classify_hit(prediction, results[prediction.race_id])
            summary.record_outcome(hit)
            venue_bucket.record_outcome(hit)
            weather_bucket.record_outcome(hit)
            by_wind[wind_key].record_outcome(hit)
            by_race_class[race_class_key].record_outcome(hit)

        # 反省カテゴリ
        for ref in reflections:
            summary.record_reflection(ref)
            venue_bucket.record_reflection(ref)
            weather_bucket.record_reflection(ref)
            by_wind[wind_key].record_reflection(ref)
            by_race_class[race_class_key].record_reflection(ref)
            for c in ref.categories:
                all_categories[c] += 1

    improvement_notes = _build_improvement_notes(
        summary=summary, by_weather=by_weather, by_wind=by_wind, by_race_class=by_race_class
    )

    value_label_summary, high_gami_hit_count = _build_value_label_summary(
        predictions, results
    )

    return {
        "filters": {
            "venue": venue,
            "from_date": from_date,
            "to_date": to_date,
            "weather_condition": weather_condition,
        },
        "summary": summary.to_dict(top_n=limit_reflections),
        "by_venue": {
            k: v.to_dict(top_n=limit_reflections)
            for k, v in sorted(by_venue.items(), key=lambda kv: -kv[1].total)
        },
        "by_weather": {
            k: v.to_dict(top_n=limit_reflections)
            for k, v in sorted(by_weather.items(), key=lambda kv: -kv[1].total)
        },
        "by_wind_bucket": {
            k: by_wind[k].to_dict(top_n=limit_reflections) for k in WIND_BUCKETS
        },
        "by_race_class": {
            k: v.to_dict(top_n=limit_reflections) for k, v in by_race_class.items()
        },
        "top_reflection_categories": all_categories.most_common(limit_reflections),
        "value_label_summary": value_label_summary,
        "high_gami_hit_count": high_gami_hit_count,
        "improvement_notes": improvement_notes,
    }


def _build_value_label_summary(
    predictions: list[Prediction],
    results: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], int]:
    """value_label ごとの total / hit / hit_rate を集計する。

    Prediction が value_label を持っていない（旧データ）場合は無視される。
    """
    summary: dict[str, dict[str, int]] = {}
    high_gami_hit = 0

    def _ensure(lbl: str) -> dict[str, int]:
        if lbl not in summary:
            summary[lbl] = {"total": 0, "hit": 0}
        return summary[lbl]

    for prediction in predictions:
        result_str = results.get(prediction.race_id)
        # 同着対応 (2026-05-24): parse_results で複数結果のいずれかマッチで的中
        parsed_list = parse_results(result_str) if result_str else []
        for b in (
            list(prediction.honsen)
            + list(prediction.osae)
            + list(prediction.ana)
            + list(prediction.ooana)
        ):
            label = b.value_label
            if not label:
                continue
            row = _ensure(label)
            row["total"] += 1
            if parsed_list and any(
                _combo_matches(b.combination, p) for p in parsed_list
            ):
                row["hit"] += 1
                if b.gami_risk and b.gami_risk >= 0.6:
                    high_gami_hit += 1

    # hit_rate を付与
    out: dict[str, dict[str, Any]] = {}
    for lbl, row in summary.items():
        total = row["total"]
        hit = row["hit"]
        rate = hit / total if total > 0 else 0.0
        out[lbl] = {
            "total": total,
            "hit": hit,
            "hit_rate": round(rate, 4),
        }
    return out, high_gami_hit


# ---------------------------------------------------------------------------
# 改善メモ（ルールベース）
# ---------------------------------------------------------------------------


def _bucket_category_count(stats: BucketStats, category: str) -> int:
    return stats.categories.get(category, 0)


def _build_improvement_notes(
    *,
    summary: BucketStats,
    by_weather: dict[str, BucketStats],
    by_wind: dict[str, BucketStats],
    by_race_class: dict[str, BucketStats],
) -> list[str]:
    notes: list[str] = []

    # 雨天時に別線番手を軽視
    rain_buckets = [by_weather.get(k) for k in ("雨", "小雨", "強雨")]
    rain_bessen_total = sum(
        _bucket_category_count(b, "別線番手を軽視") for b in rain_buckets if b
    )
    if rain_bessen_total >= 3:
        notes.append(
            f"雨天時に「別線番手を軽視」が{rain_bessen_total}件発生。"
            "雨の予想では別線番手2着を押さえに追加してください。"
        )

    # 強風時（4m/s以上）に3番手の伸びを軽視
    strong_wind_buckets = [by_wind.get("4-6m/s"), by_wind.get("6m/s以上")]
    strong_third = sum(
        _bucket_category_count(b, "3番手の伸びを軽視") + _bucket_category_count(b, "3番手の2着上がりを軽視した")
        for b in strong_wind_buckets
        if b
    )
    if strong_third >= 3:
        notes.append(
            f"風速4m/s以上で「3番手軽視」が{strong_third}件発生。"
            "強風時は3番手2着を増やしてください。"
        )

    # ガールズで位置取り評価不足
    girls = by_race_class.get("girls")
    girls_pos = _bucket_category_count(girls, "ガールズの位置取り評価不足") if girls else 0
    if girls_pos >= 3:
        notes.append(
            f"ガールズで「位置取り評価不足」が{girls_pos}件発生。"
            "ガールズの予想では2着中穴を残してください。"
        )

    # 穴の広げすぎ
    gami_total = summary.categories.get("穴を広げすぎてガミリスク増加", 0)
    if gami_total >= 3:
        notes.append(
            f"「穴を広げすぎてガミリスク増加」が{gami_total}件発生。"
            "穴・大穴の点数を絞ることを検討してください。"
        )

    # 全体的中率が低すぎる
    if summary.with_result >= 5 and summary.hit_rate < 0.1:
        notes.append(
            f"全体の的中率が{summary.hit_rate:.1%}と低めです。"
            "本線の選定や軸選手の評価を見直してみてください。"
        )

    # 本命自力の過信
    hone_overconf = summary.categories.get("本命自力の過信", 0)
    if hone_overconf >= 3:
        notes.append(
            f"「本命自力の過信」が{hone_overconf}件発生。"
            "先行頭固定を弱め、別線頭の押さえを増やしてください。"
        )

    if not notes:
        notes.append(
            "明確な改善ポイントは検出されませんでした。"
            "予想数や結果入力数が増えると傾向が見えやすくなります。"
        )
    return notes


# ---------------------------------------------------------------------------
# テキストレンダリング
# ---------------------------------------------------------------------------


def _fmt_pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def _render_summary_block(stats: dict[str, Any]) -> list[str]:
    lines = []
    lines.append(f"- 予想数: {stats['total']}")
    lines.append(f"- 結果入力済み: {stats['with_result']}")
    hits = stats["main_hit"] + stats["backup_hit"] + stats["longshot_hit"] + stats["big_longshot_hit"]
    lines.append(f"- 的中数: {hits}")
    lines.append(f"- 的中率: {_fmt_pct(stats['hit_rate'])}")
    lines.append(f"- 本線的中: {stats['main_hit']}")
    lines.append(f"- 押さえ的中: {stats['backup_hit']}")
    lines.append(f"- 穴的中: {stats['longshot_hit']}")
    lines.append(f"- 大穴的中: {stats['big_longshot_hit']}")
    lines.append(f"- 買い目にはあったが本線ではなかった: {stats['listed_but_not_main']}")
    lines.append(f"- 外し: {stats['miss']}")
    lines.append(f"- 平均買い目点数: {stats['avg_bet_count']}")
    lines.append(f"- ガミリスク高 件数: {stats['high_gami_count']}")
    return lines


def _render_breakdown(title: str, items: dict[str, dict[str, Any]]) -> list[str]:
    out = [f"## {title}"]
    if not items:
        out.append("（データなし）")
        return out
    for key, st in items.items():
        hits = st["main_hit"] + st["backup_hit"] + st["longshot_hit"] + st["big_longshot_hit"]
        out.append(
            f"- {key}: 予想 {st['total']} / 結果 {st['with_result']} / "
            f"的中 {hits} ({_fmt_pct(st['hit_rate'])}) / 平均点数 {st['avg_bet_count']}"
        )
        if st["top_categories"]:
            top3 = st["top_categories"][:3]
            out.append("    反省上位: " + ", ".join(f"{k} ({v})" for k, v in top3))
    return out


def render_report_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 成績レポート")
    filt = report["filters"]
    if any(filt.values()):
        cond = [f"{k}={v}" for k, v in filt.items() if v]
        lines.append("フィルタ: " + " / ".join(cond))
    lines.append("")
    lines.append("## 成績サマリー")
    lines.extend(_render_summary_block(report["summary"]))
    lines.append("")
    lines.extend(_render_breakdown("場別成績", report["by_venue"]))
    lines.append("")
    lines.extend(_render_breakdown("天候別成績", report["by_weather"]))
    lines.append("")
    lines.extend(_render_breakdown("風速別成績", report["by_wind_bucket"]))
    lines.append("")
    lines.extend(_render_breakdown("レース種別成績", report["by_race_class"]))
    lines.append("")
    lines.append("## 妙味ラベル別成績")
    vls = report.get("value_label_summary", {})
    if not vls:
        lines.append("（オッズ妙味情報を持つ買い目データなし）")
    else:
        for lbl, row in sorted(vls.items(), key=lambda kv: -kv[1]["total"]):
            lines.append(
                f"- {lbl}: 買い目 {row['total']} 件 / 的中 {row['hit']} 件"
                f" ({_fmt_pct(row['hit_rate'])})"
            )
    high_gami = report.get("high_gami_hit_count", 0)
    lines.append(f"- ガミリスク高で的中した買い目: {high_gami} 件")
    lines.append("")
    lines.append("## 反省カテゴリ上位")
    if report["top_reflection_categories"]:
        for name, n in report["top_reflection_categories"]:
            lines.append(f"- {name}: {n}回")
    else:
        lines.append("（反省ログなし）")
    lines.append("")
    lines.append("## 改善メモ")
    for note in report["improvement_notes"]:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("---")
    lines.append("（本ツールは予想支援・検証目的のみ。自動投票は持ちません）")
    return "\n".join(lines)
