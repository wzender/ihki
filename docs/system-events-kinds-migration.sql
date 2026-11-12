-- system_events.kind was limited to the first four kinds, so every 'integration', 'push', 'push_open' and
-- 'push_prompt' event was refused (400, code 23514) and dropped. Run once in the Supabase SQL Editor.
-- When a new kind is added to a log_event(...) call, add it here, in mathly-supabase.sql and in
-- CREATE_SYSTEM_EVENTS (app/main.py); tests/test_system_event_kinds.py fails until all three agree.
alter table public.system_events drop constraint if exists system_events_kind_check;
alter table public.system_events add constraint system_events_kind_check
  check (kind in ('exercise','game','permission','content','integration','push','push_open','push_prompt'));
