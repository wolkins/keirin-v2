"""LLMクライアントのテスト。

実API呼び出しは絶対に行わない。SDK の呼び出しは monkeypatch / MagicMock で
すべて差し替える。
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.llm_client import (
    AnthropicClient,
    MockLLMClient,
    OpenAIClient,
    UnknownProviderError,
    _extract_json,
    _merge_llm_response,
    build_client,
)
from app.prompt_builder import build_full_prompt
from app.scoring import build_candidate_bets, compute_scores


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    base = dict(
        provider="mock",
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-4-6",
    )
    base.update(overrides)
    return Settings(**base)


def _collect_warnings() -> tuple[list[str], callable]:
    bucket: list[str] = []

    def warn(msg: str) -> None:
        bucket.append(msg)

    return bucket, warn


def _llm_payload() -> dict:
    return {
        "summary": "LLM要約",
        "venue_trend_text": "LLM場傾向",
        "weather_text": "LLM天候",
        "lines_text": "LLM並び",
        "final_conclusion": "LLM最終結論",
        "gami_memo": "LLMガミメモ",
        "reflection_points": ["反省1", "反省2"],
        "honsen": [{"combination": "5-1-2", "reason": "LLM本線理由"}],
        "osae": [{"combination": "1-5-2", "reason": "LLM押さえ"}],
        "ana": [{"combination": "6-5-1", "reason": "LLM穴"}],
        "ooana": [{"combination": "7-5-1", "reason": "LLM大穴"}],
    }


# ---------------------------------------------------------------------------
# ファクトリ / フォールバック
# ---------------------------------------------------------------------------


def test_build_client_unknown_provider_raises():
    with pytest.raises(UnknownProviderError):
        build_client("not-a-provider", settings=_settings())


def test_build_client_mock_returns_mock():
    c = build_client("mock", settings=_settings())
    assert isinstance(c, MockLLMClient)


def test_build_client_openai_without_key_falls_back_with_warning():
    warns, warn = _collect_warnings()
    c = build_client("openai", settings=_settings(openai_api_key=None), warn=warn)
    assert isinstance(c, MockLLMClient)
    assert warns and "OPENAI_API_KEY" in warns[0]
    assert "Mock" in warns[0]


def test_build_client_anthropic_without_key_falls_back_with_warning():
    warns, warn = _collect_warnings()
    c = build_client(
        "anthropic", settings=_settings(anthropic_api_key=None), warn=warn
    )
    assert isinstance(c, MockLLMClient)
    assert warns and "ANTHROPIC_API_KEY" in warns[0]


def test_build_client_openai_with_key_returns_openai_client():
    c = build_client(
        "openai", settings=_settings(openai_api_key="sk-test"), warn=lambda _: None
    )
    assert isinstance(c, OpenAIClient)


def test_build_client_anthropic_with_key_returns_anthropic_client():
    c = build_client(
        "anthropic",
        settings=_settings(anthropic_api_key="sk-ant-test"),
        warn=lambda _: None,
    )
    assert isinstance(c, AnthropicClient)


# ---------------------------------------------------------------------------
# JSON 抽出 / マージ
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    out = _extract_json('{"a": 1, "b": "x"}')
    assert out == {"a": 1, "b": "x"}


def test_extract_json_with_code_fence():
    txt = '```json\n{"a": 2}\n```'
    assert _extract_json(txt) == {"a": 2}


def test_extract_json_with_surrounding_text():
    txt = "ここに説明...\n{\n  \"a\": 3\n}\nおまけ"
    assert _extract_json(txt) == {"a": 3}


def test_extract_json_invalid_raises():
    with pytest.raises(ValueError):
        _extract_json("これはJSONではありません")


def test_merge_llm_response_overrides_text_only(sample_input):
    """LLM 応答は文章化フィールドのみマージ。買い目・印・スコアは温存。

    新仕様: LLM に買い目を任せると、ライン構造優先のロジックや上限制御が
    無視される。アプリ側で生成した buy 候補をそのまま保持する。
    """
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    base = MockLLMClient().generate_prediction(sample_input, scores, bets, prompt="")
    merged = _merge_llm_response(base, _llm_payload())
    # 文章化フィールドはマージされる
    assert merged.summary == "LLM要約"
    assert merged.final_conclusion == "LLM最終結論"
    # 買い目は LLM 応答で上書きされない（決定論的な base のまま）
    assert merged.honsen == base.honsen, "honsen が LLM 応答で上書きされている"
    assert merged.osae == base.osae
    assert merged.ana == base.ana
    assert merged.ooana == base.ooana
    # marks / rider_scores も温存
    assert merged.marks == base.marks
    assert len(merged.rider_scores) == len(base.rider_scores)


def test_merge_llm_response_keeps_base_when_field_missing(sample_input):
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    base = MockLLMClient().generate_prediction(sample_input, scores, bets, prompt="")
    # 空のpayloadなら base のまま
    merged = _merge_llm_response(base, {})
    assert merged.summary == base.summary
    assert merged.honsen == base.honsen


# ---------------------------------------------------------------------------
# OpenAI クライアント (SDKをモック)
# ---------------------------------------------------------------------------


def _install_fake_openai(monkeypatch, response_text: str):
    """偽の openai モジュールを sys.modules に注入する。"""

    class _Resp:
        def __init__(self, text: str) -> None:
            self.choices = [
                SimpleNamespace(message=SimpleNamespace(content=text))
            ]

    class _Chat:
        def __init__(self, text: str) -> None:
            self._text = text
            self.completions = self  # 同一オブジェクトでcompletionsをぶら下げる

        def create(self, **kwargs):  # noqa: D401, ANN001
            return _Resp(self._text)

    class _Client:
        def __init__(self, api_key: str | None = None) -> None:
            self.chat = _Chat(response_text)

    fake_module = SimpleNamespace(OpenAI=_Client)
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return _Client


def test_openai_client_uses_llm_response_text_only(monkeypatch, sample_input):
    """OpenAI 応答は文章のみ反映、buy 候補は決定論的計算結果を保持。"""
    _install_fake_openai(monkeypatch, json.dumps(_llm_payload(), ensure_ascii=False))
    warns, warn = _collect_warnings()
    client = OpenAIClient(api_key="sk-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    prompt = build_full_prompt(sample_input, scores, bets)
    pred = client.generate_prediction(sample_input, scores, bets, prompt)
    # 文章はマージされる
    assert pred.summary == "LLM要約"
    # 買い目は LLM 応答で上書きされない
    # base = _build_deterministic_prediction で生成された候補と一致する
    base_honsen = [b.combination for b in bets["本線"]]
    pred_honsen = [b.combination for b in pred.honsen]
    assert pred_honsen == base_honsen, (
        f"honsen が LLM 応答で書き換えられている: "
        f"base={base_honsen} pred={pred_honsen}"
    )
    # 警告は出ていない（フォールバックではない）
    assert warns == []


def test_openai_client_invalid_json_falls_back(monkeypatch, sample_input):
    _install_fake_openai(monkeypatch, "これは壊れたJSONです")
    warns, warn = _collect_warnings()
    client = OpenAIClient(api_key="sk-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    pred = client.generate_prediction(sample_input, scores, bets, prompt="")
    # 決定論的なMock相当の出力にフォールバック
    assert pred.summary != "LLM要約"
    assert warns and "Mock" in warns[0]
    assert "JSON" in warns[0] or "パース" in warns[0]


def test_openai_client_api_exception_falls_back(monkeypatch, sample_input):
    class _Boom:
        def __init__(self, *a, **kw):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise RuntimeError("接続失敗")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_Boom))
    warns, warn = _collect_warnings()
    client = OpenAIClient(api_key="sk-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    pred = client.generate_prediction(sample_input, scores, bets, prompt="")
    assert warns and "Mock" in warns[0]
    assert "RuntimeError" in warns[0] or "接続失敗" in warns[0]
    # フォールバック結果が正しく Prediction であること
    assert pred.race_id == sample_input.race.race_id


def test_openai_client_missing_sdk_falls_back(monkeypatch, sample_input):
    # openai モジュールが import できない状況をシミュレート
    monkeypatch.setitem(sys.modules, "openai", None)
    warns, warn = _collect_warnings()
    client = OpenAIClient(api_key="sk-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    pred = client.generate_prediction(sample_input, scores, bets, prompt="")
    assert warns and ("openai" in warns[0] or "Mock" in warns[0])
    assert pred.race_id == sample_input.race.race_id


# ---------------------------------------------------------------------------
# Anthropic クライアント (SDKをモック)
# ---------------------------------------------------------------------------


def _install_fake_anthropic(monkeypatch, response_text: str):
    class _Resp:
        def __init__(self, text: str) -> None:
            self.content = [SimpleNamespace(text=text)]

    class _Messages:
        def __init__(self, text: str) -> None:
            self._text = text

        def create(self, **kwargs):
            return _Resp(self._text)

    class _Anth:
        def __init__(self, api_key: str | None = None) -> None:
            self.messages = _Messages(response_text)

    fake_module = SimpleNamespace(Anthropic=_Anth)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return _Anth


def test_anthropic_client_uses_llm_response_text_only(monkeypatch, sample_input):
    """Anthropic 応答も文章のみ反映、buy 候補は保持。"""
    _install_fake_anthropic(monkeypatch, json.dumps(_llm_payload(), ensure_ascii=False))
    warns, warn = _collect_warnings()
    client = AnthropicClient(api_key="sk-ant-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    prompt = build_full_prompt(sample_input, scores, bets)
    pred = client.generate_prediction(sample_input, scores, bets, prompt)
    assert pred.summary == "LLM要約"
    # 大穴は LLM 応答ではなくアプリ側の決定論的計算結果
    base_ooana = [b.combination for b in bets["大穴"]]
    pred_ooana = [b.combination for b in pred.ooana]
    assert pred_ooana == base_ooana
    assert warns == []


def test_anthropic_client_invalid_json_falls_back(monkeypatch, sample_input):
    _install_fake_anthropic(monkeypatch, "壊れた応答")
    warns, warn = _collect_warnings()
    client = AnthropicClient(api_key="sk-ant-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    pred = client.generate_prediction(sample_input, scores, bets, prompt="")
    assert warns and "Mock" in warns[0]
    # Mockへのフォールバックなので、決定論的な要約に戻っているはず
    assert "LLM要約" not in pred.summary


def test_anthropic_client_api_exception_falls_back(monkeypatch, sample_input):
    class _Boom:
        def __init__(self, *a, **kw):
            self.messages = self

        def create(self, **kwargs):
            raise RuntimeError("rate limited")

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_Boom))
    warns, warn = _collect_warnings()
    client = AnthropicClient(api_key="sk-ant-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    pred = client.generate_prediction(sample_input, scores, bets, prompt="")
    assert warns and "Mock" in warns[0]
    assert pred.race_id == sample_input.race.race_id


# ---------------------------------------------------------------------------
# LLM が買い目を勝手に書き換えても、アプリ側の候補が保持されることの検証
# ---------------------------------------------------------------------------


def test_openai_ignores_llm_bets_completely(monkeypatch, sample_input):
    """LLM が買い目を捏造して返しても、アプリ側の決定論的計算結果が保持される。

    仕様: LLM は文章化のみ担当。buy 候補は app/scoring.py の build_candidate_bets
    が固定する。
    """
    # LLM が「変な買い目」を返すケース
    malicious_payload = {
        "summary": "短い要約",
        "venue_trend_text": "傾向",
        "weather_text": "天候",
        "lines_text": "ライン",
        "final_conclusion": "最終結論",
        "gami_memo": "ガミ",
        "reflection_points": ["反省1", "反省2"],
        # ↓ LLM が勝手に作った買い目（決定論的計算と異なる）
        "honsen": [{"combination": "9-9-9", "reason": "捏造", "bet_type": "3連単"}],
        "osae": [{"combination": "8-8-8", "reason": "捏造", "bet_type": "3連単"}],
        "ana": [{"combination": "7-7-7", "reason": "捏造", "bet_type": "3連単"}],
        "ooana": [{"combination": "6-6-6", "reason": "捏造", "bet_type": "3連単"}],
    }
    _install_fake_openai(monkeypatch, json.dumps(malicious_payload, ensure_ascii=False))
    warns, warn = _collect_warnings()
    client = OpenAIClient(api_key="sk-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    pred = client.generate_prediction(sample_input, scores, bets, prompt="")
    # アプリ側の決定論的計算結果と一致
    base_combos = [b.combination for b in bets["本線"]]
    pred_combos = [b.combination for b in pred.honsen]
    assert pred_combos == base_combos
    # LLM の捏造 9-9-9 は反映されていない
    assert "9-9-9" not in pred_combos
    # 各カテゴリも同様
    assert [b.combination for b in pred.osae] == [b.combination for b in bets["押さえ"]]
    assert [b.combination for b in pred.ana] == [b.combination for b in bets["穴"]]
    assert [b.combination for b in pred.ooana] == [b.combination for b in bets["大穴"]]


def test_anthropic_ignores_llm_bets_completely(monkeypatch, sample_input):
    """Anthropic 側も同様: LLM の捏造買い目は無視される。"""
    malicious_payload = {
        "summary": "要約",
        "venue_trend_text": "傾向",
        "weather_text": "天候",
        "lines_text": "ライン",
        "final_conclusion": "結論",
        "gami_memo": "ガミ",
        "reflection_points": [],
        "honsen": [{"combination": "1-1-1", "reason": "捏造"}],
        "osae": [],
        "ana": [],
        "ooana": [],
    }
    _install_fake_anthropic(monkeypatch, json.dumps(malicious_payload, ensure_ascii=False))
    warns, warn = _collect_warnings()
    client = AnthropicClient(api_key="sk-ant-test", warn=warn)
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    pred = client.generate_prediction(sample_input, scores, bets, prompt="")
    base_combos = [b.combination for b in bets["本線"]]
    pred_combos = [b.combination for b in pred.honsen]
    assert pred_combos == base_combos
    assert "1-1-1" not in pred_combos


def test_openai_no_real_api_call_made(monkeypatch, sample_input):
    """テストで実 API を呼ばないことの確認。

    monkeypatch で openai を差し替えており、ネットワーク通信が発生しない。
    """
    called: list[str] = []

    class _Spy:
        def __init__(self, api_key=None):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            called.append("create")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary":"x"}'))]
            )

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_Spy))
    client = OpenAIClient(api_key="sk-test")
    scores = compute_scores(sample_input)
    bets = build_candidate_bets(sample_input, scores)
    client.generate_prediction(sample_input, scores, bets, prompt="")
    # API は呼ばれているが、テスト内のスタブのみ
    assert called == ["create"]
