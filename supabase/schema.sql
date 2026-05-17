-- ============================================================
-- コミュニケーション支援AI — Supabase スキーマ
-- Supabase SQL Editor でそのまま実行できます
-- ============================================================

-- ---- profiles (auth.users の拡張) ----
create table if not exists public.profiles (
  id          uuid references auth.users(id) on delete cascade primary key,
  plan        text not null default 'free'
                check (plan in ('free', 'pro', 'premium')),
  is_student          boolean not null default false,
  student_verified_at timestamptz,
  stripe_customer_id  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "profiles_select_own" on public.profiles
  for select using (auth.uid() = id);

-- 新規ユーザー登録時に自動で profiles レコードを作成
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer
set search_path = public
as $$
begin
  insert into public.profiles (id) values (new.id)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();


-- ---- usage_monthly (月次文字起こし使用量) ----
create table if not exists public.usage_monthly (
  id                    bigserial primary key,
  user_id               uuid references auth.users(id) on delete cascade not null,
  year_month            text not null,  -- 'YYYY-MM'
  transcription_seconds integer not null default 0,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique(user_id, year_month)
);

alter table public.usage_monthly enable row level security;

create policy "usage_monthly_select_own" on public.usage_monthly
  for select using (auth.uid() = user_id);

-- アトミックインクリメント用 RPC
create or replace function public.increment_transcription_usage(
  p_user_id   uuid,
  p_year_month text,
  p_seconds   integer
) returns void language plpgsql security definer as $$
begin
  insert into public.usage_monthly (user_id, year_month, transcription_seconds)
  values (p_user_id, p_year_month, p_seconds)
  on conflict (user_id, year_month) do update
    set transcription_seconds = usage_monthly.transcription_seconds + p_seconds,
        updated_at = now();
end;
$$;


-- ---- usage_daily_chat (日次 AI チャット回数) ----
create table if not exists public.usage_daily_chat (
  id          bigserial primary key,
  user_id     uuid references auth.users(id) on delete cascade not null,
  date        date not null,
  chat_count  integer not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique(user_id, date)
);

alter table public.usage_daily_chat enable row level security;

create policy "usage_daily_chat_select_own" on public.usage_daily_chat
  for select using (auth.uid() = user_id);

-- アトミックインクリメント用 RPC
create or replace function public.increment_chat_count(
  p_user_id uuid,
  p_date    date
) returns void language plpgsql security definer as $$
begin
  insert into public.usage_daily_chat (user_id, date, chat_count)
  values (p_user_id, p_date, 1)
  on conflict (user_id, date) do update
    set chat_count = usage_daily_chat.chat_count + 1,
        updated_at = now();
end;
$$;


-- ---- summaries (録音・要約の永続履歴) ----
create table if not exists public.summaries (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid references auth.users(id) on delete cascade not null,
  topic            text not null default '',
  scene            text not null default 'default',
  transcript       text not null default '',
  summary          text not null default '',
  duration_seconds integer not null default 0,
  created_at       timestamptz not null default now()
);

alter table public.summaries enable row level security;

create policy "summaries_select_own" on public.summaries
  for select using (auth.uid() = user_id);

create policy "summaries_insert_own" on public.summaries
  for insert with check (auth.uid() = user_id);

create policy "summaries_delete_own" on public.summaries
  for delete using (auth.uid() = user_id);


-- ---- chat_sessions (チャット履歴の永続化) ----
create table if not exists public.chat_sessions (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid references auth.users(id) on delete cascade not null,
  session_id text not null,
  messages   jsonb not null default '[]',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(user_id, session_id)
);

alter table public.chat_sessions enable row level security;

create policy "chat_sessions_select_own" on public.chat_sessions
  for select using (auth.uid() = user_id);

create policy "chat_sessions_insert_own" on public.chat_sessions
  for insert with check (auth.uid() = user_id);

create policy "chat_sessions_update_own" on public.chat_sessions
  for update using (auth.uid() = user_id);
