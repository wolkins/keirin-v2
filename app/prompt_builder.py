"""LLMに渡すプロンプト構築。

生HTMLは決して渡さない。構造化JSON + ルールベーススコアのみを渡す。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import BetRecommendation, RaceInput, Reflection, RiderScore
from .value_analysis import analyze_value


PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "prediction_prompt.md"


def _serialize_input(input_data: RaceInput) -> dict[str, Any]:
    return json.loads(input_data.model_dump_json())


def _serialize_scores(scores: list[RiderScore]) -> list[dict[str, Any]]:
    out = []
    for s in scores:
        d = json.loads(s.model_dump_json())
        d["total"] = round(s.total(), 3)
        out.append(d)
    return out


def _serialize_bets(bets: dict[str, list[BetRecommendation]]) -> dict[str, Any]:
    return {
        category: [json.loads(b.model_dump_json()) for b in items]
        for category, items in bets.items()
    }


def load_template() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"プロンプトテンプレートが見つかりません: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_prompt(
    input_data: RaceInput,
    scores: list[RiderScore],
    candidate_bets: dict[str, list[BetRecommendation]],
    template: str | None = None,
) -> str:
    """テンプレートをレンダリングしてLLMプロンプト文字列を返す。"""
    tmpl = template or load_template()
    return tmpl.format(
        race_json=json.dumps(_serialize_input(input_data), ensure_ascii=False, indent=2),
        scores_json=json.dumps(_serialize_scores(scores), ensure_ascii=False, indent=2),
        candidate_bets_json=json.dumps(
            _serialize_bets(candidate_bets), ensure_ascii=False, indent=2
        ),
    )


# ---------------------------------------------------------------------------
# 実LLM向け: JSON応答指示
# ---------------------------------------------------------------------------


JSON_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "summary",
        "venue_trend_text",
        "weather_text",
        "lines_text",
        "final_conclusion",
        "gami_memo",
        "reflection_points",
    ],
    "properties": {
        "summary": {"type": "string"},
        "venue_trend_text": {"type": "string"},
        "weather_text": {"type": "string"},
        "lines_text": {"type": "string"},
        "final_conclusion": {"type": "string"},
        "gami_memo": {"type": "string"},
        "reflection_points": {"type": "array", "items": {"type": "string"}},
    },
}


JSON_INSTRUCTION_HEAD = """

---

## 追加指示（実LLM呼び出し用）

あなたの役割は **文章化と最終結論の整理だけ** です。
**買い目（honsen / osae / ana / ooana）は絶対に書き換えないでください**。
アプリ側で生成済みの候補をそのまま使います。

最終出力は **必ず以下のJSONオブジェクトのみ** で返してください。前後に説明文・コードフェンス・コメントを付けないこと。すべて日本語で書く。

出力フィールド:
- summary: レース概要（1〜3文）
- venue_trend_text: 直近結果からの場の傾向（1〜3文）
- weather_text: 天候・雨・風補正の解釈（1〜3文）
- lines_text: 並び（ガールズの場合は「並びなし（個人戦扱い）」）
- final_conclusion: 最終結論（1〜4文）。本線・押さえ・穴・大穴の候補を踏まえて整理する。
- gami_memo: ガミ回避メモ（箇条書きを許容）
- reflection_points: 結果入力後に保存すべき反省ポイントの配列（3〜6個）

**絶対に守るルール**:
1. **buy 候補（honsen/osae/ana/ooana）はJSONに含めないでください**。アプリ側で固定するため、書いても無視されます。
2. **印（marks）も書き換えないでください**。
3. 最終結論文では、与えられた本線・押さえの上位候補から **そのまま** 引用する。新しい combination を作らない。
4. 「対抗」は印の◯と一致させる。本線の2着筆頭を尊重する。
5. market_odds が None または 20倍以上の買い目は「ガミになりやすい」と表現しない。「妙味あり」「点数注意」「穴として少額」などの表現を使う。
6. 的中保証・回収率保証の表現は禁止。生HTMLや構造化されていない情報は引用しない。
7. 候補に矛盾がありそうな場合は、候補生成結果を優先しつつ補足文で説明する。

