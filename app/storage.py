"""SQLiteによる予想・結果・反省の永続化。

予想実行時にスナップショットを保存し、結果入力後に反省ログを追記する。
本MVPでは外部DBやマイグレーションフレームワークは使わず、起動時に
CREATE TABLE IF NOT EXISTS でスキーマを保証する。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .models import Prediction, RaceInput, Reflection


DEFAULT_DB_PATH = Path.cwd() / "keirin.db"


def _race_id_date(race_id: Optional[str]) -> Optional[str]:
    """race_id の先頭8桁が数字なら YYYY-MM-DD に整形して返す。"""
    if not race_id or len(race_id) < 8:
        return None
    head = race_id[:8]
    if not head.isdigit():
        return None
    return f"{head[:4]}-{head[4:6]}-{head[6:8]}"


def _row_to_reflection(row) -> Reflection:
    """SQLite行を Reflection に変換し、created_at を後付けする。"""
    r = Reflection.model_validate_json(row["payload_json"])
    try:
        r.created_at = row["created_at"]
    except (IndexError, KeyError):
        pass
    return r


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    # SQLite の CURRENT_TIMESTAMP は "YYYY-MM-DD HH:MM:SS" 形式
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None


def _relevance_score(
    r: Reflection, input_data: RaceInput, *, now: Optional[datetime] = None
) -> float:
    """過去 reflection と当該レースの関連度スコア。

    is_girls 不一致は呼び出し側で除外済み前提なので、ここでは扱わない。
    """
    score = 0.0
    race = input_data.race
    weather = input_data.weather

    # 場一致
    if r.venue and r.venue == race.venue:
        score += 5.0

    # クラス
    if r.class_name and race.class_name:
        if r.class_name == race.class_name:
            score += 1.5
        else:
            # 「A級」「S級」「ガールズ」など先頭2文字一致なら少し
            if r.class_name[:2] == race.class_name[:2]:
                score += 0.5

    # 天候
    if weather is not None:
        if r.weather_condition and r.weather_condition == weather.condition:
            score += 2.0
        # 雨量の同層
        rain_a = _rain_layer(r.rain_mm_per_hour)
        rain_b = _rain_layer(weather.rain_mm_per_hour)
        if rain_a == rain_b:
            score += 1.0
        # 風速の近さ
        wd = abs(r.wind_speed_mps - weather.wind_speed_mps)
        if wd <= 1.0:
            score += 2.0
        elif wd <= 2.0:
            score += 1.0
        # 強風帯 (>=5m/s) で双方が強風ならボーナス
        if r.wind_speed_mps >= 5.0 and weather.wind_speed_mps >= 5.0:
            score += 0.5

    # 直近度（時間減衰）
    dt = _parse_dt(r.created_at)
    if dt is not None:
        ref = now or datetime.now()
        delta = ref - dt
        days = delta.total_seconds() / 86400.0
        if days < 0:
            days = 0
        if days <= 1:
            score += 2.0
        elif days <= 7:
            score += 1.0
        elif days <= 30:
            score += 0.5
        # 半年超は加点なし

    return score


def _rain_layer(mm: float) -> int:
    if mm >= 1.0:
        return 2
    if mm > 0:
        return 1
    return 0


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    race_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    race_no INTEGER NOT NULL,
    is_girls INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    race_id TEXT PRIMARY KEY,
    venue TEXT,
    race_no INTEGER,
    result TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    venue TEXT,
    race_no INTEGER,
    is_girls INTEGER,
    weather_condition TEXT,
    wind_speed_mps REAL,
    rain_mm_per_hour REAL,
    actual_result TEXT,
    categories_json TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reflections_venue ON reflections(venue);
CREATE INDEX IF NOT EXISTS idx_reflections_weather ON reflections(weather_condition);
"""


