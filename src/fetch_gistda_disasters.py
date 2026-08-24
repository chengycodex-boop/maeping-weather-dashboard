# -*- coding: utf-8 -*-
"""Fetch GISTDA flood or VIIRS fire features for Northern Thailand."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from init_db import ROOT
    from source_portfolio import (
        ensure_source_portfolio,
        feature_row,
        record_source_health,
        replace_hazard_features,
    )
except ModuleNotFoundError:
    from src.init_db import ROOT
    from src.source_portfolio import (
        ensure_source_portfolio,
        feature_row,
        record_source_health,
        replace_hazard_features,
    )


DATABASE = ROOT / "data" / "maeping_weather.db"
BASE_URL = "https://api-gateway.gistda.or.th/api/2.0/resources"
DEFAULT_NORTH_BBOX = "97.0,14.0,101.5,20.8"
PRODUCTS = {
    "flood": {
        "source_id": "gistda_disaster_flood",
        "route_id": "gistda_flood",
        "hazard_type": "flood",
        "path": "/features/flood/1day",
        "freshness_minutes": 2160,
    },
    "fire": {
        "source_id": "gistda_disaster_fire",
        "route_id": "gistda_fire",
        "hazard_type": "wildfire",
        "path": "/features/viirs/1day",
        "freshness_minutes": 360,
    },
}


def iso_from_value(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def property_time(properties: dict) -> str | None:
    for key in (
        "observed_at", "datetime", "date_time", "acq_datetime", "acq_date",
        "image_date", "date", "timestamp", "created_at", "updated_at",
    ):
        parsed = iso_from_value(properties.get(key))
        if parsed:
            return parsed
    return None


def payload_features(payload) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("features"), list):
        return payload["features"]
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        nested = payload["data"].get("features")
        if isinstance(nested, list):
            return nested
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    raise ValueError("GISTDA response does not contain a feature list")


def numeric_property(properties: dict, names: tuple[str, ...]) -> float | None:
    for name in names:
        value = properties.get(name)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def request_json(url: str, api_key: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/geo+json, application/json",
            "API-Key": api_key,
            "User-Agent": "maeping-environment-hub/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(1)
    raise RuntimeError(f"GISTDA request failed: {last_error}")


def convert_features(payload, product: str, endpoint: str) -> tuple[list[dict], str | None]:
    config = PRODUCTS[product]
    rows: list[dict] = []
    newest: str | None = None
    for index, feature in enumerate(payload_features(payload)):
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
        fingerprint = json.dumps(
            {"geometry": geometry, "properties": properties},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        feature_id = feature.get("id") or properties.get("id") or properties.get("gid")
        if feature_id in (None, ""):
            feature_id = hashlib.sha256(fingerprint).hexdigest()[:32]
        observed_at = property_time(properties)
        newest = max(newest, observed_at) if newest and observed_at else (observed_at or newest)
        if product == "fire":
            value = numeric_property(properties, ("frp", "FRP", "brightness", "bright_ti4"))
            unit = "MW" if value is not None else None
            title = properties.get("name") or properties.get("address") or "GISTDA VIIRS hotspot"
            severity = str(properties.get("confidence") or properties.get("status") or "") or None
        else:
            value = numeric_property(properties, ("area_sqkm", "area_km2", "sq_km", "area_rai"))
            unit = "km²" if any(k in properties for k in ("area_sqkm", "area_km2", "sq_km")) else ("rai" if value is not None else None)
            title = properties.get("name") or properties.get("pv_tn") or "GISTDA flood extent"
            severity = str(properties.get("severity") or properties.get("level") or "") or None
        rows.append(
            feature_row(
                source_id=config["source_id"],
                feature_id=str(feature_id),
                hazard_type=config["hazard_type"],
                geometry=geometry,
                properties=properties,
                observed_at=observed_at,
                value=value,
                unit=unit,
                severity=severity,
                title=str(title),
                source_url=endpoint,
            )
        )
    return rows, newest


def run(database: Path, product: str) -> int:
    ensure_source_portfolio(database)
    config = PRODUCTS[product]
    cycle_id = os.environ.get("MAEPING_CYCLE_ID", "").strip() or datetime.now(timezone.utc).isoformat()
    api_key = os.environ.get("GISTDA_API_KEY", "").strip()
    started = time.monotonic()
    if not api_key:
        record_source_health(
            database,
            route_id=config["route_id"],
            cycle_id=cycle_id,
            status="credentials_missing",
            duration_seconds=0,
            records_received=0,
            newest_source_time=None,
            freshness_lag_minutes=None,
            error_code="GISTDA_API_KEY_MISSING",
            message="GISTDA connector is ready but GISTDA_API_KEY is not configured",
        )
        print(f"gistda_product={product} status=credentials_missing records=0")
        return 0

    bbox = os.environ.get("GISTDA_BBOX", DEFAULT_NORTH_BBOX).strip()
    query = urllib.parse.urlencode({"bbox": bbox, "limit": 10000, "offset": 0})
    endpoint = f"{BASE_URL}{config['path']}?{query}"
    try:
        payload = request_json(endpoint, api_key)
        rows, newest = convert_features(payload, product, endpoint)
        count = replace_hazard_features(database, config["source_id"], rows)
        lag = None
        if newest:
            lag = max(
                0.0,
                (datetime.now(timezone.utc) - datetime.fromisoformat(newest)).total_seconds() / 60,
            )
        status = "no_data" if count == 0 else (
            "stale" if lag is not None and lag > config["freshness_minutes"] else "success"
        )
        record_source_health(
            database,
            route_id=config["route_id"],
            cycle_id=cycle_id,
            status=status,
            duration_seconds=time.monotonic() - started,
            records_received=count,
            newest_source_time=newest,
            freshness_lag_minutes=lag,
            error_code=None,
            message=f"GISTDA {product} features clipped to Northern Thailand bbox {bbox}",
        )
        print(
            f"gistda_product={product} status={status} records={count} "
            f"newest_source_time={newest or 'unknown'}"
        )
        return 0
    except Exception as error:
        record_source_health(
            database,
            route_id=config["route_id"],
            cycle_id=cycle_id,
            status="failed",
            duration_seconds=time.monotonic() - started,
            records_received=0,
            newest_source_time=None,
            freshness_lag_minutes=None,
            error_code=type(error).__name__,
            message=str(error),
        )
        print(f"gistda_product={product} status=failed records=0 error={type(error).__name__}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=DATABASE)
    parser.add_argument("--product", choices=tuple(PRODUCTS), required=True)
    args = parser.parse_args()
    if not args.database.exists():
        raise SystemExit(f"database not found: {args.database}")
    return run(args.database, args.product)


if __name__ == "__main__":
    raise SystemExit(main())
