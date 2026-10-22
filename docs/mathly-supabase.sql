-- The whole database of a new Supabase project for the app, in one go: paste it into the SQL Editor and run it.
-- Gathered from the README's sections (each is explained there); made for mathly, the math Space, whose classes
-- are math classes by default (the last lines).
create table public.attempts (
  id         bigint generated always as identity primary key,
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  cat        text not null,      -- the topic id, e.g. l1-verbs
  phrase     text not null,      -- the Hebrew prompt
  score      int  not null,      -- 0-100
  unrelated  boolean not null default false,   -- "different sentence?"
  issues     jsonb not null default '[]'::jsonb, -- compact [{word, expected, heard, score}], never audio
  model      text not null default '',
  version    text not null default '',
  created_at timestamptz not null default now()
);
create index attempts_user_time on public.attempts (user_id, created_at desc);
create index attempts_created_at_idx on public.attempts (created_at desc);

alter table public.attempts enable row level security;
grant select, insert on public.attempts to authenticated;
create policy "students add their own attempts"  on public.attempts
  for insert to authenticated with check (user_id = auth.uid());
create policy "students read their own attempts" on public.attempts
  for select to authenticated using (user_id = auth.uid());

-- Anonymous aggregate buckets for the teacher's Monitoring graph. Only the backend service role may call it.
create or replace function public.activity_buckets(p_hours integer default 24, p_bucket_minutes integer default 15)
returns table (bucket timestamptz, attempts bigint, active_students bigint)
language sql stable security invoker set search_path = '' as $$
  select date_bin(make_interval(mins => greatest(1, least(p_bucket_minutes, 60))), a.created_at,
                  timestamptz '2000-01-01 00:00:00+00'),
         count(*)::bigint, count(distinct a.user_id)::bigint
  from public.attempts a
  where a.created_at >= now() - make_interval(hours => greatest(1, least(p_hours, 168)))
  group by 1 order by 1
$$;
revoke execute on function public.activity_buckets(integer, integer) from public, anon, authenticated;
grant execute on function public.activity_buckets(integer, integer) to service_role;

-- Totals for the full selected window. This is separate from the buckets so a student who practised in several
-- buckets is counted only once.
drop function if exists public.activity_summary(integer);
create or replace function public.activity_summary(p_hours integer default 24)
returns table (attempts bigint, active_students bigint, returning_students bigint, active_days bigint)
language sql stable security invoker set search_path = '' as $$
  with windowed as (
    select a.user_id, (a.created_at at time zone 'Asia/Jerusalem')::date as practice_day
    from public.attempts a
    where a.created_at >= now() - make_interval(hours => greatest(1, least(p_hours, 168)))
  ), per_user as (
    select user_id, count(distinct practice_day) as days from windowed group by user_id
  )
  select (select count(*) from windowed)::bigint,
         (select count(*) from per_user)::bigint,
         (select count(*) from per_user where days >= 2)::bigint,
         (select count(distinct practice_day) from windowed)::bigint
$$;
revoke execute on function public.activity_summary(integer) from public, anon, authenticated;
grant execute on function public.activity_summary(integer) to service_role;

-- Subject-scoped monitoring aggregates. These keep busy classes accurate without downloading individual attempts.
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
revoke execute on function public.activity_buckets_scoped(uuid[], timestamptz, integer) from public, anon, authenticated;
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
revoke execute on function public.activity_summary_scoped(uuid[], timestamptz) from public, anon, authenticated;
grant execute on function public.activity_summary_scoped(uuid[], timestamptz) to service_role;

-- Recent system activity shown only in the management panel. The backend writes with the service-role key;
-- no browser role gets direct table access.
create table public.system_events (
  id          bigint generated always as identity primary key,
  kind        text not null check (kind in ('exercise','game','permission','content')),
  actor_id    uuid references auth.users(id) on delete set null,
  actor_email text not null default '',
  label       text not null default '',
  details     jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index system_events_created_at_idx on public.system_events (created_at desc);
alter table public.system_events enable row level security;

create table public.app_secrets (
  name        text primary key check (name in ('SAWTAK_API_KEY','ANTHROPIC_API_KEY','TUTOR_MODEL')),
  ciphertext  text not null,
  last4       text not null default '',
  updated_by  text not null default '',
  updated_at  timestamptz not null default now()
);
alter table public.app_secrets enable row level security;
revoke all on public.app_secrets from anon, authenticated;

alter table public.app_secrets drop constraint if exists app_secrets_name_check;
alter table public.app_secrets add constraint app_secrets_name_check
  check (name in ('SAWTAK_API_KEY','ANTHROPIC_API_KEY','TUTOR_MODEL'));

alter table public.attempts
  add column if not exists issues jsonb not null default '[]'::jsonb,
  add column if not exists model text not null default '',
  add column if not exists version text not null default '';

create table public.user_access (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  roles text[] not null default array['student']::text[],
  entitlements text[] not null default '{}'::text[],
  subject text not null default '',                 -- developer-set subject for admin-created classes
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id),
  constraint valid_roles check (roles <@ array['student','teacher','flagger','admin','developer']::text[]),
  constraint valid_entitlements check (entitlements <@ array['practice','games','tutor','path','library']::text[])
);
create unique index user_access_email_idx on public.user_access (lower(email));
alter table public.user_access enable row level security;
create table public.access_audit (
  id bigint generated always as identity primary key,
  target_user_id uuid not null,
  changed_by uuid not null,
  old_access jsonb not null,
  new_access jsonb not null,
  created_at timestamptz not null default now()
);
alter table public.access_audit enable row level security;

