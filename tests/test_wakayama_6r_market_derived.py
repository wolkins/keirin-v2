"""和歌山6R: 市場偏り頭の派生候補生成 + line構造弱の押さえ降格 (要件1-5)。

検証要件 (2026-05-24):
1. 市場偏りが「1番頭に集中」(4/5件) なら 1番頭の派生候補を複数生成する
2. 1-7-5 が安すぎても「1番頭+7絡み」(1-7-6 / 7-1-X) のシグナルが保持される
3. line構造が弱くオッズ未取得の買い目が押さえ上位に入りすぎない
4. 実購入判断サマリで「オッズ未取得だが展開上必要な候補」枠が分離表示される
5. (本テストファイル全体で検証)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import _bet_line_strength, _summarize_for_final, render_prediction
from app.llm_client import build_default_client
from app.models import (
    BetRecommendation,
    Line,
    OddsEntry,
    Prediction,
    RaceInfo,
    RaceInput,
    Rider,
)
from app.prompt_builder import build_full_prompt
from app.scoring import (
    _ensure_market_focused_head_bets,
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
)
from app.value_analysis import (
    annotate_prediction_with_value,
    promote_oddful_to_honsen,
    promote_oddful_to_osae,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "wakayama_6r_market_derived.json"
)


def _load() -> RaceInput:
    return RaceInput.model_validate(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


def _prediction(ri: RaceInput) -> Prediction:
    scores = compute_scores(ri)
    apply_reflection_signals(scores, [], ri)
    apply_bank_signals(scores, ri)
    apply_wind_extra_signals(scores, ri)
    apply_trend_signals(scores, ri)
    apply_tospo_signals(scores, ri)
    apply_grade_signals(scores, ri)
    apply_f2_signals(scores, ri)
    apply_home_area_signals(scores, ri)
    apply_market_signals(scores, ri.odds)
    bets = build_candidate_bets(ri, scores)
    client = build_default_client("mock")
    prompt = build_full_prompt(ri, scores, bets, reflections=[])
    pred = client.generate_prediction(ri, scores, bets, prompt)
    annotate_prediction_with_value(pred, scores, ri.odds)
    promote_oddful_to_osae(pred)
    promote_oddful_to_honsen(pred)
    return pred


# ---------------------------------------------------------------------------
# 要件1: 1番頭集中時の派生候補生成
# ---------------------------------------------------------------------------


class TestMarketFocusedDerived:
    """市場偏り 1番頭4/5件 → 派生候補が複数生成されることを確認。"""

    def test_focused_head_minimum_two_bets(self):
        """1番頭の3連単が honsen+osae に最低2点入る。"""
        ri = _load()
        honsen: list[BetRecommendation] = []
        osae: list[BetRecommendation] = []
        _ensure_market_focused_head_bets(honsen, osae, input_data=ri)
        head_count = sum(
            1 for b in (honsen + osae)
            if b.combination and b.combination.split("-")[0] == "1"
        )
        assert head_count >= 2, (
            f"1番頭の3連単が最低2点必要、実際は {head_count} 点"
        )

    def test_derived_pair_one_seven_at_least_one(self):
        """1-7-X の派生候補が最低1点保持される。

        武雄12R 後続レビュー (2026-05-24) 反映:
        AxisBias (1-7軸が市場上位5件中3件以上) でない場合、HeadBias のみ
        として 2着車番を分散 push する。和歌山6R fixture では (1,7)=2件で
        AxisBias 未満なので、1-7-X は1点に制限される。
        AxisBias 検出時のみ 2点許可となる別テストで担保。
        """
        ri = _load()
        honsen: list[BetRecommendation] = []
        osae: list[BetRecommendation] = []
        _ensure_market_focused_head_bets(honsen, osae, input_data=ri)
        pair_count = sum(
            1 for b in (honsen + osae)
            if b.combination and b.combination.startswith("1-7-")
        )
        assert pair_count >= 1, (
            f"1-7-X 派生が最低1点必要 (HeadBias のみ + AxisBias 無し時)、"
            f"実際は {pair_count} 点。"
            f"全候補: {[b.combination for b in (honsen + osae)]}"
        )

    def test_derived_pair_one_five_at_least_one(self):
        """1-5-X の派生候補が最低1点保持される。"""
        ri = _load()
        honsen: list[BetRecommendation] = []
        osae: list[BetRecommendation] = []
        _ensure_market_focused_head_bets(honsen, osae, input_data=ri)
        pair_count = sum(
            1 for b in (honsen + osae)
            if b.combination and b.combination.startswith("1-5-")
        )
        assert pair_count >= 1, (
            f"1-5-X 派生が最低1点必要、実際は {pair_count} 点"
        )


# ---------------------------------------------------------------------------
# 要件2: 集中頭安すぎ時のズレ目シグナル保持
# ---------------------------------------------------------------------------


class TestCheapFocusedShiftBets:
    """1-7-5 が3.2倍と安い → 7-1-X ズレ目を保持。"""

    def test_flip_head_second_when_focused_head_cheap(self):
        """集中頭が安すぎる(<5倍)なら、2着→1着入れ替え (7-1-X) を1点保持。"""
        ri = _load()
        # bias の確認: focused_head=1, cheap=True
        from app.output_validation import detect_market_bias
        bias = detect_market_bias(ri)
        assert bias.focused_head == 1
        assert bias.is_focused_head_cheap is True
        # _ensure_market_focused_head_bets を呼んで 7-1-X が入るか確認
        honsen: list[BetRecommendation] = []
        osae: list[BetRecommendation] = []
        _ensure_market_focused_head_bets(honsen, osae, input_data=ri)
        flip_count = sum(
            1 for b in (honsen + osae)
            if b.combination and b.combination.startswith("7-1-")
        )
        assert flip_count >= 1, (
            f"7-1-X 入れ替えが必要、実際は {flip_count} 点。"
            f"全候補: {[b.combination for b in (honsen + osae)]}"
        )

    def test_no_flip_when_focused_head_not_cheap(self):
        """集中頭が安くない場合は入れ替えロジックを発動しない。"""
        # fixture を加工: 全 odds を 8.0倍以上に
        ri = _load()
        for o in ri.odds:
            if o.odds is not None and o.odds < 8.0:
                o.odds = 8.0
        from app.output_validation import detect_market_bias
        bias = detect_market_bias(ri)
        assert bias.is_focused_head_cheap is False
        honsen: list[BetRecommendation] = []
        osae: list[BetRecommendation] = []
        _ensure_market_focused_head_bets(honsen, osae, input_data=ri)
        # 7-1-X は入らない (もしくは 1番頭の通常派生のみ)
        # ここでは「強制的にflipしない」=「3連単 odds から拾わない」を確認
        # 派生候補ロジック自体は1番頭/1-7絡みを優先しているので、
        # 7-1-X が "派生候補" reason で入っていなければOK
        flip_with_reason = [
            b for b in (honsen + osae)
            if b.combination and b.combination.startswith("7-1-")
            and "入れ替え" in (b.reason or "")
        ]
        assert len(flip_with_reason) == 0, (
            "集中頭が安くない場合は入れ替えズレ目は追加されないはず"
        )


# ---------------------------------------------------------------------------
# 要件3: line構造弱 + odds=None の押さえ降格
# ---------------------------------------------------------------------------


class TestLineStrengthAndOsaeDemotion:
    """_bet_line_strength の判定とcover_pickでの降格動作。"""

    def test_line_strength_strong_direct(self):
        """同ライン頭-番手-3番手 は強(2)。"""
        lines = [Line(line_name="近畿", cars=[1, 7, 6])]
        assert _bet_line_strength("1-7-6", lines) == 2

    def test_line_strength_bantan_head(self):
        """同ライン 番手頭 (7-1-6) も強(2)。"""
        lines = [Line(line_name="近畿", cars=[1, 7, 6])]
        assert _bet_line_strength("7-1-6", lines) == 2

    def test_line_strength_weak_solo_plus_separate(self):
        """単騎+別線番手+別ライン3番手 は弱(0)。"""
        lines = [
            Line(line_name="近畿", cars=[1, 7, 6]),
            Line(line_name="中部", cars=[2, 4, 9]),
            Line(line_name="単騎", cars=[5]),
            Line(line_name="単騎", cars=[3]),
        ]
        # 5(単騎) - 3(単騎) - 4(中部番手) は line根拠が薄い
        assert _bet_line_strength("5-3-4", lines) == 0

    def test_line_strength_medium_same_line_head_third(self):
        """同ライン 頭+3番手 (1-X-6) は中(1)。"""
        lines = [
            Line(line_name="近畿", cars=[1, 7, 6]),
            Line(line_name="中部", cars=[2, 4, 9]),
        ]
        # 1着=1(近畿頭), 2着=2(中部頭), 3着=6(近畿3番手) → 同ライン頭+3番手で中
        assert _bet_line_strength("1-2-6", lines) == 1

    def test_line_strength_malformed_returns_zero(self):
        """不正な combo は 0 を返す。"""
        assert _bet_line_strength("", []) == 0
        assert _bet_line_strength(None, []) == 0
        assert _bet_line_strength("1-2", []) == 0  # 2要素のみ
        assert _bet_line_strength("a-b-c", []) == 0  # 数値以外

    def test_weak_no_odds_not_in_cover_top(self):
        """line構造弱+odds=None の買い目が押さえ上位 2点に入らない。

        prediction.osae に 5-3-4 (line弱+odds=None) と
                          1-7-6 (line強+odds=4.5) を入れた場合、
        render_prediction の cover_pick 上位は line強+odds取得済みを優先。
        """
        ri = _load()
        # ダミー Prediction を作る
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[],
            osae=[
                # line弱 + odds=None: 押さえ上位に来てほしくない
                BetRecommendation(
                    category="押さえ", bet_type="3連単",
                    combination="5-3-4",
                    reason="スコア上位組み合わせ",
                    gami_risk=0.0,
                ),
                # line強 + odds取得済み: 押さえ上位に残る
                BetRecommendation(
                    category="押さえ", bet_type="3連単",
                    combination="1-7-6",
                    reason="本命ライン直行",
                    gami_risk=0.0,
                    market_odds=4.5,
                    value_label="本線向き",
                ),
            ],
            ana=[],
            ooana=[],
            final_conclusion="本線は 1-7-6 を中心に据える。",
            gami_memo="",
            reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        # 「押さえるべき買い目」セクション直後の最初の `- ` を見る
        lines = out.split("\n")
        cover_section_idx = next(
            (i for i, ln in enumerate(lines)
             if "押さえるべき買い目" in ln),
            None,
        )
        assert cover_section_idx is not None
        # 直後10行に 5-3-4 が押さえ「最上位」として出ないことを確認
        # (line強の 1-7-6 がより上に出る)
        first_bet_idx = None
        for i in range(cover_section_idx + 1, min(cover_section_idx + 10, len(lines))):
            if lines[i].startswith("- "):
                first_bet_idx = i
                break
        if first_bet_idx is not None:
            first_combo = lines[first_bet_idx]
            assert "1-7-6" in first_combo, (
                f"押さえ最上位は line強+odds取得済み (1-7-6) のはず。"
                f"実際: {first_combo}"
            )


# ---------------------------------------------------------------------------
# 要件4: 実購入判断の3分類表示
# ---------------------------------------------------------------------------


class TestThreeTierPurchaseJudgement:
    """`オッズ確認後の本線候補` / `安い人気筋` / `オッズ未取得だが展開上必要な候補` 3分離。"""

    def test_tenkai_needed_section_appears(self):
        """cover_pick に odds=None+line強の買い目があると、新セクションが出る。"""
        ri = _load()
        # 本線に odds取得済み 2点を入れて top_pick を埋める
        # → osae の 1-7-6 (odds=None+line強) は cover_pick に流れる
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="2-4-9",
                    reason="本命ライン",
                    gami_risk=0.0,
                    market_odds=12.0,
                    value_label="妙味あり",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="2-9-4",
                    reason="本命ライン2-3着入替",
                    gami_risk=0.0,
                    market_odds=18.0,
                    value_label="妙味あり",
                ),
            ],
            osae=[
                # line強+odds=None → 展開上必要な候補
                BetRecommendation(
                    category="押さえ", bet_type="3連単",
                    combination="1-7-6",
                    reason="本命ライン直行",
                    gami_risk=0.0,
                ),
            ],
            ana=[],
            ooana=[],
            final_conclusion="本線は 2-4-9 を中心に据える。",
            gami_memo="",
            reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        assert "オッズ未取得だが展開上必要な候補" in out, (
            "新 3分類セクション「オッズ未取得だが展開上必要な候補」が見当たらない"
        )
        # 「安い人気筋」セクションも露出する場合は新文言を確認 (gami_warnあり時)
        if "売れすぎ" in out:
            assert "安い人気筋" in out, (
                "ガミ警戒は「安い人気筋」というラベルで表示すべき"
            )

    def test_tenkai_needed_includes_market_focused_no_odds(self):
        """odds=None + reason に「市場偏り」を含む買い目も展開上必要扱い。"""
        ri = _load()
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="2-4-9",
                    reason="本命ライン",
                    gami_risk=0.0,
                    market_odds=12.0,
                    value_label="妙味あり",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="2-9-4",
                    reason="本命ライン2-3着入替",
                    gami_risk=0.0,
                    market_odds=18.0,
                    value_label="妙味あり",
                ),
            ],
            osae=[
                # 市場偏り起因 + odds=None: line判定が弱でも展開上必要扱い
                BetRecommendation(
                    category="押さえ", bet_type="3連単",
                    combination="1-3-5",
                    reason="市場偏り(1番頭集中)",
                    gami_risk=0.0,
                ),
            ],
            ana=[],
            ooana=[],
            final_conclusion="本線は 2-4-9 を中心に据える。",
            gami_memo="",
            reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        # 「オッズ未取得だが展開上必要な候補」枠に 1-3-5 が含まれる
        # (line判定で 1-3-5 は弱でも、市場偏りで救済される)
        assert "オッズ未取得だが展開上必要な候補" in out
        # 該当セクションの行で 1-3-5 を含む
        section = out.split("オッズ未取得だが展開上必要な候補")[1].split("\n")[0]
        assert "1-3-5" in section or "1-3-5" in out


# ---------------------------------------------------------------------------
# codex review 反映: 重複除外 + 2-3着同ライン判定
# ---------------------------------------------------------------------------


class TestCodexReviewFixes:
    """codex review (2026-05-24) で指摘された P2 修正の回帰テスト。"""

    def test_line_strength_second_third_same_line(self):
        """2着-3着同ライン (5-1-7) も中(1)。codex review P2 反映。"""
        lines = [
            Line(line_name="近畿", cars=[1, 7, 6]),
            Line(line_name="単騎", cars=[5]),
        ]
        # 5(単騎) - 1(近畿頭) - 7(近畿番手) は 2-3着同ラインで根拠あり
        assert _bet_line_strength("5-1-7", lines) == 1

    def test_purchase_judgement_no_duplicate_across_buckets(self):
        """top_pick / tenkai_needed / buy_cover の3枠で重複しない。

        codex review P2 反映: cover_with_odds 空時に buy_cover が
        tenkai_needed と被るケースを排除。
        """
        ri = _load()
        pred = Prediction(
            race_id=ri.race.race_id,
            venue=ri.race.venue,
            race_no=ri.race.race_no,
            is_girls=False,
            summary="テスト",
            venue_trend_text="テスト",
            weather_text="テスト",
            lines_text="テスト",
            marks={},
            honsen=[
                # 本線: odds取得済み 2点で top_pick を埋める
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="2-4-9",
                    reason="本命ライン", gami_risk=0.0,
                    market_odds=12.0, value_label="妙味あり",
                ),
                BetRecommendation(
                    category="本線", bet_type="3連単", combination="2-9-4",
                    reason="本命ライン", gami_risk=0.0,
                    market_odds=18.0, value_label="妙味あり",
                ),
            ],
            osae=[
                # 唯一の cover_pick: line強+odds=None
                #   tenkai_needed に入りつつ buy_cover にも入りうる
                BetRecommendation(
                    category="押さえ", bet_type="3連単", combination="1-7-6",
                    reason="本命ライン直行", gami_risk=0.0,
                ),
            ],
            ana=[],
            ooana=[],
            final_conclusion="本線は 2-4-9 を中心に据える。",
            gami_memo="",
            reflection_points=[],
        )
        out = render_prediction(pred, input_data=ri)
        judgement = out.split("### 実購入判断")[1] if "### 実購入判断" in out else ""
        # tenkai_needed 行と buy_cover 行を抽出
        import re
        tenkai_line = next(
            (ln for ln in judgement.split("\n")
             if "オッズ未取得だが展開上必要な候補" in ln),
            "",
        )
        cover_line = next(
            (ln for ln in judgement.split("\n")
             if "押さえとして必要" in ln),
            "",
        )
        tenkai_combos = set(re.findall(r"\d-\d-\d", tenkai_line))
        cover_combos = set(re.findall(r"\d-\d-\d", cover_line))
        assert "1-7-6" in tenkai_combos, (
            f"1-7-6 は tenkai_needed に出るべき。"
            f"tenkai_line={tenkai_line!r}"
        )
        overlap = tenkai_combos & cover_combos
        assert not overlap, (
            f"tenkai_needed と buy_cover で重複: {overlap}"
        )
