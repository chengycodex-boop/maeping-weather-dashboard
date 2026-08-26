# -*- coding: utf-8 -*-
"""Fail a deployment when the generated operational artifact is incomplete."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from init_db import ROOT
except ModuleNotFoundError:
    from src.init_db import ROOT


def validate(
    database: Path, dashboard: Path, now: datetime | None = None
) -> dict[str, int | float | str | None]:
    reference_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    connection = sqlite3.connect(database)
    try:
        reporting_sites = connection.execute(
            """SELECT COUNT(*) FROM locations
               WHERE location_id NOT LIKE 'THAIWATER_%'
                 AND coordinate_role <> 'grid_centroid'"""
        ).fetchone()[0]
        estimate_rows = connection.execute(
            "SELECT COUNT(*) FROM site_estimates_latest"
        ).fetchone()[0]
        variables = connection.execute(
            "SELECT COUNT(DISTINCT variable) FROM site_estimates_latest"
        ).fetchone()[0]
        rainfall_24h_rows = connection.execute(
            "SELECT COUNT(*) FROM site_rainfall_24h_latest"
        ).fetchone()[0]
        rainfall_24h_end = connection.execute(
            "SELECT MAX(window_end) FROM site_rainfall_24h_latest"
        ).fetchone()[0]
        rainfall_today_rows = connection.execute(
            "SELECT COUNT(*) FROM site_rainfall_today_latest"
        ).fetchone()[0]
        rainfall_today_window = connection.execute(
            "SELECT MIN(window_start), MAX(window_end) FROM site_rainfall_today_latest"
        ).fetchone()
        latest_hourly_rain = connection.execute(
            """SELECT observed_at FROM observations
               WHERE variable='precipitation' AND period_minutes=60
                 AND quality_flag IN ('raw', 'provisional', 'validated')
               ORDER BY julianday(observed_at) DESC LIMIT 1"""
        ).fetchone()
    finally:
        connection.close()
    html = dashboard.read_text(encoding="utf-8")
    checks = {
        "reporting_sites": reporting_sites,
        "estimate_rows": estimate_rows,
        "variables": variables,
        "rainfall_24h_rows": rainfall_24h_rows,
        "rainfall_24h_end": rainfall_24h_end,
        "rainfall_today_rows": rainfall_today_rows,
        "rainfall_today_start": rainfall_today_window[0],
        "rainfall_today_end": rainfall_today_window[1],
        "dashboard_bytes": dashboard.stat().st_size,
    }
    if reporting_sites != 13:
        raise RuntimeError(f"expected 13 reporting sites, found {reporting_sites}")
    if estimate_rows != reporting_sites * 2 or variables != 2:
        raise RuntimeError(f"incomplete estimates: {checks}")
    latest_hourly_at = latest_hourly_rain[0] if latest_hourly_rain else None
    latest_hourly_age = None
    if latest_hourly_at:
        latest = datetime.fromisoformat(latest_hourly_at.replace("Z", "+00:00"))
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        latest_hourly_age = max(
            0.0, (reference_now - latest.astimezone(timezone.utc)).total_seconds() / 3600
        )
    checks["latest_hourly_rain_age_hours"] = (
        None if latest_hourly_age is None else round(latest_hourly_age, 2)
    )
    if rainfall_24h_rows not in (0, reporting_sites):
        raise RuntimeError(f"incomplete 24-hour rainfall estimates: {checks}")
    if rainfall_24h_rows == 0 and latest_hourly_age is not None and latest_hourly_age <= 6:
        raise RuntimeError(f"fresh hourly rain did not produce 24-hour estimates: {checks}")
    if rainfall_24h_rows == reporting_sites and rainfall_24h_end:
        window_end = datetime.fromisoformat(rainfall_24h_end.replace("Z", "+00:00"))
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)
        if reference_now - window_end.astimezone(timezone.utc) > timedelta(hours=6):
            raise RuntimeError(f"stale 24-hour rainfall estimates must not be published: {checks}")
    if rainfall_today_rows not in (0, reporting_sites):
        raise RuntimeError(f"incomplete rainfall-since-07 estimates: {checks}")
    if rainfall_today_rows == reporting_sites and rainfall_today_window[0]:
        window_start = datetime.fromisoformat(rainfall_today_window[0].replace("Z", "+00:00"))
        window_end = datetime.fromisoformat(rainfall_today_window[1].replace("Z", "+00:00"))
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)
        bangkok = timezone(timedelta(hours=7))
        local_start = window_start.astimezone(bangkok)
        if (local_start.hour, local_start.minute) != (7, 0):
            raise RuntimeError(f"rainfall day must start at 07:00 Asia/Bangkok: {checks}")
        if reference_now - window_end.astimezone(timezone.utc) > timedelta(hours=6):
            raise RuntimeError(f"stale rainfall-since-07 estimates must not be published: {checks}")
    required_text = (
        "ข้อมูลล่าสุด",
        "สร้างหน้าเว็บ",
        "รอบถัดไปภายใน",
        "ฝนและอุณหภูมิรายพื้นที่",
        "ฝนสูงสุด 24 ชั่วโมง",
        "ข้อมูลฝนล่าช้า",
        "พยากรณ์ 7 วัน",
        "โอกาสฝน 6 ชั่วโมงหน้า",
        "ฝนสะสมวันนี้ (ตั้งแต่ 07:00 น.)",
        "rainTodayWindowText",
        "rain24WindowText",
    )
    missing = [text for text in required_text if text not in html]
    if missing or dashboard.stat().st_size < 100_000:
        raise RuntimeError(f"dashboard artifact failed validation; missing={missing}")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    parser.add_argument("dashboard", nargs="?", type=Path, default=ROOT / "dashboard" / "index.html")
    args = parser.parse_args()
    print(" ".join(f"{key}={value}" for key, value in validate(args.database, args.dashboard).items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
