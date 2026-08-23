# -*- coding: utf-8 -*-
"""Build the canonical Data Analytics artifact for accuracy readiness."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

try:
    from init_db import ROOT
except ModuleNotFoundError:
    from src.init_db import ROOT


VERIFICATION_SQL = """SELECT l.code, v.variable, v.lead_bucket, v.pair_count,
       v.sample_days, v.mae, v.rmse, v.mean_bias, v.csi,
       v.readiness_status, v.computed_at
FROM verification_results v
JOIN locations l USING (location_id)
WHERE v.computed_at = (SELECT MAX(computed_at) FROM verification_results)
ORDER BY l.code, v.variable, v.lead_bucket"""

COVERAGE_SQL = """SELECT l.code, f.variable, COUNT(*) AS forecast_points,
       MIN(f.valid_at) AS first_valid_at, MAX(f.valid_at) AS last_valid_at,
       COUNT(DISTINCT substr(f.valid_at, 1, 10)) AS forecast_days
FROM forecasts f
JOIN locations l USING (location_id)
WHERE f.model_run = (SELECT MAX(model_run) FROM forecasts)
  AND f.location_id LIKE 'THAIWATER_%'
  AND f.variable IN ('precipitation', 'temperature')
  AND EXISTS (
        SELECT 1 FROM verification_results v
        WHERE v.location_id = f.location_id
          AND v.variable = f.variable
          AND v.computed_at = (SELECT MAX(computed_at) FROM verification_results)
      )
