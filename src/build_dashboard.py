# -*- coding: utf-8 -*-
"""Build a self-contained HTML map dashboard from the Mae Ping SQLite data."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from dashboard_v2_template import DASHBOARD_V2_HTML
    from discover_thaiwater_stations import PARK_URL, fetch_json
    from init_db import ROOT
except ModuleNotFoundError:  # Imported as src.build_dashboard in tests.
    from src.dashboard_v2_template import DASHBOARD_V2_HTML
    from src.discover_thaiwater_stations import PARK_URL, fetch_json
    from src.init_db import ROOT


STATIONS = ROOT / "data" / "stations.csv"
SHORTLIST = ROOT / "data" / "support_station_shortlist.csv"
OPERATIONAL_STATUS = ROOT / "data" / "operational_status.json"
QUALITY_STATUS = ROOT / "data" / "quality_latest.json"
BOUNDARY_COMPARISON = ROOT / "data" / "boundary_comparison_latest.json"
TMD_QPE_AUDIT = ROOT / "data" / "tmd_qpe_source_audit.json"
SYSTEM_READINESS = ROOT / "data" / "system_readiness_latest.json"
EXISTING_DASHBOARD = ROOT / "dashboard" / "index.html"


def _csv_by(path: Path, key: str) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row[key]: row for row in csv.DictReader(handle)}


def park_boundary() -> dict:
    """Prefer the live OSM boundary and fall back to the last published copy."""
    try:
        return fetch_json(PARK_URL)["geometry"]
    except Exception:
        if not EXISTING_DASHBOARD.exists():
            raise
        html = EXISTING_DASHBOARD.read_text(encoding="utf-8")
        match = re.search(r"const DATA=(\{.*?\});\s*const \$=", html, flags=re.DOTALL)
        if not match:
            raise RuntimeError("live boundary unavailable and published fallback is unreadable")
        payload = json.loads(match.group(1))
        if not isinstance(payload.get("boundary"), dict):
            raise RuntimeError("published dashboard does not contain a boundary")
        return payload["boundary"]


def dashboard_data(database: Path) -> dict:
    master = _csv_by(STATIONS, "station_id")
    support = _csv_by(SHORTLIST, "station_id")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        locations = []
        for row in connection.execute(
            """
            SELECT location_id, code, name_th, latitude, longitude,
                   coordinate_role, confidence, verification_status
            FROM locations WHERE coordinate_role <> 'grid_centroid' ORDER BY code
            """
        ):
            item = dict(row)
            if item["location_id"].startswith("THAIWATER_"):
                station_id = item["location_id"].removeprefix("THAIWATER_")
                meta = support.get(station_id, {})
                item.update(
                    {
                        "group": "support",
                        "agency": meta.get("agency_short_th", ""),
                        "distance_km": meta.get("distance_to_park_km", ""),
                        "distance_band": meta.get("distance_band", ""),
                        "decision": meta.get("operational_decision", ""),
                        "quality_note": meta.get("quality_note", ""),
                        "rain_completeness": meta.get("rain_daily_non_null_of_30", ""),
                        "temperature_completeness": meta.get("temp_non_null_of_73_hours", ""),
                    }
                )
            else:
                meta = master.get(item["location_id"], {})
                item.update(
                    {
                        "group": "reporting",
                        "agency": "DNP",
                        "decision": item["verification_status"],
                        "quality_note": meta.get("notes", ""),
                    }
                )
            locations.append(item)

        observations = [
            dict(row)
            for row in connection.execute(
                """
                SELECT location_id, variable, observed_at, period_minutes,
                       value, unit, quality_flag
                FROM observations
                ORDER BY observed_at
                """
            )
        ]
        latest_run = connection.execute("SELECT MAX(model_run) FROM forecasts").fetchone()[0]
        forecasts = []
        if latest_run:
            forecasts = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT location_id, variable, valid_at, value, unit,
                           lead_hours, model_name, model_run
                    FROM forecasts WHERE model_run = ? ORDER BY valid_at
                    """,
                    (latest_run,),
                )
            ]
        grid_cells = [
            dict(row)
            for row in connection.execute(
                """
                SELECT g.grid_id, l.code, l.latitude, l.longitude, g.spacing_km,
                       g.boundary_source, g.boundary_status
                FROM grid_cells g JOIN locations l ON l.location_id=g.grid_id
                ORDER BY g.grid_id
                """
            )
        ]
        grid_forecasts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT grid_id, valid_at, variable, value, unit, model_run
                FROM grid_forecasts_latest
                ORDER BY valid_at, grid_id, variable
                """
            )
        ]
        grid_estimates = [
            dict(row)
            for row in connection.execute(
                """
                SELECT grid_id, observed_at, variable, value, unit,
                       product_name, quality_flag, source_id
                FROM grid_estimates_latest
                ORDER BY observed_at, grid_id
                """
            )
        ]
        site_estimates = []
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_estimates_latest'"
        ).fetchone():
            site_estimates = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT location_id, variable, estimate_at, period_minutes,
                           value, unit, estimate_type, spatial_basis,
                           ground_value, model_value, radar_satellite_value,
                           source_count, source_summary, confidence_score,
                           confidence_level, uncertainty_low, uncertainty_high,
                           historical_error_percent, validation_status,
                           method_version, updated_at
                    FROM site_estimates_latest
                    ORDER BY location_id, variable
                    """
                )
            ]
        rainfall_24h = []
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_rainfall_24h_latest'"
        ).fetchone():
            rainfall_24h = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT location_id, window_start, window_end, period_minutes,
                           value, unit, estimate_type, spatial_basis,
                           source_count, source_summary, coverage_hours,
                           coverage_ratio, nearest_station_km, confidence_score,
                           confidence_level, uncertainty_low, uncertainty_high,
                           validation_status, method_version, updated_at
                    FROM site_rainfall_24h_latest
                    ORDER BY location_id
                    """
                )
            ]
        issues = {
            row["severity"]: row["count"]
            for row in connection.execute(
                """
                SELECT severity, COUNT(*) AS count
                FROM data_quality_issues WHERE resolved_at IS NULL
                GROUP BY severity
                """
            )
        }
        latest_verification = connection.execute(
            "SELECT MAX(computed_at) FROM verification_results"
        ).fetchone()[0]
        verification = []
        if latest_verification:
            verification = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT v.location_id, l.code, l.name_th, v.variable,
                           v.lead_bucket, v.pair_count, v.sample_days,
                           v.mae, v.rmse, v.mean_bias, v.pod, v.far, v.csi,
                           v.readiness_status, v.computed_at
                    FROM verification_results v
                    JOIN locations l USING (location_id)
                    WHERE v.computed_at = ?
                    ORDER BY l.code, v.variable, v.lead_bucket
                    """,
                    (latest_verification,),
                )
            ]
    finally:
        connection.close()

    operational_status = None
    if OPERATIONAL_STATUS.exists():
        operational_status = json.loads(OPERATIONAL_STATUS.read_text(encoding="utf-8"))
    quality_status = None
    if QUALITY_STATUS.exists():
        quality_status = json.loads(QUALITY_STATUS.read_text(encoding="utf-8"))
    boundary_reference = None
    if BOUNDARY_COMPARISON.exists():
        boundary_reference = json.loads(BOUNDARY_COMPARISON.read_text(encoding="utf-8"))
    tmd_qpe_audit = None
    if TMD_QPE_AUDIT.exists():
        tmd_qpe_audit = json.loads(TMD_QPE_AUDIT.read_text(encoding="utf-8"))
    system_readiness = None
    if SYSTEM_READINESS.exists():
        system_readiness = json.loads(SYSTEM_READINESS.read_text(encoding="utf-8"))
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "timezone_assumption": "Asia/Bangkok (+07:00), provisional",
        "boundary": park_boundary(),
        "locations": locations,
        "observations": observations,
        "forecasts": forecasts,
        "grid_cells": grid_cells,
        "grid_forecasts": grid_forecasts,
        "grid_estimates": grid_estimates,
        "site_estimates": site_estimates,
        "rainfall_24h": rainfall_24h,
        "issues": issues,
        "verification": verification,
        "operational_status": operational_status,
        "quality_status": quality_status,
        "boundary_reference": boundary_reference,
        "tmd_qpe_audit": tmd_qpe_audit,
        "system_readiness": system_readiness,
        "sources": {
            "observations": "https://www.thaiwater.net/",
            "boundary": "https://www.openstreetmap.org/relation/6004000",
            "forecast": "https://open-meteo.com/en/docs",
        },
    }


