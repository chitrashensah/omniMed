-- OmniMed: Run this entire file in Supabase SQL Editor
-- Safe to re-run multiple times
--
-- ⚠ ADMIN EMAIL: this file grants full data access to a single admin email via
-- RLS policies and SECURITY DEFINER functions. Replace every occurrence of
-- 'chitrashenshah@gmail.com' below with your own admin email before running,
-- and set ADMIN_EMAIL (backend) / VITE_ADMIN_EMAIL (frontend) to the same value.

-- ── User Quota (5/day PER gated model: Claude and GPT-4o separately) ──
create table if not exists user_quota (
  user_id uuid  not null references auth.users(id) on delete cascade,
  date    date  not null default current_date,
  count   int   not null default 1,
  primary key (user_id, date)
);
-- Migrate to per-model quota: add model column + rebuild primary key
alter table user_quota add column if not exists model text not null default 'gated';
alter table user_quota drop constraint if exists user_quota_pkey;
alter table user_quota add primary key (user_id, date, model);
-- Cleanup rows older than 7 days
delete from user_quota where date < current_date - 7;

-- Old single-arg function is replaced by a per-model version
drop function if exists increment_user_quota(uuid, date);

-- Atomic increment per (user, date, model) — returns new count
create or replace function increment_user_quota(p_user_id uuid, p_date date, p_model text)
returns int language plpgsql security definer as $$
declare v_count int;
begin
  insert into user_quota(user_id, date, model, count) values(p_user_id, p_date, p_model, 1)
  on conflict(user_id, date, model) do update set count = user_quota.count + 1;
  select count into v_count from user_quota
    where user_id = p_user_id and date = p_date and model = p_model;
  return v_count;
end;
$$;

-- ── Granted Users (admin grants premium access) ───────────
create table if not exists granted_users (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null unique references auth.users(id) on delete cascade,
  email      text,
  granted_at timestamp with time zone default now()
);
-- RLS: a user can read only their own grant row; admin can read all.
-- Writes happen via the backend service role (bypasses RLS).
alter table granted_users enable row level security;
drop policy if exists "granted_users: service only" on granted_users;
drop policy if exists "granted_users: own or admin" on granted_users;
create policy "granted_users: own or admin" on granted_users
  for select using (
    auth.uid() = user_id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );

-- ── User Prompts Table ────────────────────────────────────
create table if not exists user_prompts (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid references auth.users(id) on delete cascade,
  name       text not null,
  content    text not null,
  mode       text default 'normal',
  created_at timestamp with time zone default now()
);
alter table user_prompts enable row level security;
drop policy if exists "user_prompts: own only" on user_prompts;
create policy "user_prompts: own only" on user_prompts
  for all using (auth.uid() = user_id);

-- ── Step 1: Add missing columns to existing tables ────────
alter table sessions    add column if not exists user_id uuid references auth.users(id) on delete set null;
alter table extractions add column if not exists user_id uuid references auth.users(id) on delete set null;
alter table comparisons add column if not exists user_id uuid references auth.users(id) on delete set null;

alter table validations add column if not exists user_id      uuid references auth.users(id) on delete set null;
alter table validations add column if not exists msg_id       text;
alter table validations add column if not exists model        text;
alter table validations add column if not exists verdict      text; -- numeric 1-10 stored as text
-- Drop old check constraint if it exists (was 'correct/partial/incorrect', now 1-10)
alter table validations drop constraint if exists verdict_check;
alter table validations add column if not exists submitted_by text;

-- ── Step 2: Profiles table (tracks all registered users) ──
create table if not exists profiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  email      text,
  created_at timestamp with time zone default now(),
  last_seen  timestamp with time zone default now()
);

-- Backfill existing auth users into profiles
insert into profiles (id, email)
select id, email from auth.users
on conflict (id) do nothing;

-- Auto-create profile on new signup
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- ── Step 3: Drop ALL existing policies ────────────────────
drop policy if exists "allow all sessions"          on sessions;
drop policy if exists "allow all extractions"       on extractions;
drop policy if exists "allow all comparisons"       on comparisons;
drop policy if exists "allow all validations"       on validations;
drop policy if exists "sessions: own or admin"      on sessions;
drop policy if exists "extractions: own or admin"   on extractions;
drop policy if exists "comparisons: own or admin"   on comparisons;
drop policy if exists "validations: own or admin"   on validations;
drop policy if exists "profiles: admin only"        on profiles;
drop policy if exists "profiles: own or admin"      on profiles;

