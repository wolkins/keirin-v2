# keirin-v2 現状サマリ (2026-05-24)

ChatGPT に相談する用の概要ドキュメント。実装状況・設計判断・残課題を整理。

---

## 1. プロジェクト概要

**競輪予想支援システム** (Python CLI + Streamlit Web UI)。

- 場名・天候・出走表・並び・直近結果・オッズを入力に、本線/押さえ/穴/大穴 + 最終結論 + 反省ポイントを Markdown で出力
- **予想支援のみ** — 自動投票・購入処理は持たない (絶対禁止)
- LLM (OpenAI / Anthropic / mock) は **説明文の整形のみ** — 買い目は決定論的に生成

### スタック

- Python 3.10+ / Pydantic v2 / Click / SQLite / pytest / Streamlit
- Kドリームス出走表取得 (read-only, GETのみ)
- yen-joy 競走得点取得 (補完用)
- OpenAI gpt-4o-mini (デフォルトプロバイダ)

---

## 2. 実装フェーズ完了状況

| フェーズ | 内容 | 状態 |
| --- | --- | --- |
| **基本機能** | 本命ライン優先 / ガールズ判定 / 新人戦判定 / 反省ログ | ✅ |
| **A: グレードレース対応** | race_grade判定 + 格上加点 + 決勝係数 | ✅ |
| **B: F1/F2 判別** | チャレンジ判別 + F2点数差 + F1強化 | ✅ |
| **C: 地元・地区連係** | home_area + 47都道府県→8地区マッピング + Kドリームス自動取得 | ✅ |
| **出力品質強化** | 整合性チェック / オッズ取得率 / data_quality / 市場偏り検出 | ✅ |
| **広島9R系修正** | odds取得済み妙味の最終結論残存 (codex review 経由で完成) | ✅ |

---

## 3. スコアリングパイプライン

```
compute_scores
→ apply_reflection_signals         # 過去反省からの補正
→ apply_bank_signals               # バンク特性 (333/400/500)
→ apply_wind_extra_signals         # 強風時 4番手評価
→ apply_trend_signals              # 直近結果トレンド (3番手2着上がり等)
→ apply_tospo_signals              # トスポメモ
→ apply_grade_signals              # F1+/グレードの格上加点 ← フェーズA
→ apply_f2_signals                 # F2点数差/チャレンジ自力/3車加点 ← フェーズB
→ apply_home_area_signals          # 地元加点 ← フェーズC
→ apply_market_signals             # 3連単オッズ人気を反映
→ build_candidate_bets             # 本線/押さえ/穴/大穴 生成
   ├── _ensure_three_car_lines_in_osae        # 3車ライン尊重
   ├── _demote_third_sec_up_from_honsen       # 3番手2着上がり押さえ降格
   ├── _promote_bessen_bantan_head_to_osae    # 別線番手頭の押さえ昇格
   ├── _ensure_market_focused_head_bets       # 市場偏り頭の保持
   └── _enforce_max_points_by_grade           # ガールズ/新人戦strict
→ annotate_prediction_with_value   # value_label 付与
→ promote_oddful_to_osae           # 穴→押さえ昇格
→ promote_oddful_to_honsen         # 押さえ→本線昇格
→ sanitize_prediction              # 穴馬→穴目 / gami_risk正規化
→ validate_prediction_output       # 整合性チェック
```

---

## 4. レース種別ごとの差分 (`docs/race_type_policy.md`)

### 格上判定閾値 (`KAKUJOU_THRESHOLD`)

| 種別 | 競走得点 |
| --- | --- |
| F2 / 新人戦 | ≥ 85 |
| F1 | ≥ 95 |
| G3 / G2 / G1 | ≥ 100 |
| GP | ≥ 105 |

### グレード加点係数 (`GRADE_BOOST_MULTIPLIER`)

