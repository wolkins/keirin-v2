# keirin-predictor

競輪予想支援CLI（手入力JSON対応のMVP）。

このツールは **予想支援目的のみ** です。  
自動投票・自動購入・投票サイトへのログイン・外部投票サイトへの送信は  
**実装していません**。今後も実装しません。

---

## 何ができるか

- 手入力JSONを読み込んで競輪予想を生成
- 出走表 / 並び / 直近成績 / オッズ / 直近結果 / 天候を考慮
- ルールベースのスコアリング + LLMによる文章化（LLMは差し替え可能、MVPはモック）
- レース結果を入力して反省ログを自動分類・SQLiteに保存
- 反省ログを場/天候で絞り込み表示
- 手入力JSONのテンプレート出力

予想の出力フォーマット（必ずこの順で出る）

1. レース概要
2. 直近結果からの場の傾向
3. 天候・雨・風補正
4. 並び
5. 印
6. 本線
7. 押さえ
8. 穴
9. 大穴
10. 最終結論
11. ガミ回避メモ
12. 結果入力後に保存すべき反省ポイント

---

## セットアップ

Python 3.10 以上が必要です。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .            # または pip install pydantic click python-dotenv requests pytest
# 実LLMを使う場合のみ、対応SDKを追加
pip install -e ".[openai]"     # OpenAI
pip install -e ".[anthropic]"  # Anthropic
# Web UI を使う場合
pip install -e ".[ui]"         # Streamlit
```

依存:

- `pydantic >= 2.6`
- `click >= 8.1`
- `python-dotenv >= 1.0`
- `requests >= 2.31`（外部データ取得用）
- `pytest >= 8.0`（テスト時のみ）
- `openai` / `anthropic`（実LLM接続時のみ・任意）

### .env の用意

実LLMを使う場合は `.env.example` をコピーして `.env` を作成し、APIキーを入れてください。

```bash
cp .env.example .env
# .env を編集してキーを入れる
```

`.env` は `.gitignore` で除外しています。APIキーをコードに直書きしないでください。

環境変数一覧:

| 変数 | 用途 | 既定 |
| --- | --- | --- |
| `LLM_PROVIDER` | 既定で使うプロバイダ。`mock` / `openai` / `anthropic` | `mock` |
| `OPENAI_API_KEY` | OpenAI APIキー | （未設定） |
| `OPENAI_MODEL` | OpenAIモデル名 | `gpt-4o-mini` |
| `ANTHROPIC_API_KEY` | Anthropic APIキー | （未設定） |
| `ANTHROPIC_MODEL` | Anthropicモデル名 | `claude-sonnet-4-6` |
| `KEIRIN_USE_OUTPUT_PLAN` | renderer の選択。`0`/`false`/`no`/`off` で legacy v1 に戻せる。`1`/`true`/`yes`/`on` または未設定で v2 (デフォルト) | (未設定: v2) |

---

## Renderer の選択 (v2 デフォルト)

**2026-05-24 以降、OutputPlan v2 が標準レンダラー**です。
legacy v1 は切り戻し用に残されています。

- **OutputPlan v2 (デフォルト)**: deterministic に最終結論を生成。
  LLM が捏造した「honsen に存在しない買い目」を構造的に排除する。
  最終結論の整合性を強制。
- **v1 legacy (互換用)**: LLM の final_conclusion をベースに整形。
  互換性のため残されているが、最終結論に未登録 buy が混入するリスクあり。
  新規ユースケースでは使用を推奨しない。

### 切り替え方法

| 目的 | 方法 |
| --- | --- |
| v2 (デフォルト) で使う | 何もしない (CLI / Streamlit いずれもデフォルト) |
| v1 legacy に切り戻す (CLI) | `python -m app.cli predict ... --renderer v1` |
| v1 legacy に切り戻す (環境変数) | `export KEIRIN_USE_OUTPUT_PLAN=0` (`false` / `no` / `off` も可) |
| v1 legacy に切り戻す (Streamlit UI) | サイドバー「Legacy v1 renderer を使う」チェックボックス ON |
| 明示的に v2 を指定 | `--renderer v2` または `KEIRIN_USE_OUTPUT_PLAN=1` |

優先順位: **明示フラグ > 環境変数 > 既定 (v2)**。

### `--renderer` フラグの値

- `v2`: 明示的に v2 を使う (環境変数を上書き)
- `v1`: 明示的に legacy v1 を使う (環境変数を上書き)
- `auto` (デフォルト): 環境変数 `KEIRIN_USE_OUTPUT_PLAN` を参照
  - `0` / `false` / `no` / `off` → v1
  - それ以外 (未設定 / `1` / `true` 等) → v2

### v2 出力の見分け方

v2 を使用すると Markdown 末尾に `<!-- renderer=output_plan_v2 -->` が
追記され、stderr ログにも記録されます。Streamlit UI では
「Renderer: v2 (default)」の緑色バッジが表示されます。

### v2 の final_conclusion 文言

v2 では LLM の `final_conclusion` を完全に無視し、OutputPlan からの
deterministic 生成に切り替わります。出力フォーマット:

| 状態 | フォーマット |
| --- | --- |
| `final_best` あり | `一番買いたい買い目は X, Y を中心に据える。` |
| `final_best` 空 + `final_osae` あり | `本線はオッズ確認後の判断とし、押さえるべき買い目は X を確認推奨。` |
| 両方空 | `オッズ取得済みで買える候補なし — オッズ確認後に判断してください。` |
| `final_ana` あり (追記) | ` 少額で足す穴は Z。` |
| `gami_warning` あり (追記) | ` 安い人気筋・ガミ注意は W — 厚く買わない (確認程度)。` |

これにより「osae を本線扱いする」「LLM が捏造した buy が結論に出る」
事故を構造的に防止します。

### 3経路サマリ (2026-05-24 v2 デフォルト化)

OutputPlan v2 は **デフォルト動作** です。明示的に切り戻したい場合のみ
以下のいずれかで legacy v1 を選択できます。優先順位は
**CLI flag > 環境変数 > Streamlit UI > 既定 (v2)** です:

1. **CLI フラグ**: `--renderer v1|v2|auto` (`auto` がデフォルトで v2)
   ```bash
   # 明示的に v2
   python -m app.cli predict --input <file> --renderer v2
   # legacy v1 に切り戻し
   python -m app.cli predict --input <file> --renderer v1
   ```
2. **環境変数**: `KEIRIN_USE_OUTPUT_PLAN`
   ```bash
   # legacy v1 に戻す (0 / false / no / off いずれも可)
   export KEIRIN_USE_OUTPUT_PLAN=0
   python -m app.cli predict --input <file>

   # 明示的に v2 を ON (1 / true / yes / on / 未設定でも v2)
   export KEIRIN_USE_OUTPUT_PLAN=1
   ```
3. **Streamlit UI**: サイドバー「Legacy v1 renderer を使う (非推奨)」
   チェックボックス。OFF (デフォルト) = v2、ON = v1。
   初期値は環境変数 `KEIRIN_USE_OUTPUT_PLAN` が `0`/`false`/`no`/`off` の
   ときのみ ON。

外部モジュールから判定するときは public API
`app.renderer_selector.env_says_output_plan_v2()` /
`env_explicitly_disables_v2()` / `default_renderer_from_env()` を
使ってください。

---

## クイックスタート

```bash
# サンプルJSONで予想を生成（v2 が標準レンダラー、追加指定不要）
python -m app.cli predict --input examples/race_sample.json

# 明示的に v2 を指定 (環境変数を上書き)
python -m app.cli predict --input examples/race_sample.json --renderer v2

# Legacy v1 renderer に戻す
python -m app.cli predict --input examples/race_sample.json --renderer v1
# または環境変数で legacy ON
export KEIRIN_USE_OUTPUT_PLAN=0
python -m app.cli predict --input examples/race_sample.json

# 明示的にプロバイダを指定
python -m app.cli predict --input examples/race_sample.json --provider mock
python -m app.cli predict --input examples/race_sample.json --provider openai
python -m app.cli predict --input examples/race_sample.json --provider anthropic

# 反省ログ参照を有効/無効化、件数を変える
python -m app.cli predict --input examples/race_sample.json --use-reflections
python -m app.cli predict --input examples/race_sample.json --no-reflections
python -m app.cli predict --input examples/race_sample.json --reflection-limit 10

# 結果を入力して反省ログを保存
python -m app.cli result --race-id 20260522-ogaki-1 --result 5-1-3 \
  --input examples/race_sample.json

# 反省ログを表示
python -m app.cli reflections --venue 大垣

# 手入力JSONのテンプレートを生成（全項目空のひな型）
python -m app.cli create-json --out examples/new_race.json
# ガールズ用テンプレートが必要なら
python -m app.cli create-json --out examples/new_girls.json --girls
# 対話形式で1問1答で作る
python -m app.cli create-json --out examples/new_race.json --interactive

# フラグだけで素早く作る（最低限の出走表は placeholder）
python -m app.cli quick-json \
  --out examples/omiya_8r.json \
  --venue 大宮 \
  --race-no 8 \
  --class-name A級特選 \
  --weather 曇り \
  --wind-direction 北 \
  --wind-speed 4.0 \
  --lines "3-7-2 / 1-5 / 4-6"

# 外部ソースから取得（現状 manual のみ実装）
python -m app.cli fetch-json --source manual --input examples/race_sample.json --out tmp.json
python -m app.cli predict --input tmp.json --no-save
```

`predict` を実行すると、SQLite (`./keirin.db`) に予想結果が保存されます。  
保存先を変えたいときは `--db /path/to/your.db` を渡してください。

`--provider` を省略した場合は `.env` の `LLM_PROVIDER` を見ます。さらに未設定なら `mock` を使います。

### LLM プロバイダの選び方

| provider | 用途 | 文章品質 | 必要設定 |
| --- | --- | --- | --- |
| **`openai`**（推奨） | **実運用** | 高品質な文章化・最終結論 | `OPENAI_API_KEY` |
| `anthropic` | 実運用 | 高品質な文章化・最終結論 | `ANTHROPIC_API_KEY` |
| `mock` | **動作確認用** | 簡素な定型文（数値・候補は本物） | なし |

#### 実運用は `openai` （または `anthropic`）を推奨

`mock` は文章を **定型テンプレートで生成** するため、最終結論の言い回しや候補解説の自然さに限界があります。実レースでの予想出力を見るなら `openai` か `anthropic` を使ってください。

```bash
# .env に APIキーを書く
echo 'OPENAI_API_KEY=sk-...' >> .env
echo 'LLM_PROVIDER=openai' >> .env

# または CLI で都度指定
python -m app.cli predict --input tmp/武雄_2026-05-23_03r.json --provider openai
```

#### 安全設計: LLM は買い目を **書き換えない**

このシステムは **スコアリングと買い目候補生成をアプリ側で固定** し、LLM には「文章化と最終結論の整理」のみを任せる設計です:

| アプリ側で固定（LLM変更不可） | LLM が作成 |
| --- | --- |
| 印（marks）・スコア・rider_scores | summary（レース概要） |
| **honsen / osae / ana / ooana**（買い目候補） | venue_trend_text（場の傾向） |
| value_label / gami_risk | weather_text（天候解釈） |
| | lines_text（並び） |
| | final_conclusion（最終結論） |
| | gami_memo（ガミ回避メモ） |
| | reflection_points（反省項目案） |

LLM が誤って `honsen` などを返しても、`_merge_llm_response` で **無視されます**。これにより:
- ライン構造優先のロジックが LLM 応答で崩れない
- 別線スコア反映・トレンド形 push が必ず保持される
- 上限（MAX_HONSEN / HARD_OSAE 等）が守られる

APIキー未設定時は自動的に `mock` にフォールバックします（日本語警告つき）。

#### 動作確認の流れ

```bash
# 1. mock で動作確認
python -m app.cli predict --input examples/race_sample.json --provider mock

