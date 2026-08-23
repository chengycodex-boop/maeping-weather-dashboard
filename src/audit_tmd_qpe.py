# -*- coding: utf-8 -*-
"""Audit freshness and grid metadata of TMD's numerical radar QPE feed."""

from __future__ import annotations

import argparse
import io
import json
import re
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from init_db import ROOT
except ModuleNotFoundError:
    from src.init_db import ROOT


URL = "https://weather.tmd.go.th/composite/compositeQPE_VTBB_latest.asc.zip"
OUTPUT = ROOT / "data" / "tmd_qpe_source_audit.json"
TIMESTAMP_PATTERN = re.compile(r"_(\d{14})_")


def timestamp_from_filename(filename: str) -> datetime:
    match = TIMESTAMP_PATTERN.search(filename)
    if not match:
        raise ValueError(f"timestamp missing from QPE filename: {filename}")
    # TMD radar pages state that displayed radar time is UTC. Keep the
    # assumption explicit until the product metadata confirms it separately.
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def parse_header(archive_bytes: bytes) -> tuple[str, dict[str, float]]:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".asc")]
        if len(names) != 1:
            raise RuntimeError(f"expected one ASCII grid, received {len(names)}")
        filename = names[0]
        with archive.open(filename) as handle:
            header: dict[str, float] = {}
            for _ in range(6):
                key, value = handle.readline().decode("ascii").strip().split()
                header[key.lower()] = float(value)
    return filename, header


def audit(now: datetime | None = None, max_age_hours: float = 6.0) -> dict:
    request = urllib.request.Request(URL, headers={"User-Agent": "Codex-MaePingWeather/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        archive_bytes = response.read()
    filename, header = parse_header(archive_bytes)
    product_time = timestamp_from_filename(filename)
    checked_at = now or datetime.now(timezone.utc)
    age_hours = (checked_at - product_time).total_seconds() / 3600
    north = header["yllcorner"] + header["nrows"] * header["cellsize"]
    east = header["xllcorner"] + header["ncols"] * header["cellsize"]
    return {
        "checked_at": checked_at.replace(microsecond=0).isoformat(),
        "source": "TMD Radar composite QPE ASCII",
        "url": URL,
        "filename": filename,
        "product_time": product_time.isoformat(),
        "time_assumption": "UTC inferred from TMD radar page convention",
        "age_hours": round(age_hours, 2),
        "max_age_hours": max_age_hours,
        "status": "fresh" if -1 <= age_hours <= max_age_hours else "stale",
        "ingestion_decision": "eligible" if -1 <= age_hours <= max_age_hours else "skip_stale_product",
        "product": "60-minute radar composite quantitative precipitation estimate",
        "unit": "mm",
        "grid": {
            "ncols": int(header["ncols"]),
            "nrows": int(header["nrows"]),
            "cellsize_degrees": header["cellsize"],
            "west": header["xllcorner"],
            "south": header["yllcorner"],
            "east": east,
            "north": north,
            "nodata_value": header["nodata_value"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path, default=OUTPUT)
    parser.add_argument("--max-age-hours", type=float, default=6.0)
    args = parser.parse_args()
    payload = audit(max_age_hours=args.max_age_hours)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"tmd_qpe={payload['status']} product_time={payload['product_time']} "
        f"age_hours={payload['age_hours']} decision={payload['ingestion_decision']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