-- ── Step 4: Enable RLS ────────────────────────────────────
alter table sessions    enable row level security;
alter table extractions enable row level security;
alter table comparisons enable row level security;
alter table validations enable row level security;
alter table profiles    enable row level security;

-- ── Step 5: RLS Policies ──────────────────────────────────
create policy "sessions: own or admin" on sessions
  for all using (
    auth.uid() = user_id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );

create policy "extractions: own or admin" on extractions
  for all using (
    auth.uid() = user_id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );

create policy "comparisons: own or admin" on comparisons
  for all using (
    auth.uid() = user_id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );

create policy "validations: own or admin" on validations
  for all using (
    auth.uid() = user_id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );

-- Profiles: only admin can read all, users can read their own
create policy "profiles: own or admin" on profiles
  for all using (
    auth.uid() = id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );

-- ══════════════════════════════════════════════════════════
--  Step 6: Documents — per-session uploaded file text.
--  Stored once, re-injected (and prompt-cached) on every turn
--  so models keep file context without the client re-uploading.
--  NOTE: session_id is TEXT (the frontend uses string ids like
--  "sess_abc123", not the sessions.session_id uuid). No FK.
-- ══════════════════════════════════════════════════════════
create table if not exists documents (
  id         uuid primary key default gen_random_uuid(),
  session_id text,
  user_id    uuid references auth.users(id) on delete set null,
  filename   text,
  file_type  text,
  content    text,
  created_at timestamptz default now()
);
create index if not exists documents_session_idx on documents(session_id);

alter table documents enable row level security;
drop policy if exists "documents: own or admin" on documents;
create policy "documents: own or admin" on documents
  for all using (
    auth.uid() = user_id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );

-- ══════════════════════════════════════════════════════════
--  Step 7: Usage logs — token accounting per model call.
--  Written by the backend (service role). session_id is TEXT.
-- ══════════════════════════════════════════════════════════
create table if not exists usage_logs (
  id                 uuid primary key default gen_random_uuid(),
  session_id         text,
  user_id            uuid references auth.users(id) on delete set null,
  email              text,
  model              text,
  mode               text,
  input_tokens       integer default 0,
  output_tokens      integer default 0,
  cache_read_tokens  integer default 0,
  cache_write_tokens integer default 0,
  status             text,
  created_at         timestamptz default now()
);
create index if not exists usage_logs_created_idx on usage_logs(created_at);
create index if not exists usage_logs_user_idx    on usage_logs(user_id);

alter table usage_logs enable row level security;
drop policy if exists "usage: own or admin" on usage_logs;
create policy "usage: own or admin" on usage_logs
  for all using (
    auth.uid() = user_id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );

-- ══════════════════════════════════════════════════════════
--  Step 8: Atomic session-turn append (kills the read-modify-
--  write race in update_session_memory). Appends one turn,
--  keeps the last 5, spills older turns into compressed_history
--  — all under a single row lock. Only affects sessions keyed
--  by a real uuid.
-- ══════════════════════════════════════════════════════════
create or replace function public.append_session_turn(p_session_id uuid, p_turn jsonb)
returns void
language plpgsql
as $$
declare
  combined jsonb;
  keep     jsonb;
  spill    text := '';
  total    int;
begin
  select coalesce(recent_turns, '[]'::jsonb) || p_turn into combined
  from sessions where session_id = p_session_id for update;

  if combined is null then
    return;  -- no such session
  end if;

  total := jsonb_array_length(combined);

  if total > 5 then
    keep := (select coalesce(jsonb_agg(e order by ord), '[]'::jsonb)
             from jsonb_array_elements(combined) with ordinality as t(e, ord)
             where ord > total - 5);
    select string_agg('[' || coalesce(e->>'role','user') || ']: '
                      || left(coalesce(e->>'content',''), 200), E'\n' order by ord)
      into spill
      from jsonb_array_elements(combined) with ordinality as t(e, ord)
      where ord <= total - 5;
  else
    keep := combined;
  end if;

  update sessions
  set recent_turns = keep,
      compressed_history = case when coalesce(spill,'') <> ''
        then trim(both E'\n' from coalesce(compressed_history,'') || E'\n' || spill)
        else compressed_history end,
      updated_at = now()
  where session_id = p_session_id;