# 2. openai で実LLM文章化
export OPENAI_API_KEY=sk-...
python -m app.cli predict --input examples/race_sample.json --provider openai
# → 買い目は mock と完全一致（アプリ側で固定）、文章だけ自然になる
```

---

## Web UI（Streamlit）

ローカルブラウザから操作できる Web UI を同梱しています（optional dependency）。

### インストールと起動

```bash
pip install -e ".[ui]"
streamlit run app/ui/streamlit_app.py
```

ブラウザが自動で開き、サイドバー + 6タブの画面が表示されます。

### UI で何ができるか

- 場名・日付・レース番号を入力して **出走表/結果/オッズ/天候を一括取得**
- 取得した RaceInput を確認 + JSON ダウンロード
- 手入力 RaceInput JSON の **アップロード** (バリデーションエラーは日本語)
- mock/openai/anthropic で **予想を生成**（Markdown 表示）
- レース後の **結果入力**（反省カテゴリ自動分類）
- 蓄積された **反省ログをフィルタ表示**
- 場別・天候別・風速別の **成績レポート**
- ヘルプ・APIキー設定・トラブルシューティング

### UI でやらないこと（仕様)

- **自動投票・購入処理は一切ありません**
- 投票サイトへのログインも実装しません
- 外部投票サイトへの POST 送信は無し

このシステムは予想支援目的のみで、決済処理は持ちません。

### 6タブの構成

| タブ | 内容 |
| --- | --- |
| 予想作成 | サイドバー設定で prepare-json + predict を実行 |
| 入力JSON確認 | 現在JSON表示・ダウンロード・アップロード |
| 結果入力 | race_id + 結果 を入力して反省ログ生成 |
| 反省ログ | venue/weather フィルタで一覧表示 |
| 成績レポート | 場/天候/風速別の成績集計 |
| 設定/ヘルプ | APIキー設定・トラブルシュート |

### APIキー設定（UI用）

UI から OpenAI/Anthropic を使う場合も、APIキーは環境変数で渡します:

```bash
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run app/ui/streamlit_app.py
```

`.env` 経由でもOK。

### Kドリームス取得が失敗した場合

サイドバーの source を `manual` に切り替え、「入力JSON確認」タブから RaceInput JSON をアップロードしてください。

### Open-Meteo 取得が失敗した場合

サイドバーの weather-source を `manual` に切り替え、予想作成タブの「天候の手動上書き」欄で値を入れてください。

### 既存テストへの影響

- `pyproject.toml` で `ui = ["streamlit>=1.30"]` を optional dependency にしているため、**streamlit 未インストールでも既存 CLI/テストは動作します**
- `app/ui/streamlit_app.py` は `import streamlit` を try/except で防御。サーバー起動には `streamlit run` 必須
- `tests/test_ui_helpers.py` は helpers の単体テストのみ（実通信・実LLM・サーバー起動なし）

### トラブルシューティング

- 「ModuleNotFoundError: streamlit」 → `pip install -e ".[ui]"`
- 取得失敗で予想ができない → 手入力JSONアップロードに切り替え
- 予想が車番順になる → 出走表に score が無いケース。市場人気が反映されていない場合は手動で score を入れる
- ガールズで「番手」表現が出ない → 仕様通り（ガールズはライン無し）

---

## 手入力JSON仕様

必須:

- `race` — `race_id`, `date`, `venue`, `race_no`, `class_name`
- `riders` — 出走選手の配列
- `lines` — ライン構成（ガールズの場合は空でOK）

任意:

- `weather` — 天候・雨量・風向・風速
- `odds` — 買い目とオッズ（3連単/2車単/単勝など）
- `recent_results` — 直近結果（メモから別線番手/3番手の好走傾向を抽出）
- `venue_trend` — 場の当日傾向と加点したい脚質タグ
- `user_note` — フリーメモ

最小例:

```json
{
  "race": {
    "race_id": "20260522-ogaki-1",
    "date": "2026-05-22",
    "venue": "大垣",
    "race_no": 1,
    "class_name": "A級一般",
    "start_time": "10:53"
  },
  "weather": {
    "condition": "曇り",
    "rain_mm_per_hour": 0,
    "wind_direction": "西",
    "wind_speed_mps": 5.0
  },
  "lines": [
    {"line_name": "九州", "cars": [5, 1, 3], "description": "⑤-①-③"},
    {"line_name": "中部中国", "cars": [2, 6, 4], "description": "②-⑥-④"}
  ],
  "riders": [
    {"car_no": 5, "name": "池部", "score": 85.71, "b_count": 4, "nige": 4, "style_tags": ["先行", "自力"]}
  ]
}
```

完全な例は `examples/race_sample.json` を、`quick-json` の出力例は `examples/quick_sample.json` を参照してください。

### 並び文字列 (`--lines`) の文法

`quick-json` や対話モードで使う並び文字列の仕様:

- ライン間の区切り: `/`, `|`, `｜`, `・`
- ライン内の車番区切り: `-`, `_`, 半角/全角空白, `,`, `、`（全角ハイフン・ダッシュ各種も正規化）
- 全角数字 (`０`〜`９`) は半角に正規化
- 車番は `1〜9`。範囲外・非数値・重複は **日本語エラー** で弾く
- 1人ラインは自動で `単騎` と命名、複数人は `ライン1`, `ライン2`, ... と連番

例:

| 入力 | 解釈 |
| --- | --- |
| `5-1-3 / 2-6-4 / 7` | ライン1=[5,1,3], ライン2=[2,6,4], 単騎=[7] |
| `３－７－２ ／ １－５ ／ ４－６` | ライン1=[3,7,2], ライン2=[1,5], ライン3=[4,6] |
| `3 7 2 \| 1,5 \| 4-6` | 区切り混在も可 |

ライン名（"九州", "中部中国" 等の地域名）が必要な場合は、生成後にJSONを直接編集してください。

### 対話モード (`create-json --interactive`)

`click.prompt` で1問1答に答える。空欄Enterで既定値/Noneを採用。  
入力途中で並びを誤ると日本語エラーが出てその場で再入力できます。  
各車の選手情報は最後に Yes/No を聞き、No なら最低限の placeholder で埋めます。

---

## 役割分類（RiderRole）と バンク補正

仕様3章「通常ライン戦の役割分類」+ 7章「バンク補正」を実装。

### 役割タグ（10種）

`app/scoring.py::resolve_rider_roles` が各車に下記タグを割り当て:

| タグ | 意味 |
| --- | --- |
| `line_leader` | 本命ライン先頭（自力） |
| `second` | 本命ライン番手 |
| `third` | 本命ライン3番手 |
| `fourth` | 4番手以降 |
| `separate_leader` | 別線自力 |
| `separate_second` | 別線番手 |
| `separate_third` | 別線3番手 |
| `solo` | 単騎 |
| `jizai` | 自在型（style_tags に "自在"） |
| `girls` | ガールズ選手（個人戦扱い） |

本命ラインは **スコア最上位車のライン** で自動判定。ライン無し選手は `style_tags` に "自在" があれば `jizai`、無ければ `solo`。ガールズレースは全員 `girls` で固定。

### バンク補正 (`apply_bank_signals`)

`race.bank_length` / `race.bank_style` を見て自動補正:

| バンク条件 | 補正内容 |
| --- | --- |
| `bank_length >= 470` (500バンク) | 番手・3番手・追込型に win/second/third 加点 |
| `bank_length <= 350` (333バンク) | 先頭・別線自力・番手に win 加点、後方からの捲りを軽減 |
| `bank_style = "差し有利"` (or bank_note にキーワード) | 番手・3番手・追込型を加点 |
| `bank_style = "先行有利"` | 本命ライン先頭・番手・3番手を加点 |

`bank_length` も `bank_style` も無ければ no-op。

### CLI から指定

```bash
python -m app.cli prepare-json \
  --source kdreams \
  --venue 大宮 --date 2026-05-22 --race-no 1 --session-no 1 \
  --bank-length 500 \
  --bank-style 差し有利 \
  --out tmp/omiya_1r.json
