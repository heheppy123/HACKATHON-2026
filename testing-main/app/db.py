from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

DB_PATH = Path("frostflow.db")


SEED_SEGMENTS = [
    (
        "S1",
        "SUB to Quad",
        "SUB",
        "Quad",
        220.0,
        "concrete",
        2.0,
        "fair",
        0.72,
        5,
        1,
        0,
        1,
        1,
        1,
        0,
    ),
    (
        "S2",
        "Quad to CAB",
        "Quad",
        "CAB",
        280.0,
        "brick",
        1.5,
        "poor",
        0.68,
        5,
        1,
        0,
        1,
        1,
        1,
        1,
    ),
    (
        "S3",
        "CAB to Cameron Library",
        "CAB",
        "Library",
        240.0,
        "bridge",
        4.2,
        "fair",
        0.34,
        4,
        0,
        0,
        0,
        0,
        0,
        1,
    ),
    (
        "S4",
        "SUB to Cameron Library",
        "SUB",
        "Library",
        650.0,
        "asphalt",
        0.5,
        "good",
        0.25,
        3,
        0,
        1,
        1,
        1,
        0,
        0,
    ),
    (
        "S5",
        "Cameron Library to HUB",
        "Library",
        "HUB",
        300.0,
        "concrete",
        3.0,
        "poor",
        0.81,
        5,
        1,
        0,
        1,
        0,
        1,
        1,
    ),
    (
        "S6",
        "CAB to HUB",
        "CAB",
        "HUB",
        700.0,
        "asphalt",
        2.5,
        "good",
        0.57,
        4,
        1,
        0,
        0,
        1,
        1,
        1,
    ),
]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS WalkwaySegments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                start_node TEXT NOT NULL,
                end_node TEXT NOT NULL,
                distance_m REAL NOT NULL,
                slope_pct REAL NOT NULL,
                shaded INTEGER NOT NULL,
                treatment_status INTEGER NOT NULL DEFAULT 0,
                surface_type TEXT NOT NULL,
                drainage_quality TEXT NOT NULL,
                shading_exposure REAL NOT NULL,
                foot_traffic_importance INTEGER NOT NULL,
                emergency_route INTEGER NOT NULL DEFAULT 0,
                accessible_route INTEGER NOT NULL DEFAULT 0,
                main_corridor INTEGER NOT NULL DEFAULT 0,
                wind_corridor INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS Reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment_id TEXT NOT NULL,
                report_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                lat REAL,
                lon REAL,
                FOREIGN KEY(segment_id) REFERENCES WalkwaySegments(id)
            );

            CREATE TABLE IF NOT EXISTS WeatherData (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                temp_c REAL NOT NULL,
                precip_mm REAL NOT NULL
            );
            """
        )
        # Demo control room behavior: start each server session from a clean
        # reporting slate so map colors begin clear.
        conn.execute("DELETE FROM Reports")
        conn.execute("UPDATE WalkwaySegments SET treatment_status = 0")
        _ensure_segment_schema(conn)

        existing = conn.execute("SELECT COUNT(*) c FROM WalkwaySegments").fetchone()["c"]
        if existing == 0:
            conn.executemany(
                """
                INSERT INTO WalkwaySegments
                (
                    id,
                    name,
                    start_node,
                    end_node,
                    distance_m,
                    surface_type,
                    slope_pct,
                    drainage_quality,
                    shading_exposure,
                    foot_traffic_importance,
                    shaded,
                    treatment_status,
                    emergency_route,
                    accessible_route,
                    main_corridor,
                    wind_corridor
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                SEED_SEGMENTS,
            )
        else:
            _backfill_segment_profiles(conn)

        weather_count = conn.execute("SELECT COUNT(*) c FROM WeatherData").fetchone()["c"]
        if weather_count == 0:
            now = datetime.now(timezone.utc)
            rows = []
            for hours, temp, precip in [
                (-12, 1.8, 1.2),
                (-6, 0.4, 0.8),
                (0, -2.3, 0.3),
                (6, -6.4, 0.0),
                (12, -11.8, 0.1),
                (18, -15.5, 0.0),
                (24, -8.2, 0.0),
            ]:
                rows.append(((now + timedelta(hours=hours)).isoformat(), temp, precip))
            conn.executemany("INSERT INTO WeatherData(timestamp,temp_c,precip_mm) VALUES (?,?,?)", rows)