| 種別 | 係数 | 備考 |
| --- | --- | --- |
| F2 | 0.0 | apply_f2_signals 別ロジック |
| F1 | 1.0 | |
| G3 | 1.0 | |
| G2 | 1.1 | |
| G1 | 1.2 | |
| GP | 1.3 | |
| 決勝戦 | ×1.3 | 上記係数に乗算 |

### 地元加点係数

| 種別 | 係数 |
| --- | --- |
| F2 | 0.5 (控えめ) |
| F1 | 1.0 |
| G3 / G2 | 1.2 |
| G1 / GP | 1.3 |
| 決勝戦 | ×1.2 |

### 最大点数 (`MAX_POINTS_*`)

| 種別 | 本線 | 押さえ | 穴 | 大穴 | 適用 |
| --- | --- | --- | --- | --- | --- |
| F2 A級一般 | 3 | 6 | 4 | 3 | ソフト |
| F2 チャレンジ | 3 | 6 | 4 | 3 | ソフト |
| F1 S級 | 3 | 7 | 5 | 3 | ソフト |
| F1 A級 | 3 | 6 | 4 | 3 | ソフト |
| グレード | 4 | 8 | 5 | 3 | ソフト |
| **ガールズ** | 3 | 4 | 2 | 1 | **strict** |
| **新人戦** | 3 | 4 | 2 | 1 | **strict** |

---

## 5. 出力構成

### 本文

```
## 1. レース概要
## 2. 直近結果からの場の傾向
## 3. 天候・雨・風補正
## 4. 並び
## 5. 印 (◎○▲△×α)
## 6. 本線
  **実購入候補**: odds取得済み+妙味あり 優先表示
  **安い人気筋・ガミ注意（買うなら少額）**: 見送り寄り/高gami/odds<5
## 7. 押さえ
## 8. 穴
## 9. 大穴
## 10. 最終結論 (自然言語 + 構造化セクション)
## 11. ガミ回避メモ
## 12. 結果入力後に保存すべき反省ポイント
```

### 最終結論セクション (## 10)

```
### 一番買いたい買い目  (最大2点、odds+妙味あり優先)
### 押さえるべき買い目  (最大4点、odds取得済み妙味は top_pick と重複してでも残す)
### 少額で足す穴      (最大2点、妙味あり/穴として少額)
### ガミになりやすい買い目  (odds<15+gami>=0.6 等)

### 実購入判断 (4枠分割)
- **オッズ取得済みで買える候補**: 妙味/本線向き 買い目
- **オッズ確認後の本線候補**: odds=None 本線
- **押さえとして必要**: 押さえ上位
- **少額の穴**: 妙味穴
- **{combos}** は売れすぎ / ガミ注意

### オッズ取得率
- オッズ取得済み: X/Y点 (Z%)
- 本線オッズ取得済み: X/Y点 (Z%)

### データ品質: high / medium / low / very_low

### 市場の偏り (3連単上位5件の頭集中検出)

### 出力整合性チェック (warning 表示)
```

---

## 6. 整合性チェック (`output_validation.py`)

`validate_prediction_output(input_data, prediction)` で以下を検出:

| Code | 検出条件 |
| --- | --- |
| `HONSEN_ALL_NO_ODDS` | 本線がすべて market_odds=None |
| `HONSEN_JUDGEMENT_MISMATCH` | 実購入判断「本線として有力」が honsen に無い |
| `GIRLS_LINE_TERM` | ガールズなのに「番手」「本命ライン」等の表現 |
| `ROOKIE_LINE_TERM` | 新人戦なのに通常ライン戦表現 |
| `ANAUMA_TERM` | 「穴馬」(競馬用語) 混入 |
| `ODDS_NONE_HIGH_GAMI` | market_odds=None なのに gami_risk が高い (sanitize 後通知) |

`sanitize_prediction` で以下を自動補正:
- 穴馬 → 穴目 / 本命馬 → 本命
- market_odds=None の gami_risk を 0 に強制
- 反省ポイント「市場人気に基づく無理な展開予想をしない」→ 適切な文言に置換

