-- Privacy-safe operational telemetry. Only the server-side service role may use it.
create table if not exists public.operational_error_events (
  id bigint generated always as identity primary key,
  occurred_at timestamptz not null default statement_timestamp(),
  category text not null check (category in (
    'live_connection_failure', 'timeout', 'authentication', 'database', 'upstream_service', 'internal'
  )),
  component text not null check (component in (
    'live_session', 'authentication', 'database', 'model', 'text_to_speech', 'mcp', 'application'
  )),
  outcome text not null check (outcome in (
    'poll_failed', 'timed_out', 'unavailable', 'rejected', 'exception'
  ))
);

create index if not exists operational_error_events_occurred_category_idx
  on public.operational_error_events (occurred_at, category);

alter table public.operational_error_events enable row level security;
revoke all on table public.operational_error_events from public, anon, authenticated;
grant select, insert, delete on table public.operational_error_events to service_role;
grant usage, select on sequence public.operational_error_events_id_seq to service_role;
do $$
begin
  if not exists (select 1 from pg_policies where schemaname = 'public'
                 and tablename = 'operational_error_events'
                 and policyname = 'service role manages operational errors') then
    create policy "service role manages operational errors"
      on public.operational_error_events for all to service_role
      using (true) with check (true);
  end if;
end $$;

create or replace function public.operational_error_summary(
  p_since timestamptz, p_until timestamptz, p_category text default null,
  p_timezone text default 'Asia/Jerusalem')
returns table(local_date date, category text, errors bigint, database_read_at timestamptz)
language plpgsql security invoker set search_path = '' as $$
begin
  if p_since >= p_until or (p_category is not null and p_category not in (
    'live_connection_failure', 'timeout', 'authentication', 'database', 'upstream_service', 'internal'
  )) then
    raise exception 'invalid operational error summary parameters';
  end if;
  perform statement_timestamp() at time zone p_timezone;
  return query
    select (event.occurred_at at time zone p_timezone)::date, event.category,
           count(*)::bigint, statement_timestamp()
      from public.operational_error_events event
     where event.occurred_at >= p_since and event.occurred_at < p_until
       and (p_category is null or event.category = p_category)
     group by (event.occurred_at at time zone p_timezone)::date, event.category
     order by (event.occurred_at at time zone p_timezone)::date, event.category;
end $$;

revoke execute on function public.operational_error_summary(timestamptz, timestamptz, text, text)
  from public, anon, authenticated;
grant execute on function public.operational_error_summary(timestamptz, timestamptz, text, text)
  to service_role;

-- Supabase Cron uses pg_cron. Re-running this migration replaces the named job.
create extension if not exists pg_cron with schema extensions;
do $$
declare existing_job bigint;
begin
  select jobid into existing_job from cron.job
    where jobname = 'delete-operational-errors-after-90-days';
  if existing_job is not null then
    perform cron.unschedule(existing_job);
  end if;
  perform cron.schedule(
    'delete-operational-errors-after-90-days',
    '17 2 * * *',
    $job$delete from public.operational_error_events
          where occurred_at < statement_timestamp() - interval '90 days'$job$
  );
end $$;
