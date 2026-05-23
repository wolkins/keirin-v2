"""Kドリームス取得時の home_area 自動セット検証 (フェーズC 拡張)。

検証項目:
- PREFECTURE_TO_AREA / resolve_prefecture_area() の網羅性
- Kドリームスパーサーで pref → home_area が自動セットされる
- pref が「東京都」「大阪府」等の接尾辞付きでも解決できる
- comment 経由のフォールバック
"""

from __future__ import annotations

import pytest

from app.fetchers.parsers.kdreams_race_card import (
    _build_rider, _build_rider_real,
)
from app.scoring import (
    PREFECTURE_TO_AREA,
    resolve_prefecture_area,
)


# ---------------------------------------------------------------------------
# resolve_prefecture_area
# ---------------------------------------------------------------------------


class TestResolvePrefectureArea:
    def test_exact_match(self):
        assert resolve_prefecture_area("広島") == "中国"
        assert resolve_prefecture_area("東京") == "南関東"
        assert resolve_prefecture_area("大阪") == "近畿"
        assert resolve_prefecture_area("福岡") == "九州"
        assert resolve_prefecture_area("北海道") == "北日本"

    def test_suffix_stripped(self):
        """「東京都」「大阪府」「広島県」等の接尾辞付きも解決できる。"""
        assert resolve_prefecture_area("東京都") == "南関東"
        assert resolve_prefecture_area("大阪府") == "近畿"
        assert resolve_prefecture_area("広島県") == "中国"
        assert resolve_prefecture_area("京都府") == "近畿"

    def test_partial_match_in_comment(self):
        """comment 風の長い文字列でも、都道府県を含めば解決できる。"""
        assert resolve_prefecture_area("広島-A1") == "中国"
        assert resolve_prefecture_area("東京 / S級") == "南関東"

    def test_none_for_unknown(self):
        assert resolve_prefecture_area("") is None
        assert resolve_prefecture_area("不明") is None

    def test_all_47_prefectures(self):
        """全 47 都道府県がマップに存在する。"""
        assert len(PREFECTURE_TO_AREA) == 47

    def test_all_8_areas_covered(self):
        """全 8 地区がマップに存在する。"""
        areas = set(PREFECTURE_TO_AREA.values())
        expected = {
            "北日本", "関東", "南関東", "中部",
            "近畿", "中国", "四国", "九州",
        }
        assert areas == expected

    def test_area_assignment_sample(self):
        """各地区の代表的な配属が正しい。"""
        # 北日本
        assert resolve_prefecture_area("青森") == "北日本"
        assert resolve_prefecture_area("福島") == "北日本"
        # 関東 (東京・神奈川は南関東)
        assert resolve_prefecture_area("茨城") == "関東"
        assert resolve_prefecture_area("埼玉") == "関東"
        assert resolve_prefecture_area("千葉") == "関東"
        # 南関東
        assert resolve_prefecture_area("神奈川") == "南関東"
        assert resolve_prefecture_area("静岡") == "南関東"
        # 中部
        assert resolve_prefecture_area("愛知") == "中部"
        assert resolve_prefecture_area("新潟") == "中部"
        # 近畿
        assert resolve_prefecture_area("京都") == "近畿"
        assert resolve_prefecture_area("兵庫") == "近畿"
        # 中国
        assert resolve_prefecture_area("岡山") == "中国"
        # 四国
        assert resolve_prefecture_area("香川") == "四国"
        assert resolve_prefecture_area("高知") == "四国"
        # 九州 (沖縄も九州扱い)
        assert resolve_prefecture_area("熊本") == "九州"
        assert resolve_prefecture_area("沖縄") == "九州"


# ---------------------------------------------------------------------------
# _build_rider_real (実 Kドリームスパーサー)
# ---------------------------------------------------------------------------


class TestBuildRiderRealHomeArea:
    def test_pref_sets_home_area(self):
        """pref="広島" → home_area="中国"。"""
        row = {
            "car_no": "1", "name": "テスト",
            "pref": "広島", "rank": "S1", "style": "両",
        }
        out = _build_rider_real(row)
        assert out is not None
        assert out["home_area"] == "中国"

    def test_no_pref_no_home_area(self):
        """pref 未指定なら home_area=None。"""
        row = {
            "car_no": "1", "name": "テスト",
            "pref": "", "rank": "S1", "style": "両",
        }
        out = _build_rider_real(row)
        assert out is not None
        assert out["home_area"] is None

    def test_unknown_pref_returns_none(self):
        """マッピング外の pref は None。"""
        row = {
            "car_no": "1", "name": "テスト",
            "pref": "海外", "rank": "S1", "style": "両",
        }
        out = _build_rider_real(row)
        assert out is not None
        assert out["home_area"] is None

    def test_comment_contains_pref(self):
        """comment にも pref が含まれる（pref / rank / style を / で連結）。"""
        row = {
            "car_no": "1", "name": "テスト",
            "pref": "広島", "rank": "S1", "style": "両",
        }
        out = _build_rider_real(row)
        assert out is not None
        assert "広島" in (out["comment"] or "")


# ---------------------------------------------------------------------------
# _build_rider (mock パーサー)
# ---------------------------------------------------------------------------


class TestBuildRiderMockHomeArea:
    def test_pref_sets_home_area(self):
        """pref フィールドがある場合は優先利用。"""
        row = {
            "car-no": "1", "name": "テスト",
            "pref": "福岡", "comment": "自力",
        }
        out = _build_rider(row)
        assert out is not None
        assert out["home_area"] == "九州"

    def test_comment_fallback(self):
        """pref が無くても comment から都道府県を拾える。"""
        row = {
            "car-no": "1", "name": "テスト",
            "comment": "広島-A1-両",
        }
        out = _build_rider(row)
        assert out is not None
        assert out["home_area"] == "中国"

    def test_neither_returns_none(self):
        """pref も comment にも県名がなければ None。"""
        row = {
            "car-no": "1", "name": "テスト",
            "comment": "自力",
        }
        out = _build_rider(row)
        assert out is not None
        assert out["home_area"] is None


# ---------------------------------------------------------------------------
# Rider モデルとの統合
# ---------------------------------------------------------------------------


def test_rider_model_accepts_home_area_from_parser():
    """パーサ出力 dict を Rider モデルに渡せる。"""
    from app.models import Rider
    row = {
        "car_no": "1", "name": "テスト",
        "pref": "広島", "rank": "S1", "style": "両",
    }
    out = _build_rider_real(row)
    assert out is not None
    rider = Rider(**out)
    assert rider.home_area == "中国"
