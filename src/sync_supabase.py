# -*- coding: utf-8 -*-
"""Synchronize the operational SQLite cache with private Supabase tables.

The scheduled GitHub runner is ephemeral.  Historical observations and point
forecasts are therefore restored before each cycle and written back after the
cycle.  Only a server-side Supabase secret is accepted; it must never be
embedded in the dashboard or committed to Git.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

try:
    from init_db import ROOT, build_database
except ModuleNotFoundError:
    from src.init_db import ROOT, build_database


DATABASE = ROOT / "data" / "maeping_weather.db"
STATUS_FILE = ROOT / "data" / "operational_status.json"
PAGE_SIZE = 1000
WRITE_BATCH_SIZE = 250

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "sources": ("source_id",),
    "source_routes": ("route_id",),
    "source_health_latest": ("route_id",),
    "hazard_features_latest": ("source_id", "feature_id"),
    "locations": ("location_id",),
    "grid_cells": ("grid_id",),
    "observations": ("observation_id",),
    "forecasts": ("forecast_id",),
    "grid_forecasts_latest": ("grid_id", "variable", "valid_at"),
    "grid_estimates_latest": ("grid_id", "source_id", "product_name"),
    "verification_results": ("result_id",),
    "calibration_models_latest": (
        "forecast_source_id",
        "model_name",
        "location_id",
        "variable",
        "lead_bucket",
    ),
    "site_estimates_latest": ("location_id", "variable"),
    "site_rainfall_24h_latest": ("location_id",),
    "operational_runs": ("run_id",),
}

PUSH_TABLES = (
    "sources",
    "source_routes",
    "source_health_latest",
    "hazard_features_latest",
    "locations",
    "grid_cells",
    "observations",
    "forecasts",
    "grid_forecasts_latest",
    "grid_estimates_latest",
    "verification_results",
    "calibration_models_latest",
    "site_estimates_latest",
    "site_rainfall_24h_latest",
)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def chunks(rows: list[dict], size: int = WRITE_BATCH_SIZE) -> Iterable[list[dict]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def json_safe(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def required_environment() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (
        os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_ROLE_KEY) are required"
        )
    if not url.startswith("https://"):
        raise RuntimeError("SUPABASE_URL must use https")
    if not (key.startswith("sb_secret_") or key.startswith("eyJ")):
        raise RuntimeError("Supabase key is not a server-side secret/service-role key")
    return url, key


class SupabaseRestClient:
    def __init__(self, url: str, key: str, timeout: int = 90):
        self.base = f"{url.rstrip('/')}/rest/v1"
        self.key = key
        self.timeout = timeout

    def _request(
        self,
        method: str,
        table: str,
        *,
        parameters: dict[str, str] | None = None,
        payload: list[dict] | dict | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        query = urllib.parse.urlencode(parameters or {})
        endpoint = f"{self.base}/{urllib.parse.quote(table)}" + (f"?{query}" if query else "")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        request = urllib.request.Request(endpoint, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1500]
            raise RuntimeError(f"Supabase REST {method} {table} failed ({error.code}): {detail}") from error

    def select_all(self, table: str, filters: dict[str, str] | None = None) -> list[dict]:
        output: list[dict] = []
        for start in range(0, 10_000_000, PAGE_SIZE):
            rows = self._request(
                "GET",
                table,
                parameters={"select": "*", **(filters or {})},
                extra_headers={
                    "Range-Unit": "items",
                    "Range": f"{start}-{start + PAGE_SIZE - 1}",
                },
            ) or []
            output.extend(rows)
            if len(rows) < PAGE_SIZE:
                break
        return output

    def upsert(self, table: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        conflict = ",".join(PRIMARY_KEYS[table])
        written = 0
        for batch in chunks(rows):
            self._request(
                "POST",
                table,
                parameters={"on_conflict": conflict},
                payload=batch,
                extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            written += len(batch)
        return written

    def delete_before(self, table: str, column: str, before: str) -> None:
        self._request(
            "DELETE",
            table,
            parameters={column: f"lt.{before}"},
            extra_headers={"Prefer": "return=minimal"},
        )

    def delete_where(self, table: str, filters: dict[str, str]) -> None:
        self._request(
            "DELETE",
            table,
            parameters=filters,
            extra_headers={"Prefer": "return=minimal"},
        )


def sqlite_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def sqlite_rows(connection: sqlite3.Connection, table: str) -> list[dict]:
    connection.row_factory = sqlite3.Row
    where = ""
    if table == "forecasts":
        # Accuracy can only be verified at support sensors that have observations.
        # Keep 0–72 h precipitation/temperature history there; current forecasts
        # for every reporting point remain in the generated dashboard artifact.
        where = (
            " WHERE location_id LIKE 'THAIWATER_%'"
            " AND variable IN ('precipitation', 'temperature')"
            " AND lead_hours <= 72"
        )
    return [
        {key: json_safe(row[key]) for key in row.keys()}
        for row in connection.execute(f'SELECT * FROM "{table}"{where}')
    ]


def restore_rows(connection: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    allowed = set(sqlite_columns(connection, table))
    columns = [column for column in rows[0] if column in allowed]
    placeholders = ",".join("?" for _ in columns)
    quoted = ",".join(f'"{column}"' for column in columns)
    statement = f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({placeholders})'
    connection.executemany(
        statement,
        [[row.get(column) for column in columns] for row in rows],
    )
    return len(rows)


def pull_history(database: Path, client: SupabaseRestClient, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    observation_since = (now - timedelta(days=400)).replace(microsecond=0).isoformat()
    forecast_since = (now - timedelta(days=100)).replace(microsecond=0).isoformat()
    requests = {
        "observations": {"observed_at": f"gte.{observation_since}"},
        "forecasts": {"issued_at": f"gte.{forecast_since}"},
        "source_health_latest": {},
        "hazard_features_latest": {},
        "verification_results": {},
        "calibration_models_latest": {},
    }
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        counts = {}
        for table, filters in requests.items():
            rows = client.select_all(table, filters)
            counts[table] = restore_rows(connection, table, rows)
        connection.commit()
        return counts
    finally:
        connection.close()


def operational_run_row(connection: sqlite3.Connection, sync_counts: dict) -> dict | None:
    if not STATUS_FILE.exists():
        return None
    status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    data_latest = connection.execute(
        "SELECT MAX(estimate_at) FROM site_estimates_latest"
    ).fetchone()[0]
    started = status.get("cycle_started_at")
    if not started:
        return None
    return {
        "run_id": started,
        "cycle_started_at": started,
        "cycle_finished_at": status.get("cycle_finished_at", iso_now()),
        "status": status.get("status", "partial_failure"),
        "failed_steps": status.get("failed_steps", []),
        "counts": status.get("counts", {}),
        "sync_counts": sync_counts,
        "data_latest_at": data_latest,
        "dashboard_generated_at": iso_now(),
    }


def push_all(database: Path, client: SupabaseRestClient, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    connection = sqlite3.connect(database)
    try:
        counts: dict[str, int] = {}
        latest_grid_model_run = None
        for table in PUSH_TABLES:
            rows = sqlite_rows(connection, table)
            counts[table] = client.upsert(table, rows)
            if table == "grid_forecasts_latest" and rows:
                latest_grid_model_run = max(row["model_run"] for row in rows)
        run = operational_run_row(connection, counts)
        if run:
            counts["operational_runs"] = client.upsert("operational_runs", [run])
    finally:
        connection.close()

    # Explicit retention bounds keep the free database useful for accuracy work.
    client.delete_before(
        "forecasts", "issued_at", (now - timedelta(days=100)).replace(microsecond=0).isoformat()
    )
    client.delete_before(
        "observations", "observed_at", (now - timedelta(days=400)).replace(microsecond=0).isoformat()
    )
    if latest_grid_model_run:
        client.delete_where(
            "grid_forecasts_latest", {"model_run": f"neq.{latest_grid_model_run}"}
        )
    client.delete_before(
        "operational_runs",
        "cycle_started_at",
        (now - timedelta(days=400)).replace(microsecond=0).isoformat(),
    )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("pull", "push", "roundtrip", "check"))
    parser.add_argument("database", nargs="?", type=Path, default=DATABASE)
    args = parser.parse_args()
    if not args.database.exists():
        build_database(args.database)
    url, key = required_environment()
    client = SupabaseRestClient(url, key)
    result: dict = {}
    if args.command in ("pull", "roundtrip"):
        result["pulled"] = pull_history(args.database, client)
    if args.command in ("push", "roundtrip"):
        result["pushed"] = push_all(args.database, client)
    if args.command == "check":
        result["remote_sources"] = len(client.select_all("sources"))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
