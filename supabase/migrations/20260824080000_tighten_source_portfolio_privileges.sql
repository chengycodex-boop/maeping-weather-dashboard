revoke all on public.source_routes from service_role;
revoke all on public.source_health_latest from service_role;
revoke all on public.hazard_features_latest from service_role;

grant select, insert, update, delete on public.source_routes to service_role;
grant select, insert, update, delete on public.source_health_latest to service_role;
grant select, insert, update, delete on public.hazard_features_latest to service_role;
