-- Service-role-only aggregates. The caller supplies a deduplicated authorized user set.
create or replace function public.hourly_activity_scoped(
  p_user_ids uuid[], p_since timestamptz, p_until timestamptz,
  p_breakdown text default 'hour_of_day', p_timezone text default 'Asia/Jerusalem')
returns table(local_date date, local_hour integer, utc_offset text,
              utc_hour_start timestamptz, attempts bigint, active_students bigint)
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if p_breakdown not in ('hour_of_day', 'date_hour') or p_since >= p_until then
    raise exception 'invalid hourly activity parameters';
  end if;
  perform now() at time zone p_timezone;
  if p_breakdown = 'date_hour' then
    return query select (date_trunc('hour', a.created_at) at time zone p_timezone)::date,
      extract(hour from date_trunc('hour', a.created_at) at time zone p_timezone)::integer,
      to_char(date_trunc('hour', a.created_at) at time zone p_timezone, 'OF'),
      date_trunc('hour', a.created_at), count(*)::bigint, count(distinct a.user_id)::bigint
    from public.attempts a where a.user_id = any(p_user_ids)
      and a.created_at >= p_since and a.created_at < p_until
    group by date_trunc('hour', a.created_at) order by date_trunc('hour', a.created_at);
  else
    return query select null::date,
      extract(hour from a.created_at at time zone p_timezone)::integer,
      null::text, null::timestamptz, count(*)::bigint, count(distinct a.user_id)::bigint
    from public.attempts a where a.user_id = any(p_user_ids)
      and a.created_at >= p_since and a.created_at < p_until
    group by extract(hour from a.created_at at time zone p_timezone)
    order by extract(hour from a.created_at at time zone p_timezone);
  end if;
end $$;

create or replace function public.hourly_activity_summary_scoped(
  p_user_ids uuid[], p_since timestamptz, p_until timestamptz,
  p_breakdown text default 'hour_of_day', p_timezone text default 'Asia/Jerusalem')
returns table(attempts bigint, active_students bigint, missing_timestamps bigint,
              database_read_at timestamptz)
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if p_breakdown not in ('hour_of_day', 'date_hour') or p_since >= p_until then
    raise exception 'invalid hourly activity parameters'; end if;
  perform now() at time zone p_timezone;
  return query select
    count(*) filter (where a.created_at >= p_since and a.created_at < p_until)::bigint,
    count(distinct a.user_id) filter (where a.created_at >= p_since and a.created_at < p_until)::bigint,
    count(*) filter (where a.created_at is null)::bigint, statement_timestamp()
  from public.attempts a where a.user_id = any(p_user_ids);
end $$;

revoke execute on function public.hourly_activity_scoped(uuid[], timestamptz, timestamptz, text, text)
  from public, anon, authenticated;
revoke execute on function public.hourly_activity_summary_scoped(uuid[], timestamptz, timestamptz, text, text)
  from public, anon, authenticated;
grant execute on function public.hourly_activity_scoped(uuid[], timestamptz, timestamptz, text, text) to service_role;
grant execute on function public.hourly_activity_summary_scoped(uuid[], timestamptz, timestamptz, text, text) to service_role;
