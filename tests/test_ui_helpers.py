"""Web UI helpers の単体テスト（Streamlit 非依存）。

Streamlit 自体は呼ばないので、`streamlit` 未インストールでも全テストが通る。
実ネットワーク・実LLM APIテストは一切行わない。
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path

import pytest

from app.ui import helpers as h


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "race_sample.json"


# ---------------------------------------------------------------------------
# import 可能性
# ---------------------------------------------------------------------------


def test_helpers_module_imports_without_streamlit():
    """helpers は streamlit 未インストールでも import できる。"""
    # 既に上で import 済みなので、再 import して通れば OK
    import importlib
    mod = importlib.reload(h)
    assert hasattr(mod, "validate_uploaded_json")


def test_streamlit_app_imports_without_running_server():
    """streamlit_app の import で重い処理が走らない。"""
    from app.ui import streamlit_app  # noqa: F401
    # main() を呼ばずに import するだけ → サーバー起動しない
    assert hasattr(streamlit_app, "main")
    # streamlit 未インストール時は st=None
    # （CI 環境では streamlit がない想定）
    # 値は環境次第なので存在チェックのみ


# ---------------------------------------------------------------------------
# parse_date_input
# ---------------------------------------------------------------------------


def test_parse_date_input_accepts_date_object():
    assert h.parse_date_input(Date(2026, 5, 22)) == "2026-05-22"


def test_parse_date_input_accepts_string():
    assert h.parse_date_input("2026-05-22") == "2026-05-22"


def test_parse_date_input_rejects_invalid():
    with pytest.raises(ValueError) as e:
        h.parse_date_input("2026/05/22")
    assert "YYYY-MM-DD" in str(e.value)


def test_parse_date_input_rejects_empty():
    with pytest.raises(ValueError):
        h.parse_date_input("")


# ---------------------------------------------------------------------------
# validate_uploaded_json
# ---------------------------------------------------------------------------


def test_validate_uploaded_json_ok():
    text = SAMPLE.read_text(encoding="utf-8")
    ri, errs = h.validate_uploaded_json(text)
    assert ri is not None
    assert errs == []
    assert ri.race.venue == "大垣"


def test_validate_uploaded_json_invalid_json():
    ri, errs = h.validate_uploaded_json("{ not json }")
    assert ri is None
    assert any("JSON" in e for e in errs)


def test_validate_uploaded_json_schema_mismatch():
    """必須キーが欠けている JSON は日本語エラーで返る。"""
    bad = json.dumps({"race": {"race_id": "x"}})  # 多数のキーが欠如
    ri, errs = h.validate_uploaded_json(bad)
    assert ri is None
    assert errs
    # 日本語の説明が含まれる
    assert any("スキーマ検証エラー" in e or "JSON" in e for e in errs)


def test_validate_uploaded_json_empty():
    ri, errs = h.validate_uploaded_json("")
    assert ri is None
    assert any("空" in e for e in errs)


# ---------------------------------------------------------------------------
# build_prepare_kwargs / build_predict_kwargs
# ---------------------------------------------------------------------------


def test_build_prepare_kwargs_minimum():
    inputs = {
        "source": "kdreams",
        "venue": "平塚",
        "date": Date(2026, 5, 22),
        "race_no": 6,
    }
    kw = h.build_prepare_kwargs(inputs)
    assert kw["source"] == "kdreams"
    assert kw["venue"] == "平塚"
    assert kw["date_str"] == "2026-05-22"
    assert kw["race_no"] == 6


def test_build_prepare_kwargs_with_optionals():
    inputs = {
        "source": "kdreams",
        "venue": "平塚",
        "date": "2026-05-22",
        "race_no": 6,
        "weather": "雨",
        "rain": 2.5,
        "wind_direction": "西",
        "wind_speed": 5.0,
        "bank_length": 500,
        "bank_style": "差し有利",
        "include_results": True,
        "include_odds": False,
        "session_no": 2,
        "odds_bet_type": "trifecta",
        "odds_limit": 20,
        "odds_source": "oddspark",
    }
    kw = h.build_prepare_kwargs(inputs)
    assert kw["weather"] == "雨"
    assert kw["bank_length"] == 500
    assert kw["bank_style"] == "差し有利"
    assert kw["include_odds"] is False
    assert kw["session_no"] == 2
    assert kw["odds_source"] == "oddspark"


def test_build_prepare_kwargs_skips_empty_optionals():
    inputs = {
        "source": "manual",
        "venue": "平塚",
        "date": "2026-05-22",
        "race_no": 1,
        "weather": None,
        "rain": None,
        "bank_note": "",
        "bank_style": "",
    }
    kw = h.build_prepare_kwargs(inputs)
    # None/'' は kwargs に入らない
    assert "weather" not in kw
    assert "rain" not in kw
    assert "bank_note" not in kw
    assert "bank_style" not in kw


def test_build_predict_kwargs_defaults():
    kw = h.build_predict_kwargs({})
    assert kw["provider"] == "openai"
    assert kw["use_reflections"] is True
    assert kw["value_analysis"] is True
    assert kw["save"] is False


def test_build_predict_kwargs_overrides():
    kw = h.build_predict_kwargs({
        "provider": "openai",
        "use_reflections": False,
        "reflection_limit": 10,
        "value_analysis": False,
        "save": True,
    })
    assert kw["provider"] == "openai"
    assert kw["use_reflections"] is False
    assert kw["reflection_limit"] == 10
    assert kw["save"] is True


# ---------------------------------------------------------------------------
# format_error_message
# ---------------------------------------------------------------------------


def test_format_error_message_returns_japanese():
    msg = h.format_error_message(ValueError("test error"))
    assert isinstance(msg, str)
    # 日本語のラベル or 説明が含まれる
    assert "エラー" in msg or "エラー" in msg.lower() or "test error" in msg


def test_format_error_message_pydantic_validation():
    from pydantic import ValidationError, BaseModel

    class Schema(BaseModel):
        name: str
        age: int

    try:
        Schema.model_validate({"name": "x"})
    except ValidationError as e:
        msg = h.format_error_message(e)
        assert "スキーマ検証エラー" in msg
        assert "age" in msg


def test_format_error_message_json_decode():
    try:
        json.loads("{")
    except json.JSONDecodeError as e:
        msg = h.format_error_message(e)
        assert "JSON" in msg


# ---------------------------------------------------------------------------
# race_input_to_json_text / prediction_to_markdown
# ---------------------------------------------------------------------------


def test_race_input_to_json_text_round_trip():
    text = SAMPLE.read_text(encoding="utf-8")
    ri, _ = h.validate_uploaded_json(text)
    assert ri is not None
    dumped = h.race_input_to_json_text(ri)
    assert isinstance(dumped, str)
    # 再パースできる
    ri2, _ = h.validate_uploaded_json(dumped)
    assert ri2 is not None
    assert ri2.race.race_id == ri.race.race_id


def test_prediction_to_markdown_includes_sections():
    """mock provider で予想 → Markdown に本線/押さえ/穴/大穴セクションが含まれる。"""
    text = SAMPLE.read_text(encoding="utf-8")
    ri, _ = h.validate_uploaded_json(text)
    assert ri is not None
    res = h.predict_from_race_input(ri, provider="mock", use_reflections=False, save=False)
    assert res.prediction is not None
    md = res.markdown
    assert "本線" in md
    assert "押さえ" in md
    assert "穴" in md
    assert "大穴" in md
    # 4区分も
    assert "一番買いたい買い目" in md
    assert "押さえるべき買い目" in md


# ---------------------------------------------------------------------------
# predict_from_race_input
# ---------------------------------------------------------------------------


def test_predict_with_mock_provider_no_save(tmp_path):
    text = SAMPLE.read_text(encoding="utf-8")
    ri, _ = h.validate_uploaded_json(text)
    assert ri is not None
    res = h.predict_from_race_input(
        ri, provider="mock", use_reflections=False, save=False,
        db_path=tmp_path / "t.db",
    )
    assert res.errors == []
    assert res.prediction is not None
    assert res.provider == "mock"


def test_predict_save_persists_to_db(tmp_path):
    text = SAMPLE.read_text(encoding="utf-8")
    ri, _ = h.validate_uploaded_json(text)
    assert ri is not None
    db = tmp_path / "t.db"
    res = h.predict_from_race_input(
        ri, provider="mock", use_reflections=False, save=True, db_path=db,
    )
    assert res.prediction is not None
    # DB に保存されている
    from app import storage as storage_module
    store = storage_module.Storage(db)
    fetched = store.get_prediction(ri.race.race_id)
    assert fetched is not None


# ---------------------------------------------------------------------------
# save_result_from_ui
# ---------------------------------------------------------------------------


def test_save_result_from_ui_creates_reflection(tmp_path):
    text = SAMPLE.read_text(encoding="utf-8")
    ri, _ = h.validate_uploaded_json(text)
    assert ri is not None
    db = tmp_path / "t.db"

    # 先に予想を保存
    h.predict_from_race_input(
        ri, provider="mock", use_reflections=False, save=True, db_path=db,
    )

    resp = h.save_result_from_ui(
        race_id=ri.race.race_id,
        result_str="5-1-3",
        input_json_text=text,
        db_path=db,
    )
    assert resp.errors == []
    assert resp.saved is True
    assert resp.reflection is not None
    assert resp.reflection.categories


def test_save_result_from_ui_missing_prediction(tmp_path):
    db = tmp_path / "t.db"
    resp = h.save_result_from_ui(
        race_id="nope-1",
        result_str="1-2-3",
        db_path=db,
    )
    assert resp.saved is False
    assert any("予想が見つかりません" in e for e in resp.errors)


def test_save_result_from_ui_empty_race_id(tmp_path):
    resp = h.save_result_from_ui(
        race_id="",
        result_str="1-2-3",
        db_path=tmp_path / "t.db",
    )
    assert resp.saved is False
    assert any("race_id" in e for e in resp.errors)


def test_save_result_from_ui_empty_result(tmp_path):
    resp = h.save_result_from_ui(
        race_id="x",
        result_str="",
        db_path=tmp_path / "t.db",
    )
    assert resp.saved is False
    assert any("結果" in e for e in resp.errors)


# ---------------------------------------------------------------------------
# 反省ログ / 成績レポート
# ---------------------------------------------------------------------------


def test_list_reflections_empty(tmp_path):
    refs = h.list_reflections_from_ui(db_path=tmp_path / "t.db")
    assert refs == []


def test_save_race_input_to_disk(tmp_path):
    """save_race_input_to_disk が tmp/{venue}_{date}_{NN}r.json に保存する。"""
    text = SAMPLE.read_text(encoding="utf-8")
    ri, _ = h.validate_uploaded_json(text)
    assert ri is not None
    saved = h.save_race_input_to_disk(ri, base_dir=tmp_path / "out")
    assert saved.exists()
    # ファイル名フォーマット
    assert saved.name.endswith("r.json")
    # 内容が JSON として読み込める
    raw = json.loads(saved.read_text(encoding="utf-8"))
    assert raw["race"]["venue"] == ri.race.venue


def test_save_race_input_to_disk_overwrites(tmp_path):
    """同じ場所に上書き保存される。"""
    text = SAMPLE.read_text(encoding="utf-8")
    ri, _ = h.validate_uploaded_json(text)
    base = tmp_path / "out"
    p1 = h.save_race_input_to_disk(ri, base_dir=base)
    # 同じ ri を再保存 → 同じパス、エラーなし
    p2 = h.save_race_input_to_disk(ri, base_dir=base)
    assert p1 == p2
    assert p1.exists()


def test_build_report_empty_db(tmp_path):
    report = h.build_report_from_ui_filters(db_path=tmp_path / "t.db")
    assert isinstance(report, dict)
    # 全体サマリーは存在する
    assert "summary" in report or "by_venue" in report or len(report) >= 1


# ---------------------------------------------------------------------------
# 自動探索成功時に race_id / race.date が「予想したい日」になることを確認
# ---------------------------------------------------------------------------


def test_auto_session_search_rewrites_race_id_to_target_date(tmp_path, monkeypatch):
    """自動探索成功時、race_id と race.date は「予想したい日」に書き換えられる。

    Kドリームス URL 構築用には『初日日付 + session_no』を使うが、
    ユーザー視点の表示は「予想したい日」を使う。
    """
    from datetime import date
    from app.models import RaceInput, RaceInfo, Rider

    # 「予想したい日」= 2026-05-23、自動探索で session_no=3 / 初日=2026-05-21 が成功するシナリオ
    def fake_do_prepare(inputs, http_client=None):
        result = h.PrepareResult()
        attempt_date = inputs.get("date")
        s = inputs.get("session_no")
        # session_no=3 + 初日=2026-05-21 のときだけ成功
        if attempt_date == "2026-05-21" and s == 3:
            ri = RaceInput.model_validate({
                "race": {
                    "race_id": "20260521-宇都宮-5",  # 取得時の race_id は初日基準
                    "date": "2026-05-21",
                    "venue": "宇都宮",
                    "race_no": 5,
                    "class_name": "S級一般",
                },
                "riders": [
                    {"car_no": i, "name": f"R{i}", "score": 70.0}
                    for i in range(1, 8)
                ],
                "lines": [],
            })
            result.ri = ri
        return result

    monkeypatch.setattr(h, "_do_prepare", fake_do_prepare)

    inputs = {
        "venue": "宇都宮",
        "date": "2026-05-23",  # 予想したい日
        "race_no": 5,
        "session_no": 1,
        "auto_session_search": True,
        "source": "kdreams",
        "include_results": False,
        "include_odds": False,
    }
    res = h.prepare_from_ui_inputs(inputs)
    assert res.ri is not None
    # race.date と race_id が target_date (2026-05-23) に書き換えられている
    assert res.ri.race.date == date(2026, 5, 23), (
        f"race.date が予想したい日に書き換えられていない: {res.ri.race.date}"
    )
    assert res.ri.race.race_id == "20260523-宇都宮-5", (
        f"race_id が予想したい日ベースになっていない: {res.ri.race.race_id}"
    )
    # 案内メッセージに「初日=2026-05-21」が含まれる
    assert any("初日=2026-05-21" in w for w in res.warnings)


def test_auto_session_search_first_try_keeps_target_date(tmp_path, monkeypatch):
    """1回目の試行（session_no=1, 初日=予想したい日）成功時もそのまま。"""
    from datetime import date
    from app.models import RaceInput

    def fake_do_prepare(inputs, http_client=None):
        result = h.PrepareResult()
        if inputs.get("date") == "2026-05-23" and inputs.get("session_no") == 1:
            ri = RaceInput.model_validate({
                "race": {
                    "race_id": "20260523-武雄-1",
                    "date": "2026-05-23",
                    "venue": "武雄",
                    "race_no": 1,
                    "class_name": "A級予選",
                },
                "riders": [
                    {"car_no": i, "name": f"R{i}", "score": 70.0}
                    for i in range(1, 8)
                ],
                "lines": [],
            })
            result.ri = ri
        return result

    monkeypatch.setattr(h, "_do_prepare", fake_do_prepare)

    res = h.prepare_from_ui_inputs({
        "venue": "武雄",
        "date": "2026-05-23",
        "race_no": 1,
        "session_no": 1,
        "auto_session_search": True,
        "source": "kdreams",
        "include_results": False,
        "include_odds": False,
    })
    assert res.ri is not None
    assert res.ri.race.date == date(2026, 5, 23)
    assert res.ri.race.race_id == "20260523-武雄-1"