HTML = r'''<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mae Ping Weather Control</title>
<style>
:root{--ink:#17221b;--muted:#627067;--paper:#f4f1e8;--card:#fffdf7;--line:#d9d7cb;--forest:#174a36;--moss:#63834f;--rain:#1677a7;--heat:#d65a31;--warn:#bd7b16;--danger:#a33b32;--shadow:0 12px 32px rgba(31,48,37,.09)}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:radial-gradient(circle at 0 0,#e1eadb 0,transparent 35%),var(--paper);font-family:"Noto Sans Thai","Th Sarabun New",system-ui,sans-serif}.shell{max-width:1500px;margin:auto;padding:22px}.topbar{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-bottom:18px}.eyebrow{letter-spacing:.11em;text-transform:uppercase;color:var(--moss);font-size:.75rem;font-weight:800}.title{font-family:Georgia,"Noto Serif Thai",serif;font-size:clamp(1.8rem,4vw,3.4rem);line-height:1.05;margin:.18em 0}.subtitle{color:var(--muted);max-width:760px}.status{background:#fff3d9;color:#7d5515;border:1px solid #e9c77e;padding:9px 12px;border-radius:999px;font-weight:800;white-space:nowrap}.toolbar{display:flex;flex-wrap:wrap;gap:9px;margin:16px 0}.toolbar button,.toolbar select{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:10px 14px;border-radius:12px;font:inherit;cursor:pointer}.toolbar button.active{background:var(--forest);border-color:var(--forest);color:white}.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:14px}.card{background:rgba(255,253,247,.94);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.kpi{padding:16px}.kpi small{color:var(--muted)}.kpi strong{display:block;font-family:Georgia,serif;font-size:2rem;margin-top:4px}.grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,.75fr);gap:14px}.mapcard{padding:14px;min-height:640px}.map-head,.section-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:8px}.map-head h2,.section-head h2{margin:0;font-size:1.1rem}.legend{display:flex;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:.8rem}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}.map-wrap{position:relative;height:570px;border-radius:14px;overflow:hidden;background:linear-gradient(145deg,#dce7d5,#eef1e4);border:1px solid #cbd4c5}.map-wrap:after{content:"แนวเขต OSM — ใช้ชั่วคราว";position:absolute;left:12px;bottom:10px;color:#506353;background:rgba(255,255,255,.84);padding:5px 8px;border-radius:7px;font-size:.72rem}.map{width:100%;height:100%}.park{fill:#9bb78666;stroke:var(--forest);stroke-width:1.8;vector-effect:non-scaling-stroke}.marker{stroke:#fff;stroke-width:2;cursor:pointer;filter:drop-shadow(0 2px 2px #17221b44);transition:r .15s}.marker:hover,.marker:focus{r:8;outline:none}.marker.reporting{fill:#d08a2d}.marker.support{fill:var(--rain)}.marker.selected{stroke:#17221b;stroke-width:4}.label{font-size:10px;fill:#243329;paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round}.side{display:grid;gap:14px;align-content:start}.detail,.chartcard,.tablecard,.notes{padding:17px}.detail h2{margin:3px 0 4px}.meta{color:var(--muted);font-size:.88rem}.reading{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:15px 0}.reading div{background:#f0f2e9;padding:12px;border-radius:12px}.reading span{color:var(--muted);font-size:.78rem}.reading b{display:block;font-size:1.35rem}.flag{display:inline-block;margin-top:5px;padding:4px 7px;border-radius:8px;background:#fff1cc;color:#83580d;font-size:.75rem}.chartcard{grid-column:1/-1}.chart{width:100%;height:280px;background:#faf9f4;border-radius:12px}.axis{stroke:#bdc5bc;stroke-width:1}.gridline{stroke:#e4e6df;stroke-width:1}.obsline{fill:none;stroke:var(--rain);stroke-width:2.5}.templine{fill:none;stroke:var(--heat);stroke-width:2.5}.forecastline{fill:none;stroke:#7763a9;stroke-width:2;stroke-dasharray:6 5}.charttext{fill:#6a746c;font-size:10px}.empty{text-align:center;color:var(--muted);padding:80px 10px}.tablecard{grid-column:1/-1;overflow:auto}table{border-collapse:collapse;width:100%;font-size:.88rem}th,td{text-align:left;border-bottom:1px solid var(--line);padding:10px 8px;white-space:nowrap}th{color:var(--muted);font-size:.78rem}.pill{padding:4px 7px;border-radius:999px;background:#e6eee2}.notes{grid-column:1/-1;color:var(--muted);font-size:.88rem}.notes h2{color:var(--ink);margin-top:0}.notes ul{margin-bottom:0}.source-links a{color:var(--forest)}
.heatcell{stroke:#fff8;stroke-width:.45;vector-effect:non-scaling-stroke}.government-boundary{fill:none;stroke:#8b5a9f;stroke-width:2;stroke-dasharray:8 5;vector-effect:non-scaling-stroke;pointer-events:none}
@media(max-width:1000px){.kpis{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}.mapcard{min-height:auto}.map-wrap{height:520px}}@media(max-width:620px){.shell{padding:14px}.topbar{display:block}.status{display:inline-block;margin-top:10px}.kpis{grid-template-columns:1fr}.map-wrap{height:440px}.reading{grid-template-columns:1fr}}
</style>
</head>
<body><main class="shell">
<header class="topbar"><div><div class="eyebrow">Mae Ping National Park · Weather Intelligence</div><h1 class="title">ฝนและอุณหภูมิ<br>อุทยานแห่งชาติแม่ปิง</h1><div class="subtitle">แยกจุดรายงานของอุทยานออกจากสถานีตรวจวัดจริง พร้อมแสดงความสด แหล่งที่มา และข้อจำกัดของข้อมูลทุกจุด</div></div><div class="status" id="operationalBadge">ข้อมูลทดลอง · PROVISIONAL</div></header>
<div class="toolbar" aria-label="ตัวกรอง"><button id="rainBtn" class="active">ฝน</button><button id="tempBtn">อุณหภูมิ</button><button id="heatmapBtn" class="active">ชั้นข้อมูลพื้นที่ 5 กม.</button><select id="areaLayerSelect" aria-label="เลือกชั้นข้อมูลพื้นที่"><option value="forecast">Open-Meteo Forecast</option><option value="radar">TMD Radar QPE</option></select><select id="gridTimeSelect" aria-label="เลือกเวลาพยากรณ์"></select><button id="allBtn">ทุกจุด</button><select id="stationSelect" aria-label="เลือกสถานี"></select></div>
<section class="kpis"><div class="card kpi"><small>ระบบด้านเทคนิค</small><strong id="kpiTechnical">—</strong></div><div class="card kpi"><small>หลักฐานความแม่นยำ</small><strong id="kpiEvidence">—</strong></div><div class="card kpi"><small>สถานีปฏิบัติการที่มีข้อมูล</small><strong id="kpiStations">—</strong></div><div class="card kpi"><small>ฝนสูงสุด 24 ชม.</small><strong id="kpiRain">—</strong></div><div class="card kpi"><small>ช่วงอุณหภูมิล่าสุด</small><strong id="kpiTemp">—</strong></div><div class="card kpi"><small>ประเด็นคุณภาพข้อมูลเปิดอยู่</small><strong id="kpiIssues">—</strong></div></section>
<section class="grid">
<article class="card mapcard"><div class="map-head"><div><h2>แผนที่ตรวจวัดและพยากรณ์เชิงพื้นที่</h2><div class="meta" id="mapTime">Area Forecast · เลือกเวลา</div></div><div class="legend"><span><i class="dot" style="background:#d08a2d"></i>จุดรายงาน</span><span><i class="dot" style="background:#1677a7"></i>ค่าตรวจวัด ThaiWater</span><span><i class="dot" style="background:#7763a9;border-radius:2px"></i>Area Forecast 5 กม.</span><span style="color:#8b5a9f">– – แนวอ้างอิงรัฐ 2557</span></div></div><div class="map-wrap"><svg id="map" class="map" viewBox="0 0 760 570" role="img" aria-label="แผนที่อุทยานแห่งชาติแม่ปิง สถานีตรวจวัด และพยากรณ์แบบกริด"></svg></div></article>
<aside class="side"><section class="card detail"><div class="eyebrow" id="detailGroup">—</div><h2 id="detailName">เลือกจุดบนแผนที่</h2><div class="meta" id="detailMeta">—</div><div class="reading"><div><span>ฝน 24 ชม.</span><b id="detailRain">—</b></div><div><span>อุณหภูมิล่าสุด</span><b id="detailTemp">—</b></div></div><div class="meta" id="detailTime">—</div><div class="flag" id="detailFlag">ไม่มีข้อมูล</div><p class="meta" id="detailNote"></p></section></aside>
<section class="card chartcard"><div class="section-head"><h2 id="chartTitle">อนุกรมเวลา</h2><div class="legend"><span><i class="dot" style="background:#1677a7"></i>ตรวจวัด</span><span><i class="dot" style="background:#7763a9"></i>พยากรณ์</span></div></div><svg id="chart" class="chart" viewBox="0 0 1200 280" aria-label="กราฟอนุกรมเวลา"></svg></section>
<section class="card tablecard"><div class="section-head"><h2>ความพร้อมประเมินความแม่นยำ</h2><span class="meta">จับคู่พิกัดและเวลาเดียวกัน · ready เมื่อ ≥ 60 วัน</span></div><table><thead><tr><th>รหัส</th><th>ตัวแปร</th><th>Lead time</th><th>จำนวนคู่</th><th>จำนวนวัน</th><th>MAE</th><th>RMSE</th><th>Bias</th><th>CSI</th><th>สถานะ</th></tr></thead><tbody id="accuracyTable"></tbody></table></section>
<section class="card tablecard"><div class="section-head"><h2>สถานีสนับสนุนที่คัดเลือก</h2><span class="meta">ความครบถ้วนจากช่วงตรวจเบื้องต้น</span></div><table><thead><tr><th>รหัส</th><th>สถานี</th><th>ระยะถึงอุทยาน</th><th>ฝน 30 วัน</th><th>อุณหภูมิ 73 รอบ</th><th>ฝน 24 ชม.</th><th>อุณหภูมิล่าสุด</th><th>สถานะ</th></tr></thead><tbody id="stationTable"></tbody></table></section>
<section class="card notes"><h2>อ่านผลอย่างระมัดระวัง</h2><div class="flag" id="tmdQpeStatus">TMD QPE · กำลังตรวจความสด</div><ul><li>สีพื้นกริดคือ Open-Meteo Area Forecast ณ เวลาที่เลือก ไม่ใช่ค่าตรวจวัดจากสถานี</li><li>เวลา ThaiWater ถูกตีความเป็น Asia/Bangkok (+07:00) และยังเป็น provisional จนผู้ให้บริการยืนยัน convention</li><li>เส้นทึบ/กริดใช้ OSM ชั่วคราว ส่วนเส้นประเป็นชั้นอ้างอิง GISTDA/MNRE ปี 2557; พื้นที่ต่างกันประมาณ 1.33% และศูนย์กลางกริด OSM ตรงกับแนวรัฐ 39/40 ช่อง แต่ยังต้องขอ geometry ปัจจุบันจาก DNP</li><li>ระบบพบ TMD Radar composite QPE ASCII แบบตัวเลข 0.01°/60 นาทีแล้ว แต่ freshness gate จะไม่รับไฟล์ที่เก่ากว่า 6 ชั่วโมง</li><li>มป.7 มีพิกัดตรงกับ DNP088 แต่ชื่อ “น้ำห่อ/ถ้ำหม้อ” ขัดกัน จึงยังไม่รวมอัตลักษณ์เป็นจุดเดียว</li><li>ยังไม่แสดง “ความแม่นยำ %” เพราะต้องเก็บคู่พยากรณ์–ค่าจริง rolling 60–90 วันก่อน แล้วรายงาน MAE/RMSE/Bias/POD/FAR/CSI แยกตาม lead time</li></ul><p class="source-links">แหล่งข้อมูล: <a href="https://www.thaiwater.net/">ThaiWater</a> · <a href="https://weather.tmd.go.th/composite/compositeQPE_VTBB_latest.asc.zip">TMD QPE ASCII</a> · <a href="https://www.openstreetmap.org/relation/6004000">OpenStreetMap</a> · <a href="https://gistdaportal.gistda.or.th/data/rest/services/L10_Forest/L10_NPRK_MNRE_50k/MapServer">GISTDA/MNRE</a> · <a href="https://catalog.dnp.go.th/dataset/141a61a6-e744-45e2-bde5-4449c1068da3">DNP Catalog</a> · <a href="https://open-meteo.com/en/docs">Open-Meteo baseline</a></p><div class="meta" id="generatedAt"></div></section>
</section></main>
<script>
const DATA=__DASHBOARD_DATA__;
const forecastAreaRows=DATA.grid_forecasts.map(d=>({...d,layer:'forecast'}));
const radarAreaRows=DATA.grid_estimates.map(d=>({...d,valid_at:d.observed_at,layer:'radar'}));
let gridTimes=[...new Set(forecastAreaRows.map(d=>d.valid_at))].sort();
const nextGridTime=gridTimes.find(t=>Date.parse(t)>=Date.now())||gridTimes[0]||null;
const state={variable:'precipitation',selected:null,gridTime:nextGridTime,heatmap:true,areaLayer:'forecast'};
function currentAreaRows(){return state.areaLayer==='radar'?radarAreaRows:forecastAreaRows}
const $=id=>document.getElementById(id);
const located=DATA.locations.filter(d=>d.latitude!==null&&d.longitude!==null);
const operational=DATA.locations.filter(d=>d.group==='support'&&(d.decision.startsWith('priority_1')||d.decision.startsWith('priority_2')));
const priority=DATA.locations.filter(d=>d.group==='support'&&d.decision.startsWith('priority_1'));
const obsFor=(id,v)=>DATA.observations.filter(d=>d.location_id===id&&d.variable===v);
const fcFor=(id,v)=>DATA.forecasts.filter(d=>d.location_id===id&&d.variable===v);
const latest=arr=>arr.length?arr.reduce((a,b)=>a.observed_at>b.observed_at?a:b):null;
function rain24(id){const rows=obsFor(id,'precipitation').filter(d=>d.period_minutes===60);if(!rows.length)return null;const end=Math.max(...rows.map(d=>Date.parse(d.observed_at)));return rows.filter(d=>Date.parse(d.observed_at)>end-24*3600000&&Date.parse(d.observed_at)<=end).reduce((s,d)=>s+d.value,0)}
function tempLatest(id){return latest(obsFor(id,'temperature'))}
function fmt(v,n=1){return v===null||v===undefined?'—':Number(v).toFixed(n)}
function freshness(item){if(!item)return 'ไม่มีข้อมูล';const hours=(Date.now()-Date.parse(item.observed_at))/36e5;if(hours<=2)return 'สด ≤ 2 ชม.';if(hours<=6)return 'ล่าช้า ≤ 6 ชม.';return `เก่ากว่า ${Math.max(0,Math.round(hours))} ชม.`}
function rings(g){if(g.type==='Polygon')return [g.coordinates[0]];return g.coordinates.map(p=>p[0])}
const governmentGeometry=DATA.boundary_reference?.government_reference?.geometry||null;
const coords=rings(DATA.boundary).flat().concat(governmentGeometry?rings(governmentGeometry).flat():[],located.map(d=>[d.longitude,d.latitude]),DATA.grid_cells.map(d=>[d.longitude,d.latitude]));
const xs=coords.map(c=>c[0]),ys=coords.map(c=>c[1]);const bounds={minX:Math.min(...xs),maxX:Math.max(...xs),minY:Math.min(...ys),maxY:Math.max(...ys)};
function project(c){const pad=34,w=760-pad*2,h=570-pad*2;return [pad+(c[0]-bounds.minX)/(bounds.maxX-bounds.minX)*w,pad+(bounds.maxY-c[1])/(bounds.maxY-bounds.minY)*h]}
function geometryPath(geometry){return rings(geometry).map(r=>r.map((c,i)=>`${i?'L':'M'}${project(c).join(',')}`).join(' ')+' Z').join(' ')}
function parkPath(){return geometryPath(DATA.boundary)}
function areaColor(v){if(state.variable==='temperature')return v>=35?'#a33b32':v>=32?'#d65a31':v>=29?'#e69547':v>=26?'#e7c267':'#79a9bd';return v>=20?'#5b3f92':v>=10?'#315f9f':v>=5?'#287fa5':v>=1?'#5ca2b4':v>0?'#a6cad0':'#e6ede3'}
function renderMap(){const svg=$('map');svg.innerHTML='';const ns='http://www.w3.org/2000/svg';const defs=document.createElementNS(ns,'defs');const clip=document.createElementNS(ns,'clipPath');clip.setAttribute('id','parkClip');const clipShape=document.createElementNS(ns,'path');clipShape.setAttribute('d',parkPath());clip.appendChild(clipShape);defs.appendChild(clip);svg.appendChild(defs);const park=document.createElementNS(ns,'path');park.setAttribute('d',parkPath());park.setAttribute('class','park');svg.appendChild(park);if(state.heatmap&&state.gridTime){const group=document.createElementNS(ns,'g');group.setAttribute('clip-path','url(#parkClip)');const values=new Map(currentAreaRows().filter(d=>d.valid_at===state.gridTime&&d.variable===state.variable).map(d=>[d.grid_id,d]));DATA.grid_cells.forEach(d=>{const item=values.get(d.grid_id);if(!item)return;const halfLat=d.spacing_km/111.32/2,halfLon=d.spacing_km/(111.32*Math.cos(d.latitude*Math.PI/180))/2;const a=project([d.longitude-halfLon,d.latitude+halfLat]),b=project([d.longitude+halfLon,d.latitude-halfLat]);const rect=document.createElementNS(ns,'rect');rect.setAttribute('x',a[0]);rect.setAttribute('y',a[1]);rect.setAttribute('width',Math.max(1,b[0]-a[0]));rect.setAttribute('height',Math.max(1,b[1]-a[1]));rect.setAttribute('fill',areaColor(item.value));rect.setAttribute('fill-opacity','.78');rect.setAttribute('class','heatcell');const title=document.createElementNS(ns,'title');title.textContent=`${state.areaLayer==='radar'?'Radar Area Estimate':'Area Forecast'} ${d.code} · ${new Date(item.valid_at).toLocaleString('th-TH')} · ${fmt(item.value)} ${item.unit}`;rect.appendChild(title);group.appendChild(rect)});svg.appendChild(group);svg.appendChild(park)}if(governmentGeometry){const reference=document.createElementNS(ns,'path');reference.setAttribute('d',geometryPath(governmentGeometry));reference.setAttribute('class','government-boundary');const title=document.createElementNS(ns,'title');title.textContent='แนวอ้างอิง GISTDA/MNRE ปี 2557 · ใช้ตรวจเทียบ ไม่ใช่แนวเขต DNP ปัจจุบัน';reference.appendChild(title);svg.appendChild(reference)}located.forEach(d=>{const [x,y]=project([d.longitude,d.latitude]);const g=document.createElementNS(ns,'g');const c=document.createElementNS(ns,'circle');c.setAttribute('cx',x);c.setAttribute('cy',y);c.setAttribute('r',d.group==='support'?6:4.5);c.setAttribute('class',`marker ${d.group} ${state.selected===d.location_id?'selected':''}`);c.setAttribute('tabindex','0');c.setAttribute('role','button');c.setAttribute('aria-label',`${d.code} ${d.name_th}`);if(d.group==='support'){const val=state.variable==='precipitation'?rain24(d.location_id):(tempLatest(d.location_id)||{}).value;c.style.fill=val===null||val===undefined?'#8b948d':state.variable==='precipitation'?rainColor(val):tempColor(val)}c.addEventListener('click',()=>selectStation(d.location_id));c.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectStation(d.location_id)}});g.appendChild(c);if(d.code==='HQ'||d.group==='support'){const t=document.createElementNS(ns,'text');t.setAttribute('x',x+8);t.setAttribute('y',y-7);t.setAttribute('class','label');t.textContent=d.code;g.appendChild(t)}svg.appendChild(g)});const label=state.areaLayer==='radar'?'Radar Area Estimate':'Area Forecast';$('mapTime').textContent=state.gridTime?`${label} 5 กม. · ${new Date(state.gridTime).toLocaleString('th-TH')} · ${state.variable==='precipitation'?'ฝนรายชั่วโมง (mm)':'อุณหภูมิ (°C)'}`:`${label} · ยังไม่มีข้อมูลสด`}
function rainColor(v){return v>=50?'#7247a6':v>=20?'#1778a8':v>0?'#4e9bb8':'#8aa08b'}function tempColor(v){return v>=35?'#a33b32':v>=30?'#d65a31':v>=25?'#e69547':'#4f8da8'}
function selectStation(id){state.selected=id;$('stationSelect').value=id;renderMap();renderDetail();renderChart()}
function renderDetail(){const d=DATA.locations.find(x=>x.location_id===state.selected);if(!d)return;$('detailGroup').textContent=d.group==='support'?'สถานีตรวจวัด ThaiWater':'จุดรายงานอุทยาน';$('detailName').textContent=`${d.code} · ${d.name_th}`;$('detailMeta').textContent=`${d.coordinate_role} · ความเชื่อมั่น ${d.confidence}${d.agency?' · '+d.agency:''}`;const r=rain24(d.location_id),t=tempLatest(d.location_id);$('detailRain').textContent=r===null?'—':`${fmt(r)} mm`;$('detailTemp').textContent=t?`${fmt(t.value)} °C`:'—';const newest=latest(DATA.observations.filter(x=>x.location_id===d.location_id));$('detailTime').textContent=newest?`ล่าสุด ${new Date(newest.observed_at).toLocaleString('th-TH')} · ${freshness(newest)}`:'จุดนี้ยังมีเฉพาะข้อมูลพยากรณ์/พิกัด';$('detailFlag').textContent=newest?newest.quality_flag:'ไม่มีค่าตรวจวัด';$('detailNote').textContent=d.quality_note||''}
function renderChart(){const id=state.selected,v=state.variable;const observed=obsFor(id,v).filter(d=>v!=='precipitation'||d.period_minutes===60).slice(-168).map(d=>({t:Date.parse(d.observed_at),v:d.value,type:'obs'}));const forecast=fcFor(id,v).slice(0,168).map(d=>({t:Date.parse(d.valid_at),v:d.value,type:'forecast'}));const all=observed.concat(forecast);const svg=$('chart');const d=DATA.locations.find(x=>x.location_id===id);$('chartTitle').textContent=`${v==='precipitation'?'ฝนรายชั่วโมง':'อุณหภูมิ'} · ${d?d.code:'—'}`;if(!all.length){svg.innerHTML='<text x="600" y="140" text-anchor="middle" class="charttext">ยังไม่มีอนุกรมเวลาสำหรับจุดนี้</text>';return}const W=1200,H=280,p={l:54,r:20,t:20,b:35},minT=Math.min(...all.map(x=>x.t)),maxT=Math.max(...all.map(x=>x.t)),minV=Math.min(...all.map(x=>x.v)),maxV=Math.max(...all.map(x=>x.v));const spanV=maxV-minV||1,spanT=maxT-minT||1;const x=t=>p.l+(t-minT)/spanT*(W-p.l-p.r),y=v=>p.t+(maxV-v)/spanV*(H-p.t-p.b);let html='';for(let i=0;i<5;i++){const yy=p.t+i*(H-p.t-p.b)/4,val=maxV-i*spanV/4;html+=`<line x1="${p.l}" y1="${yy}" x2="${W-p.r}" y2="${yy}" class="gridline"/><text x="${p.l-8}" y="${yy+4}" text-anchor="end" class="charttext">${fmt(val)}</text>`}const line=arr=>arr.map((q,i)=>`${i?'L':'M'}${x(q.t).toFixed(1)},${y(q.v).toFixed(1)}`).join(' ');if(observed.length)html+=`<path d="${line(observed)}" class="${v==='temperature'?'templine':'obsline'}"/>`;if(forecast.length)html+=`<path d="${line(forecast)}" class="forecastline"/>`;html+=`<line x1="${p.l}" y1="${H-p.b}" x2="${W-p.r}" y2="${H-p.b}" class="axis"/><text x="${p.l}" y="${H-10}" class="charttext">${new Date(minT).toLocaleDateString('th-TH')}</text><text x="${W-p.r}" y="${H-10}" text-anchor="end" class="charttext">${new Date(maxT).toLocaleDateString('th-TH')}</text>`;svg.innerHTML=html}
function renderTable(){$('stationTable').innerHTML=DATA.locations.filter(d=>d.group==='support').map(d=>{const t=tempLatest(d.location_id),r=rain24(d.location_id);return `<tr><td><button onclick="selectStation('${d.location_id}')">${d.code}</button></td><td>${d.name_th}</td><td>${d.distance_km||'—'} km</td><td>${d.rain_completeness||'—'}/30</td><td>${d.temperature_completeness||'—'}/73</td><td>${r===null?'—':fmt(r)+' mm'}</td><td>${t?fmt(t.value)+' °C':'—'}</td><td><span class="pill">${d.decision}</span></td></tr>`}).join('')}
function renderAccuracy(){$('accuracyTable').innerHTML=DATA.verification.map(d=>`<tr><td><button onclick="selectStation('${d.location_id}')">${d.code}</button></td><td>${d.variable==='precipitation'?'ฝน':'อุณหภูมิ'}</td><td>${d.lead_bucket} ชม.</td><td>${d.pair_count}</td><td>${d.sample_days}/60</td><td>${d.mae===null?'—':fmt(d.mae)}</td><td>${d.rmse===null?'—':fmt(d.rmse)}</td><td>${d.mean_bias===null?'—':fmt(d.mean_bias)}</td><td>${d.csi===null?'—':fmt(d.csi,2)}</td><td><span class="pill">${d.readiness_status}</span></td></tr>`).join('')||'<tr><td colspan="10">ยังไม่ได้รันการประเมิน</td></tr>'}
function renderKpis(){const withData=operational.filter(d=>DATA.observations.some(o=>o.location_id===d.location_id)).length;const rains=operational.map(d=>rain24(d.location_id)).filter(v=>v!==null),temps=priority.map(d=>tempLatest(d.location_id)).filter(Boolean).map(d=>d.value);const dynamic=(DATA.quality_status?.counts?.critical||0)+(DATA.quality_status?.counts?.warning||0),completion=DATA.system_readiness?.completion;$('kpiTechnical').textContent=completion?`${fmt(completion.technical_percent,0)}%`:'—';$('kpiEvidence').textContent=completion?`${fmt(completion.evidence_percent,0)}%`:'—';$('kpiStations').textContent=`${withData}/${operational.length}`;$('kpiRain').textContent=rains.length?`${fmt(Math.max(...rains))} mm`:'—';$('kpiTemp').textContent=temps.length?`${fmt(Math.min(...temps))}–${fmt(Math.max(...temps))} °C`:'—';$('kpiIssues').textContent=Object.values(DATA.issues).reduce((a,b)=>a+b,0)+dynamic}
function setVariable(v){state.variable=v;$('rainBtn').classList.toggle('active',v==='precipitation');$('tempBtn').classList.toggle('active',v==='temperature');renderMap();renderChart()}
$('rainBtn').onclick=()=>setVariable('precipitation');$('tempBtn').onclick=()=>setVariable('temperature');$('heatmapBtn').onclick=()=>{state.heatmap=!state.heatmap;$('heatmapBtn').classList.toggle('active',state.heatmap);renderMap()};$('gridTimeSelect').innerHTML=gridTimes.map(t=>`<option value="${t}">${new Date(t).toLocaleString('th-TH',{dateStyle:'short',timeStyle:'short'})}</option>`).join('')||'<option>ยังไม่มีพยากรณ์พื้นที่</option>';$('gridTimeSelect').value=state.gridTime||'';$('gridTimeSelect').disabled=!gridTimes.length;$('gridTimeSelect').onchange=e=>{state.gridTime=e.target.value;renderMap()};$('allBtn').onclick=()=>{state.selected='HQ';setVariable('precipitation');selectStation('HQ')};$('stationSelect').innerHTML=DATA.locations.map(d=>`<option value="${d.location_id}">${d.code} · ${d.name_th}</option>`).join('');$('stationSelect').onchange=e=>selectStation(e.target.value);const qpe=DATA.tmd_qpe_audit;if(qpe){$('tmdQpeStatus').textContent=`TMD QPE · ${qpe.status==='fresh'?'พร้อมใช้':'หยุดรับไฟล์เก่า'} · ข้อมูล ${new Date(qpe.product_time).toLocaleString('th-TH')} · อายุ ${Math.round(qpe.age_hours)} ชม.`;$('tmdQpeStatus').style.background=qpe.status==='fresh'?'#e3eee0':'#fff1cc'}else $('tmdQpeStatus').textContent='TMD QPE · ยังไม่ได้ตรวจแหล่งข้อมูล';const cycle=DATA.operational_status;const cycleText=cycle?` · cycle ${cycle.status} ${new Date(cycle.cycle_finished_at).toLocaleString('th-TH')}`:'';$('generatedAt').textContent=`สร้างข้อมูล ณ ${new Date(DATA.generated_at).toLocaleString('th-TH')} · ${DATA.timezone_assumption}${cycleText}`;if(cycle)$('operationalBadge').textContent=`รอบข้อมูล · ${cycle.status==='success'?'ปกติ':'มีขั้นตอนล้มเหลว'}`;state.selected=(priority.find(d=>d.code==='DNP089')||priority[0]||{location_id:'HQ'}).location_id;$('stationSelect').value=state.selected;renderKpis();renderTable();renderAccuracy();renderMap();renderDetail();renderChart();
const radarOption=$('areaLayerSelect').querySelector('option[value="radar"]');radarOption.disabled=!radarAreaRows.length;radarOption.textContent=radarAreaRows.length?'TMD Radar QPE':'TMD Radar QPE · ไม่มีข้อมูลสด';$('areaLayerSelect').onchange=e=>{state.areaLayer=e.target.value;gridTimes=[...new Set(currentAreaRows().map(d=>d.valid_at))].sort();state.gridTime=state.areaLayer==='forecast'?(gridTimes.find(t=>Date.parse(t)>=Date.now())||gridTimes[0]||null):(gridTimes.at(-1)||null);$('gridTimeSelect').innerHTML=gridTimes.map(t=>`<option value="${t}">${new Date(t).toLocaleString('th-TH',{dateStyle:'short',timeStyle:'short'})}</option>`).join('')||'<option>ไม่มีข้อมูลสด</option>';$('gridTimeSelect').value=state.gridTime||'';$('gridTimeSelect').disabled=!gridTimes.length;$('tempBtn').disabled=state.areaLayer==='radar';if(state.areaLayer==='radar')setVariable('precipitation');else renderMap()};
</script></body></html>'''


def build(database: Path, output: Path) -> None:
    data = dashboard_data(database)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(DASHBOARD_V2_HTML.replace("__DASHBOARD_DATA__", payload), encoding="utf-8")
    print(
        f"dashboard={output} locations={len(data['locations'])} "
        f"observations={len(data['observations'])} forecasts={len(data['forecasts'])}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    parser.add_argument("output", nargs="?", type=Path, default=ROOT / "dashboard" / "index.html")
    args = parser.parse_args()
    if not args.database.exists():
        raise SystemExit(f"database not found: {args.database}")
    build(args.database, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
