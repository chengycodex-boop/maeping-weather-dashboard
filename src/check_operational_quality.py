# -*- coding: utf-8 -*-
"""Run stable operational quality gates against the Mae Ping SQLite data."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from init_db import ROOT
    from station_capabilities import verification_variables
except ModuleNotFoundError:
    from src.init_db import ROOT
    from src.station_capabilities import verification_variables


TMD_QPE_AUDIT = ROOT / "data" / "tmd_qpe_source_audit.json"


def freshness_level(age_hours: float | None) -> str:
    if age_hours is None:
        return "critical"
    if age_hours > 24:
        return "critical"
    if age_hours > 6:
        return "warning"
    return "ok"


def _age_hours(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    return max(0.0, (now - datetime.fromisoformat(value).astimezone(timezone.utc)).total_seconds() / 3600)


def inspect(database: Path, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        operational = connection.execute(
            """
            SELECT location_id, code, name_th, verification_status
            FROM locations
            WHERE verification_status LIKE 'priority_1%'
               OR verification_status LIKE 'priority_2%'
            ORDER BY code
            """
        ).fetchall()
        findings = []
        station_profile = []
        for station in operational:
            variables = verification_variables(
                station["code"], station["verification_status"]
            )
            profile = {
                "location_id": station["location_id"],
                "code": station["code"],
                "expected_variables": list(variables),
            }
            for variable in variables:
                period_filter = "AND period_minutes = 60" if variable == "precipitation" else ""
                latest = connection.execute(
                    f"""
                    SELECT observed_at FROM observations
                    WHERE location_id = ? AND variable = ? {period_filter}
                    ORDER BY julianday(observed_at) DESC LIMIT 1
                    """,
                    (station["location_id"], variable),
                ).fetchone()[0]
                age = _age_hours(latest, now)
                level = freshness_level(age)
                profile[f"{variable}_latest"] = latest
                profile[f"{variable}_age_hours"] = round(age, 2) if age is not None else None
                if level != "ok":
                    findings.append(
                        {
                            "severity": level,
                            "check": "freshness",
                            "entity": station["code"],
                            "variable": variable,
                            "evidence": f"latest={latest or 'missing'} age_hours={age}",
                            "impact": "ค่าบน Dashboard อาจไม่สะท้อนสถานการณ์ล่าสุด",
                        }
                    )
            daily_count = connection.execute(
                """
                SELECT COUNT(*) FROM observations
                WHERE location_id = ? AND variable = 'precipitation'
                  AND period_minutes = 1440
                  AND observed_at >= datetime(?, '-30 days')
                """,
                (station["location_id"], now.isoformat()),
            ).fetchone()[0]
            profile["rain_daily_rows_30d"] = daily_count
            if daily_count < 24:
                findings.append(
                    {
                        "severity": "warning",
                        "check": "rain_daily_completeness",
                        "entity": station["code"],
                        "variable": "precipitation",
                        "evidence": f"{daily_count}/30 days",
                        "impact": "ยังไม่เหมาะสำหรับ calibration หรือสรุป bias รายเดือน",
                    }
                )
            station_profile.append(profile)

        duplicate_keys = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT source_id, location_id, variable,
                       strftime('%s', observed_at) AS observed_epoch, period_minutes
                FROM observations
                GROUP BY source_id, location_id, variable, observed_epoch, period_minutes
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        if duplicate_keys:
            findings.append(
                {
                    "severity": "critical",
                    "check": "observation_key_uniqueness",
                    "entity": "observations",
                    "variable": None,
                    "evidence": f"duplicate_keys={duplicate_keys}",
                    "impact": "การรวมฝนและคำนวณคะแนนอาจถูกนับซ้ำ",
                }
            )
        foreign_key_issues = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if foreign_key_issues:
            findings.append(
                {
                    "severity": "critical",
                    "check": "foreign_key_integrity",
                    "entity": "database",
                    "variable": None,
                    "evidence": f"violations={foreign_key_issues}",
                    "impact": "ข้อมูลอาจผูกกับสถานีหรือแหล่งข้อมูลไม่ได้",
                }
            )
        suspect_values = connection.execute(
            "SELECT COUNT(*) FROM observations WHERE quality_flag = 'suspect'"
        ).fetchone()[0]
        unexpected_priority2_temperature = connection.execute(
            """
            SELECT COUNT(*) FROM observations o JOIN locations l USING (location_id)
            WHERE l.verification_status LIKE 'priority_2%'
              AND o.variable = 'temperature'
            """
        ).fetchone()[0]
        if unexpected_priority2_temperature:
            findings.append(
                {
                    "severity": "critical",
                    "check": "variable_capability",
                    "entity": "priority_2",
                    "variable": "temperature",
                    "evidence": f"unexpected_rows={unexpected_priority2_temperature}",
                    "impact": "ระบบอาจสร้างหรือผูกอุณหภูมิกับสถานี rain-only ผิดจุด",
                }
            )
        if TMD_QPE_AUDIT.exists():
            tmd_qpe = json.loads(TMD_QPE_AUDIT.read_text(encoding="utf-8"))
            if tmd_qpe.get("status") != "fresh":
                findings.append(
                    {
                        "severity": "warning",
                        "check": "tmd_qpe_freshness",
                        "entity": "TMD Radar composite QPE",
                        "variable": "precipitation",
                        "evidence": f"product_time={tmd_qpe.get('product_time')} age_hours={tmd_qpe.get('age_hours')}",
                        "impact": "ไม่มี radar spatial ground truth สด; ระบบใช้ gauge และ forecast โดยไม่แสดง QPE เก่า",
                    }
                )
    finally:
        connection.close()

    counts = {
        "critical": sum(row["severity"] == "critical" for row in findings),
        "warning": sum(row["severity"] == "warning" for row in findings),
        "suspect_observations": suspect_values,
        "duplicate_observation_keys": duplicate_keys,
        "foreign_key_issues": foreign_key_issues,
        "unexpected_priority2_temperature_rows": unexpected_priority2_temperature,
    }
    return {
        "checked_at": now.isoformat(),
        "status": "failed" if counts["critical"] else ("warning" if counts["warning"] else "passed"),
        "thresholds": {"fresh_warning_hours": 6, "fresh_critical_hours": 24, "rain_daily_minimum_of_30": 24},
        "counts": counts,
        "station_profile": station_profile,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "quality_latest.json")
    args = parser.parse_args()
    payload = inspect(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = payload["counts"]
    print(
        f"quality_status={payload['status']} critical={counts['critical']} "
        f"warning={counts['warning']} suspect={counts['suspect_observations']}"
    )
    return 1 if counts["critical"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