```

- `--bank-length`: 200〜600 の整数（範囲外はバリデーションエラー）
- `--bank-style`: `差し有利` / `先行有利` / `中立` を想定（自由文も受けるが案内が出る）

### キャッシュ堅牢化（エラー非キャッシュ・refresh・開催なしインデックス）

#### 1. エラーレスポンスはキャッシュしない

Kドリームスから一時的に `SYSTEM_ERROR` ページ（HTTP 200だがエラー本文）が返された場合でも、**キャッシュに保存されません**。これにより「キャッシュに古いエラーが残って TTL 内ずっと失敗する」事象を防ぎます。

実装: `HttpClient.get(url, validate_body=...)` で本文判定→`False` ならキャッシュスキップ。Kドリームスの fetcher は `SYSTEM_ERROR` / `エラーが発生しました` を含む本文をキャッシュしないよう自動設定済み。

#### 2. `--refresh-cache` フラグ

既存キャッシュを無視して再取得し、新結果でキャッシュを上書きします。`--no-cache` とは違い、新しい取得結果はキャッシュに保存されます。

```bash
python -m app.cli prepare-json --venue 広島 --date 2026-05-22 --refresh-cache
python -m app.cli fetch-json --source kdreams --kind race_card ... --refresh-cache
```

| フラグ | キャッシュ読み | キャッシュ書き |
| --- | --- | --- |
| (なし) | 有効 | 有効 |
| `--no-cache` | 無効 | 無効 |
| `--refresh-cache` | **無効**（強制再取得） | **有効** |

#### 3. 開催なしインデックス

`SYSTEM_ERROR` を検出した場合、`{場名, 日付, session_no}` の組を `.cache/keirin/no_meet_index.json` に記録します。次回同じ組み合わせで `prepare-json` を実行すると **通信せずに即時スキップ**:

```
[案内] 「開催なし」が記録済み: 広島 2026-05-22 (session_no=1)。
強制再取得するには --refresh-cache を指定してください。
```

- TTL: 12時間（情報更新を考慮して短めに設定）
- `--refresh-cache` を付けると無視して再取得
- `--no-cache` 時も無視

これにより、休催日の場名でうっかり実行しても1回目で記録され、無駄な通信が発生しません。

### バンク情報の自動補完

場名から **バンク周長/特性を自動補完** します（`app/bank_info.py` のマッピング）。

```bash
# 「大宮」と指定するだけで bank_length=500, bank_style=差し有利 が自動セット
python -m app.cli prepare-json --venue 大宮 --date 2026-05-22 --race-no 1
# → [案内] バンク情報を自動補完: 大宮 → 周長 500m / 差し有利
```

- ユーザーが `--bank-length` / `--bank-style` を **明示した場合はそちらが優先**
- 未登録の場名は補完されない（None のまま）
- 500バンクの大宮・宇都宮・高知は「差し有利」をベース値として登録

### レース種別の自動取得

レース種別（A級一般 / ガールズ新人予選 等）は **出走表取得時に自動取得** されるため、CLI/UI で明示する必要はありません。`source=kdreams` で取得すれば `RaceInput.race.class_name` に正しい値が入ります（ガールズ判定も `is_girls` フィールドに自動反映）。

## 天候・トレンド別の必須候補追加（仕様5/6/8章）

`build_candidate_bets` は基本候補を出した後、**役割タグ × 天候 × 直近トレンド** に応じて「必ず候補に入れる形」を追加で push します（仕様5/6/8章準拠）。

### 雨（rain_mm_per_hour > 0）— 5形

| 形 | カテゴリ |
| --- | --- |
| 本命自力-本命番手-別線番手 | 押さえ |
| 本命自力-別線番手-本命番手 | 押さえ |
| 本命自力-3番手-本命番手 | 押さえ |
| 番手-自力-3番手 | 穴 |
| 別線番手-別線自力-本命自力 | 穴 |

### 強風（wind_speed_mps ≥ 4.0）— 6形

| 形 | カテゴリ |
| --- | --- |
| 本線先頭-別線番手-本線番手 | 押さえ |
| 本線先頭-3番手-本線番手 | 押さえ |
| 番手-先行-3番手 | 押さえ |
| 3番手-番手-先行 | 穴 |
| 別線番手-別線自力-本線自力 | 穴 |
| 本命自力-別線番手-本線番手 | 押さえ |

### 直近結果トレンド（memo解析）

| 検出条件 | 動作 |
| --- | --- |
| 「番手頭」が複数 | 番手頭(`sec-ll-thr`)を **本線** に追加 |
| 「3番手2着上がり」が複数 | `ll-thr-sec` を **押さえ** に追加 |
| 「別線番手」絡みが複数 | `ll-sep_s-sec` を押さえ、`sep_s-ll-thr` を穴に追加 |
| 「本命ライン決着/順当」が複数 | `ll-sec-thr` を本線に再強化 |
| 「波乱/ズレ目/中穴」が複数 (荒れ傾向) | 単騎/自在頭・別線番手頭・3番手頭を **穴/大穴** に追加 |

### カテゴリ間重複時の挙動

同じ買い目が **複数の根拠** で push された場合（例: 強風補正の「本命先頭-別線番手-本命番手」が、既存の「中位2着の捲り展開」と一致）、`reason` に `＋` で **複合理由を追記** します:

```
5-6-1  / 本命頭・中位2着の捲り展開を想定（オッズ60.0） ＋ 強風補正: 本線先頭-別線番手-本線番手 ＋ 直近トレンド: 別線番手絡み多発
```

ガールズレースでは役割タグが全員 `girls` なのでこの拡張はスキップされます（代わりに後述の「ガールズ専用候補」が走ります）。

## フェーズD: 仕様の穴埋め

「仕様100%準拠」に近づけるため、以下の補完を実装。

### D-1: 結果列パターン認識（memoに依存しない）

`recent_results` の `5-1-3` のような結果列の **1着車番** を集計:

| 検出条件 | 動作 |
| --- | --- |
| 同じ車番が3回以上頭 | `main_line_dominant_count` 引き上げ → 鉄板傾向 |
| 4件中ほぼ全部別の車番が頭 | `chaotic_count` 底上げ → 荒れ傾向 |

memo に書いていなくても結果列だけで自動判定するので、Kドリームスから取得した結果（memo が「Kドリームス結果ページから取得」のみ）でもトレンド分析が効きます。

### D-2: ガールズ脚質タグ分類

`classify_girls_role(rider)` が `style_tags` / `comment` から脚質を判定:

| 判定 | 条件 |
| --- | --- |
| 前々型 | `先行` / `自力` タグ or comment に "逃" |
| 追走型 | `追走` / `差し` / `追込` タグ or comment に "追" |
| 自在型 | `自在` タグ or comment に "両" |
| 不明 | 上記いずれも無い |

ガールズ候補生成で仕様の必須形を追加:
- `本命頭-前々型-追走型`
- `対抗頭-本命-追走型`
- `本命頭-前々型-対抗`

### D-3: 補正ルール網羅

`apply_trend_signals` を新規追加し、`predict` パイプラインに `compute_scores → apply_reflection_signals → apply_bank_signals → apply_wind_extra_signals → apply_trend_signals → apply_market_signals → build_candidate_bets` の順で適用:

| 補正 | 動作 |
| --- | --- |
| 番手差し決着多発 | second の win + line_leader の second + third の third 加点 |
| 別線番手絡み多発 | separate_second の second/third 加点 + 本命番手の win 弱め |
| 3番手2着上がり多発 | third の second_score 加点 |
| 強風 + line_length<=2 の line_leader | win/second 弱め（ラインの短い自力） |
| 強風 + solo + 捲りタグ | win 弱め（単独外踏み） |
| 強風 + line_leader | second 弱め（先行2着固定減点） |
| 人気1位 < 4.0倍 | 市場で全く登場しない車番に win 微加点 |
| 3連複 < 5.0倍 | 本線の gami_risk 底上げ |
| 本線全件が gami_risk >= 0.6 | 押さえに比重を移すよう注記 |

### D-4: 基本候補の漏れ補完

仕様12章で挙げられているが未実装だった3形を追加（ガールズ以外）:

| 形 | カテゴリ |
| --- | --- |
| `second-third-line_leader` | 穴 |
| `separate_leader-separate_second-main_leader` | 穴 |
| `solo/jizai-main_leader-main_second` | 大穴 |

仕様準拠率は **80% → 95% 以上** に上がりました（残る5%は「地元番手」「個別車番の好調自動抽出」など情報源不足の項目）。

## ガールズ専用候補（仕様10章）

ガールズ判定 (`is_girls=True`) のとき、`_add_girls_candidate_bets` がスコア順 1〜5位の組み合わせから以下の必須形を生成:

| 形 | カテゴリ | 意図 |
| --- | --- | --- |
| `top1-top2-top3` | 本線 | 本命-対抗-3位の素直 |
| `top1-top3-top2` | 本線 | 2-3着入替 |
| `top1-top2-top4` | 本線 | 3着に中穴 |
| `top1-top4-top2` | 押さえ | 本命頭-中穴2着-対抗3着 |
| `top2-top1-top3` | 押さえ | 対抗頭-本命-3位 |
| `top2-top1-top4` | 押さえ | 対抗頭-本命-中穴 |
| `top1-top5-top2` | 押さえ | 本命-追走型-対抗 |
| `top4-top1-top2` | 穴 | 中穴頭(4位)の波乱形 |
| `top1-top2-top5` | 穴 | 追走型(5位)の3着突っ込み |
| `top5-top1-top2` | 大穴 | 5位頭の大波乱 |

ライン・番手の概念は使わず、純粋にスコア順位による組み合わせ。仕様10章の「ラインがある前提で予想しない」「番手差しという表現を使わない」を遵守。

## 最終結論の4区分化（仕様16章）

`render_prediction` の「10. 最終結論」セクションで以下の4区分を **常に表示** します。仕様16章「最終的には常に出す」要件:

```
## 10. 最終結論
（LLM/Mock の文章）

### 一番買いたい買い目
- 6-3-4 / 4.4倍 / 堅いが安い
- 6-4-3 / 5.6倍 / 本線向き

### 押さえるべき買い目
- 3-6-4 / 61.5倍 / 穴として少額
- 6-5-3 / 28.3倍 / 妙味あり

### 少額で足す穴
- 4-6-3 / 90.9倍 / 穴として少額

### ガミになりやすい買い目
- 6-3-4 / 4.4倍 / 堅いが安い  [gami_risk 0.80]
- 6-4-3 / 5.6倍 / 本線向き  [gami_risk 0.80]
```

各区分のロジック:

| 区分 | 抽出元 |
| --- | --- |
| 一番買いたい買い目 | 本線の上位2点 |
| 押さえるべき買い目 | 押さえカテゴリ全件 |
| 少額で足す穴 | 穴+大穴のうち `value_label = 妙味あり / 穴として少額` |
| ガミになりやすい買い目 | 全カテゴリから `gami_risk >= 0.6`（combination 重複除去） |

「一番買いたい」と「ガミ警戒」に同じ買い目が出ることもあります（高人気=ガミの両面評価）。これは仕様意図通りで、ユーザーが資金配分を決めるための補助情報です。

## スコアリングの考え方

`app/scoring.py` がルールベースで以下のスコアを計算します。  
LLMが落ちている時でも、このスコアと既定のバケット（本線/押さえ/穴/大穴）で予想が完結します。

- `win_score` / `second_score` / `third_score`
- `line_strength` （ガールズでは無効）
- `weather_bonus` （雨補正）
- `wind_bonus` （風補正）
- `odds_value_score` （頭オッズ妙味）
- `trend_bonus` （直近結果 + 場傾向 + 脚質タグ）
- `risk_score` （強風時の先行末脚リスク）
- `gami_risk` （オッズが安すぎる頭の警戒）

ルール抜粋:

- 番手は風が強いほど加点
- 3番手は風が強いほど2着/3着に加点
- 先行自力はB数・逃げ数で加点。強風時は末脚リスクも加算
- 雨なら前々・番手差し・3番手残り・追走を加点
- 直近結果のメモに「別線番手」「3番手」「番手頭」などのキーワードがあれば trend_bonus
- ガールズではライン強さを無効化し、自力・追走・位置取りを評価
- 頭オッズが 5 倍以下なら gami_risk を上げる

---

## LLM クライアント

`app/llm_client.py` に抽象基底 `LLMClient` があります。

- `MockLLMClient` — API不要。スコアと候補買い目から決定論的に Prediction を構築。
- `OpenAIClient` — `openai` SDK を遅延importし、`response_format=json_object` で構造化応答を取得してマージ。
- `AnthropicClient` — `anthropic` SDK を遅延importし、`messages.create` で JSON応答を取得してマージ。

実LLMには `app/prompt_builder.py::build_full_prompt` が組み立てたプロンプトを渡します。これは `prompts/prediction_prompt.md` のテンプレ + JSON応答スキーマ指示を連結したものです。  
**生HTMLは絶対に渡しません。** プロンプトに含まれるのは構造化JSON、スコアリング数値、買い目候補のみです。

実LLMの出力はJSONで以下のフィールドを返します（マージ対象）:

- `summary`, `venue_trend_text`, `weather_text`, `lines_text`
- `final_conclusion`, `gami_memo`, `reflection_points`
- `honsen`, `osae`, `ana`, `ooana`（各要素は `combination` / `reason` 必須、`bet_type` / `gami_risk` 任意）

印（`marks`）と各車のスコア（`rider_scores`）は決定論的計算結果を保持し、LLMには上書きさせません。

### フォールバック仕様

以下のケースで **日本語の警告を stderr に出して `MockLLMClient` にフォールバック** します（例外で落ちません）。

- APIキーが `.env` / 環境変数に設定されていない
- 対応SDK (`openai` / `anthropic`) が pip install されていない
- API呼び出しが例外で失敗した（認証/レート/ネットワーク等）
- LLM応答がJSONとしてパースできない

フォールバック時もCLIの出力フォーマット12項目は保たれます。

### 不正な provider

`--provider` に未対応の値を渡すと、日本語のエラーで終了します。

```bash
$ python -m app.cli predict --input examples/race_sample.json --provider gemini
Error: 未知のLLMプロバイダ: 'gemini'。サポート対象: mock, openai, anthropic
```

---

## 外部データ取得（土台）

`app/fetchers/` 配下に、将来 Kドリームス / オッズパーク / KEIRIN.JP / 天候API など外部ソースから出走表・並び・オッズ・結果・場の傾向を取得するための土台を実装しています。

**現フェーズで実装済み:**

| ソース | クラス | 状態 |
| --- | --- | --- |
| `manual` | `ManualFetcher` | 実装済み。手入力JSONをロードして RaceInput 互換 dict を返す |
| `kdreams` | `KDreamsFetcher` | `fetch_results` / `fetch_race_card` / `fetch_odds` を **試験実装**。場の傾向は未実装 |
| `oddspark` | `OddsParkFetcher` | `fetch_odds` のみ **試験実装**。出走表/結果/場の傾向は未実装 |

**設計原則（CLAUDE.md 準拠）:**

- **自動投票・購入・サイトログインは絶対に実装しない**
- 過剰アクセスをしない（`RateLimiter` でドメイン別の最低wait秒を強制）
- キャッシュを使う（`FileCache`、`.cache/keirin/<sha256>.json`、既定TTL 180秒）
- `User-Agent` を必ず設定する（`HttpClient.DEFAULT_USER_AGENT`）
- 取得失敗時は手入力JSONにフォールバックできる（`--fallback-input`）
- **生HTMLをLLMへ渡さない**（Fetcherは構造化dictのみ上位に返す）
- 取得処理と予想処理を密結合させない（`app/fetchers/` は独立パッケージ）
- サイト規約を尊重する（将来パーサ実装時の自己ルールとして `app/fetchers/kdreams.py` の docstring に記載）

### `fetch-json` コマンド

```bash
# manual ソースから（外部通信なし、ローカルJSONをロード→正規化→出力）
python -m app.cli fetch-json \
  --source manual --kind race_card \
  --input examples/race_sample.json \
  --out examples/fetched_race.json

# Kドリームスの結果ページから1日分の結果を取得（試験実装）
python -m app.cli fetch-json \
  --source kdreams --kind results \
  --venue 大垣 --date 2026-05-22 \
  --out examples/ogaki_results.json

# レース番号指定
python -m app.cli fetch-json \
  --source kdreams --kind results \
  --venue 大垣 --date 2026-05-22 --race-no 1 \
  --out examples/ogaki_1r_result.json

# 取得失敗 → 手入力JSONへフォールバック
python -m app.cli fetch-json \
  --source kdreams --kind results \
  --venue 大垣 --date 2026-05-22 \
  --fallback-input examples/race_sample.json \
  --out examples/ogaki_results.json