---

## 7. ファイル構成

```
keirin-v2/
├── app/
│   ├── cli.py                     # Click CLI + render_prediction
│   ├── models.py                  # Pydantic Models (RaceInfo/Rider/Line/Weather/Prediction)
│   ├── scoring.py                 # スコアリング全般 (3000行)
│   ├── value_analysis.py          # value_label + 押さえ⇆本線昇格
│   ├── output_validation.py       # 整合性チェック + data_quality + 市場偏り (新規)
│   ├── prompt_builder.py          # LLMプロンプト構築
│   ├── llm_client.py              # OpenAI/Anthropic/Mock provider
│   ├── reflection.py              # 結果と予想の比較
│   ├── storage.py                 # SQLite 反省ログ
│   ├── fetchers/                  # Kドリームス/yen-joy/oddspark/tospo
│   └── ui/
│       ├── streamlit_app.py
│       └── helpers.py
├── docs/
│   ├── race_type_policy.md            # レース種別ポリシー仕様
│   ├── codex_gemini_review_workflow.md # レビューフロー
│   └── current_state_summary.md       # このファイル
├── tests/                         # 1082件
│   ├── fixtures/                  # 各種実レース fixture
│   └── test_*.py
├── examples/                      # dry-run 用 7ケース
├── outputs/dry_run/               # dry-run 出力結果
├── CLAUDE.md                      # Claude 用指示書
├── AGENTS.md                      # Codex 用指示書 (CLAUDE.md と同期)
└── README.md
```

---

## 8. テスト状況

- **全 1082件パス**
- pytest, dry-run 全7ケース警告ゼロ
- 主なテストカテゴリ:
  - レース種別 fixture (G3/F1/F2/ガールズ/新人戦)
  - 広島R1〜R9 個別ケース (実観測ベース)
  - 整合性チェック / 4枠分割 / 市場偏り

---

## 9. 設計判断・トレードオフ

### 9.1 LLM の役割境界

**LLM は買い目を作らない**。`build_candidate_bets` (決定論的) が生成した買い目を、LLM は説明・整理するのみ。

- 理由: LLM の創造性で買い目が変わると、ルールベースのテストが書けない / 説明責任が崩れる

### 9.2 オッズ未取得時の挙動

- `market_odds=None` の買い目は **ガミ判定不能** → `gami_risk` を 0 に強制
- 本線が全 `odds=None` なら「一番買いたい買い目」ではなく「**オッズ確認後に判断する本線候補**」と表示
- 実購入判断を 4枠 (オッズ取得済み / オッズ確認後 / 押さえ / 穴) に分割

### 9.3 重複表示の許容

通常は「一番買いたい」と「押さえ」で同じ combo は重複除外。
ただし以下は **重複表示 OK** (整合性確保のため):
- `market_odds` 取得済み + `value_label` が「妙味あり」「本線向き」
- `reason` に「市場偏り」を含む

→ 例: 3-1-2 が「一番買いたい」+「押さえ」+「実購入判断」3セクションに残る

### 9.4 ガールズ・新人戦の絞り込み

ガールズ/新人戦は **strict 制限** (本線3/押さえ4/穴2/大穴1)。
理由:
- 個人戦扱い (ライン依存ロジックを使わない)
- データ品質が低くオッズ偏りを参照しすぎないため

---

## 10. 既知の課題・議論したい点

### 10.1 LLM プロバイダの実 API 動作確認

mock provider では全テストが通っているが、**実 OpenAI API での挙動は限定的にしか確認していない**。
- `final_conclusion` の文体・口調
- `gami_memo` の妥当性
- 文字数の上限/下限

### 10.2 強風・雨補正の数値

`apply_wind_extra_signals` / 雨補正の係数は仕様メモから抜粋しているが、実データで検証していない。

### 10.3 反省ログの実運用

反省カテゴリ判定 (`reflection.py`) は実装済みだが、ユーザーの結果入力が長期に蓄積された場合の閾値設定が未調整。

