create table public.site_rainfall_24h_latest (
  location_id text primary key references public.locations(location_id),
  window_start timestamptz not null,
  window_end timestamptz not null,
  period_minutes integer not null check (period_minutes = 1440),
  value double precision not null check (value >= 0),
  unit text not null check (unit = 'mm'),
  estimate_type text not null check (
    estimate_type in ('sensor', 'spatial_interpolation', 'regional_fallback')
  ),
  spatial_basis text not null check (
    spatial_basis in ('exact_point', 'area_anchor', 'park_regional')
  ),
  source_count integer not null check (source_count >= 1),
  source_summary text not null,
  coverage_hours double precision not null check (coverage_hours between 0 and 24),
  coverage_ratio double precision not null check (coverage_ratio between 0 and 1),
  nearest_station_km double precision,
  confidence_score double precision not null check (confidence_score between 0 and 100),
  confidence_level text not null check (confidence_level in ('high', 'medium', 'low')),
  uncertainty_low double precision not null check (uncertainty_low >= 0),
  uncertainty_high double precision not null check (uncertainty_high >= uncertainty_low),
  validation_status text not null check (
    validation_status in ('awaiting_validation', 'provisional', 'validated')
  ),
  method_version text not null,
  updated_at timestamptz not null
);

create index site_rainfall_24h_latest_time_idx
  on public.site_rainfall_24h_latest (window_end);

grant select, insert, update, delete on public.site_rainfall_24h_latest to service_role;
revoke all on public.site_rainfall_24h_latest from anon, authenticated;
alter table public.site_rainfall_24h_latest enable row level security;
