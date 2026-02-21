from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

DB_PATH = Path("frostflow.db")


SEED_SEGMENTS = [
    ("S1", "SUB to Quad", "SUB", "Quad", 230.0, 2.0, 1, 0),
    ("S2", "Quad to CAB", "Quad", "CAB", 180.0, 1.5, 1, 0),
    ("S3", "CAB to Library", "CAB", "Library", 140.0, 4.2, 0, 0),
    ("S4", "SUB to Library", "SUB", "Library", 200.0, 0.5, 0, 1),
    ("S5", "Library to HUB", "Library", "HUB", 260.0, 3.0, 1, 0),
    ("S6", "CAB to HUB", "CAB", "HUB", 220.0, 2.5, 1, 0),
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
                treatment_status INTEGER NOT NULL DEFAULT 0
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

        existing = conn.execute("SELECT COUNT(*) c FROM WalkwaySegments").fetchone()["c"]
        if existing == 0:
            conn.executemany(
                """
                INSERT INTO WalkwaySegments
                (id, name, start_node, end_node, distance_m, slope_pct, shaded, treatment_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                SEED_SEGMENTS,
            )

        weather_count = conn.execute("SELECT COUNT(*) c FROM WeatherData").fetchone()["c"]
        if weather_count == 0:
            now = datetime.now(timezone.utc)
            rows = []
            for hours, temp, precip in [(-12, 2.2, 1.8), (-6, 0.9, 0.6), (0, -1.5, 0.1), (6, -3.2, 0.0), (12, -2.1, 0.0), (24, -0.6, 0.2)]:
                rows.append(((now + timedelta(hours=hours)).isoformat(), temp, precip))
            conn.executemany("INSERT INTO WeatherData(timestamp,temp_c,precip_mm) VALUES (?,?,?)", rows)


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