### 10.4 オッズ取得サイト

- 現状: oddspark / kdreams 両対応
- 課題: oddspark のページ構造変更があった場合のパーサ追従

### 10.5 拡張余地

- **複数日開催**: 現状1レースずつ処理
- **連動投票補助** (買い目を投票画面に貼り付け易い形式で出すなど) は **意図的に非対応**
- **F1 グレード戦 G2/G1の決勝/準決ロジック差分**: 一部しか実装されていない

---

## 11. ChatGPT に相談したい観点

1. **スコアリング設計の妥当性**: パイプライン順序 (`apply_*_signals` 群) は合理的か？
2. **整合性チェックの網羅性**: 抜けている検出パターンはあるか？
3. **重複表示の UX**: 3-1-2 が3セクションに出るのは情報過多ではないか？
4. **データ品質スコア**: 4段階 (high/medium/low/very_low) は適切か？
5. **「実購入判断」の4枠**: 分割しすぎていないか？
6. **テスト戦略**: 1082件のテストカバレッジは妥当か？欠けている観点は？
7. **LLM プロンプト改善**: `prompt_builder.py` の構造は LLM に伝わりやすいか？
8. **将来のデータ拡張**: Kドリームス以外の取得対象 (yen-joy/tospo) の取り込み方針は？

---

## 12. 直近の出力例 (広島9R - 市場偏り3番頭集中ケース)

```
## 10. 最終結論

スコア最上位は6番(本命先行)。対抗は3番(本命番手)。
本線は 3-1-2, 6-3-7, 3-6-7 を中心に据える。
配当狙いとして 2-6-3, 6-2-7 を少額で残す。

### 一番買いたい買い目
- 3-1-2 / 20.7倍 / 妙味あり        ← 市場偏り(3番頭) + 妙味あり
- 6-3-7 / オッズ未取得・要確認
- ※ オッズ未取得の買い目あり → 取得後に再確認してください

### 押さえるべき買い目
- 3-1-2 / 20.7倍 / 妙味あり        ← 重複表示OK (整合性確保)
- 6-7-3 / オッズ未取得・要確認
- 6-3-2 / オッズ未取得・要確認
- 6-2-3 / オッズ未取得・要確認

### 実購入判断
- **オッズ取得済みで買える候補**: 3-1-2（妙味/本線向き、購入対象）
- **オッズ確認後の本線候補**: 6-3-7（オッズ取得後に再判断）
- **押さえとして必要**: 3-1-2 / 6-7-3（押さえ2点）

### オッズ取得率
- オッズ取得済み: 5/18点 (28%)
- 本線オッズ取得済み: 1/3点 (33%)

### データ品質: low
- データ不足のため買い目を広げすぎず、オッズ取得済み買い目を優先してください

### 市場の偏り
- 市場（3連単人気上位5件）は **3番頭** に集中（4/5件）
```

---

## 13. コミット履歴 (直近)

```
a50bf03 [codex review 反映] promote 後の honsen 買い目も押さえセクションに残す
8886e04 最終結論で odds取得済み妙味/市場偏り買い目を押さえセクションに残す
5ac28e3 codex/gemini 連携フロー整備: AGENTS.md + ワークフロー文書
209885b 実購入判断を4枠分割 / odds=None本線時の昇格表示明確化
fee9bdf 本線表示順と最終結論の文章順を「odds取得済み+妙味あり」優先に統一
274d589 市場偏り→候補昇格 / odds=None gami_risk補正 / top_pick優先順位修正
a5bbb1d 出力品質向上: 3車ライン尊重 / 整合性チェック / オッズ取得率 / data_quality
9d880d3 Kドリームス取得時に home_area を自動セット
2dc051f UIデフォルトを openai / 予想点数9 に変更
fb3cbae レース種別ポリシー実装 (フェーズA/B/C) + 仕様ドキュメント
c3b5a57 init
```
