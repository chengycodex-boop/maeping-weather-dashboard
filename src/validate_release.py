# -*- coding: utf-8 -*-
"""Fail a deployment when the generated operational artifact is incomplete."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

try:
    from init_db import ROOT
except ModuleNotFoundError:
    from src.init_db import ROOT


def validate(database: Path, dashboard: Path) -> dict[str, int]:
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
    finally:
        connection.close()
    html = dashboard.read_text(encoding="utf-8")
    checks = {
        "reporting_sites": reporting_sites,
        "estimate_rows": estimate_rows,
        "variables": variables,
        "dashboard_bytes": dashboard.stat().st_size,
    }
    if reporting_sites != 13:
        raise RuntimeError(f"expected 13 reporting sites, found {reporting_sites}")
    if estimate_rows != reporting_sites * 2 or variables != 2:
        raise RuntimeError(f"incomplete estimates: {checks}")
    required_text = (
        "ข้อมูลล่าสุด",
        "สร้างหน้าเว็บ",
        "รอบถัดไปภายใน",
        "ฝนและอุณหภูมิรายพื้นที่",
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