end;
$$;

-- ══════════════════════════════════════════════════════════
--  Step 9: Admin aggregation (server-side, no client row dumps).
--  SECURITY DEFINER + admin-email guard. Call from the admin UI
--  via supabase.rpc(...). Returns DB-side aggregates only.
-- ══════════════════════════════════════════════════════════
create or replace function public.admin_reliability_summary(p_from timestamptz default null, p_to timestamptz default null)
returns table(model text, avg_score numeric, n bigint)
language plpgsql security definer set search_path = public
as $$
begin
  if (auth.jwt() ->> 'email') is distinct from 'chitrashenshah@gmail.com' then
    raise exception 'not authorized';
  end if;
  return query
    select v.model,
           round(avg(nullif(v.verdict,'')::numeric), 2) as avg_score,
           count(*) as n
    from validations v
    where v.model is not null and v.verdict ~ '^[0-9.]+$'
      and (p_from is null or v.created_at >= p_from)
      and (p_to   is null or v.created_at <= p_to)
    group by v.model
    order by avg_score desc nulls last;
end;
$$;

-- ══════════════════════════════════════════════════════════
--  Step 10: Wet-lab score integrity. One score per
--  (session, message, model): de-duplicate any existing rows,
--  then enforce a unique natural key so re-scoring UPDATES the
--  same row instead of inserting duplicates that skew averages.
--  Legacy rows (null model/msg_id from the old boolean schema)
--  are untouched — equality with NULL never matches.
-- ══════════════════════════════════════════════════════════
delete from validations a using validations b
where a.ctid < b.ctid
  and a.session_id = b.session_id
  and a.msg_id     = b.msg_id
  and a.model      = b.model;

create unique index if not exists validations_natural_key
  on validations (session_id, msg_id, model);

create or replace function public.admin_usage_summary(p_from timestamptz default null, p_to timestamptz default null)
returns table(model text, calls bigint, input_tokens bigint, output_tokens bigint,
              cache_read_tokens bigint, cache_write_tokens bigint)
language plpgsql security definer set search_path = public
as $$
begin
  if (auth.jwt() ->> 'email') is distinct from 'chitrashenshah@gmail.com' then
    raise exception 'not authorized';
  end if;
  return query
    select u.model,
           count(*) as calls,
           coalesce(sum(u.input_tokens),0)::bigint,
           coalesce(sum(u.output_tokens),0)::bigint,
           coalesce(sum(u.cache_read_tokens),0)::bigint,
           coalesce(sum(u.cache_write_tokens),0)::bigint
    from usage_logs u
    where (p_from is null or u.created_at >= p_from)
      and (p_to   is null or u.created_at <= p_to)
    group by u.model
    order by input_tokens desc;
end;
$$;

-- ══════════════════════════════════════════════════════════
--  Step 11: Conversation persistence (cross-device chat history).
--  A "conversation" is a sessions row (+ title); each turn is a
--  messages row whose `content` jsonb is the full message object
--  the UI renders. RLS scopes everything to the owning user.
-- ══════════════════════════════════════════════════════════
alter table sessions add column if not exists title text;

create table if not exists messages (
  id         uuid primary key default gen_random_uuid(),
  session_id uuid references sessions(session_id) on delete cascade,
  user_id    uuid references auth.users(id) on delete set null,
  seq        bigint,            -- client message id; orders turns within a session
  role       text,             -- 'user' | 'assistant'
  content    jsonb,            -- full UI message object (text, responses, conclusion, …)
  created_at timestamptz default now()
);
-- One row per (session, seq) so re-saving a turn UPDATES instead of duplicating.
create unique index if not exists messages_session_seq on messages (session_id, seq);
create index if not exists messages_session_idx on messages (session_id);

alter table messages enable row level security;
drop policy if exists "messages: own or admin" on messages;
create policy "messages: own or admin" on messages
  for all using (
    auth.uid() = user_id
    or (auth.jwt() ->> 'email') = 'chitrashenshah@gmail.com'
  );
