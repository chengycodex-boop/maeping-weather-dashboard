"""High-signal quality checks for data/stations.csv."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIONS = ROOT / "data" / "stations.csv"
EXPECTED_CODES = {"HQ", *(f"มป.{i}" for i in range(1, 13))}
ALLOWED_ROLES = {"exact_station", "area_anchor", "grid_centroid", "unknown"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def main() -> int:
    with STATIONS.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    errors: list[str] = []
    warnings: list[str] = []
    codes = [row["code"].strip() for row in rows]

    if len(rows) != 13:
        errors.append(f"expected 13 rows, found {len(rows)}")
    if len(codes) != len(set(codes)):
        errors.append("station code is not unique")
    missing_codes = EXPECTED_CODES - set(codes)
    extra_codes = set(codes) - EXPECTED_CODES
    if missing_codes:
        errors.append(f"missing station codes: {sorted(missing_codes)}")
    if extra_codes:
        errors.append(f"unexpected station codes: {sorted(extra_codes)}")

    for row in rows:
        code = row["code"]
        latitude = row["latitude"].strip()
        longitude = row["longitude"].strip()
        role = row["coordinate_role"].strip()
        confidence = row["confidence"].strip()

        if bool(latitude) != bool(longitude):
            errors.append(f"{code}: latitude/longitude must both be present or both be blank")
        if role not in ALLOWED_ROLES:
            errors.append(f"{code}: invalid coordinate_role={role}")
        if confidence not in ALLOWED_CONFIDENCE:
            errors.append(f"{code}: invalid confidence={confidence}")
        if latitude:
            lat = float(latitude)
            lon = float(longitude)
            if not (5.0 <= lat <= 21.0 and 97.0 <= lon <= 106.0):
                errors.append(f"{code}: coordinates fall outside Thailand bounds")
        if role == "exact_station" and not row["source_url"].strip():
            errors.append(f"{code}: exact_station requires source_url")
        if role == "unknown" and latitude:
            errors.append(f"{code}: unknown coordinate_role must not contain coordinates")
        if row["verification_status"] == "source_conflict":
            warnings.append(f"{code}: unresolved source conflict; excluded from ground-truth scoring")
        if role in {"area_anchor", "unknown"}:
            warnings.append(f"{code}: {role}; do not label as a rain-gauge location")

    print(f"rows={len(rows)} errors={len(errors)} warnings={len(warnings)}")
    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARN: {message}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
