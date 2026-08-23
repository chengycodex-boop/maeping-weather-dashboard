"""Create and seed the Mae Ping weather database from reviewed CSV masters."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db" / "schema.sql"
SOURCE_REGISTRY = ROOT / "data" / "source_registry.csv"
STATIONS = ROOT / "data" / "stations.csv"
SUPPORT_STATIONS = ROOT / "data" / "support_station_shortlist.csv"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()

    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))

        source_rows = _rows(SOURCE_REGISTRY)
        connection.executemany(
            """
            INSERT INTO sources (
                source_id, source_name, provider, source_class, variables,
                spatial_grain, temporal_grain, latency, access_mode,
                authority_tier, operational_role, status, url, limitations
            ) VALUES (
                :source_id, :source_name, :provider, :source_class, :variables,
                :spatial_grain, :temporal_grain, :latency, :access_mode,
                :authority_tier, :operational_role, :status, :url, :limitations
            )
            """,
            source_rows,
        )

        station_rows = _rows(STATIONS)
        connection.executemany(
            """
            INSERT INTO locations (
                location_id, code, name_th, latitude, longitude,
                coordinate_role, confidence, verification_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["station_id"],
                    row["code"],
                    row["name_th"],
                    float(row["latitude"]) if row["latitude"] else None,
                    float(row["longitude"]) if row["longitude"] else None,
                    row["coordinate_role"],
                    row["confidence"],
                    row["verification_status"],
                )
                for row in station_rows
            ],
        )

        support_rows = _rows(SUPPORT_STATIONS)
        connection.executemany(
            """
            INSERT INTO locations (
                location_id, code, name_th, latitude, longitude,
                coordinate_role, confidence, verification_status
            ) VALUES (?, ?, ?, ?, ?, 'exact_station', 'medium', ?)
            """,
            [
                (
                    f"THAIWATER_{row['station_id']}",
                    row["station_code"],
                    row["station_name_th"],
                    float(row["latitude"]),
                    float(row["longitude"]),
                    row["operational_decision"],
                )
                for row in support_rows
            ],
        )

        captured_at = datetime.now(timezone.utc).isoformat()
        for row in station_rows:
            aliases = [alias.strip() for alias in row["aliases_th"].split("|") if alias.strip()]
            for alias in aliases:
                connection.execute(
                    "INSERT INTO location_aliases (location_id, alias, source_id) VALUES (?, ?, ?)",
                    (row["station_id"], alias, row["primary_source_id"] or None),
                )

            if row["primary_source_id"]:
                accepted = int(
                    row["coordinate_role"] == "exact_station"
                    and row["verification_status"] == "documented"
                )
                connection.execute(
                    """
                    INSERT INTO location_evidence (
                        location_id, source_id, asserted_name, asserted_latitude,
                        asserted_longitude, captured_at, evidence_note, accepted
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["station_id"],
                        row["primary_source_id"],
                        row["name_th"],
                        float(row["latitude"]) if row["latitude"] else None,
                        float(row["longitude"]) if row["longitude"] else None,
                        captured_at,
                        row["notes"],
                        accepted,
                    ),
                )

            status = row["verification_status"]
            if status == "source_conflict":
                severity = "high"
                issue_code = "LOCATION_SOURCE_CONFLICT"
            elif status == "missing_coordinate":
                severity = "high"
                issue_code = "LOCATION_COORDINATE_MISSING"
            elif row["coordinate_role"] == "area_anchor":
                severity = "medium"
                issue_code = "LOCATION_AREA_ANCHOR_ONLY"
            else:
                continue
            connection.execute(
                """
                INSERT INTO data_quality_issues (
                    entity_type, entity_id, severity, issue_code, description, detected_at
                ) VALUES ('location', ?, ?, ?, ?, ?)
                """,
                (row["station_id"], severity, issue_code, row["notes"], captured_at),
            )

        for row in support_rows:
            location_id = f"THAIWATER_{row['station_id']}"
            connection.execute(
                """
                INSERT INTO location_evidence (
                    location_id, source_id, asserted_name, asserted_latitude,
                    asserted_longitude, captured_at, evidence_note, accepted
                ) VALUES (?, 'thaiwater_data_service', ?, ?, ?, ?, ?, 0)
                """,
                (
                    location_id,
                    row["station_name_th"],
                    float(row["latitude"]),
                    float(row["longitude"]),
                    row["checked_at"],
                    row["quality_note"],
                ),
            )

            if row["operational_decision"].startswith("hold_"):
                connection.execute(
                    """
                    INSERT INTO data_quality_issues (
                        entity_type, entity_id, severity, issue_code, description, detected_at
                    ) VALUES ('location', ?, 'medium', 'SUPPORT_STATION_ON_HOLD', ?, ?)
                    """,
                    (location_id, row["quality_note"], captured_at),
                )

        connection.commit()
        foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_issues:
            raise RuntimeError(f"foreign-key violations: {foreign_key_issues}")

        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sources", "locations", "location_aliases", "location_evidence", "data_quality_issues")
        }
        print(" ".join(f"{table}={count}" for table, count in counts.items()))
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    args = parser.parse_args()
    build_database(args.database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
