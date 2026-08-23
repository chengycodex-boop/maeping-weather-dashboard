create table public.sources (
  source_id text primary key,
  source_name text not null,
  provider text not null,
  source_class text not null,
  variables text not null,
  spatial_grain text not null,
  temporal_grain text not null,
  latency text not null,
  access_mode text not null,
  authority_tier text not null check (authority_tier in ('primary', 'secondary', 'tertiary')),
  operational_role text not null,
  status text not null,
  url text,
  limitations text,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table public.locations (
  location_id text primary key,
  code text not null unique,
  name_th text not null,
  latitude double precision,
  longitude double precision,
  elevation_m double precision,
  coordinate_role text not null check (coordinate_role in ('exact_station', 'area_anchor', 'grid_centroid', 'unknown')),
  confidence text not null check (confidence in ('high', 'medium', 'low')),
  verification_status text not null,
  valid_from timestamptz,
  valid_to timestamptz,
  check ((latitude is null and longitude is null) or (latitude is not null and longitude is not null)),
  check (latitude is null or latitude between -90 and 90),
  check (longitude is null or longitude between -180 and 180)
);

create table public.location_aliases (
  location_id text not null references public.locations(location_id),
  alias text not null,
  source_id text references public.sources(source_id),
  valid_from timestamptz,
  valid_to timestamptz,
  primary key (location_id, alias)
);

create table public.location_evidence (
  evidence_id bigint generated always as identity primary key,
  location_id text not null references public.locations(location_id),
  source_id text not null references public.sources(source_id),
  asserted_name text,
  asserted_latitude double precision,
  asserted_longitude double precision,
  captured_at timestamptz not null,
  document_page text,
  evidence_note text,
  accepted boolean not null default false
);

create table public.observations (
  observation_id text primary key,
  source_id text not null references public.sources(source_id),
  location_id text not null references public.locations(location_id),
  variable text not null check (variable in ('precipitation', 'temperature', 'apparent_temperature', 'relative_humidity', 'dew_point')),
  observed_at timestamptz not null,
  period_minutes integer not null check (period_minutes > 0),
  value double precision not null,
  unit text not null,
  quality_flag text not null check (quality_flag in ('raw', 'provisional', 'validated', 'suspect', 'missing')),
  spatial_support text not null check (spatial_support in ('point', 'area', 'grid')),
  ingested_at timestamptz not null,
  unique (source_id, location_id, variable, observed_at, period_minutes)
);

create table public.forecasts (
  forecast_id text primary key,
  source_id text not null references public.sources(source_id),
  model_name text not null,
  model_version text,
  model_run timestamptz not null,
  location_id text not null references public.locations(location_id),
  variable text not null check (variable in ('precipitation', 'temperature', 'apparent_temperature', 'relative_humidity', 'dew_point', 'precipitation_probability')),
  issued_at timestamptz not null,
  valid_at timestamptz not null,
  lead_hours double precision not null check (lead_hours >= 0),
  period_minutes integer not null check (period_minutes > 0),
  value double precision not null,
  unit text not null,
  ensemble_member text not null default 'deterministic',
  quantile double precision,
  ingested_at timestamptz not null,
  unique nulls not distinct (source_id, model_run, location_id, variable, valid_at, ensemble_member, quantile)
);

create table public.grid_cells (
  grid_id text primary key references public.locations(location_id),
  spacing_km double precision not null check (spacing_km > 0),
  boundary_source text not null,
  boundary_status text not null,
  created_at timestamptz not null
);

create table public.grid_forecasts_latest (
  grid_id text not null references public.grid_cells(grid_id),
  source_id text not null references public.sources(source_id),
  model_name text not null,
  model_run timestamptz not null,
  issued_at timestamptz not null,
  valid_at timestamptz not null,
  variable text not null check (variable in ('precipitation', 'temperature', 'precipitation_probability')),
  period_minutes integer not null check (period_minutes > 0),
  value double precision not null,
  unit text not null,
  updated_at timestamptz not null,
  primary key (grid_id, variable, valid_at)
);

create table public.grid_estimates_latest (
  grid_id text not null references public.grid_cells(grid_id),
  source_id text not null references public.sources(source_id),
  product_name text not null,
  observed_at timestamptz not null,
  variable text not null check (variable = 'precipitation'),
  period_minutes integer not null check (period_minutes > 0),
  value double precision not null check (value >= 0),
  unit text not null,
  quality_flag text not null check (quality_flag in ('provisional', 'validated', 'suspect')),
  updated_at timestamptz not null,
  primary key (grid_id, source_id, product_name)
);

create table public.data_quality_issues (
  issue_id bigint generated always as identity primary key,
  entity_type text not null,
  entity_id text not null,
  severity text not null check (severity in ('critical', 'high', 'medium', 'low')),
  issue_code text not null,
  description text not null,
  detected_at timestamptz not null,
  resolved_at timestamptz,
  resolution_note text
);

create table public.verification_results (
  result_id text primary key,
  computed_at timestamptz not null,
  forecast_source_id text not null references public.sources(source_id),
  model_name text not null,
  location_id text not null references public.locations(location_id),
  variable text not null check (variable in ('precipitation', 'temperature')),
  lead_bucket text not null,
  window_start timestamptz,
  window_end timestamptz,
  pair_count integer not null check (pair_count >= 0),
  sample_days integer not null check (sample_days >= 0),
  mae double precision,
  rmse double precision,
  mean_bias double precision,
  percent_bias double precision,
  wape double precision,
  event_threshold double precision,
  hits integer,
  misses integer,
  false_alarms integer,
  correct_negatives integer,
  pod double precision,
  far double precision,
  csi double precision,
  readiness_status text not null check (readiness_status in ('no_pairs', 'accumulating', 'provisional', 'ready')),
  methodology_note text not null,
  unique (forecast_source_id, model_name, location_id, variable, lead_bucket, computed_at)
);

create table public.calibration_models_latest (
  forecast_source_id text not null references public.sources(source_id),
  model_name text not null,
  location_id text not null references public.locations(location_id),
  variable text not null check (variable in ('precipitation', 'temperature')),
  lead_bucket text not null,
  trained_at timestamptz not null,
  window_start timestamptz,
  window_end timestamptz,
  pair_count integer not null check (pair_count >= 0),
  sample_days integer not null check (sample_days >= 0),
  event_count integer not null check (event_count >= 0),
  method text not null,
  parameter_value double precision,
  readiness_status text not null check (readiness_status in ('no_pairs', 'accumulating', 'provisional', 'ready')),
  methodology_note text not null,
  primary key (forecast_source_id, model_name, location_id, variable, lead_bucket)
);

create index observations_lookup_idx on public.observations (location_id, variable, observed_at);
create index observations_source_id_idx on public.observations (source_id);
create index forecasts_verification_idx on public.forecasts (location_id, variable, valid_at, lead_hours, issued_at);
create index forecasts_source_id_idx on public.forecasts (source_id);
create index location_aliases_source_id_idx on public.location_aliases (source_id);
create index location_evidence_location_id_idx on public.location_evidence (location_id);
create index location_evidence_source_id_idx on public.location_evidence (source_id);
create index grid_forecasts_latest_time_idx on public.grid_forecasts_latest (variable, valid_at);
create index grid_forecasts_latest_source_id_idx on public.grid_forecasts_latest (source_id);
create index grid_estimates_latest_time_idx on public.grid_estimates_latest (variable, observed_at);
create index grid_estimates_latest_source_id_idx on public.grid_estimates_latest (source_id);
create index verification_results_lookup_idx on public.verification_results (location_id, variable, lead_bucket, computed_at);
create index verification_results_source_id_idx on public.verification_results (forecast_source_id);

alter table public.sources enable row level security;
alter table public.locations enable row level security;
alter table public.location_aliases enable row level security;
alter table public.location_evidence enable row level security;
alter table public.observations enable row level security;
alter table public.forecasts enable row level security;
alter table public.grid_cells enable row level security;
alter table public.grid_forecasts_latest enable row level security;
alter table public.grid_estimates_latest enable row level security;
alter table public.data_quality_issues enable row level security;
alter table public.verification_results enable row level security;
alter table public.calibration_models_latest enable row level security;

revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