def _ensure_segment_schema(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(WalkwaySegments)").fetchall()}
    migrations = [
        ("surface_type", "ALTER TABLE WalkwaySegments ADD COLUMN surface_type TEXT NOT NULL DEFAULT 'concrete'"),
        ("drainage_quality", "ALTER TABLE WalkwaySegments ADD COLUMN drainage_quality TEXT NOT NULL DEFAULT 'fair'"),
        ("shading_exposure", "ALTER TABLE WalkwaySegments ADD COLUMN shading_exposure REAL NOT NULL DEFAULT 0.5"),
        (
            "foot_traffic_importance",
            "ALTER TABLE WalkwaySegments ADD COLUMN foot_traffic_importance INTEGER NOT NULL DEFAULT 3",
        ),
        ("emergency_route", "ALTER TABLE WalkwaySegments ADD COLUMN emergency_route INTEGER NOT NULL DEFAULT 0"),
        ("accessible_route", "ALTER TABLE WalkwaySegments ADD COLUMN accessible_route INTEGER NOT NULL DEFAULT 0"),
        ("main_corridor", "ALTER TABLE WalkwaySegments ADD COLUMN main_corridor INTEGER NOT NULL DEFAULT 0"),
        ("wind_corridor", "ALTER TABLE WalkwaySegments ADD COLUMN wind_corridor INTEGER NOT NULL DEFAULT 0"),
    ]
    for column, ddl in migrations:
        if column not in columns:
            conn.execute(ddl)

    conn.execute("UPDATE WalkwaySegments SET surface_type = 'concrete' WHERE surface_type IS NULL")
    conn.execute("UPDATE WalkwaySegments SET drainage_quality = 'fair' WHERE drainage_quality IS NULL")
    conn.execute("UPDATE WalkwaySegments SET shading_exposure = 0.5 WHERE shading_exposure IS NULL")
    conn.execute("UPDATE WalkwaySegments SET foot_traffic_importance = 3 WHERE foot_traffic_importance IS NULL")


def _backfill_segment_profiles(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        UPDATE WalkwaySegments
        SET name = 'SUB to Quad', distance_m = 220.0, surface_type = 'concrete', drainage_quality = 'fair', shading_exposure = 0.72, foot_traffic_importance = 5, emergency_route = 1, accessible_route = 1, main_corridor = 1, wind_corridor = 0
        WHERE id = 'S1'
        AND surface_type = 'concrete' AND drainage_quality = 'fair' AND shading_exposure = 0.5 AND foot_traffic_importance = 3;

        UPDATE WalkwaySegments
        SET name = 'Quad to CAB', distance_m = 280.0, surface_type = 'brick', drainage_quality = 'poor', shading_exposure = 0.68, foot_traffic_importance = 5, emergency_route = 1, accessible_route = 1, main_corridor = 1, wind_corridor = 1
        WHERE id = 'S2'
        AND surface_type = 'concrete' AND drainage_quality = 'fair' AND shading_exposure = 0.5 AND foot_traffic_importance = 3;

        UPDATE WalkwaySegments
        SET name = 'CAB to Cameron Library', distance_m = 240.0, surface_type = 'bridge', drainage_quality = 'fair', shading_exposure = 0.34, foot_traffic_importance = 4, emergency_route = 0, accessible_route = 0, main_corridor = 0, wind_corridor = 1
        WHERE id = 'S3'
        AND surface_type = 'concrete' AND drainage_quality = 'fair' AND shading_exposure = 0.5 AND foot_traffic_importance = 3;

        UPDATE WalkwaySegments
        SET name = 'SUB to Cameron Library', distance_m = 650.0, surface_type = 'asphalt', drainage_quality = 'good', shading_exposure = 0.25, foot_traffic_importance = 3, emergency_route = 1, accessible_route = 1, main_corridor = 0, wind_corridor = 0
        WHERE id = 'S4'
        AND surface_type = 'concrete' AND drainage_quality = 'fair' AND shading_exposure = 0.5 AND foot_traffic_importance = 3;

        UPDATE WalkwaySegments
        SET name = 'Cameron Library to HUB', distance_m = 300.0, surface_type = 'concrete', drainage_quality = 'poor', shading_exposure = 0.81, foot_traffic_importance = 5, emergency_route = 1, accessible_route = 0, main_corridor = 1, wind_corridor = 1
        WHERE id = 'S5'
        AND surface_type = 'concrete' AND drainage_quality = 'fair' AND shading_exposure = 0.5 AND foot_traffic_importance = 3;

        UPDATE WalkwaySegments
        SET name = 'CAB to HUB', distance_m = 700.0, surface_type = 'asphalt', drainage_quality = 'good', shading_exposure = 0.57, foot_traffic_importance = 4, emergency_route = 0, accessible_route = 1, main_corridor = 1, wind_corridor = 1
        WHERE id = 'S6'
        AND surface_type = 'concrete' AND drainage_quality = 'fair' AND shading_exposure = 0.5 AND foot_traffic_importance = 3;
        """
    )


def upsert_weather(timestamp_iso: str, temp_c: float, precip_mm: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO WeatherData(timestamp,temp_c,precip_mm) VALUES (?,?,?)",
            (timestamp_iso, temp_c, precip_mm),
        )


def fetch_rows(query: str, params: Iterable | tuple = ()) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(query, params).fetchall()


def execute(query: str, params: Iterable | tuple = ()) -> None:
    with get_conn() as conn:
        conn.execute(query, params)