```

主なフラグ:

| フラグ | 既定 | 用途 |
| --- | --- | --- |
| `--source` | （必須） | `manual` / `kdreams` / `oddspark` |
| `--kind` | `race_card` | `race_card` / `results` |
| `--out` | （必須） | 出力JSONパス |
| `--input` | - | manual ソースの入力JSON |
| `--venue` / `--race-no` / `--date` | - | 外部ソース用のレース特定パラメータ |
| `--no-cache` | off | HTTPキャッシュを使わない |
| `--cache-ttl` | 180 | キャッシュTTL秒 |
| `--rate-limit-seconds` | 1.0 | 同一ドメインへのアクセス最低間隔（秒） |
| `--fallback-input` | - | 外部失敗時のフォールバック手入力JSON |

`--kind race_card` の出力は RaceInput と同じスキーマで、保存前に `RaceInput.model_validate` を通します。失敗すれば日本語エラーで終了します。  
`--kind results` の出力は次のような envelope 形式です:

```json
{
  "source": "kdreams",
  "kind": "results",
  "venue": "大垣",
  "date": "2026-05-22",
  "results": [
    {
      "date": "2026-05-22",
      "venue": "大垣",
      "race_no": 1,
      "result": "5-6-2",
      "payout": 12340,
      "memo": "Kドリームス結果ページから取得"
    }
  ]
}
```

### `enrich-json` コマンド（外部結果 → recent_results 反映）

`fetch-json --kind results` で取得した結果（envelope or list）や、Fetcher から直接取った結果を、既存の RaceInput JSON に `recent_results` として取り込みます。生成されたJSONは `predict` にそのまま渡せます。

```bash
# 1) 結果を取得（試験実装）
python -m app.cli fetch-json \
  --source kdreams --kind results \
  --venue 大垣 --date 2026-05-22 \
  --out examples/ogaki_results.json

# 2) 既存JSONに取り込む
python -m app.cli enrich-json \
  --input examples/race_sample.json \
  --results-json examples/ogaki_results.json \
  --out examples/race_enriched.json

# 3) そのまま予想
python -m app.cli predict --input examples/race_enriched.json

# 取得とマージを一気にやる
python -m app.cli enrich-json \
  --input examples/race_sample.json \
  --results-source kdreams \
  --venue 大垣 --date 2026-05-22 \
  --out examples/race_enriched.json
```

主なフラグ:

| フラグ | 既定 | 用途 |
| --- | --- | --- |
| `--input` | （必須） | 既存 RaceInput JSON のパス |
| `--out` | （必須） | 出力先 |
| `--results-json` | - | 取得済みの結果JSON（envelope or list） |
| `--results-source` | - | `kdreams` / `manual` から直接取得 |
| `--results-input` | - | `--results-source manual` 用の入力JSON |
| `--venue` / `--date` / `--race-no` | - | `--results-source` 利用時のレース特定 |
| `--max-results` | 制限なし | 最終 recent_results の件数上限（date降順→race_no降順） |
| `--no-dedupe` | off | 同一 (venue/date/race_no/result) の重複を除去しない（既定は dedupe ON） |
| `--no-cache` / `--cache-ttl` / `--rate-limit-seconds` | - | `fetch-json` と同様（`--results-source` 利用時） |

**仕様メモ:**

- `--results-json` と `--results-source` の両方が指定された場合は `--results-json` を優先（案内メッセージあり）
- `--results-json` には envelope（`{source,kind,venue,date,results:[...]}`）と list（`[{date,venue,race_no,result,...}]`）の両方を受け付けます
- envelope の `source` から自動でラベルを決定（`kdreams`→「Kドリームス結果」、`oddspark`→「オッズパーク結果」、`manual`→「手入力結果」）
- 各 result に `memo` が無ければ `f"{ラベル}: {result} / 払戻 {payout:,}円"` を自動生成（`payout` 無しなら `f"{ラベル}: {result}"`）
- dedupe=ON のときは **新規が既存を上書き**（新しい memo / payout 情報を優先）
- 出力は必ず `RaceInput.model_validate` を通します
- 必須項目（`result`）欠如、不正 date 形式、未対応 source、`--venue`/`--date` 欠如はすべて日本語エラーで終了

### `prepare-json` 最短実行

`--venue` と `--date` （任意で `--race-no`）だけで動きます。出力先は `tmp/{venue}_{date}_{NN}r.json` に自動。

```bash
# 1レース（最短）
python -m app.cli prepare-json --venue 平塚 --date 2026-05-22 --race-no 6
# → tmp/平塚_2026-05-22_06r.json

# 全レース（1〜12R を一括生成、取得不能なレースはスキップして警告）
python -m app.cli prepare-json --venue 平塚 --date 2026-05-22
# → tmp/平塚_2026-05-22_01r.json
# → tmp/平塚_2026-05-22_02r.json
# ...
# → tmp/平塚_2026-05-22_12r.json
```

**既定値（最短コマンドで自動的に有効）**:

| フラグ | 既定 | 説明 |
| --- | --- | --- |
| `--source` | `kdreams` | 出走表/結果の取得元 |
| `--session-no` | `1` | 開催日番号（初日） |
| `--weather-source` | `open-meteo` | 天候APIを既定で叩く |
| `--results` | on | 同日の前レース結果を取り込む |
| `--odds` | on | 人気上位オッズを取り込む |
| `--odds-source` | `oddspark` | オッズはオッズパークから |

不要なものは `--no-odds` `--no-results` `--weather-source manual` などで個別に抑制できます。

### `prepare-json` コマンド（取得 → 天候マージ → 結果取り込みを一発）

外部取得・天候上書き・recent_results 取り込みを **1コマンド** で行います。出力は `predict` にそのまま渡せる RaceInput JSON です。

```bash
# Kドリームスから出走表を取り、天候を上書き、同日の前レース結果を取り込んで出力
python -m app.cli prepare-json \
  --source kdreams \
  --venue 大垣 \
  --date 2026-05-22 \
  --race-no 1 \
  --weather 曇り \
  --rain 0 \
  --wind-direction 西 \
  --wind-speed 5.0 \
  --out examples/ogaki_1r_prepared.json

# そのまま予想
python -m app.cli predict --input examples/ogaki_1r_prepared.json

# 結果が出たら反省ログへ保存
python -m app.cli result --race-id 20260522-大垣-1 --result 5-1-3 \
  --input examples/ogaki_1r_prepared.json
```

主なフラグ:

| フラグ | 既定 | 用途 |
| --- | --- | --- |
| `--source` | （必須） | `kdreams` / `manual` |
| `--venue` / `--date` / `--race-no` / `--out` | （必須） | レース指定と出力先 |
| `--weather` / `--rain` | - | 天候・降雨量(mm/h) |
| `--wind-direction` / `--wind-speed` / `--wind-note` | - | 風向・風速(m/s)・自由メモ |
| `--bank-note` | - | バンク特性の自由メモ |
| `--bank-length` | - | バンク周長(m)。bank_note に `周長Nm` として追記 |
| `--results / --no-results` | on | 同日の結果を recent_results に取り込むか |
| `--results-race-no` | - | 特定レースだけ取り込む。未指定なら `--race-no` 未満を取り込む |
| `--max-results` | 制限なし | 取り込み件数の上限（date降順→race_no降順） |
| `--fallback-input` | - | 出走表取得失敗時の手入力JSON |
| `--no-cache` / `--cache-ttl` / `--rate-limit-seconds` | - | HTTP挙動 |

**仕様メモ:**

- 出走表は外部 Fetcher で取得 → 天候上書き → `RaceInput.model_validate` → 必要なら同日結果を取り込み、という順序
- **recent_results に取り込むのは `--race-no` より前のレースのみ**（未来結果の混入を防ぐ）
- 結果取得が失敗しても **出走表が取れていれば処理継続**（日本語警告をstderrへ）
- 出走表取得が失敗したら `--fallback-input` で手入力JSONに切替。それも失敗なら日本語 `PreparationError`（CLI上は `ClickException`）
- 出力前に必ず `RaceInput.model_validate` を通します
- **生HTMLは Fetcher と Parser に閉じ込め**、出力JSONに HTML は混入しません

### 典型フロー

```bash
# 1) JSONを準備
python -m app.cli prepare-json \
  --source kdreams --venue 大垣 --date 2026-05-22 --race-no 1 \
  --weather 曇り --wind-direction 西 --wind-speed 5.0 \
  --out examples/ogaki_1r.json

# 2) 予想
python -m app.cli predict --input examples/ogaki_1r.json

# 3) 結果が出たら反省ログへ
python -m app.cli result --race-id 20260522-大垣-1 --result 5-1-3 \
  --input examples/ogaki_1r.json

# 反省ログを確認
python -m app.cli reflections --venue 大垣

# 4) 蓄積した予想を集計して傾向を見る
python -m app.cli reports
```

### オッズ妙味分析（value-analysis）

予想で組み立てた買い目に対して、ルールベースで **「妙味」「安すぎ」「過剰人気」「狙う価値」** を評価します。LLMには **「厚く買う本線」と「少額穴」を分ける指示** を付けてプロンプトに渡します。

**重要:** これは買い目整理の補助であり、的中保証ではありません。

`predict` 既定で有効。`--no-value-analysis` で無効化できます。

```bash
# 既定（妙味分析あり）
python -m app.cli predict --input examples/race_sample.json

# 妙味分析を抑制（プロンプトと出力からセクション省略）
python -m app.cli predict --input examples/race_sample.json --no-value-analysis
```

**ラベルとスコア:**

| value_label | value_score | 意味 |
| --- | --- | --- |
| 堅いが安い | -0.3 | 強度は高いがオッズが安すぎる。ガミ警戒 |
| 本線向き | +0.3 | 強度・オッズともに妥当。素直に厚く |
| 妙味あり | +0.7 | 強度の割にオッズが高め。積極的に拾う |
| 穴として少額 | +0.2 | 強度は低いが配当狙いで少額残し |
| 見送り寄り | -0.5 | 強度・オッズともに見送り推奨 |
| オッズ未取得・要確認 | 0.0 | オッズが取れていない。要確認 |

判定ロジック（`app/value_analysis.py`）:

1. `predicted_strength` を計算（3連単: `s1.win + s2.second*0.8 + s3.third*0.6` / 2車単 / 3連複ごとに重み付け）
2. 全買い目内での **percentile** で `高/中/低` の3段階に分類
3. 市場オッズの層 `<5 / 5-15 / 15-50 / 50+` と組み合わせてラベル決定
4. オッズが取得できない買い目は **「オッズ未取得・要確認」** で `value_score=0`

ガミリスク調整: 「堅いが安い」と判定された買い目は `gami_risk` を最低 0.6 / 0.7 まで底上げします。

**出力例（render_prediction）:**

```
## 6. 本線
  - 3連単 5-1-6  / スコア上位3名の素直な並び  (18.0倍 / 妙味あり)
  - 3連単 5-1=6  / 上位2-3着を入れ替え  (オッズ未取得・要確認)

## 7. 押さえ
  - 3連単 1-5-6  / 番手頭の捲られ展開  (45.0倍 / 妙味あり)
  - 3連単 5-1-3  / 本命ライン3番手  (12.5倍 / 本線向き)  [低配当注意]
```

LLMプロンプトには `## オッズ妙味分析` セクションが差し込まれ、各買い目の `市場オッズ / 人気順位 / 強度 / ラベル` が伝わります。

### 成績レポート（reports）

保存済みの予想・結果・反省ログを集計します。**これは予想支援・検証機能であり、自動投票や購入処理は持ちません。**

```bash
# 全体集計（text）
python -m app.cli reports

# 場名・期間・天候でフィルタ
python -m app.cli reports --venue 大垣 --from-date 2026-05-01 --to-date 2026-05-31

# 雨天時のみ
python -m app.cli reports --weather 雨

# JSON 出力（機械処理向け）
python -m app.cli reports --format json
```

主なフラグ:

| フラグ | 既定 | 用途 |
| --- | --- | --- |
| `--venue` | - | 場名フィルタ |
| `--from-date` / `--to-date` | - | 期間フィルタ（YYYY-MM-DD、race_id 先頭8桁で比較） |
| `--weather` | - | 天候フィルタ（reflection の weather_condition と完全一致） |
| `--format` | `text` | `text` / `json` |
| `--limit-reflections` | 10 | 反省カテゴリ上位の表示件数 |

集計内容:

