"""Streamlit Web UI（ローカル予想支援ツール）。

起動方法:
    pip install -e ".[ui]"
    streamlit run app/ui/streamlit_app.py

このファイルは UI 組み立て専用。ビジネスロジックは app/ui/helpers.py に分離。

Streamlit が未インストールでも import だけは成功するように防御している
（main() を呼び出すとエラー表示して終了）。
"""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime
from typing import Any, Optional

try:  # streamlit が未インストールでもこのモジュールを import 可能にする
    import streamlit as st
except ImportError:  # pragma: no cover - 通常テストでは streamlit 未インストール
    st = None  # type: ignore[assignment]

from app.bank_info import list_canonical_venues
from app.ui import helpers


# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------


def _render_sidebar() -> dict[str, Any]:
    """共通設定を返す。タブ間で共有する。"""
    st.sidebar.header("共通設定")
    # 場名は主表記43場のセレクトボックス（地域順）。デフォルトは「平塚」
    _venue_options = list_canonical_venues()
    _default_venue_idx = (
        _venue_options.index("平塚") if "平塚" in _venue_options else 0
    )
    venue = st.sidebar.selectbox(
        "場名", _venue_options, index=_default_venue_idx, key="sb_venue",
    )
    date_val = st.sidebar.date_input(
        "日付（予想したい日）", value=Date.today(), key="sb_date",
        help=(
            "予想したいレースの開催日（本日でOK）。\n"
            "**自動探索 ON** なら、初日日付＋session_no の組み合わせを内部で全探索します。\n"
            "OFF にすると、Kドリームスの URL 仕様通り **開催初日の日付** を入れる必要あり。"
        ),
    )
    race_no = st.sidebar.number_input(
        "レース番号", min_value=1, max_value=12, value=1, key="sb_race_no",
    )
    session_no = st.sidebar.number_input(
        "開催日番号 (session_no)", min_value=1, max_value=10, value=1,
        key="sb_session_no",
        help=(
            "連戦の何日目か（初日=1, 2日目=2, 3日目=3...）。\n"
            "**重要**: 上の「日付」欄は **開催初日の日付** を入れます。\n"
            "例: 連戦が 5/22〜24 の大宮 2日目(=5/23)を見たいときは "
            "日付=5/22, session_no=2 と指定。"
        ),
    )
    auto_search = st.sidebar.checkbox(
        "開催日を自動探索 (初日逆算)", value=True,
        key="sb_auto_session_search",
        help=(
            "ON: 上の「予想したい日」を起点に、(0日前, session=1) / "
            "(1日前, session=2) / (2日前, session=3) ... の組み合わせを試して "
            "実在する開催日を自動的に見つけます。\n"
            "OFF: 上の「日付」は『開催初日』として扱われ、session_no をそのまま使います。"
        ),
    )
    st.sidebar.caption(
        "レース種別とバンク情報は出走表/場名から自動取得されます。"
        "「予想作成」タブの「バンク情報」欄で上書き可能。"
    )
    st.sidebar.markdown("---")

    source = st.sidebar.selectbox(
        "出走表 source", ("kdreams", "manual"), key="sb_source",
    )
    provider = st.sidebar.selectbox(
        "LLM provider", ("openai", "mock", "anthropic"), key="sb_provider",
    )
    weather_source = st.sidebar.selectbox(
        "天候 source", ("open-meteo", "manual"), key="sb_weather_source",
    )

    st.sidebar.markdown("---")
    include_results = st.sidebar.checkbox(
        "results を取り込む", value=True, key="sb_include_results",
    )
    include_odds = st.sidebar.checkbox(
        "odds を取り込む", value=True, key="sb_include_odds",
    )
    odds_source = st.sidebar.selectbox(
        "odds source", ("oddspark", "kdreams"), key="sb_odds_source",
    )
    use_reflections = st.sidebar.checkbox(
        "反省ログを使う", value=True, key="sb_use_reflections",
    )
    value_analysis = st.sidebar.checkbox(
        "オッズ妙味分析を行う", value=True, key="sb_value_analysis",
    )
    bet_budget = st.sidebar.slider(
        "予想点数（目安）",
        min_value=7, max_value=30, value=9, step=1,
        key="sb_bet_budget",
        help=(
            "目標合計買い目点数。本線/押さえ/穴/大穴 に自動配分される。\n"
            "7〜10: 厳選運用 / 12〜18: 標準 / 20〜30: 広め"
        ),
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("HTTP")
    use_cache = st.sidebar.checkbox("キャッシュ ON", value=True, key="sb_use_cache")
    refresh_cache = st.sidebar.checkbox(
        "強制再取得 (refresh-cache)", value=False, key="sb_refresh_cache",
        help=(
            "既存キャッシュを無視して再取得する。"
            "「SYSTEM_ERROR」がキャッシュ汚染で続く場合は ON にしてください。"
            "また、開催なしインデックスも無視されます。"
        ),
    )
    cache_ttl = st.sidebar.number_input(
        "cache TTL (秒)", min_value=0, value=180, key="sb_cache_ttl",
    )
    rate_limit_seconds = st.sidebar.number_input(
        "rate limit (秒)", min_value=0.0, value=1.0, step=0.5, key="sb_rate_limit",
    )

    return {
        "venue": venue,
        "date": date_val,
        "race_no": int(race_no),
        "session_no": int(session_no),
        "auto_session_search": bool(auto_search),
        "source": source,
        "provider": provider,
        "weather_source": weather_source,
        "include_results": include_results,
        "include_odds": include_odds,
        "odds_source": odds_source,
        "use_reflections": use_reflections,
        "value_analysis": value_analysis,
        "bet_budget": int(bet_budget),
        "use_cache": use_cache,
        "refresh_cache": refresh_cache,
        "cache_ttl": int(cache_ttl),
        "rate_limit_seconds": float(rate_limit_seconds),
    }


# ---------------------------------------------------------------------------
# タブ1: 予想作成
# ---------------------------------------------------------------------------


def _render_predict_tab(common: dict[str, Any]) -> None:
    st.subheader("予想作成")
    st.caption(
        "サイドバーの設定で出走表/天候/オッズを取得して予想を出します。"
        "自動投票機能はありません。"
    )

    with st.expander("天候の手動上書き (任意)", expanded=False):
        weather = st.text_input("天候", value="", key="pred_weather")
        rain = st.number_input("雨量 mm/h", min_value=0.0, value=0.0, key="pred_rain")
        wind_direction = st.text_input("風向", value="", key="pred_wind_dir")
        wind_speed = st.number_input(
            "風速 m/s", min_value=0.0, value=0.0, key="pred_wind_speed"
        )
        wind_note = st.text_input("風メモ", value="", key="pred_wind_note")

    with st.expander("バンク情報 (任意)", expanded=False):
        bank_note = st.text_input("バンク特性メモ", value="", key="pred_bank_note")
        bank_length = st.number_input(
            "バンク周長 (m)", min_value=0, max_value=600, value=0, key="pred_bank_length",
        )
        bank_style = st.selectbox(
            "バンク特性", ("", "差し有利", "先行有利", "中立"), key="pred_bank_style",
        )

    with st.expander("東スポ補助情報 (任意・試験実装)", expanded=False):
        st.caption(
            "東スポ予想ページのURLを直接指定するとコメント要約・signalsを取り込みます。"
            "失敗しても予想は続行します。**全文転載しません**（要約と signals のみ）。"
        )
        tospo_url = st.text_input(
            "東スポ予想ページURL", value="", key="pred_tospo_url",
            help="例: https://keirin.tokyo-sports.co.jp/...",
        )
        include_tospo_notes = st.checkbox(
            "東スポ補助情報を取り込む", value=False, key="pred_include_tospo",
        )

    with st.expander("コメント・記者補助情報 (テキスト貼り付け・任意)", expanded=False):
        st.caption(
            "新聞・予想記事・公式コメントをコピペで貼り付けると、選手コメント要約と "
            "signals (自力/番手/状態良い等) を抽出して取り込みます。"
            "**全文は保存しません**（要約120文字・signalsのみ）。"
            "東スポ/WINTICKET/netkeirin/オッズパーク/yenjoy/manual_text に対応。"
        )
        race_notes_source = st.selectbox(
            "情報源",
            ("manual_text", "tospo", "winticket", "netkeirin", "oddspark", "yenjoy"),
            key="pred_race_notes_source",
        )
        race_notes_text = st.text_area(
            "コメントテキスト",
            value="",
            height=200,
            key="pred_race_notes_text",
            help=(
                "形式例:\n"
                "  並び: 5-1-3 / 6-4 / 7\n"
                "  記者見解: 本線は5-1、穴は6-4\n"
                "  5 長野 自力。状態良い。\n"
                "  1 久樹 番手。差し脚良好。"
            ),
        )
        apply_race_notes = st.checkbox(
            "予想生成時に取り込む", value=False, key="pred_apply_race_notes",
        )

    col1, col2, col3 = st.columns(3)
    btn_prepare = col1.button("予想用JSONを作成", key="pred_btn_prepare")
    btn_predict = col2.button("予想を生成", key="pred_btn_predict")
    btn_combo = col3.button("JSON作成 + 予想生成", key="pred_btn_combo")

    inputs: dict[str, Any] = {
        **common,
        "weather": weather or None,
        "rain": rain if rain > 0 else None,
        "wind_direction": wind_direction or None,
        "wind_speed": wind_speed if wind_speed > 0 else None,
        "wind_note": wind_note or None,
        "bank_note": bank_note or None,
        "bank_length": int(bank_length) if bank_length > 0 else None,
        "bank_style": bank_style or None,
        "tospo_url": tospo_url or None,
        "include_tospo_notes": include_tospo_notes,
    }
    # 予想生成時に取り込む手入力コメント
    pending_race_notes_text = race_notes_text if apply_race_notes else ""
    pending_race_notes_source = race_notes_source

    if btn_prepare or btn_combo:
        with st.spinner("JSON を準備中..."):
            res = helpers.prepare_from_ui_inputs(inputs)
        for w in res.warnings:
            st.warning(w)
        for e in res.errors:
            st.error(e)
        if res.ri is not None:
            st.session_state["race_input"] = res.ri
            st.session_state["race_input_json"] = helpers.race_input_to_json_text(res.ri)
            # ディスクにも保存（tmp/{venue}_{date}_{NN}r.json）
            try:
                saved_path = helpers.save_race_input_to_disk(res.ri)
                st.success(
                    f"予想用JSONを生成しました。`{saved_path}` に保存しました。"
                )
            except Exception as e:
                st.warning(f"JSON生成は成功しましたが、ディスク保存に失敗: {e}")
                st.success("予想用JSONを生成しました（session のみ）。")
        else:
            st.info("手入力JSONをアップロードする場合は『入力JSON確認』タブを使ってください。")

    if btn_predict or btn_combo:
        ri = st.session_state.get("race_input")
        # session_state に無ければ、サイドバーの場名/日付/R番号から
        # tmp/{venue}_{date}_{NN}r.json を探して自動ロードする
        if ri is None:
            date_str = helpers.parse_date_input(common["date"])
            existing_path = helpers.find_existing_race_input(
                common["venue"], date_str, common["race_no"]
            )
            if existing_path is not None:
                text = existing_path.read_text(encoding="utf-8")
                ri, errs = helpers.validate_uploaded_json(text)
                if ri is not None:
                    st.session_state["race_input"] = ri
                    st.session_state["race_input_json"] = (
                        helpers.race_input_to_json_text(ri)
                    )
                    st.info(
                        f"既存JSONを自動読込: `{existing_path}` を使って予想します"
                    )
                else:
                    for e in errs:
                        st.error(e)

        if ri is None:
            st.error(
                "予想用JSONが見つかりません。以下のいずれかを実行してください:\n"
                "- 同じ条件で「予想用JSONを作成」を押す\n"
                "- 「入力JSON確認」タブから JSON をアップロード\n"
                "- サイドバーの場名・日付・レース番号を、既に作成済みのレースに合わせる"
            )
        else:
            # 手入力コメントの取り込み（任意）
            if pending_race_notes_text and pending_race_notes_text.strip():
                date_str_for_notes = helpers.parse_date_input(common["date"])
                merged, errs = helpers.parse_and_merge_race_notes_text(
                    text=pending_race_notes_text,
                    source=pending_race_notes_source,
                    venue=common["venue"],
                    date_str=date_str_for_notes,
                    race_no=common["race_no"],
                    ri=ri,
                )
                if errs:
                    for e in errs:
                        st.warning(e)
                if merged is not None:
                    ri = merged
                    st.session_state["race_input"] = ri
                    st.session_state["race_input_json"] = (
                        helpers.race_input_to_json_text(ri)
                    )
                    st.success(
                        f"[{pending_race_notes_source}] のコメント"
                        f" {len(merged.user_note or '')} 文字を取り込みました"
                    )

            with st.spinner("予想を生成中..."):
                pres = helpers.predict_from_race_input(
                    ri,
                    provider=common["provider"],
                    use_reflections=common["use_reflections"],
                    value_analysis=common["value_analysis"],
                    bet_budget=common.get("bet_budget"),
                    save=True,
                )
            for w in pres.warnings:
                st.warning(w)
            for e in pres.errors:
                st.error(e)
            if pres.prediction is not None:
                st.session_state["prediction"] = pres.prediction
                st.session_state["prediction_md"] = pres.markdown
                st.success(
                    f"予想を生成しました（provider={pres.provider}, "
                    f"使用反省ログ {pres.used_reflections} 件）。"
                )

    md = st.session_state.get("prediction_md", "")
    if md:
        st.markdown("---")
        st.markdown(md)


# ---------------------------------------------------------------------------
# タブ2: 入力JSON確認
# ---------------------------------------------------------------------------


def _render_json_tab() -> None:
    st.subheader("入力JSON確認 / アップロード / 読み込み")
    text = st.session_state.get("race_input_json", "")
    if text:
        with st.expander("現在の RaceInput JSON", expanded=False):
            st.code(text, language="json")
        st.download_button(
            "JSONをダウンロード",
            data=text.encode("utf-8"),
            file_name="race_input.json",
            mime="application/json",
        )
    else:
        st.info("まだJSONが現在のセッションに無いです（ディスクには残っている可能性があります）。")

    # ディスク上の既存 JSON をセレクトボックスから選択
    st.markdown("---")
    st.caption("tmp/ に保存済みの既存JSONを読み込む")
    existing = helpers.list_existing_race_inputs(limit=30)
    if existing:
        labels = ["（選択しない）"] + [str(p.relative_to(p.parent.parent)) for p in existing]
        chosen = st.selectbox(
            "既存JSON（更新日降順）", labels, index=0, key="json_pick_existing",
        )
        if chosen != "（選択しない）" and st.button(
            "選択した既存JSONを読み込む", key="json_btn_load_existing",
        ):
            target = next(
                p for p in existing
                if str(p.relative_to(p.parent.parent)) == chosen
            )
            raw = target.read_text(encoding="utf-8")
            ri, errs = helpers.validate_uploaded_json(raw)
            if errs:
                for e in errs:
                    st.error(e)
            else:
                st.session_state["race_input"] = ri
                st.session_state["race_input_json"] = helpers.race_input_to_json_text(ri)
                st.success(f"読み込み成功: {target}")
                st.rerun()
    else:
        st.caption("（tmp/ に既存JSONはありません。prepare-json または下のアップロードを使ってください）")

    st.markdown("---")
    st.caption("手入力JSONをアップロードして、現在のJSONとして使えます。")
    uploaded = st.file_uploader("RaceInput JSON", type=["json"], key="json_uploader")
    if uploaded is not None:
        raw = uploaded.read().decode("utf-8")
        ri, errs = helpers.validate_uploaded_json(raw)
        if errs:
            for e in errs:
                st.error(e)
        else:
            st.success("バリデーション成功。現在のJSONとして使えます。")
            st.session_state["race_input"] = ri
            st.session_state["race_input_json"] = helpers.race_input_to_json_text(ri)
            if st.button("このJSONで予想を生成", key="json_btn_predict"):
                pres = helpers.predict_from_race_input(ri, save=True)
                for w in pres.warnings:
                    st.warning(w)
                for e in pres.errors:
                    st.error(e)
                if pres.prediction is not None:
                    st.markdown(pres.markdown)


# ---------------------------------------------------------------------------
# タブ3: 結果入力
# ---------------------------------------------------------------------------


def _render_result_tab() -> None:
    st.subheader("結果入力 → 反省ログ生成")
    st.caption(
        "race_id は予想を識別する文字列（例: `20260522-平塚-6`）。"
        "「予想作成」タブで予想を生成した直後はその race_id が自動入力されます。"
        "後から入力する場合は、下のセレクトボックスから過去の予想を選んでください。"
    )

    # 過去の予想一覧（DB から）
    try:
        recent = helpers.list_recent_predictions(limit=30)
    except Exception:
        recent = []

    # 現在の session_state の race_id を最優先で初期値に
    default_race_id = ""
    ri = st.session_state.get("race_input")
    if ri is not None:
        default_race_id = ri.race.race_id

    # セレクトボックスで過去の予想から選ぶ（任意）
    # selectbox の値を session_state に書き込んでから text_input を描画することで、
    # text_input が確実にその値を表示する（Streamlit の widget key 仕様への対応）
    selected_rid: str = ""
    if recent:
        labels = ["（手入力）"] + [label for (_, label) in recent]
        default_idx = 0
        if default_race_id:
            for i, (rid, _) in enumerate(recent, start=1):
                if rid == default_race_id:
                    default_idx = i
                    break
        selected_label = st.selectbox(
            "過去の予想から選ぶ（または手入力）",
            labels, index=default_idx, key="result_pick_recent",
        )
        if selected_label != "（手入力）":
            for rid, label in recent:
                if label == selected_label:
                    selected_rid = rid
                    break
    else:
        st.info("まだ保存された予想がありません。「予想作成」タブで生成してください。")

    # 反映ロジック:
    # 1. selectbox で過去予想が選ばれているなら、その race_id を text_input に流し込む
    # 2. そうでなければ default_race_id（現在のJSON）を初期値に
    # session_state を text_input より **前** に更新することで反映を確実にする
    effective_default = selected_rid or default_race_id
    if effective_default and st.session_state.get("result_race_id", "") != effective_default:
        # selectbox が変わったときに text_input を強制更新（ただしユーザーが手入力した値は尊重）
        # 「session_state の現在値が空」または「セレクトの値が変わった」場合のみ書き換える
        previous_pick = st.session_state.get("_prev_result_pick", "")
        current_pick = st.session_state.get("result_pick_recent", "")
        if (
            not st.session_state.get("result_race_id")  # text_input が空
            or previous_pick != current_pick  # selectbox が切り替わった
        ):
            st.session_state["result_race_id"] = effective_default
        st.session_state["_prev_result_pick"] = current_pick

    race_id = st.text_input(
        "race_id", key="result_race_id",
        help="例: 20260522-平塚-6。上のセレクトボックスから選ぶか、直接入力。",
    )
    result_str = st.text_input(
        "結果 (例: 1-4-3 / 同着は `3-5-1 / 3-5-9`)", value="", key="result_str",
        help="着順を - 区切りで。3連単の結果のみ。"
             "同着の場合は `/` または `,` で区切って複数指定可。",
    )
    note = st.text_input("メモ (任意)", value="", key="result_note")
    use_current_json = st.checkbox(
        "現在のJSONを使う（反省カテゴリ判定の精度向上）", value=True,
        key="result_use_current_json",
    )

    if st.button("結果を保存して反省を生成", key="result_btn_save"):
        input_json = (
            st.session_state.get("race_input_json", "") if use_current_json else ""
        )
        resp = helpers.save_result_from_ui(
            race_id=race_id,
            result_str=result_str,
            input_json_text=input_json,
            note=note,
        )
        for w in resp.warnings:
            st.warning(w)
        for e in resp.errors:
            st.error(e)
        if resp.saved and resp.reflection is not None:
            st.success(f"結果を保存しました: {race_id} → {result_str}")
            st.markdown("**反省カテゴリ:**")
            for cat in resp.reflection.categories:
                st.write(f"- {cat}")
            if resp.reflection.note:
                st.markdown(f"**メモ:** {resp.reflection.note}")


# ---------------------------------------------------------------------------
# タブ4: 反省ログ
# ---------------------------------------------------------------------------


def _render_reflections_tab() -> None:
    st.subheader("反省ログ")
    col1, col2, col3 = st.columns(3)
    venue = col1.text_input("場名フィルタ", value="", key="ref_venue")
    weather = col2.text_input("天候フィルタ", value="", key="ref_weather")
    limit = col3.number_input(
        "件数上限", min_value=1, max_value=500, value=50, key="ref_limit"
    )

    if st.button("反省ログを取得", key="ref_btn_fetch"):
        refs = helpers.list_reflections_from_ui(
            venue=venue, weather_condition=weather, limit=int(limit)
        )
        if not refs:
            st.info("該当する反省ログがありません。")
        else:
            st.success(f"{len(refs)} 件取得しました。")
            for r in refs:
                with st.expander(
                    f"{r.race_id}  {r.venue or '?'}  {r.weather_condition or '?'}"
                ):
                    st.write(f"結果: {r.actual_result or '?'}")
                    st.write(f"分類: {', '.join(r.categories) if r.categories else '?'}")
                    if r.predicted_honsen:
                        st.write(f"予想本線: {', '.join(r.predicted_honsen)}")
                    if r.note:
                        st.write(f"メモ: {r.note}")


# ---------------------------------------------------------------------------
# タブ5: 成績レポート
# ---------------------------------------------------------------------------


def _render_reports_tab() -> None:
    st.subheader("成績レポート")
    col1, col2 = st.columns(2)
    venue = col1.text_input("場名フィルタ", value="", key="rpt_venue")
    weather = col2.text_input("天候フィルタ", value="", key="rpt_weather")
    col3, col4 = st.columns(2)
    from_date = col3.text_input("開始日 YYYY-MM-DD", value="", key="rpt_from")
    to_date = col4.text_input("終了日 YYYY-MM-DD", value="", key="rpt_to")

    if st.button("レポート生成", key="rpt_btn_build"):
        try:
            report = helpers.build_report_from_ui_filters(
                venue=venue, weather_condition=weather,
                from_date=from_date, to_date=to_date,
            )
        except Exception as e:
            st.error(helpers.format_error_message(e))
            return
        st.json(report)


# ---------------------------------------------------------------------------
# タブ6: 設定/ヘルプ
# ---------------------------------------------------------------------------


_HELP_TEXT = """
## このUIでできること

- Kドリームスから出走表・結果を取得
- オッズパークから人気上位オッズを取得
- Open-Meteo から天候を取得
- 競輪予想を生成（mock/openai/anthropic）
- 結果入力と反省ログ保存
- 反省ログの蓄積を次回予想に自動注入
- 成績レポート（場別/天候別/風速別）

## このUIでできないこと（意図的に未実装）

- 自動投票
- 車券の自動購入
- 投票サイトへのログイン
- 外部投票サイトへのPOST送信

このシステムは **予想支援ツール** であり、購入処理は一切ありません。

## APIキー設定

LLM provider に openai / anthropic を選ぶときは、環境変数で API キーを設定してください:

```
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
```

`.env` ファイルでも読まれます。

## mock / openai / anthropic の違い

- **mock**: APIキー不要。決定論的にプレースホルダ文章を生成。スコアリング結果はそのまま反映される。テスト用
- **openai**: OpenAI API を使用（既定: gpt-4o-mini）
- **anthropic**: Anthropic API を使用（既定: claude-sonnet-4-6）

## Kドリームス取得が失敗した場合

- 「Kドリームスから SYSTEM_ERROR ページが返されました」: その日に開催が無い可能性
- 場名・日付・開催日番号(session_no)を確認してください
- 取得不能な場合は「入力JSON確認」タブから手入力JSONをアップロード可能

## Open-Meteo取得が失敗した場合

- サイドバーで天候 source を `manual` に切り替え、UI上で天候を手入力
- HTTP 失敗時は警告のみ表示され、出走表は使い続けられます

## 手入力JSONへのフォールバック

「入力JSON確認」タブから RaceInput JSON をアップロードできます。
スキーマ検証エラーは日本語で表示されます。

## よく使うCLIコマンド

```
python -m app.cli prepare-json --venue 平塚 --date 2026-05-22 --race-no 6
python -m app.cli predict --input tmp/race.json
python -m app.cli result 6-4-3
python -m app.cli reflections --venue 平塚
python -m app.cli reports --venue 平塚
```

## トラブルシューティング

- 全レースで「開催なし」エラー → その日その場が休催。別の日/場を試す
- オッズ取得失敗 → 出走表は維持される。`--no-odds` で抑制可
- predict 結果が車番順になっている → score=0 の場合、市場人気で補正される。
  オッズが取れていれば反映、なければ手入力で score を埋めてください
"""


def _render_help_tab() -> None:
    st.subheader("設定/ヘルプ")
    st.markdown(_HELP_TEXT)


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    """Streamlit エントリポイント。`streamlit run` から呼ばれる。"""
    if st is None:
        print(
            "[エラー] streamlit がインストールされていません。\n"
            "    pip install -e \".[ui]\"\n"
            "を実行してから再度起動してください。"
        )
        return
    st.set_page_config(
        page_title="競輪予想支援",
        page_icon=None,
        layout="wide",
    )
    st.title("競輪予想支援")
    st.caption("ローカルツール / 自動投票機能なし / 予想支援目的のみ")

    common = _render_sidebar()
    tabs = st.tabs([
        "予想作成",
        "入力JSON確認",
        "結果入力",
        "反省ログ",
        "成績レポート",
        "設定/ヘルプ",
    ])
    with tabs[0]:
        _render_predict_tab(common)
    with tabs[1]:
        _render_json_tab()
    with tabs[2]:
        _render_result_tab()
    with tabs[3]:
        _render_reflections_tab()
    with tabs[4]:
        _render_reports_tab()
    with tabs[5]:
        _render_help_tab()


if __name__ == "__main__":
    main()