GROUP BY l.code, f.variable
ORDER BY l.code, f.variable"""


def build_artifact(database: Path) -> dict:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        verification = [dict(row) for row in connection.execute(VERIFICATION_SQL)]
        coverage = [dict(row) for row in connection.execute(COVERAGE_SQL)]
        latest_run = connection.execute("SELECT MAX(model_run) FROM forecasts").fetchone()[0]
    finally:
        connection.close()

    for row in verification:
        row["variable_label"] = "ฝน" if row["variable"] == "precipitation" else "อุณหภูมิ"
    for row in coverage:
        row["variable_label"] = "ฝน" if row["variable"] == "precipitation" else "อุณหภูมิ"

    computed_at = max((row["computed_at"] for row in verification), default=latest_run)
    matched_pairs = sum(row["pair_count"] for row in verification)
    ready_groups = sum(row["readiness_status"] == "ready" for row in verification)
    max_days = max((row["sample_days"] for row in verification), default=0)
    overview = [
        {
            "priority_sensors": len({row["code"] for row in coverage}),
            "forecast_sensor_locations": len({row["code"] for row in coverage}),
            "forecast_points": sum(row["forecast_points"] for row in coverage),
            "matched_pairs": matched_pairs,
            "verification_groups": len(verification),
            "ready_groups": ready_groups,
            "max_sample_days": max_days,
            "minimum_ready_days": 60,
        }
    ]
    source = {
        "id": "maeping_sqlite_accuracy",
        "label": "Mae Ping weather SQLite verification snapshot",
        "path": "data/maeping_weather.db",
        "query": {
            "engine": "sqlite",
            "sql": VERIFICATION_SQL,
            "description": "ผลจับคู่ forecast–observation ล่าสุดที่พิกัดและเวลาเดียวกัน",
            "executed_at": computed_at,
            "tables_used": ["verification_results", "forecasts", "observations", "locations"],
            "filters": [
                "Priority 1 ThaiWater sensor locations only",
                "ThaiWater quality_flag excludes suspect and missing",
                "Rainfall uses 60-minute accumulation only",
                "Latest verification computation snapshot",
            ],
            "metric_definitions": {
                "matched_pairs": "จำนวน forecast records ที่มี observation ณ location_id, variable และ valid_at เดียวกัน",
                "sample_days": "จำนวนวันปฏิทินไม่ซ้ำที่มีคู่ข้อมูลในแต่ละ lead bucket",
                "ready": "sample_days อย่างน้อย 60 วัน",
                "MAE": "ค่าเฉลี่ย absolute(forecast-observed) ในหน่วยเดิม",
                "CSI": "hits / (hits + misses + false alarms), threshold ฝน 0.1 mm/hour",
            },
        },
    }
    title = "ความพร้อมประเมินความแม่นยำระบบอากาศแม่ปิง"
    blocks = [
        {"id": "title", "type": "markdown", "body": f"# {title}"},
        {
            "id": "technical_summary",
            "type": "markdown",
            "body": (
                "## ระบบเริ่มเก็บ forecast ที่พิกัดเซนเซอร์แล้ว แต่ยังห้ามสรุปความแม่นยำ\n\n"
                f"Forecast รอบล่าสุดครอบคลุม **{overview[0]['priority_sensors']} สถานีปฏิบัติการ** "
                f"และมี **{overview[0]['forecast_points']:,} จุดพยากรณ์** สำหรับฝนกับอุณหภูมิ "
                f"แต่ขณะคำนวณมีคู่ forecast–observation **{matched_pairs} คู่** จึงยังไม่มี MAE, RMSE, Bias หรือ CSI ที่เชื่อถือได้"
            ),
            "sourceId": "maeping_sqlite_accuracy",
        },
        {"id": "metrics", "type": "metric-strip", "cardIds": ["sensors", "pairs", "sample_days"]},
        {
            "id": "coverage_finding",
            "type": "markdown",
            "body": (
                "## Coverage พร้อมเริ่มสะสมข้อมูลจากเวลาพยากรณ์ถัดไป\n\n"
                "ทุกสถานีมี forecast ฝนและอุณหภูมิใน model run ล่าสุด กราฟนี้เป็น **จำนวนจุด forecast** ไม่ใช่คะแนนความแม่นยำ"
            ),
            "sourceId": "maeping_sqlite_accuracy",
        },
        {"id": "coverage_chart_block", "type": "chart", "chartId": "coverage_chart", "layout": "full"},
        {
            "id": "definitions",
            "type": "markdown",
            "body": (
                "## นิยามหน่วยวิเคราะห์และเกณฑ์พร้อมใช้\n\n"
                "หนึ่งคู่คือ forecast หนึ่ง record เทียบกับ observation ที่ `location_id + variable + valid_at` เดียวกัน "
                "ฝนต้องเป็นยอดสะสม 60 นาที ส่วนอุณหภูมิเทียบ timestamp เดียวกัน เกณฑ์ `ready` ต้องมีอย่างน้อย **60 วัน** "
                "เพื่อไม่ให้ข้อมูลรายชั่วโมงจำนวนมากในวันเดียวทำให้ sample ดูใหญ่เกินจริง"
            ),
        },
        {
            "id": "methodology",
            "type": "markdown",
            "body": (
                "## วิธีจับคู่ป้องกัน grain mismatch และ look-ahead bias\n\n"
                "คำนวณแยกตามสถานี ตัวแปร และ lead bucket 0–6 ถึง 121–168 ชั่วโมง "
                "ตัด observation ที่เป็น `suspect` หรือ `missing` และไม่ใช้ข้อมูลย้อนหลังแบบ reanalysis แทน forecast ที่ออกจริง"
            ),
        },
        {"id": "verification_table_block", "type": "table", "tableId": "verification_table", "layout": "full"},
        {
            "id": "limitations",
            "type": "markdown",
            "body": (
                "## ผลยังเป็น Needs revision สำหรับการอ้างความแม่นยำ\n\n"
                "Timestamp ของ ThaiWater ยังไม่มี offset ใน payload และถูกตีความเป็น Asia/Bangkok แบบ provisional; "
                "ยังไม่มีคู่ valid time หลังเริ่ม forecast-at-sensor; และ Open-Meteo เป็น baseline รอง ไม่ใช่โมเดลปฏิบัติการสุดท้าย"
            ),
        },
        {
            "id": "next_steps",
            "type": "markdown",
            "body": (
                "## ขั้นถัดไปคือเก็บ snapshot ต่อเนื่องและตรวจ readiness ทุกวัน\n\n"
                "1. ดึง forecast ก่อนเวลาพยากรณ์และเก็บ model run แบบ append-only\n"
                "2. ดึง observation หลัง valid time แล้วรันตัวจับคู่\n"
                "3. เปิดคะแนน provisional เมื่อครบ 30 วัน และพร้อมใช้เมื่อครบอย่างน้อย 60 วัน\n"
                "4. แยกผลฤดูฝน/ฤดูแล้งและตรวจ threshold ฝนหลายระดับก่อนทำ weighting"
            ),
        },
        {
            "id": "questions",
            "type": "markdown",
            "body": (
                "## คำถามที่ต้องยืนยันก่อนใช้ปฏิบัติการ\n\n"
                "- ThaiWater timestamp ใช้ ICT และ cutoff ฝนรายวันเวลาใดอย่างเป็นทางการ?\n"
                "- เซนเซอร์แต่ละสถานีมี calibration/maintenance log หรือไม่?\n"
                "- เกณฑ์ฝนที่สำคัญต่อการปฏิบัติงานควรเป็น 0.1, 10, 35 หรือ 90 มม. ในช่วงเวลาใด?"
            ),
        },
    ]
    return {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": title,
            "description": "Technical validation report for forecast–observation pairing readiness.",
            "generatedAt": computed_at,
            "cards": [
                {
                    "id": "sensors",
                    "description": "สถานี Priority 1 ที่มี forecast ฝนและอุณหภูมิในรอบล่าสุด",
                    "dataset": "overview",
                    "sourceId": "maeping_sqlite_accuracy",
                    "metrics": [{"label": "สถานีที่ครอบคลุม", "field": "forecast_sensor_locations", "format": "number"}],
                },
                {
                    "id": "pairs",
                    "description": "คู่ forecast–observation ที่พิกัดและ valid time เดียวกัน",
                    "dataset": "overview",
                    "sourceId": "maeping_sqlite_accuracy",
                    "metrics": [{"label": "คู่ที่จับได้", "field": "matched_pairs", "format": "number"}],
                },
                {
                    "id": "sample_days",
                    "description": "จำนวนวันสูงสุดในกลุ่มเทียบกับเกณฑ์พร้อมใช้ 60 วัน",
                    "dataset": "overview",
                    "sourceId": "maeping_sqlite_accuracy",
                    "metrics": [
                        {"label": "วันข้อมูลสูงสุด", "field": "max_sample_days", "format": "number"},
                        {"label": "เกณฑ์พร้อมใช้", "field": "minimum_ready_days", "format": "number"},
                    ],
                },
            ],
            "charts": [
                {
                    "id": "coverage_chart",
                    "title": "Forecast coverage ที่พิกัดเซนเซอร์",
                    "subtitle": "จำนวนจุดพยากรณ์ใน model run ล่าสุด แยกสถานีและตัวแปร",
                    "type": "bar",
                    "dataset": "coverage",
                    "sourceId": "maeping_sqlite_accuracy",
                    "valueFormat": "number",
                    "encodings": {
                        "x": {"field": "code", "type": "nominal", "label": "สถานี"},
                        "y": {"field": "forecast_points", "type": "quantitative", "label": "จำนวน forecast points"},
                        "color": {"field": "variable_label", "type": "nominal", "label": "ตัวแปร"},
                        "tooltip": [
                            {"field": "forecast_days", "type": "quantitative", "label": "จำนวนวัน"},
                            {"field": "first_valid_at", "type": "temporal", "label": "เริ่ม valid"},
                            {"field": "last_valid_at", "type": "temporal", "label": "สิ้นสุด valid"},
                        ],
                    },
                }
            ],
            "tables": [
                {
                    "id": "verification_table",
                    "title": "ผลประเมินตามสถานี ตัวแปร และ lead time",
                    "subtitle": "ค่าคะแนนเป็นช่องว่างจนกว่าจะมีคู่ข้อมูลจริง; snapshot ล่าสุด",
                    "dataset": "verification",
                    "sourceId": "maeping_sqlite_accuracy",
                    "defaultSort": {"field": "code", "direction": "asc"},
                    "density": "dense",
                    "columns": [
                        {"field": "code", "label": "สถานี", "type": "text"},
                        {"field": "variable_label", "label": "ตัวแปร", "type": "text"},
                        {"field": "lead_bucket", "label": "Lead (ชม.)", "type": "text"},
                        {"field": "pair_count", "label": "คู่", "format": "number"},
                        {"field": "sample_days", "label": "วัน", "format": "number"},
                        {"field": "mae", "label": "MAE", "format": "number"},
                        {"field": "rmse", "label": "RMSE", "format": "number"},
                        {"field": "mean_bias", "label": "Bias", "format": "number"},
                        {"field": "csi", "label": "CSI", "format": "number"},
                        {"field": "readiness_status", "label": "สถานะ", "type": "text"},
                    ],
                }
            ],
            "sources": [source],
            "blocks": blocks,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": computed_at,
            "status": "partial" if matched_pairs == 0 else "ready",
            "datasets": {"overview": overview, "coverage": coverage, "verification": verification},
            "accessIssues": (
                [
                    {
                        "id": "observations_not_yet_at_forecast_valid_time",
                        "dataset": "verification",
                        "message": "ยังไม่มี observation ที่ valid time หลังเริ่มเก็บ forecast ณ พิกัดเซนเซอร์ จึงยังคำนวณคะแนนไม่ได้",
                    }
                ]
                if matched_pairs == 0
                else []
            ),
        },
        "sources": [source],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=ROOT / "reports" / "accuracy-readiness" / "artifact.json",
    )
    args = parser.parse_args()
    artifact = build_artifact(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"artifact={args.output} coverage_rows={len(artifact['snapshot']['datasets']['coverage'])} "
        f"verification_rows={len(artifact['snapshot']['datasets']['verification'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
