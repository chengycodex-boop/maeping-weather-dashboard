PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    source_class TEXT NOT NULL,
    variables TEXT NOT NULL,
    spatial_grain TEXT NOT NULL,
    temporal_grain TEXT NOT NULL,
    latency TEXT NOT NULL,
    access_mode TEXT NOT NULL,
    authority_tier TEXT NOT NULL CHECK (authority_tier IN ('primary', 'secondary', 'tertiary')),
    operational_role TEXT NOT NULL,
    status TEXT NOT NULL,
    url TEXT,
    limitations TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS locations (
    location_id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_th TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    elevation_m REAL,
    coordinate_role TEXT NOT NULL CHECK (coordinate_role IN ('exact_station', 'area_anchor', 'grid_centroid', 'unknown')),
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    verification_status TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    CHECK ((latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)),
    CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
);

CREATE TABLE IF NOT EXISTS location_aliases (
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    alias TEXT NOT NULL,
    source_id TEXT REFERENCES sources(source_id),
    valid_from TEXT,
    valid_to TEXT,
    PRIMARY KEY (location_id, alias)
);

CREATE TABLE IF NOT EXISTS location_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    asserted_name TEXT,
    asserted_latitude REAL,
    asserted_longitude REAL,
    captured_at TEXT NOT NULL,
    document_page TEXT,
    evidence_note TEXT,
    accepted INTEGER NOT NULL DEFAULT 0 CHECK (accepted IN (0, 1))
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    variable TEXT NOT NULL CHECK (variable IN ('precipitation', 'temperature', 'apparent_temperature', 'relative_humidity', 'dew_point')),
    observed_at TEXT NOT NULL,
    period_minutes INTEGER NOT NULL CHECK (period_minutes > 0),
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    quality_flag TEXT NOT NULL CHECK (quality_flag IN ('raw', 'provisional', 'validated', 'suspect', 'missing')),
    spatial_support TEXT NOT NULL CHECK (spatial_support IN ('point', 'area', 'grid')),
    ingested_at TEXT NOT NULL,
    UNIQUE (source_id, location_id, variable, observed_at, period_minutes)
);

CREATE TABLE IF NOT EXISTS forecasts (
    forecast_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    model_name TEXT NOT NULL,
    model_version TEXT,
    model_run TEXT NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    variable TEXT NOT NULL CHECK (variable IN ('precipitation', 'temperature', 'apparent_temperature', 'relative_humidity', 'dew_point', 'precipitation_probability')),
    issued_at TEXT NOT NULL,
    valid_at TEXT NOT NULL,
    lead_hours REAL NOT NULL CHECK (lead_hours >= 0),
    period_minutes INTEGER NOT NULL CHECK (period_minutes > 0),
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    ensemble_member TEXT NOT NULL DEFAULT 'deterministic',
    quantile REAL,
    ingested_at TEXT NOT NULL,
    UNIQUE (source_id, model_run, location_id, variable, valid_at, ensemble_member, quantile)
);

