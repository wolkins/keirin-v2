"""yen-joy 動的取得ソース（Playwright 経由・実験的）。

**現状: 安定取得未達成**

yen-joy は Angular SPA で「決まり手・BHJS集計」タブの実数値はクリックして
表示する必要があるが:
  - ボタンが hidden 状態で `force=True` でも click 不安定
  - API エンドポイントは外部公開されておらず（reCAPTCHA 経由のみ）
  - 取得手段が確立できていない

そのため本ソースは **スケルトン実装** で、現状は常に missing を返す。
将来 yen-joy の DOM 構造調査が進んだ段階で精緻化する。

利用想定:
  - 開発者が動的取得を試したい場合
  - 本番運用は yenjoy_static (推定値) または manual (実数手入力) を使う
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Optional

from ..models import RiderStat, RiderStatsBundle, compute_quality_summary


class YenJoyDynamicSource:
    """yen-joy 動的取得（Playwright・未完成）"""

    name = "yenjoy_dynamic"

    def __init__(self, headless: bool = True, timeout_ms: int = 30000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms

    def fetch(
        self,
        *,
        venue: str,
        date: Date,
        race_no: int,
        session_no: int = 1,
    ) -> RiderStatsBundle:
        warnings: list[str] = []
        # playwright が import できるか確認
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            warnings.append(
                "playwright がインストールされていません。"
                "`pip install playwright && playwright install chromium` を実行してください。"
            )
            return self._missing_bundle(
                venue, date, race_no, session_no, warnings,
            )

        # 実装: Playwright でページを開き、決まり手・BHJSタブをクリックして HTML 取得
        # 現状は安定取得が確立できていないため、警告を出して missing を返す。
        warnings.append(
            "yenjoy_dynamic: 現状 yen-joy の「決まり手・BHJS集計」タブの"
            "動的取得が安定化していません。yenjoy_static か manual を使用してください。"
        )
        return self._missing_bundle(
            venue, date, race_no, session_no, warnings,
        )

    def _missing_bundle(
        self, venue: str, date: Date, race_no: int,
        session_no: int, warnings: list[str],
    ) -> RiderStatsBundle:
        # 7〜9車（不明なので 9 で生成）すべて missing
        riders = [
            RiderStat(car_no=i, quality="missing", source_label=self.name)
            for i in range(1, 10)
        ]
        return RiderStatsBundle(
            source=self.name,
            venue=venue,
            date=date,
            race_no=race_no,
            session_no=session_no,
            riders=riders,
            quality_summary=compute_quality_summary(riders),
            fetched_at=datetime.now(),
            warnings=warnings,
        )