- **全体サマリー**: 予想数 / 結果入力済み / 的中数 / 的中率 / 本線・押さえ・穴・大穴的中 / 平均買い目点数 / ガミリスク高件数
- **場別**: venue ごとの予想数・的中率・反省カテゴリ上位
- **天候別**: condition ごと（晴れ/曇り/雨/小雨/強雨/不明 など）
- **風速別**: `0-2m/s` / `2-4m/s` / `4-6m/s` / `6m/s以上` / `不明`
- **レース種別**: ガールズ / 通常戦
- **反省カテゴリ上位**: 「別線番手を軽視 12回」のような頻出カテゴリ
- **妙味ラベル別成績**: `value_label` ごとの 買い目数 / 的中数 / 的中率（「本線向き」「妙味あり」「堅いが安い」など）
- **ガミリスク高で的中した買い目数**: `gami_risk >= 0.6` のうち的中したものの件数
- **改善メモ**（ルールベース、例）:
  - 雨天時に「別線番手を軽視」が多発 → 雨の予想では別線番手2着を押さえに追加
  - 風速4m/s以上で「3番手の伸びを軽視」が多発 → 強風時は3番手2着を増やす
  - ガールズで「位置取り評価不足」が多発 → 2着中穴を残す
  - 「穴を広げすぎてガミリスク増加」が多発 → 穴・大穴の点数を絞る
  - 「本命自力の過信」が多発 → 先行頭固定を弱め、別線頭の押さえを増やす

的中分類（`main_hit` / `backup_hit` / `longshot_hit` / `big_longshot_hit` / `miss`）は predict 時に保存された Prediction の本線・押さえ・穴・大穴と result の組み合わせから再計算します。優先順位は **本線 → 押さえ → 穴 → 大穴 → miss**。`listed_but_not_main` は本線以外でヒットした件数の合計です。

`--format json` の出力構造（抜粋）:

```json
{
  "filters": {"venue": "大垣", "from_date": null, "to_date": null, "weather_condition": null},
  "summary": {
    "label": "全体",
    "total": 12,
    "with_result": 10,
    "main_hit": 3,
    "backup_hit": 1,
    "longshot_hit": 1,
    "big_longshot_hit": 0,
    "miss": 5,
    "listed_but_not_main": 2,
    "hit_rate": 0.5,
    "avg_bet_count": 9.42,
    "high_gami_count": 4,
    "top_categories": [["別線番手を軽視", 5]]
  },
  "by_venue": {...},
  "by_weather": {...},
  "by_wind_bucket": {...},
  "by_race_class": {"girls": {...}, "regular": {...}},
  "top_reflection_categories": [["別線番手を軽視", 5]],
  "improvement_notes": ["雨天時に「別線番手を軽視」が5件発生。..."]
}
```

### Kドリームスの URL 構造と `--session-no`

実 Kドリームス（楽天Kドリームス競輪）の URL は以下のパス構造です:

```
出走表: https://keirin.kdreams.jp/{slug}/racecard/{kaisaiDateId}/
結果:   https://keirin.kdreams.jp/{slug}/raceresult/{kaisaiDateId}/
オッズ: https://keirin.kdreams.jp/{slug}/racedetail/{raceId}/?pageType=odds&kakeshikiType={3rentan|3renpuku|2tanshou}
```

- `slug` は場名の英字スラッグ（平塚→`hiratsuka`、大垣→`ogaki` 等）
- `kaisaiDateId` = `{jo:02d}{YYYYMMDD}{session:02d}00`（開催日単位）
- `raceId` = `{jo:02d}{YYYYMMDD}{session:02d}{race:02d}`（1レース単位）
- **session_no = 開催日番号**（初日=1, 2日目=2, ...）。CLI で `--session-no 2` のように指定可能

例: 平塚 2026-05-22 開催初日4R → URL は `https://keirin.kdreams.jp/hiratsuka/racedetail/3520260522010 04/?pageType=odds&kakeshikiType=3rentan`

### Kドリームス出走表取得（試験実装）

`fetch-json --source kdreams --kind race_card` で出走表ページから RaceInput 互換 JSON を生成できます。生成されたJSONは `predict` にそのまま渡せます。

```bash
python -m app.cli fetch-json \
  --source kdreams --kind race_card \
  --venue 大垣 --date 2026-05-22 --race-no 1 \
  --session-no 1 \
  --out examples/ogaki_1r_card.json

python -m app.cli predict --input examples/ogaki_1r_card.json
```

**重要な注意:**

- **試験実装**です。実 URL 構造は WebFetch で確認済みですが、**HTMLパーサは fixture HTML 用に設計されたまま**で、実サイトの DOM 構造と一致しない可能性があります。実接続時はパーサの調整が必要になることがあります（その場合は `app/fetchers/parsers/kdreams_*.py` を実HTMLに合わせて差し替えてください）
- 自動投票・購入・ログインの実装は **絶対にしません**（GETのみ、POST未実装）
- `HttpClient` のレート制限・キャッシュ・User-Agent をすべて経由します
- パース層は `app/fetchers/parsers/kdreams_race_card.py` に分離してあり、サイト構造変更時はここだけ差し替えればOK
- **生HTMLは Fetcher 内に閉じ込め**、上位（CLI / LLM / 予想エンジン）には構造化dictのみ渡します
- `class_name` / `start_time` / `score` / `b_count` などが取れない場合は `None`(文字列) / `0.0`(score) / `0`(整数) になります
- ライン情報が取れない場合は `lines: []` になります → その場合は手で編集するか、`quick-json` / `create-json --interactive` で並びを追記してください
- 選手が1人も取れなかった場合は日本語 `FetchError`（`--fallback-input` を渡せば手入力JSONに切替可）

**出力JSONに含まれる項目（出走表ベース）:**

- `race.race_id` — `{YYYYMMDD}-{venue}-{race_no}` 形式で自動生成
- `race.date` / `race.venue` / `race.race_no` / `race.class_name` / `race.start_time`
- `riders[]` — `car_no` / `name` / `score` / `b_count` / `nige` / `makuri` / `sashi` / `mark` / `comment` / `recent_summary`
- `lines[]` — `line_name` / `cars` / `description`

**出力に含まれない項目（このフェーズでは未対応）:**

- `weather` — `None`（必要なら `enrich-json` や手編集で追加）
- `odds` — `[]`
- `recent_results` — `[]`（`enrich-json --source kdreams --kind results` で別途取り込み可能）
- `venue_trend` — `None`

```bash
# 取得 → 結果を追加で取り込み → 予想 の典型フロー
python -m app.cli fetch-json --source kdreams --kind race_card \
  --venue 大垣 --date 2026-05-22 --race-no 1 \
  --out tmp_card.json

python -m app.cli enrich-json \
  --input tmp_card.json \
  --results-source kdreams \
  --venue 大垣 --date 2026-05-22 \
  --out examples/ogaki_1r_ready.json

python -m app.cli predict --input examples/ogaki_1r_ready.json
```

### 天候API取得（試験実装・APIキー不要想定）

`fetch-weather` で外部 APIから Weather を取得し、`prepare-json --weather-source` で出走表＋結果＋オッズと一緒にまとめられます。

