create table public.operational_runs (
  run_id text primary key,
  cycle_started_at timestamptz not null,
  cycle_finished_at timestamptz not null,
  status text not null check (status in ('success', 'partial_failure')),
  failed_steps jsonb not null default '[]'::jsonb,
  counts jsonb not null default '{}'::jsonb,
  sync_counts jsonb not null default '{}'::jsonb,
  data_latest_at timestamptz,
  dashboard_generated_at timestamptz not null,
  created_at timestamptz not null default now()
);

create index operational_runs_finished_at_idx
  on public.operational_runs (cycle_finished_at desc);

alter table public.operational_runs enable row level security;

revoke all on public.operational_runs from anon, authenticated;
grant select, insert, update, delete on public.operational_runs to service_role;