返答スキーマ（参考、buy 候補は **含めない**）:
"""


def build_json_instruction() -> str:
    """実LLM用のJSON応答指示を返す（プロンプト末尾に連結する）。"""
    schema_text = json.dumps(JSON_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    return JSON_INSTRUCTION_HEAD + schema_text + "\n"


def _format_reflection_line(r: Reflection) -> str:
    """1件のReflectionを箇条書き1行に整形する。"""
    when = r.created_at or ""
    if when:
        # YYYY-MM-DD HH:MM:SS から日付だけ抜く
        when = when.split(" ")[0]
    head = f"{when} {r.venue}{r.race_no}R".strip()
    weather_bits: list[str] = []
    if r.weather_condition:
        weather_bits.append(r.weather_condition)
    if r.wind_speed_mps:
        weather_bits.append(f"風{r.wind_speed_mps:.1f}m/s")
    if r.rain_mm_per_hour:
        weather_bits.append(f"雨{r.rain_mm_per_hour:.1f}mm/h")
    weather_str = ("[" + " ".join(weather_bits) + "] ") if weather_bits else ""
    cats = " / ".join(r.categories) if r.categories else "（カテゴリなし）"
    bet_str = ""
    if r.predicted_honsen:
        bet_str = f"  予想本線: {', '.join(r.predicted_honsen)} / 結果: {r.actual_result}"
    note = f"  メモ: {r.note}" if r.note else ""
    return f"- {head} {weather_str}{cats}{bet_str}{note}"


def build_reflections_section(reflections: list[Reflection]) -> str:
    """LLMプロンプトに差し込む『過去の反省からの補正』セクションを返す。"""
    header = "\n\n## 過去の反省からの補正\n"
    if not reflections:
        return (
            header
            + "（関連する過去の反省ログはありません。通常通り予想してください。）\n"
        )
    body_lines = [_format_reflection_line(r) for r in reflections]
    footer = (
        "\nこの補正を、買い目の本線・押さえ・穴に反映してください。"
        "ただし機械的に固定はせず、当該レースの条件と矛盾しない範囲で取り入れること。"
        "ガールズと通常ライン戦の反省は混在させないでください。\n"
    )
    return header + "\n".join(body_lines) + "\n" + footer


def build_value_analysis_section(
    input_data: RaceInput,
    scores: list[RiderScore],
    candidate_bets: dict[str, list[BetRecommendation]],
) -> str:
    """LLMプロンプトに差し込む『オッズ妙味分析』セクションを返す。"""
    # 一時 Prediction を作って analyze_value を呼ぶ
    # ここでは BetRecommendation のリストから直接評価する簡易版で十分
    from .value_analysis import (
        VALUE_LABEL_SCORES,
        build_market_rank_map,
        compute_predicted_strength,
        _odds_tier,
        _strength_tier,
        _LABEL_MATRIX,
    )

    all_bets: list[tuple[str, BetRecommendation]] = []
    for cat in ("本線", "押さえ", "穴", "大穴"):
        for b in candidate_bets.get(cat, []):
            all_bets.append((cat, b))
    if not all_bets:
        return "\n\n## オッズ妙味分析\n\n（買い目候補がありません）\n"

    rank_map = build_market_rank_map(list(input_data.odds))
    strengths = [compute_predicted_strength(b, scores) for _, b in all_bets]
    valid = sorted([v for v in strengths if v is not None])

    lines = ["\n\n## オッズ妙味分析\n"]
    for (cat, b), strength in zip(all_bets, strengths):
        info = rank_map.get((b.bet_type, b.combination))
        if info is None:
            label = "オッズ未取得・要確認"
            odds_text = "オッズ未取得"
            rank_text = ""
        else:
            o, r = info
            o_tier = _odds_tier(o) or 0
            s_tier = _strength_tier(strength, valid)
            label = _LABEL_MATRIX.get((s_tier, o_tier), "本線向き")
            odds_text = f"{o:.1f}倍"
            rank_text = f"（人気{r}位）"
        strength_text = (
            f"強度{strength:.2f}" if strength is not None else "強度不明"
        )
        lines.append(
            f"- [{cat}] {b.combination}: {odds_text}{rank_text} / {strength_text} / **{label}**"
        )
    lines.append(
        "\nこのオッズ妙味分析を踏まえ、**厚く買う本線**と **少額で残す穴** を分けて提示してください。"
        "「堅いが安い」はガミ警戒、「妙味あり」は積極的に拾い、「見送り寄り」は買い目から外すか少額にする方針です。"
        "オッズ未取得の買い目は、強度を見て本線または少額穴に分けてください。\n"
    )
    return "\n".join(lines)


# 補助情報源のラベル（rider.comment / user_note 内のプレフィックス）
_NOTE_SOURCE_LABELS = ("東スポ", "WINTICKET", "netkeirin", "オッズパーク", "yenjoy", "手入力", "補助情報")


def _extract_note_lines(text: str) -> list[tuple[str, str]]:
    """テキストから「[ソース] 内容」を全件抽出して (ソース, 内容) リストを返す。"""
    if not text:
        return []
    out: list[tuple[str, str]] = []
    for part in text.split("／"):
        p = part.strip()
        for label in _NOTE_SOURCE_LABELS:
            prefix = f"[{label}]"
            if prefix in p:
                content = p[p.index(prefix) + len(prefix):].strip()
                if content:
                    out.append((label, content))
                break
    return out


def build_race_notes_section(input_data: RaceInput) -> str:
    """補助情報セクション（汎用）。

    rider.comment 中の「[<ソース>] ...」要約と、user_note の「[<ソース>] ...」を
    集めて Markdown セクションを返す。全文ではなく要約+signals のみを LLM に渡す。
    複数ソース（東スポ + WINTICKET + 手入力 等）が混在しても処理可能。
    """
    note_lines: list[str] = []  # 選手コメント
    for r in input_data.riders:
        for label, summary in _extract_note_lines(r.comment or ""):
            tags_str = (
                f" [{', '.join(r.style_tags)}]" if r.style_tags else ""
            )
            note_lines.append(
                f"  - [{label}] 車{r.car_no} {r.name}: {summary}{tags_str}"
            )

    # user_note の補助情報部分
    note_parts = _extract_note_lines(input_data.user_note or "")

    if not note_lines and not note_parts:
        return ""

    sections = ["", "## コメント・記者補助情報", ""]
    if note_lines:
        sections.append("### 選手コメント要約")
        sections.extend(note_lines)
        sections.append("")
    if note_parts:
        sections.append("### 記者見解 / 並び / 予想ヒント")
        for label, content in note_parts:
            sections.append(f"  - [{label}] {content}")
        sections.append("")
    return "\n".join(sections)


def build_tospo_section(input_data: RaceInput) -> str:
    """後方互換: 旧名関数。build_race_notes_section へのエイリアス。"""
    return build_race_notes_section(input_data)


def build_full_prompt(
    input_data: RaceInput,
    scores: list[RiderScore],
    candidate_bets: dict[str, list[BetRecommendation]],
    reflections: list[Reflection] | None = None,
    *,
    value_analysis: bool = True,
) -> str:
    """実LLM向けに、テンプレート + 反省セクション + 妙味分析 + 東スポ補助 + JSON応答指示を連結。"""
    base = build_prompt(input_data, scores, candidate_bets)
    refs_section = build_reflections_section(reflections or [])
    value_section = (
        build_value_analysis_section(input_data, scores, candidate_bets)
        if value_analysis
        else ""
    )
    notes_section = build_race_notes_section(input_data)
    return base + refs_section + value_section + notes_section + build_json_instruction()
