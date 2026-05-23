"""yen-joy 静的取得ソース。

yen-joy の `/forecast/detail/` ページから:
  - 競走得点（4ヶ月得点） → actual 扱い（実数値）
  - 戦法ラベル（追捲/自在/逃捲 等） → 決まり手回数は estimated 扱い（推定値）
  - B数 → estimated 扱い

選手名は yen-joy では取得が複雑なので、本ソースでは name=None で
車番順のリストを返す（呼び出し側で別途突き合わせ）。
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Optional

from app.fetchers.http import HttpClient
from app.fetchers.parsers.yenjoy_race import (
    infer_stats_from_strategy,
    parse_yenjoy_race_html,
    parse_yenjoy_strategies,
)
from app.fetchers.yenjoy import _YENJOY_USER_AGENT, _build_yenjoy_url_raw

from ..models import RiderStat, RiderStatsBundle, compute_quality_summary


class YenJoyStaticSource:
    """yen-joy 静的取得（戦法ラベル経由の推定値）"""

    name = "yenjoy_static"

    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http_client = http_client

    def fetch(
        self,
        *,
        venue: str,
        date: Date,
        race_no: int,
        session_no: int = 1,
    ) -> RiderStatsBundle:
        warnings: list[str] = []
        riders: list[RiderStat] = []
        scores: list = []
        strategies: list = []

        if self.http_client is None:
            warnings.append("HttpClient が設定されていません")
        else:
            scores, strategies = self._fetch_html_and_parse(
                venue=venue, date=date, race_no=race_no,
                session_no=session_no, warnings=warnings,
            )

        # 7〜9車を想定して RiderStat を作る
        n_cars = max(len(scores), len(strategies), 7)
        for car_no in range(1, n_cars + 1):
            idx = car_no - 1
            score = scores[idx] if idx < len(scores) and scores[idx] else None
            label = strategies[idx] if idx < len(strategies) else None
            inferred = infer_stats_from_strategy(label) if label else None

            if score is None and inferred is None:
                # 何も取れない → missing
                riders.append(RiderStat(
                    car_no=car_no, quality="missing",
                    source_label=self.name,
                ))
                continue

            # score があれば actual 扱い、決まり手は estimated（混在 → quality は弱い方を採用）
            # 仕様: 「実数値・推定値・未取得を区別」
            # 競走得点だけ取れたケース → estimated（決まり手が推定値なので全体として estimated）
            # 競走得点も決まり手も取れたケース → estimated（決まり手が推定なので）
            # 競走得点だけ取れたケース → quality="estimated"
            # 競走得点も決まり手も取れたケース → quality="estimated"
            # 静的取得は **常に estimated**（実数の決まり手は取れないため）
            n, mk, sh, mr, b = (inferred or (0, 0, 0, 0, 0))
            riders.append(RiderStat(
                car_no=car_no,
                score=float(score) if score is not None else 0.0,
                b_count=b,
                nige=n,
                makuri=mk,
                sashi=sh,
                mark=mr,
                quality="estimated",
                source_label=self.name,
                notes=f"戦法:{label}" if label else None,
            ))

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

    def _fetch_html_and_parse(
        self, *, venue: str, date: Date, race_no: int,
        session_no: int, warnings: list[str],
    ) -> tuple[list, list]:
        """yen-joy 複数URL候補を試し、最初の成功 HTML から score & 戦法を取得。"""
        from datetime import timedelta
        initial = date
        target = initial + timedelta(days=int(session_no) - 1)
        candidates = []
        if target != initial:
            candidates.append((target, target))
        candidates.append((initial, target))

        scores: list = []
        strategies: list = []
        original_ua = self.http_client.user_agent
        try:
            self.http_client.user_agent = _YENJOY_USER_AGENT
            for init_d, tgt_d in candidates:
                url = _build_yenjoy_url_raw(venue, init_d, tgt_d, race_no)
                try:
                    html = self.http_client.get(url)
                except Exception as e:
                    warnings.append(
                        f"yenjoy 取得失敗 ({url}): {type(e).__name__}: {e}"
                    )
                    continue
                got_scores = parse_yenjoy_race_html(html)
                got_strategies = parse_yenjoy_strategies(html)
                if got_scores or got_strategies:
                    scores = got_scores
                    strategies = got_strategies
                    return scores, strategies
            if not scores and not strategies:
                warnings.append(
                    "yenjoy: 全 URL 候補で取得失敗。本日 開催が無い or"
                    " URL 規則変更の可能性。"
                )
        finally:
            self.http_client.user_agent = original_ua
        return scores, strategies
