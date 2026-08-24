create table public.source_routes (
  route_id text primary key,
  source_id text not null references public.sources(source_id),
  domain text not null,
  geographic_scope text not null,
  priority_order integer not null check (priority_order >= 1),
  fallback_group text not null,
  independence_group text not null,
  expected_freshness_minutes integer not null check (expected_freshness_minutes > 0),
  request_timeout_seconds integer not null check (request_timeout_seconds between 10 and 600),
  max_retries integer not null check (max_retries between 0 and 5),
  credential_env text,
  connector text,
  enabled integer not null default 0 check (enabled in (0, 1)),
  status text not null check (status in ('active', 'candidate', 'credential_required', 'blocked')),
  notes text
);

create table public.source_health_latest (
  route_id text primary key references public.source_routes(route_id),
  source_id text not null references public.sources(source_id),
  cycle_id text not null,
  checked_at timestamptz not null,
  status text not null check (
    status in (
      'success', 'stale', 'failed', 'credentials_missing',
      'no_data', 'budget_exhausted', 'not_run'
    )
  ),
  duration_seconds double precision not null check (duration_seconds >= 0),
  records_received integer check (records_received >= 0),
  newest_source_time timestamptz,
  freshness_lag_minutes double precision check (
    freshness_lag_minutes is null or freshness_lag_minutes >= 0
  ),
  error_code text,
  message text not null,
  updated_at timestamptz not null
);

create table public.hazard_features_latest (
  source_id text not null references public.sources(source_id),
  feature_id text not null,
  hazard_type text not null check (
    hazard_type in ('flood', 'wildfire', 'drought', 'earthquake', 'landslide', 'air_quality')
  ),
  observed_at timestamptz,
  latitude double precision check (latitude is null or latitude between -90 and 90),
  longitude double precision check (longitude is null or longitude between -180 and 180),
  geometry_type text not null,
  geometry_json text not null,
  value double precision,
  unit text,
  severity text,
  title text,
  source_url text,
  properties_json text not null,
  quality_flag text not null check (quality_flag in ('provisional', 'validated', 'suspect')),
  updated_at timestamptz not null,
  primary key (source_id, feature_id)
);

create index source_routes_fallback_idx
  on public.source_routes (fallback_group, priority_order, enabled);

create index source_routes_source_idx
  on public.source_routes (source_id);

create index source_health_latest_status_idx
  on public.source_health_latest (status, checked_at desc);

create index source_health_latest_source_idx
  on public.source_health_latest (source_id);

create index hazard_features_latest_lookup_idx
  on public.hazard_features_latest (hazard_type, observed_at desc);

alter table public.source_routes enable row level security;
alter table public.source_health_latest enable row level security;
alter table public.hazard_features_latest enable row level security;

revoke all on public.source_routes from anon, authenticated;
revoke all on public.source_health_latest from anon, authenticated;
revoke all on public.hazard_features_latest from anon, authenticated;

revoke all on public.source_routes from public;
revoke all on public.source_health_latest from public;
revoke all on public.hazard_features_latest from public;

grant select, insert, update, delete on public.source_routes to service_role;
grant select, insert, update, delete on public.source_health_latest to service_role;
grant select, insert, update, delete on public.hazard_features_latest to service_role;
