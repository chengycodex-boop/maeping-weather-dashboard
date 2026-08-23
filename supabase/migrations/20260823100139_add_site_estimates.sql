create table public.site_estimates_latest (
  location_id text not null references public.locations(location_id),
  variable text not null check (variable in ('precipitation', 'temperature')),
  estimate_at timestamptz not null,
  period_minutes integer not null check (period_minutes > 0),
  value double precision not null,
  unit text not null,
  estimate_type text not null check (
    estimate_type in ('sensor', 'blended', 'model_only', 'regional_fallback')
  ),
  spatial_basis text not null check (
    spatial_basis in ('exact_point', 'area_anchor', 'park_regional')
  ),
  ground_value double precision,
  model_value double precision,
  radar_satellite_value double precision,
  source_count integer not null check (source_count >= 1),
  source_summary text not null,
  confidence_score double precision not null check (confidence_score between 0 and 100),
  confidence_level text not null check (confidence_level in ('high', 'medium', 'low')),
  uncertainty_low double precision not null,
  uncertainty_high double precision not null,
  historical_error_percent double precision,
  validation_status text not null check (
    validation_status in ('awaiting_validation', 'provisional', 'validated')
  ),
  method_version text not null,
  updated_at timestamptz not null,
  primary key (location_id, variable)
);

create index site_estimates_latest_time_idx
  on public.site_estimates_latest (variable, estimate_at);

alter table public.site_estimates_latest enable row level security;

revoke all on public.site_estimates_latest from anon, authenticated;