alter table public.user_access add column if not exists subject text not null default '';
alter table public.user_access drop constraint if exists valid_entitlements;
update public.user_access
set entitlements = array_append(entitlements, 'practice')
where 'path' = any(entitlements) and not ('practice' = any(entitlements));
alter table public.user_access add constraint valid_entitlements check (
  entitlements <@ array['practice','games','tutor','path','library']::text[]
);

create table public.tutors (
  email      text primary key,
  granted_by text not null default '',   -- the teacher who gave the rank
  created_at timestamptz not null default now()
);
alter table public.tutors enable row level security;   -- no policy: only the secret key reads and writes it

create table public.flags (
  id         bigint generated always as identity primary key,
  email      text not null,      -- the tutor
  cat        text not null,      -- the topic id
  phrase     text not null,
  he         text not null,      -- the Hebrew prompt
  ar         text not null,      -- the accepted answer the scorer matched
  ipa        text not null,      -- its phonemes: what the tutor says was said
  heard_ipa  text not null,      -- the model's free decode
  score      int  not null,
  unrelated  boolean not null default false,
  note       text not null default '',
  result     jsonb not null,     -- the whole score response
  model      text not null,
  version    text not null default '',
  audio      text,               -- object name in the private flags Storage bucket
  status     text not null default 'pending', -- pending, confirmed, rejected, or fixed
  category   text,
  reviewed_by text,
  reviewed_at timestamptz,
  review_note text not null default '',
  false_issues jsonb not null default '[]'::jsonb, -- [{word, expected, heard}]
  created_at timestamptz not null default now()
);
alter table public.flags enable row level security;   -- no policy: only the secret key reads and writes it

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'flags',
  'flags',
  false,
  2000000,
  array['audio/webm','audio/ogg','audio/mp4','audio/mpeg','audio/wav','audio/x-wav']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

alter table public.flags
  add column if not exists status text not null default 'pending',
  add column if not exists category text,
  add column if not exists reviewed_by text,
  add column if not exists reviewed_at timestamptz,
  add column if not exists review_note text not null default '',
  add column if not exists false_issues jsonb not null default '[]'::jsonb;

create table public.lab_samples (
  id bigint generated always as identity primary key, email text not null,
  item text not null, ar text not null, ipa text not null, speaker text not null default '',
  label text not null,             -- 'correct' or 'mistake'
  mistake jsonb,                   -- {"index", "expected"}: the sound said wrong on purpose
  device text not null default '', score int not null, heard_ipa text not null,
  flagged jsonb not null default '[]'::jsonb,   -- the sounds the scorer marked down, "expected>heard"
  result jsonb not null, model text not null, version text not null default '',
  audio text,                      -- the recording's name in the storage bucket lab
  created_at timestamptz not null default now());
alter table public.lab_samples enable row level security;
insert into storage.buckets (id, name) values ('lab', 'lab') on conflict (id) do nothing;

create table public.calibrations (
  id          bigint generated always as identity primary key,
  expected    text not null,
  heard       text not null,
  floor       numeric not null default 70,
  scope       text not null default 'global', -- global or phrase
  phrase      text not null default '',
  evidence    jsonb not null default '{}',    -- report/reporter counts and supporting phrase keys
  model       text not null default '',
  status      text not null default 'draft',  -- draft, applied, or rejected
  proposed_by text not null,
  reviewed_by text,
  reviewed_at timestamptz,
  created_at  timestamptz not null default now()
);
alter table public.calibrations enable row level security;

create table public.scoring_rules (
  id         text primary key,                 -- a rule of rules.TOGGLES, e.g. dialect_Q, margins, rule_pharyngeal_h
  active     boolean not null default true,
  updated_by text not null default '',
  updated_at timestamptz not null default now()
);
alter table public.scoring_rules enable row level security;