**対応プロバイダ:** `open-meteo`（[Open-Meteo](https://open-meteo.com/) を想定。APIキー不要）

**対応場名（緯度経度は近似値、41場）:** Kドリームスの jo_code 対応表と同じ場名がすべて使えます（平塚 / 立川 / 川崎 / 取手 / 千葉 / 別府 / 小倉 など）

```bash
# 単独で取得
python -m app.cli fetch-weather \
  --provider open-meteo \
  --venue 松山 \
  --date 2026-05-22 \
  --start-time 17:36 \
  --out examples/matsuyama_weather.json

# prepare-json に統合
python -m app.cli prepare-json \
  --source kdreams \
  --venue 松山 \
  --date 2026-05-22 \
  --race-no 8 \
  --weather-source open-meteo \
  --start-time 17:36 \
  --out examples/matsuyama_8r.json

python -m app.cli predict --input examples/matsuyama_8r.json
```

**fetch-weather 出力例:**

```json
{
  "source": "open-meteo",
  "venue": "松山",
  "date": "2026-05-22",
  "start_time": "17:36",
  "weather": {
    "condition": "雨",
    "rain_mm_per_hour": 1.0,
    "wind_direction": "南西",
    "wind_speed_mps": 2.0,
    "wind_note": "南西2.0m/s",
    "temperature_c": 12.0
  }
}
```

**仕様メモ:**

- **APIキー不要**: Open-Meteo は無料・無キーで利用可能（ただしサイト規約・レート制限を尊重）
- 緯度経度は **近似値**。場所コードや厳密な計測点とは異なる
- `--start-time` を指定した場合は、その時刻に最も近い hourly を採用。未指定なら **正午** を採用
- `weather_code` → 日本語 condition（晴れ/曇り/雨/小雨/雪/霧/雷雨/不明）
- `wind_direction_10m` → 8方位の日本語（北/北東/東/...）
- `wind_speed_10m` は **m/s** 単位で取得（`wind_speed_unit=ms` 指定）
- **生APIレスポンスは Provider 内に閉じ込め**、上位（CLI/LLM/予想エンジン）には `Weather` モデルだけ渡す
- `HttpClient` のレート制限（既定1秒）・キャッシュ（既定TTL 180秒）・User-Agent を必ず通す

**フォールバック仕様（prepare-json --weather-source）:**

- API失敗時は **日本語警告をstderrへ** 出し、処理は継続
- 手入力の `--weather / --rain / --wind-direction / --wind-speed / --wind-note` を指定していれば、**API結果に対して手入力を上書き**（手入力が優先）
- 手入力もなく API も失敗した場合は weather 無しで RaceInput を返す（race_card は維持）

### Kドリームスオッズ取得（試験実装）

`fetch-json --kind odds` で 3連単 / 3連複 / 2車単 の **人気上位N件** を取得できます。  
全オッズの完全取得は目的外で、**ガミ回避・本線/穴の評価・人気偏りの確認** のために使います。

```bash
# 3種類まとめて取得
python -m app.cli fetch-json \
  --source kdreams --kind odds \
  --venue 大垣 --date 2026-05-22 --race-no 1 \
  --out examples/ogaki_1r_odds.json

# 種別と件数を絞る
python -m app.cli fetch-json \
  --source kdreams --kind odds \
  --venue 大垣 --date 2026-05-22 --race-no 1 \
  --bet-type trifecta --limit 20 \
  --out examples/ogaki_1r_trifecta_odds.json
```

`--bet-type` のサポート対象: `trifecta`（3連単）/ `trio`（3連複）/ `exacta`（2車単）

出力JSONの例:

```json
{
  "source": "kdreams",
  "kind": "odds",
  "venue": "大垣",
  "date": "2026-05-22",
  "race_no": 1,
  "odds": {
    "trifecta_popular": [
      {"rank": 1, "combination": "5-1-3", "odds": 8.5}
    ],
    "trio_popular": [
      {"rank": 1, "combination": "1=3=5", "odds": 4.0}
    ],
    "exacta_popular": [
      {"rank": 1, "combination": "5-1", "odds": 3.6}
    ]
  }
}
```

**仕様メモ:**

- combination は正規化: 3連単/2車単 → `-` 区切り、3連複 → `=` 区切り
- 全角数字（`０〜９`）、全角ハイフン/`＝`、`12,340`カンマ、`8.5倍`、`¥1,234` 等は自動正規化
- `-` / 空欄 / 「未確定」/ 0以下のオッズは自動スキップ
- 種別を指定しない場合は 3種類分のHTTPリクエストが発生（**3回**）— レート制限を必ず通る
- パース層は `app/fetchers/parsers/kdreams_odds.py` に分離してあり、サイト構造変更時はここだけ差し替えればOK
- **生HTMLは Fetcher 内に閉じ込め**、上位には構造化dictのみ渡す

### 予想点数の調整（`--bet-budget`）

合計買い目点数を目標値で指定すると、本線/押さえ/穴/大穴に自動配分されます。

```bash
# 絞り込み運用（合計約12点）
python -m app.cli predict --input tmp/race.json --bet-budget 12

# 標準（合計約18-20点）
python -m app.cli predict --input tmp/race.json --bet-budget 18

# 広め（合計約25-30点）
python -m app.cli predict --input tmp/race.json --bet-budget 28
```

**配分の目安**:

| `--bet-budget` | 本線 | 押さえ | 穴 | 大穴 | 合計 |
| --- | --- | --- | --- | --- | --- |
| 10 | 3 | 3 | 3 | 1 | 10 |
| 12 | 3 | 4 | 4 | 1 | 12 |
| 15 | 4 | 4 | 5 | 2 | 15 |
| 18（推奨） | 4 | 5 | 6 | 3 | 18 |
| 20 | 5 | 6 | 7 | 2 | 20 |
| 25 | 6 | 7 | 8 | 4 | 25 |
| 30 | 7 | 9 | 10 | 4 | 30 |

**配分比率**: 本線20% / 押さえ30% / 穴35% / 大穴15%（最低保証 2/2/2/1 を確保）。

#### 設定の優先順位

1. CLI フラグ `--bet-budget`（最優先）
2. `.env` の `BET_BUDGET=18`
3. 未指定: 既定値（合計13〜20点）で動作

#### Streamlit UI

サイドバーの「**予想点数（目安）**」スライダー（10〜30）で調整可能。

#### 適用範囲

各カテゴリの実点数は配分目標 + ライン構造優先・トレンド形・市場注目別線などの**必須形 force_push** で多少前後します。`build_candidate_bets` の `MAX_*` / `HARD_*` 上限が `target_total` ベースで動的計算されます。

---

### 選手統計データの品質区別（actual / estimated / missing）

競走得点・B数・決まり手の数値は **取得元によって信頼度が大きく異なる** ため、3段階で区別しています。

#### 品質タグの意味

| タグ | 意味 | 取得元 |
| --- | --- | --- |
| **actual** | 実数値（信頼度高） | 手入力・将来の動的取得（実装中） |
| **estimated** | 推定値（信頼度中） | yen-joy 静的取得（戦法ラベル → 決まり手回数の推定） |
| **missing** | 取得失敗（信頼度ゼロ） | 取得経路の失敗・該当データなし |

**重要**: `quality=missing` の場合、数値フィールドは 0 ですが、これは「**実際に 0 回**」を意味しません。**0 と未取得を厳密に区別**しています。

#### 検証用 CLI: `fetch-rider-stats`

選手の競走得点・決まり手を取得して JSON で確認できます（本番の `prepare-json` には組み込まれない独立検証用）:

```bash
# yen-joy 静的取得（estimated 扱い）
python -m app.cli fetch-rider-stats \
  --source yenjoy \
  --venue 武雄 \
  --date 2026-05-23 \
  --race-no 9 \
  --out tmp/rider_stats.json

# 手入力 JSON から（actual 扱い）
python -m app.cli fetch-rider-stats \
  --source manual \
  --venue 武雄 \
  --date 2026-05-23 \
  --race-no 9 \
  --manual-path my_rider_stats.json \
  --out tmp/rider_stats.json

# Playwright 経由（yenjoy_dynamic、現状未安定）
python -m app.cli fetch-rider-stats \
  --source yenjoy_dynamic \
  --venue 武雄 \
  --date 2026-05-23 \
  --race-no 9 \
  --out tmp/rider_stats.json
```

#### 出力フォーマット

```json
{
  "source": "yenjoy_static",
  "venue": "武雄",
  "date": "2026-05-23",
  "race_no": 9,
  "session_no": 1,
  "riders": [
    {
      "car_no": 7,
      "name": null,
      "score": 112.36,
      "b_count": 3,
      "nige": 5,
      "makuri": 5,
      "sashi": 0,
      "mark": 0,
      "quality": "estimated",
      "source_label": "yenjoy_static",
      "notes": "戦法:逃捲"
    },
    ...
  ],
  "quality_summary": {
    "actual_count": 0,
    "estimated_count": 9,
    "missing_count": 0,
    "total": 9
  },
  "fetched_at": "2026-05-23T14:36:00",
  "warnings": []
}
```

#### 各ソースの取得状況（2026-05時点）

| ソース | 競走得点 | B数 | 決まり手 | quality |
| --- | --- | --- | --- | --- |
| `yenjoy` (静的) | ✅ 実数 | ⚠️ 戦法から推定 | ⚠️ 戦法から推定 | estimated |
| `yenjoy_dynamic` (Playwright) | ❌ 未実装 | ❌ 未実装 | ❌ 未実装 | missing |
| `manual` (手入力JSON) | ✅ 入力次第 | ✅ 入力次第 | ✅ 入力次第 | actual |
| Kドリームス `/racedetail/` | ✅ 取れるが**ログイン必須**（仕様で不可） | ❌ | ✅ 同上 | - |

#### Playwright 経路は **現状未安定**

`yenjoy_dynamic` ソースは Playwright で yen-joy の「決まり手・BHJS集計」タブを動的取得する想定ですが:

- yen-joy のボタンが hidden 状態で `force=True` でも click 不安定
- API エンドポイントは外部公開されていない（reCAPTCHA 経由）
- 現状は warnings 付きで全選手 `missing` を返す

将来 DOM 構造調査が進めば actual 取得可能になります。**それまでは推定値運用（yen-joy 静的取得 → scoring の数値不足モード）を継続**します。

#### 既存パイプラインへの影響

`fetch-rider-stats` は **独立検証用** で、既存の `prepare-json` には組み込まれていません。本番経路（`prepare-json` → `predict`）は引き続き:

1. Kドリームス出走表（選手名・ライン・脚質）
2. yen-joy 補完（競走得点 + 戦法ラベル経由の推定決まり手）
3. 補完できない場合は **数値不足モード**（コメント + 市場オッズ + ライン構造で予想）

の3段階で動きます。「数値不足モード」も完備しているので、取得失敗が予想全体を止めることはありません。

### 共通 RaceNotes（複数の補助情報源対応）

東スポ・WINTICKET・netkeirin・オッズパーク・yenjoy・手入力テキストなど、複数の補助情報源を **共通の RaceNotes 構造** で取り込めます（フェーズ G）。

**Pydantic モデル**（著作権配慮で max_length 強制）:

```python
class RiderNote:
    car_no: int                       # 1-9
    name: Optional[str]               # 最大50文字
    comment_summary: str              # 最大120文字（短い要約）
    signals: list[str]                # 自力/番手/状態良い 等
    confidence: Optional[float]       # 0.0-1.0
    raw_excerpt: Optional[str]        # 最大50文字（既定で含めない）

class RaceNotes:
    source: Literal["tospo", "winticket", "netkeirin", "oddspark", "yenjoy", "manual_text", "generic"]
    venue / date / race_no
    race_summary: Optional[str]       # 最大300文字
    rider_notes: list[RiderNote]
    line_hint: Optional[str]          # 最大200文字
    prediction_hint: Optional[str]    # 最大300文字
```

**signals 共通辞書（19+α種類）**:

`自力 / 前々 / 単騎 / 自在 / 番手 / 3番手 / 地元 / 状態良い / 疲れ / 不安 / 落車明け / 穴評価 / 本命評価 / 差し有力 / 先行有力 / 位置取り良い / コメント強気 / コメント弱気 / 追込 / 状態普通 / 重い`

#### 手入力テキストから RaceNotes を生成

新聞・予想記事・公式コメントをコピペで貼り付けたファイルから RaceNotes を作れます。

入力形式（柔軟）:
```
場名: 松山
日付: 2026-05-22
R: 10

並び: 5-1-3 / 6-4 / 7
記者見解: 本線は5-1。穴は6-4

5 長野魅切 自力。状態は良い。前々に踏める。
1 久樹 長野マーク。番手。差し脚良好。
3 山本 3番手。位置取り良い。
...
```

```bash
# RaceNotes JSON を生成
python -m app.cli parse-race-notes \
  --source winticket \
  --input notes.txt \
  --venue 松山 --date 2026-05-22 --race-no 10 \
  --out matsuyama_10r_notes.json

# 既存の RaceInput JSON にマージ
python -m app.cli merge-notes \
  --input race_input.json \
  --notes matsuyama_10r_notes.json \
  --out race_with_notes.json
```

#### prepare-json から直接取り込む

```bash
# 事前作成した RaceNotes JSON を取り込み
python -m app.cli prepare-json \
  --venue 松山 --date 2026-05-22 --race-no 10 \
  --race-notes-json matsuyama_10r_notes.json

# テキストファイルから直接パース+マージ
python -m app.cli prepare-json \
  --venue 松山 --date 2026-05-22 --race-no 10 \
  --race-notes-text notes.txt \
  --race-notes-source winticket
```

#### Web UI から

「予想作成」タブの **「コメント・記者補助情報」** エクスパンダー:
- ソース選択（manual_text/tospo/winticket/netkeirin/oddspark/yenjoy）
- テキスト貼り付け欄
- 「予想生成時に取り込む」チェック → 予想生成と同時にマージ

#### 著作権・利用規約配慮（型レベル強制）

- `comment_summary`: **最大120文字**（Pydantic で強制）
- `race_summary`: **最大300文字**
- `raw_excerpt`: **最大50文字**（既定では使わない）
- 全文転載・長文引用なし
- LLMには **要約 + signals のみ** 渡す（HTML/本文を流さない）

#### ソース別の状態

| source | 状態 | 取得方法 |
| --- | --- | --- |
| `tospo` | 試験実装 | URL直接指定 (`--tospo-url`) または手入力 |
| `winticket` | 手入力のみ | テキスト貼り付け |
| `netkeirin` | 手入力のみ | テキスト貼り付け |
| `oddspark` | 手入力のみ | テキスト貼り付け（オッズ取得は別途） |
| `yenjoy` | 手入力のみ | テキスト貼り付け |
| `manual_text` | フル対応 | テキスト貼り付け |

#### マージ後の RaceInput

- `Rider.comment` に `[<ソース日本語ラベル>] 要約` が追記（既存comment は保持）
- `Rider.style_tags` に signals が追加（重複除外）
- `RaceInput.user_note` に `[<ソース>] 記者見解 / 並び / 予想ヒント` を追記

例:
```
Rider 5 comment: "自力先行 ／ [東スポ] 状態良い ／ [WINTICKET] 前々に踏める"
RaceInput user_note: "... ／ [WINTICKET] 5-1-3 / 6-4 / 本線は5-1"
```

#### プロンプトへの反映

LLM プロンプトに `## コメント・記者補助情報` セクションが自動追加されます（複数ソース混在対応）:

```
## コメント・記者補助情報

### 選手コメント要約
  - [東スポ] 車5 池部: 状態良い [先行, 自力]
  - [WINTICKET] 車1 楢原: 番手差し脚良好 [番手, 差し]

### 記者見解 / 並び / 予想ヒント
  - [WINTICKET] 5-1-3 / 6-4 / 本線は5-1
```

### 東スポ補助情報（コメント・記者見解・signals）

東スポ競輪の予想記事から **選手コメントの短い要約** と **signals**（自力/前々/単騎/番手/自在/状態良い/不安/重い/疲れ）を取り込めます。

**位置づけ:**
- **補助データ**: 主データ（出走表/結果/オッズ/天候）は Kドリームス・オッズパーク・Open-Meteo
- 東スポは **試験実装**。URL構造が安定しないためURL直接指定方式
- 取得失敗しても警告のみで処理は続行

**著作権配慮（必須）:**
- **全文転載しません**。コメントは短い要約（最大40文字）+ signals に変換
- `raw_excerpt` は既定で **含めません**（明示要求時のみ50文字以内）
- LLMには要約と signals のみ渡し、HTMLや本文全体は渡しません
- データベース・JSONには長文を保存しません

**取得方法:**

```bash
# 単独取得
python -m app.cli fetch-json \
  --source tospo --kind race_notes \
  --url "https://keirin.tokyo-sports.co.jp/..." \
  --venue 松山 --date 2026-05-22 --race-no 10 \
  --out examples/tospo_notes.json

# prepare-json から取り込む
python -m app.cli prepare-json \
  --venue 松山 --date 2026-05-22 --race-no 10 \
  --tospo-notes \
  --tospo-url "https://keirin.tokyo-sports.co.jp/..."
```

**Web UI:**
- 「予想作成」タブの **東スポ補助情報** エクスパンダーで URL を入力 + チェックボックスで有効化
- 失敗しても予想は続行（警告表示のみ）

**取り込まれる情報:**
- `Rider.comment` に `[東スポ] 要約` が追記（既存comment は上書きせず `／` 区切りで連結）
- `Rider.style_tags` に signals が追加（重複除去）
- `RaceInput.user_note` に `[東スポ] 記者見解 / 並び / 予想ヒント` を追記

**スコアリングへの反映 (`apply_tospo_signals`):**

| signal | 補正 |
| --- | --- |
| 自力 | win +0.3 |
| 前々 | win +0.3 |
| 単騎 | win +0.2 |
| 番手 | win/second +0.2 |
| 自在 | win/second/third +0.1 |
| 状態良い / 好調 | win +0.2 / second +0.1 / third +0.1 |
| 不安 / 重い / 疲れ | win -0.3 |

補正は最大±0.5程度（補助情報なので強くしすぎない）。

**プロンプトへの反映:**

LLMには「## 東スポ補助情報」セクションが追加され、各車の要約+signals + 記者見解ヒントが渡されます（HTMLや本文は渡さない）。

**取得失敗時:**

- HTTP 失敗 → 警告のみで処理続行（出走表・予想は維持）
- パース失敗 → 「サイト構造変更の可能性」と日本語エラー
- URL自動生成は未実装（`build_tospo_race_url` は `FetchError`）

### オッズパーク連携（オッズ専用）

Kドリームスのオッズページは静的HTMLには含まれず取得できないため、オッズ取得は **オッズパーク** を別ソースとして使えます。

**URL構造（実サイト確認済み）:**
```
https://www.oddspark.com/keirin/Odds.do
  ?joCode={jo}&kaisaiBi={YYYYMMDD}&raceNo={N}&betType={9|8|6}&viewType=1
```
- `joCode` は JKA 共通コード（Kドリームスと同じ。例: 平塚=35）
- `betType`: 3連単=9 / 3連複=8 / 2車単=6
- `viewType=1` で人気順表示

**使い方:**

```bash
# 出走表は kdreams、オッズだけオッズパークから
python -m app.cli prepare-json \
  --source kdreams \
  --venue 平塚 --date 2026-05-22 --race-no 4 --session-no 1 \
  --weather-source open-meteo \
  --results \
  --odds --odds-source oddspark \
  --out tmp/hiratuka_4r.json

# fetch-json でも個別取得可能
python -m app.cli fetch-json \
  --source oddspark --kind odds \
  --venue 平塚 --date 2026-05-22 --race-no 4 \
  --bet-type trifecta --limit 20 \
  --out examples/hiratuka_4r_odds.json
```

**仕様メモ:**

- `--odds-source kdreams` (既定で `--source` と同じ) / `--odds-source oddspark` で切替
- パーサは「テーブル行内で買い目パターン(`\d-\d-\d`等)と数値オッズが揃う行」を緩く拾うヒューリスティック方式。サイト構造変更に強く、まずは試験運用向け
- 取得失敗時は **race_card は維持して警告のみ**（既存仕様）
- 規約遵守: User-Agent明示、レート制限（既定1秒）、キャッシュ（既定TTL180秒）を必ず通す。**自動投票・ログインなし**

### `prepare-json --odds` でオッズも一括取得

```bash
python -m app.cli prepare-json \
  --source kdreams \
  --venue 大垣 --date 2026-05-22 --race-no 1 \
  --weather 曇り --wind-direction 西 --wind-speed 5.0 \
  --odds --odds-limit 20 \
  --out examples/ogaki_1r_prepared.json

python -m app.cli predict --input examples/ogaki_1r_prepared.json
```

- `--odds / --no-odds`（既定: off）
- `--odds-bet-type` で種別を絞れる（未指定は3種類）
- `--odds-limit` で人気上位件数の上限
- オッズ取得失敗時は **race_card / results は維持** して警告のみ stderr へ

サイト規約を尊重し、過剰アクセスはしないでください。レート制限（既定1秒間隔）とキャッシュ（既定TTL 180秒）が必ず通ります。

### Kドリームス結果取得（試験実装）

**重要な注意:**

- **試験実装**です。実サイト構造が変わるとパースが失敗します
- 自動投票・購入・ログインの実装は **絶対にしません**（GETのみ、POST未実装）
- `HttpClient` のレート制限とキャッシュを必ず通します（既定1秒間隔／TTL 180秒）
- `User-Agent` は予想支援用途であることを明示します
- 取得失敗・パース失敗・未対応場名は日本語の `FetchError` を出します。`--fallback-input` で手入力JSONに切り替え可能
- パース層は `app/fetchers/parsers/kdreams_results.py` に分離してあり、サイト構造変更時はここだけ差し替えればOK
- **生HTMLは Fetcher 内に閉じ込め**、上位（CLI / LLM / 予想エンジン）には構造化dictのみ渡します

対応場名（41場・jo_code は概ね JKA 公式準拠だが、サイト構造が変わる可能性があるため実接続時は要検証）:

| 地区 | 場名（jo_code） |
| --- | --- |
| 北海道・東北 | 函館 (11) / 青森 (12) / いわき平 (13) |
| 関東甲信越 | 弥彦 (21) / 前橋 (22) / 取手 (23) / 宇都宮 (24) / 大宮 (25) / 西武園 (26) / 京王閣 (27) / 立川 (28) / 松戸 (31) / 千葉 (32) / 川崎 (34) / **平塚 (35)** / 小田原 (36) / 伊東温泉 (37) |
| 東海 | 静岡 (38) / 名古屋 (42) / 岐阜 (43) / 大垣 (44) / 豊橋 (45) |
| 北陸 | 富山 (47) / 松阪 (48) / 四日市 (49) / 福井 (50) |
| 近畿 | 奈良 (53) / 向日町 (54) / 和歌山 (55) / 岸和田 (61) |
| 中国・四国 | 玉野 (62) / 広島 (63) / 防府 (64) / 高松 (71) / 高知 (74) / 松山 (75) |
| 九州 | 小倉 (81) / 久留米 (83) / 武雄 (84) / 佐世保 (85) / 別府 (86) / 熊本 (87) |

未対応場名や日付の形式エラーは「未対応の場名です」「日付は YYYY-MM-DD で...」のような日本語エラーで弾きます。

サイト規約を尊重し、過剰アクセスはしないでください。本機能を恒常的に使う場合は対象サイトの利用規約を再確認した上で利用してください。

### 将来の外部パーサを追加する時のチェックリスト

1. `HttpClient` を使う（直接 `requests.get` しない）→ User-Agent / timeout / キャッシュ / レート制限が自動で効く
2. 受け取った生HTMLは内部関数だけで処理し、`fetch_*` の戻り値は構造化 dict にする
3. 通信失敗・パース失敗は `FetchError`（日本語メッセージ）に変換する
4. 投票・購入・ログイン系の処理は **絶対に追加しない**
5. テストでは `session=MagicMock()` でレスポンスを差し替え、実通信しない

## 反省ログの自動注入（predict時）

`predict` 実行時、SQLite に蓄積された過去の `Reflection` から **当該レースに条件が近いもの** を自動で参照し、スコアリングとLLMプロンプトの両方に反映します。

関連度のスコアリング（`storage.get_relevant_reflections`）:

- `venue` 一致 → +5.0
- `class_name` 一致 → +1.5 / 先頭2文字一致 → +0.5
- `weather.condition` 一致 → +2.0
- 風速差 ±1.0 → +2.0 / ±2.0 → +1.0、双方 ≥5m/s → +0.5
- 雨量レイヤ一致 → +1.0
- 直近（1日 → +2.0 / 7日 → +1.0 / 30日 → +0.5）

ガールズと通常戦は **常に分離** されます（戦法体系が違うため）。スコア 0 以下のものは除外されます。

`scoring.apply_reflection_signals` によるスコア補正の例:

- 「別線番手を軽視」「別線番手の2着上がりを軽視した」 → 別線番手 (`bantan_other`) の win/second/third を加点
- 「3番手の伸びを軽視」「3番手の2着上がりを軽視した」 → 3番手 (`third_any`) の second/third を加点
- 「本命自力の過信」 → 先行 (`head`) の win を微減
- 「本線番手を過信」 → 同ライン番手 (`bantan_same`) の win を微減
- 「本線ラインの3着を固定しすぎた」 → 別線3番手/別線番手の third を加点
- 「風補正不足」「雨補正不足」 → 番手・3番手の `reflection_bonus` を加点
- 「穴を広げすぎてガミリスク増加」 → 穴/大穴の `gami_risk` を底上げ（買い目側で警告マーク）
- 「ガールズの位置取り評価不足」 → ガールズ時の自力/追走/位置取りタグを加点

すべて補正幅は ±1.0 程度に抑え、本線決定を機械的に固定しません。

LLMプロンプトには末尾に `## 過去の反省からの補正` セクションが追加され、各反省が次のように1行で渡されます:

```
- 2026-05-20 大宮5R [曇り 風4.0m/s] 別線番手を軽視 / 3番手の伸びを軽視  予想本線: 5-1-3 / 結果: 2-6-1  メモ: 北風4m/sでは別線番手の頭・3着を残す
```

CLI フラグ:

| フラグ | 既定 | 用途 |
| --- | --- | --- |
| `--use-reflections / --no-reflections` | on | 反省ログの自動注入を有効/無効 |
| `--reflection-limit` | 5 | 注入する反省ログの最大件数 |

反省ログが1件もない場合でも predict は通常通り動作します（プロンプトには「関連する過去の反省ログはありません」と1行入ります）。

## 結果反省

`result` コマンドは以下を行います。

1. 入力された結果（例: `5-1-3`）を `results` テーブルに保存
2. 予想と突き合わせて `Reflection` を生成
3. 反省カテゴリを分類して `reflections` テーブルに保存

分類カテゴリの例:

- 的中
- 買い目にはあったが本線ではなかった
- 本線番手を過信
- 別線番手を軽視
- 3番手の伸びを軽視 / 3番手の2着上がりを軽視した
- 別線番手の2着上がりを軽視した
- 本線ラインの3着を固定しすぎた
- 風補正不足 / 雨補正不足
- ガールズの位置取り評価不足
- 本命自力の過信
- 穴を広げすぎてガミリスク増加

これらは `app/reflection.py::classify` のルールで自動付与されます。  
`--note` で自由メモを残せます。

---

## ディレクトリ構成

```
keirin-v2/
  app/
    __init__.py
    cli.py
    config.py            # .env / 環境変数の読み込み
    models.py
    scoring.py
    prompt_builder.py
    reflection.py
    storage.py
    llm_client.py        # Mock / OpenAI / Anthropic
    race_input_builder.py # parse_lines / quick-json / 対話モードのビルダー
    enrichment.py         # merge_recent_results / enrich-json用ロジック
    preparation.py        # prepare_race_input / prepare-json用ロジック
    reporting.py          # build_performance_report / reports用ロジック
    value_analysis.py     # オッズ妙味分析（value_label / value_score）
    weather/              # 天候API連携
      __init__.py
      base.py             # WeatherProvider抽象 + WeatherFetchError
      venues.py           # 場名→(lat, lon) マップ
      parsers.py          # weather_code/風向/時刻選択
      open_meteo.py       # Open-Meteo 実装
    fetchers/             # 外部データ取得の土台
      __init__.py
      base.py             # Fetcher抽象 + FetchError
      http.py             # HttpClient (UA/timeout/cache/rate_limit)
      cache.py            # FileCache (TTL)
      rate_limit.py       # RateLimiter
      manual.py           # ManualFetcher（実装済み）
      kdreams.py          # fetch_results 試験実装。他は未実装
      oddspark.py         # スケルトン
      parsers/            # 外部サイト用HTMLパーサ
        __init__.py
        kdreams_results.py   # Kドリームス結果ページ専用
        kdreams_race_card.py # Kドリームス出走表ページ専用
        kdreams_odds.py      # Kドリームスオッズページ専用 (trifecta/trio/exacta)
        oddspark_odds.py     # オッズパークオッズページ専用
  examples/
    race_sample.json
    quick_sample.json
  prompts/
    prediction_prompt.md
  tests/
    conftest.py
    test_models.py
    test_scoring.py
    test_prompt_builder.py
    test_reflection.py
    test_config.py
    test_llm_client.py   # SDKはmonkeypatchでモック
    test_cli.py
    test_reflection_injection.py
    test_input_builder.py
    test_fetchers.py        # cache/rate_limit/HttpClient/各Fetcher
    test_fetch_json_cli.py  # CLI fetch-json
    test_kdreams_results.py   # 結果ページ取得・パース・CLI連携
    test_kdreams_race_card.py # 出走表ページ取得・パース・CLI連携
    test_kdreams_odds.py      # オッズページ取得・パース・CLI連携
    test_enrichment.py        # enrich-json と merge_recent_results
    test_odds_enrichment.py   # merge_odds と prepare-json --odds
    test_preparation.py       # prepare-json と prepare_race_input
    test_weather.py           # WeatherProvider 単体 (HTTPモック)
    test_weather_cli.py       # fetch-weather と prepare-json --weather-source
    test_reporting.py         # build_performance_report と reports CLI
    test_value_analysis.py    # オッズ妙味分析（ラベル判定、プロンプト、CLI、reports）
  fixtures/
    kdreams_results_sample.html
    kdreams_results_empty.html
    kdreams_race_card_sample.html
    kdreams_race_card_empty.html
    kdreams_odds_trifecta_sample.html
    kdreams_odds_trio_sample.html
    kdreams_odds_exacta_sample.html
    kdreams_odds_empty.html
  .env.example
  .gitignore
  README.md
  pyproject.toml
  CLAUDE.md
```

---

## dry-run（予想品質の手動確認）

`examples/dry_run/` に7件のサンプル JSON を用意し、`scripts/dry_run_predictions.py` で一括 predict + 観点別チェックができます（mock provider 固定、ネットワーク通信なし）。

### サンプル一覧

| ファイル | シナリオ |
| --- | --- |
| `01_normal_calm.json` | 通常ライン戦・晴れ微風 |
| `02_rainy.json` | 雨3.5mm/h |
| `03_strong_wind.json` | 西風6.5m/s・500バンク・9車立て |
| `04_girls.json` | ガールズ予選（is_girls=true、lines=[]） |
| `05_rookie.json` | A級新人予選（ライン有・経験少） |
| `06_cheap_favorite.json` | 1番圧倒的本命（オッズ2.3倍・ガミ警戒） |
| `07_chaotic.json` | 直近3レース波乱続き（荒れ傾向） |

### 実行方法

```bash
python scripts/dry_run_predictions.py
```

出力:
- `outputs/dry_run/{N}.md`：各サンプルの予想Markdown
- `outputs/dry_run/_SUMMARY.md`：観点別チェック結果一覧

### チェック観点（10項目）

1. 本線/押さえ/穴/大穴 の点数分布が自然か
2. 本線が安すぎる場合にガミ警戒が出るか
3. 雨/強風時に別線番手・3番手のズレ目候補が出るか
4. 晴れ/微風時に本線を崩しすぎていないか
5. ガールズでライン表現（番手差し/別線番手）が出ていないか
6. 新人戦で通常ライン戦のロジックが混ざっていないか
7. オッズ未取得時でも予想が破綻しないか
8. 反省ログがある場合とない場合で買い目候補が適度に変わるか
9. 穴・大穴が過多でないか（穴≤10、大穴≤5、合計≤25）
10. 最終結論が「一番買いたい買い目」「押さえるべき」「少額穴」「ガミ警戒」の4区分に分かれているか

### 想定される警告

- `穴_過多でない: False` → 雨+強風+荒れの3つが同時に発動した場合のみ。実際に起こりうるが、HARD上限（穴=10）で抑制。
- `本線安_ガミ警戒あり: True` を期待するのは `06_cheap_favorite.json`（本線2.3倍）のみ。

### サンプルを追加するには

`examples/dry_run/` に同じ構造の JSON を置けば自動で含まれます（連番命名推奨）。

## テスト

```bash
.venv/bin/python -m pytest -q
```

テスト観点:

- Pydanticモデルの読み込み
- 最小JSONでの予想生成
- weather / wind 補正
- ガールズ時のラインスコア無効化
- recent_results による trend 抽出
- prompt_builder の出力形式
- 結果入力後の reflection 保存
- gami_risk / odds_value_score の挙動
- `.env` 読み込みとデフォルト
- 未知 provider が日本語エラーで弾かれること
- OpenAI/Anthropic SDK 呼び出しを monkeypatch でモックし、フォールバック・JSONパース・API例外を検証
- CLI predict が mock / 不正値 / APIキー未設定で期待どおり動くこと
- 反省ログ自動注入: 関連度ランキング、ガールズ分離、時間減衰、上限件数
- 反省カテゴリによる scoring/buy-list 補正
- プロンプトに反省セクションが入る/入らない（`--no-reflections` 時）
- `parse_lines` の半角/全角・ダッシュ変種・重複/範囲外エラー
- `quick-json` 生成→`predict` 動作、`--girls` 時の `--lines` 無視
- `create-json --interactive` の擬似入力テスト（通常/ガールズ）
- 外部取得土台: FileCache (hit/miss/TTL/disabled)、RateLimiter (sleep差替)、HttpClient (UA/非2xx/通信失敗/キャッシュ/レート制限呼出)、ManualFetcher のロード&スキーマ検証
- `KDreamsFetcher.fetch_race_card / fetch_odds / fetch_venue_trend` / `OddsParkFetcher` が日本語の未実装エラーを返すこと
- `fetch-json` CLI: manual→predict 連携、未実装ソース、`--fallback-input`、`--no-cache`、不正日付
- Kドリームス結果取得: venue→jo_code マッピング、URL生成、HTMLパース（カンマ/円/¥/全角数字/em-dash対応）、未確定/開催前のスキップ、race_no絞り込み、不正HTML→FetchError、HttpClient.get の呼び出し（UA付き）、生HTMLが上位へ漏れないこと、CLI `--kind results` での envelope 出力
- enrich-json: envelope/list 両対応、既存recent_results保持、dedupe（新規優先で上書き）、`--no-dedupe`、`--max-results`、memo自動生成（payout有/無）、`RaceInput`/dict 入力、Kドリームス HTTPモック経由マージ、enrich後→`predict`、不正JSON/必須欠如/venue・date 欠如/未対応source の日本語エラー
- Kドリームス出走表取得: URL生成（venue/date/race_no検証）、HTMLパース（全角数字・各種ハイフン・空欄）、車番ソート、ライン抽出（丸付き数字→車番リスト）、選手0件→FetchError、HttpClient.get の呼び出し、生HTMLが上位へ漏れないこと、CLI `--kind race_card` → `predict` 連携、`--fallback-input` 切替
- prepare-json: 出走表取得+天候上書き+結果取り込みの統合、race_no未満の結果のみ取り込み、`--no-results`、`--max-results`、`--results-race-no`、結果失敗でも出走表維持、出走表失敗時の `--fallback-input` 動作、不正date/race_no/未対応source の日本語エラー、生HTMLが出力に漏れないこと、`prepare-json → predict` 連携
- Kドリームスオッズ取得: URL生成（venue/date/race_no/bet_type 検証）、combination正規化（全角/各種ハイフン/`＝`/スペース/重複/範囲外）、odds正規化（カンマ/倍/¥/未確定）、limit絞り、HttpClient.get の呼び出し（種別ごとに1回）、生HTMLが上位へ漏れないこと、CLI `--kind odds`、envelope出力、3種類まとめ取得と特定種別の指定
- merge_odds: グループdict/envelope/フラットlist、replace=True/False、`OddsEntry`変換と必須項目チェック、bet_typeなしlistはエラー、RaceInput/dict 入力対応
- prepare-json --odds: 出走表+結果+オッズの統合、--odds-bet-type / --odds-limit、--no-odds（既定）、オッズ失敗で race_card 維持、不正 odds-bet-type の日本語エラー
- 天候API: 場名→緯度経度マッピング、weather_code→日本語、風向 degree→8方位、hourly から start_time に最も近い時刻を選択、未指定時は正午、Open-Meteo URL生成（wind_speed_unit=ms）、HTTPモック経由で Weather に変換、生APIレスポンス非露出
- fetch-weather CLI: open-meteo provider、未対応 venue/provider/不正日付の日本語エラー、HTML/レスポンスキー非露出
- prepare-json --weather-source open-meteo: 取得→Weather反映、手入力が API 結果より優先される、API失敗時の警告+手入力フォールバック、既定 manual ではAPI通信なし
- 成績レポート: 風速バケット境界（0/2/4/6m/s）/ 5種類の的中分類（main/backup/longshot/big_longshot/miss）/ venue/date_range/weather フィルタ / ガールズ vs 通常戦 / 反省カテゴリ上位 / 改善メモのルールベース生成 / CLI text と json / 不正format/不正date の日本語エラー
- オッズ妙味分析: 3連単/2車単/3連複の predicted_strength 計算 / 市場オッズと人気順位マップ / 6ラベル判定マトリクス（高×安→堅いが安い、中×中穴→妙味あり、低×大穴→穴として少額、等）/ オッズ未取得時のフォールバック / プロンプトに妙味分析セクション、--no-value-analysis で除外 / CLI 表示にラベル付与 / reports に value_label 別の的中率と high_gami_hit_count

**実APIは呼びません。** SDK は `monkeypatch.setitem(sys.modules, ...)` で偽実装に差し替えています。

---

## 制限事項と今後

未実装（CLAUDE.md の方針通り）:

- 外部サイトからの具体パーサ（kdreams / oddspark / keirin.jp / 天候API）— 土台 (`HttpClient`/`FileCache`/`RateLimiter`/`Fetcher` 抽象) は実装済み
- Web UI

実装済み:

- OpenAI / Anthropic への実API接続（JSON応答 + 既存スコアとマージ）
- `.env` ベースのAPIキー管理（`python-dotenv` 利用）
- APIキー未設定・SDK未インストール・API例外・JSONパース失敗 → 日本語警告 + Mockフォールバック
- 過去の `Reflection` を関連度順にロードし、scoring と LLM プロンプト両方に自動注入（`--use-reflections` / `--reflection-limit`）
- 手入力JSON作成UX: `quick-json` コマンド、`create-json --interactive` 対話モード、並び文字列パーサ
- 外部データ取得の土台: `Fetcher` 抽象、`HttpClient`（UA/timeout/キャッシュ/レート制限統合）、`ManualFetcher` 実装＋未実装ソースのスケルトン、`fetch-json` CLI、`--fallback-input` での手入力JSONフォールバック
- Kドリームス結果ページの **試験パース実装**（venue→jo_code、URL生成、HTMLパース分離、`fetch-json --kind results` でenvelope出力）
- 外部取得結果を既存 RaceInput に取り込む **enrich-json** コマンド + `app/enrichment.py`。`fetch-json → enrich-json → predict` の流れを完成
- Kドリームス出走表ページの **試験パース実装**（`fetch-json --kind race_card` で RaceInput JSON を生成 → そのまま `predict` に渡せる）
- 取得 → 天候マージ → recent_results 取り込みを **1回でまとめる** `prepare-json` コマンド + `app/preparation.py`
- Kドリームスオッズページの **試験パース実装**（`fetch-json --kind odds` で 3連単/3連複/2車単 の人気上位を取得、`prepare-json --odds` で RaceInput にマージ）
- 天候API（Open-Meteo）連携: `fetch-weather` 単独コマンドと `prepare-json --weather-source open-meteo` 統合。APIキー不要、手入力が API 結果に優先、API失敗時のフォールバック
- 成績レポート機能 `reports` コマンド + `app/reporting.py`。場別/天候別/風速別/レース種別の的中率・反省カテゴリ集計、ルールベース改善メモ、text/json 出力
- オッズ妙味分析 `app/value_analysis.py` + `predict --value-analysis`。各買い目に `value_label` / `value_score` / `market_odds` / `market_rank` を付与し、プロンプトと CLI 出力に反映。`reports` の `value_label_summary` でラベル別の的中率も追える

外部サイト取得を実装する場合のルール:

- サイト規約を尊重する
- 過剰アクセスをしない / キャッシュ・レート制限を入れる
- 適切な User-Agent を設定する
- 取得失敗時は手入力JSONにフォールバックする
- **生HTMLをLLMへ渡さない**
- 取得処理と予想処理を密結合しない

---

## 注意

- 本ツールは **予想支援目的のみ** です
- 自動投票・自動購入・サイトログインの実装は行いません
- 的中保証・回収率保証の表現は含めません
- 公営競技のルール・規約を尊重してください