CREATE TABLE IF NOT EXISTS grid_cells (
    grid_id TEXT PRIMARY KEY REFERENCES locations(location_id),
    spacing_km REAL NOT NULL CHECK (spacing_km > 0),
    boundary_source TEXT NOT NULL,
    boundary_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grid_forecasts_latest (
    grid_id TEXT NOT NULL REFERENCES grid_cells(grid_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    model_name TEXT NOT NULL,
    model_run TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    valid_at TEXT NOT NULL,
    variable TEXT NOT NULL CHECK (variable IN ('precipitation', 'temperature', 'precipitation_probability')),
    period_minutes INTEGER NOT NULL CHECK (period_minutes > 0),
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (grid_id, variable, valid_at)
);

CREATE TABLE IF NOT EXISTS grid_estimates_latest (
    grid_id TEXT NOT NULL REFERENCES grid_cells(grid_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    product_name TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    variable TEXT NOT NULL CHECK (variable IN ('precipitation')),
    period_minutes INTEGER NOT NULL CHECK (period_minutes > 0),
    value REAL NOT NULL CHECK (value >= 0),
    unit TEXT NOT NULL,
    quality_flag TEXT NOT NULL CHECK (quality_flag IN ('provisional', 'validated', 'suspect')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (grid_id, source_id, product_name)
);

CREATE TABLE IF NOT EXISTS site_estimates_latest (
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    variable TEXT NOT NULL CHECK (variable IN ('precipitation', 'temperature')),
    estimate_at TEXT NOT NULL,
    period_minutes INTEGER NOT NULL CHECK (period_minutes > 0),
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    estimate_type TEXT NOT NULL CHECK (
        estimate_type IN ('sensor', 'blended', 'model_only', 'regional_fallback')
    ),
    spatial_basis TEXT NOT NULL CHECK (
        spatial_basis IN ('exact_point', 'area_anchor', 'park_regional')
    ),
    ground_value REAL,
    model_value REAL,
    radar_satellite_value REAL,
    source_count INTEGER NOT NULL CHECK (source_count >= 1),
    source_summary TEXT NOT NULL,
    confidence_score REAL NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
    confidence_level TEXT NOT NULL CHECK (confidence_level IN ('high', 'medium', 'low')),
    uncertainty_low REAL NOT NULL,
    uncertainty_high REAL NOT NULL,
    historical_error_percent REAL,
    validation_status TEXT NOT NULL CHECK (
        validation_status IN ('awaiting_validation', 'provisional', 'validated')
    ),
    method_version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (location_id, variable)
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    issue_code TEXT NOT NULL,
    description TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_note TEXT
);

CREATE TABLE IF NOT EXISTS verification_results (
    result_id TEXT PRIMARY KEY,
    computed_at TEXT NOT NULL,
    forecast_source_id TEXT NOT NULL REFERENCES sources(source_id),
    model_name TEXT NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    variable TEXT NOT NULL CHECK (variable IN ('precipitation', 'temperature')),
    lead_bucket TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    pair_count INTEGER NOT NULL CHECK (pair_count >= 0),
    sample_days INTEGER NOT NULL CHECK (sample_days >= 0),
    mae REAL,
    rmse REAL,
    mean_bias REAL,
    percent_bias REAL,
    wape REAL,
    event_threshold REAL,
    hits INTEGER,
    misses INTEGER,
    false_alarms INTEGER,
    correct_negatives INTEGER,
    pod REAL,
    far REAL,
    csi REAL,
    readiness_status TEXT NOT NULL CHECK (
        readiness_status IN ('no_pairs', 'accumulating', 'provisional', 'ready')
    ),
    methodology_note TEXT NOT NULL,
    UNIQUE (forecast_source_id, model_name, location_id, variable, lead_bucket, computed_at)
);

CREATE TABLE IF NOT EXISTS calibration_models_latest (
    forecast_source_id TEXT NOT NULL REFERENCES sources(source_id),
    model_name TEXT NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    variable TEXT NOT NULL CHECK (variable IN ('precipitation', 'temperature')),
    lead_bucket TEXT NOT NULL,
    trained_at TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    pair_count INTEGER NOT NULL CHECK (pair_count >= 0),
    sample_days INTEGER NOT NULL CHECK (sample_days >= 0),
    event_count INTEGER NOT NULL CHECK (event_count >= 0),
    method TEXT NOT NULL,
    parameter_value REAL,
    readiness_status TEXT NOT NULL CHECK (readiness_status IN ('no_pairs', 'accumulating', 'provisional', 'ready')),
    methodology_note TEXT NOT NULL,
    PRIMARY KEY (forecast_source_id, model_name, location_id, variable, lead_bucket)
);

CREATE INDEX IF NOT EXISTS idx_observations_lookup
    ON observations (location_id, variable, observed_at);

CREATE INDEX IF NOT EXISTS idx_forecasts_verification
    ON forecasts (location_id, variable, valid_at, lead_hours, issued_at);

CREATE INDEX IF NOT EXISTS idx_grid_forecasts_latest_time
    ON grid_forecasts_latest (variable, valid_at);

CREATE INDEX IF NOT EXISTS idx_grid_estimates_latest_time
    ON grid_estimates_latest (variable, observed_at);

CREATE INDEX IF NOT EXISTS idx_site_estimates_latest_time
    ON site_estimates_latest (variable, estimate_at);

CREATE INDEX IF NOT EXISTS idx_verification_results_lookup
    ON verification_results (location_id, variable, lead_bucket, computed_at);