class Storage:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- predictions -----------------------------------------------------

    def save_prediction(self, prediction: Prediction) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO predictions (race_id, venue, race_no, is_girls, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(race_id) DO UPDATE SET
                    venue=excluded.venue,
                    race_no=excluded.race_no,
                    is_girls=excluded.is_girls,
                    payload_json=excluded.payload_json,
                    created_at=CURRENT_TIMESTAMP
                """,
                (
                    prediction.race_id,
                    prediction.venue,
                    prediction.race_no,
                    1 if prediction.is_girls else 0,
                    prediction.model_dump_json(),
                ),
            )

    def get_prediction(self, race_id: str) -> Optional[Prediction]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM predictions WHERE race_id = ?", (race_id,)
            ).fetchone()
        if row is None:
            return None
        return Prediction.model_validate_json(row["payload_json"])

    def get_latest_prediction(self) -> Optional[Prediction]:
        """直近に保存された Prediction を返す（result コマンドの簡略化用）。

        created_at が同一秒内に並ぶケースに備えて rowid をタイブレーカーに使う。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM predictions "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return Prediction.model_validate_json(row["payload_json"])

    # ---- results ---------------------------------------------------------

    def save_result(self, race_id: str, result: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT venue, race_no FROM predictions WHERE race_id = ?", (race_id,)
            ).fetchone()
            venue = row["venue"] if row else None
            race_no = row["race_no"] if row else None
            conn.execute(
                """
                INSERT INTO results (race_id, venue, race_no, result)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(race_id) DO UPDATE SET
                    result=excluded.result,
                    recorded_at=CURRENT_TIMESTAMP
                """,
                (race_id, venue, race_no, result),
            )

    def get_result(self, race_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result FROM results WHERE race_id = ?", (race_id,)
            ).fetchone()
        return row["result"] if row else None

    # ---- reflections -----------------------------------------------------

    def save_reflection(self, reflection: Reflection) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO reflections (
                    race_id, venue, race_no, is_girls,
                    weather_condition, wind_speed_mps, rain_mm_per_hour,
                    actual_result, categories_json, note, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reflection.race_id,
                    reflection.venue,
                    reflection.race_no,
                    1 if reflection.is_girls else 0,
                    reflection.weather_condition,
                    reflection.wind_speed_mps,
                    reflection.rain_mm_per_hour,
                    reflection.actual_result,
                    json.dumps(reflection.categories, ensure_ascii=False),
                    reflection.note,
                    reflection.model_dump_json(),
                ),
            )
            return int(cur.lastrowid)

    # ---- 集計レポート用の読み取り関数 -----------------------------------

    def list_predictions(
        self,
        *,
        venue: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = 1000,
    ) -> list[Prediction]:
        """predictions テーブルから条件に合うものを Prediction として返す。

        日付フィルタは race_id の先頭8桁 (YYYYMMDD) として比較する。
        該当しない race_id は filter 指定時には除外される。
        """
        query = "SELECT payload_json, race_id FROM predictions WHERE 1=1"
        params: list = []
        if venue:
            query += " AND venue = ?"
            params.append(venue)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        out: list[Prediction] = []
        for r in rows:
            if from_date or to_date:
                d = _race_id_date(r["race_id"])
                if from_date and (d is None or d < from_date):
                    continue
                if to_date and (d is None or d > to_date):
                    continue
            out.append(Prediction.model_validate_json(r["payload_json"]))
        return out

    def list_results_raw(
        self,
        *,
        venue: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict[str, str]:
        """race_id → result 文字列の辞書を返す。"""
        query = "SELECT race_id, result FROM results WHERE 1=1"
        params: list = []
        if venue:
            query += " AND venue = ?"
            params.append(venue)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        out: dict[str, str] = {}
        for r in rows:
            rid = r["race_id"]
            if from_date or to_date:
                d = _race_id_date(rid)
                if from_date and (d is None or d < from_date):
                    continue
                if to_date and (d is None or d > to_date):
                    continue
            out[rid] = r["result"]
        return out

    def list_reflections(
        self,
        venue: Optional[str] = None,
        weather_condition: Optional[str] = None,
        limit: int = 50,
    ) -> list[Reflection]:
        query = "SELECT payload_json, created_at FROM reflections WHERE 1=1"
        params: list = []
        if venue:
            query += " AND venue = ?"
            params.append(venue)
        if weather_condition:
            query += " AND weather_condition = ?"
            params.append(weather_condition)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_reflection(r) for r in rows]

    def get_relevant_reflections(
        self,
        input_data: RaceInput,
        *,
        limit: int = 5,
        candidate_pool: int = 200,
        now: Optional[datetime] = None,
    ) -> list[Reflection]:
        """次回predict向けに関連度の高いreflectionを返す。

        - is_girls が一致しないものは除外（戦法体系が違いすぎるため）
        - venue / 天候 / 風 / 雨 / class_name / 直近性 を Python 側でスコアリング
        - candidate_pool は事前に取りすぎないための上限（直近順）

        Returns: 関連度降順の Reflection リスト
        """
        is_girls_flag = 1 if input_data.race.resolved_is_girls() else 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json, created_at
                FROM reflections
                WHERE is_girls = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (is_girls_flag, candidate_pool),
            ).fetchall()
        ref_list = [_row_to_reflection(r) for r in rows]
        if not ref_list:
            return []
        scored = [
            (_relevance_score(r, input_data, now=now), idx, r)
            for idx, r in enumerate(ref_list)
        ]
        # スコア降順、同点なら直近（idxが小さい=最新）優先
        scored.sort(key=lambda x: (-x[0], x[1]))
        # スコア 0 以下は除外（無関係扱い）
        result = [r for s, _, r in scored if s > 0]
        return result[:limit]
