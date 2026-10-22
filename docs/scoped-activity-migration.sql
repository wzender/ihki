-- Accurate, subject-scoped monitoring aggregates. Safe to run more than once.
create or replace function public.activity_buckets_scoped(
  p_user_ids uuid[], p_since timestamptz, p_bucket_minutes integer default 15
)
returns table (bucket timestamptz, attempts bigint, active_students bigint)
language sql stable security invoker set search_path = '' as $$
  select date_bin(make_interval(mins => greatest(1, least(p_bucket_minutes, 60))), a.created_at,
                  timestamptz '2000-01-01 00:00:00+00'),
         count(*)::bigint, count(distinct a.user_id)::bigint
  from public.attempts a
  where a.created_at >= p_since and a.user_id = any(p_user_ids)
  group by 1 order by 1
$$;
revoke execute on function public.activity_buckets_scoped(uuid[], timestamptz, integer)
  from public, anon, authenticated;
grant execute on function public.activity_buckets_scoped(uuid[], timestamptz, integer) to service_role;

create or replace function public.activity_summary_scoped(p_user_ids uuid[], p_since timestamptz)
returns table (attempts bigint, active_students bigint, returning_students bigint, active_days bigint)
language sql stable security invoker set search_path = '' as $$
  with windowed as (
    select a.user_id, (a.created_at at time zone 'Asia/Jerusalem')::date as practice_day
    from public.attempts a
    where a.created_at >= p_since and a.user_id = any(p_user_ids)
  ), per_user as (
    select user_id, count(distinct practice_day) as days from windowed group by user_id
  )
  select (select count(*) from windowed)::bigint,
         (select count(*) from per_user)::bigint,
         (select count(*) from per_user where days >= 2)::bigint,
         (select count(distinct practice_day) from windowed)::bigint
$$;
revoke execute on function public.activity_summary_scoped(uuid[], timestamptz)
  from public, anon, authenticated;
grant execute on function public.activity_summary_scoped(uuid[], timestamptz) to service_role;