create table public.writings (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  topic      text not null,      -- the topic id, e.g. l1-hw
  lines      jsonb not null,     -- the sentences as written, "" for one not written yet
  updated_at timestamptz not null default now(),
  primary key (user_id, topic)
);

alter table public.writings enable row level security;
grant select, insert, update on public.writings to authenticated;
create policy "students read their own writings"   on public.writings
  for select to authenticated using (user_id = auth.uid());
create policy "students add their own writings"    on public.writings
  for insert to authenticated with check (user_id = auth.uid());
create policy "students change their own writings" on public.writings
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

create table public.videos (
  id         bigint generated always as identity primary key,
  lesson     text not null,                        -- the lesson id, e.g. 2 or 2.1
  title      text not null,                        -- the topic's name on the path
  url        text not null,                        -- https://vimeo.com/<number>[/<hash>] or https://www.youtube.com/watch?v=<id>
  pos        int,                                  -- how many of the lesson's own topics stand before it; null = after them all
  ord        int  not null default 0,              -- its turn among the videos of one place
  created_at timestamptz not null default now()
);
alter table public.videos enable row level security;

alter table public.videos add column if not exists pos int, add column if not exists ord int not null default 0;

create table public.dailies (
  id         bigint generated always as identity primary key,
  level      text not null,                        -- a level id, e.g. 1
  title      text not null,
  url        text not null,                        -- https://vimeo.com/<number>[/<hash>] or a YouTube link
  created_at timestamptz not null default now()
);
alter table public.dailies enable row level security;

create table public.library (
  id         bigint generated always as identity primary key,
  level      text not null,                        -- a level id, e.g. 1
  title      text not null,
  url        text not null,
  ord        int,                                  -- its turn in the list; none until the teacher moves a video
  created_at timestamptz not null default now()
);
alter table public.library enable row level security;

alter table public.library add column if not exists ord int;
notify pgrst, 'reload schema';

create table public.texts (
  id         bigint generated always as identity primary key,
  lesson     text not null,                        -- the lesson id, e.g. 2 or 2.1
  title      text not null,                        -- the topic's name on the path
  voices     text[] not null default '{}',         -- a dialogue's two voices by name (odd lines, even lines); empty = a story
  lines      jsonb not null,                       -- [[Hebrew, Arabic in Hebrew letters], ...]
  pos        int,                                  -- as in videos
  ord        int  not null default 0,
  created_at timestamptz not null default now()
);
alter table public.texts enable row level security;

create table public.classes (
  id         bigint generated always as identity primary key,
  name       text not null unique,                 -- e.g. אוקטובר 2026
  emails     text[] not null default '{}',         -- the students' Google emails, lower-case
  nicknames  jsonb  not null default '{}',         -- {email: nickname}, for the students who have one
  lessons    text[] not null default '{}',         -- the lesson ids the class studies; empty = every lesson
  subject    text   not null default 'ערבית',      -- existing classes; new ones copy the creating admin
  teachers   text[] not null default '{}',         -- class-scoped teachers; each address is also in emails
  homework   text   not null default '',           -- the topic id the class practises now, e.g. l2-talk; '' = none
  due        date,                                 -- the day the homework is due, if it has one
  homeworks  jsonb  not null default '[]',         -- every homework of the class: [{"topic": ..., "due": ...}]
  dailies    jsonb  not null default '[]',         -- the days of its daily videos: [{"video": id, "date": ...}]
  created_at timestamptz not null default now()
);
alter table public.classes enable row level security;

alter table public.classes add column if not exists nicknames jsonb not null default '{}',
                           add column if not exists lessons text[] not null default '{}',
                           add column if not exists homework text not null default '',
                           add column if not exists due date,
                           add column if not exists homeworks jsonb not null default '[]',
                           add column if not exists teachers text[] not null default '{}',
                           add column if not exists dailies jsonb not null default '[]',
                           add column if not exists subject text not null default 'ערבית';
update public.classes set subject = 'ערבית' where subject = '';

-- the tutor's conversations (app/tutor.py)
create table public.tutor_chats (id uuid primary key, user_id uuid, email text not null default '', scenario text not null, gender text not null default '', messages jsonb not null default '[]'::jsonb, turns int not null default 0, finished boolean not null default false, model text not null default '', input_tokens int not null default 0, output_tokens int not null default 0, created_at timestamptz not null default now(), updated_at timestamptz not null default now()); alter table public.tutor_chats enable row level security;

-- mathly: a class is a math class unless it says otherwise
alter table public.classes alter column subject set default 'מתמטיקה';
notify pgrst, 'reload schema';
