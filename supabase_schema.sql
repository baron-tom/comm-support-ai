-- ================================================================
-- コミュニケーション支援AI - Supabase スキーマ定義
-- Supabase Dashboard > SQL Editor に貼り付けて実行してください
-- ================================================================


-- ----------------------------------------------------------------
-- 1. profiles  （ユーザーのプラン・Stripe情報）
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.profiles (
  id                  UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  plan                TEXT        NOT NULL DEFAULT 'free'
                                  CHECK (plan IN ('free', 'pro', 'premium')),
  stripe_customer_id  TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- ユーザーは自分のプロフィールだけ読める（書き込みはサービスロールのみ）
CREATE POLICY "profiles: 本人のみ読み取り可"
  ON public.profiles FOR SELECT
  USING (auth.uid() = id);

-- 新規ユーザー登録時に自動でプロフィール行を作成
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.profiles (id)
  VALUES (NEW.id)
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();


-- ----------------------------------------------------------------
-- 2. usage_monthly  （月次・文字起こし利用秒数）
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.usage_monthly (
  id                    BIGSERIAL   PRIMARY KEY,
  user_id               UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  year_month            TEXT        NOT NULL,   -- 例: "2026-05"
  transcription_seconds INT         NOT NULL DEFAULT 0,
  UNIQUE (user_id, year_month)
);

ALTER TABLE public.usage_monthly ENABLE ROW LEVEL SECURITY;

CREATE POLICY "usage_monthly: 本人のみ読み取り可"
  ON public.usage_monthly FOR SELECT
  USING (auth.uid() = user_id);

CREATE INDEX IF NOT EXISTS idx_usage_monthly_user_month
  ON public.usage_monthly (user_id, year_month);

-- RPC: 月次文字起こし秒数を加算（初回は INSERT、以降は加算）
CREATE OR REPLACE FUNCTION public.increment_transcription_usage(
  p_user_id    UUID,
  p_year_month TEXT,
  p_seconds    INT
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.usage_monthly (user_id, year_month, transcription_seconds)
    VALUES (p_user_id, p_year_month, p_seconds)
  ON CONFLICT (user_id, year_month)
    DO UPDATE SET transcription_seconds =
      public.usage_monthly.transcription_seconds + EXCLUDED.transcription_seconds;
END;
$$;


-- ----------------------------------------------------------------
-- 3. usage_daily_chat  （日次・チャット回数）
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.usage_daily_chat (
  id          BIGSERIAL   PRIMARY KEY,
  user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  date        DATE        NOT NULL,
  chat_count  INT         NOT NULL DEFAULT 0,
  UNIQUE (user_id, date)
);

ALTER TABLE public.usage_daily_chat ENABLE ROW LEVEL SECURITY;

CREATE POLICY "usage_daily_chat: 本人のみ読み取り可"
  ON public.usage_daily_chat FOR SELECT
  USING (auth.uid() = user_id);

CREATE INDEX IF NOT EXISTS idx_usage_daily_chat_user_date
  ON public.usage_daily_chat (user_id, date);

-- RPC: 日次チャット数を +1（初回は INSERT、以降は加算）
CREATE OR REPLACE FUNCTION public.increment_chat_count(
  p_user_id UUID,
  p_date    DATE
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.usage_daily_chat (user_id, date, chat_count)
    VALUES (p_user_id, p_date, 1)
  ON CONFLICT (user_id, date)
    DO UPDATE SET chat_count = public.usage_daily_chat.chat_count + 1;
END;
$$;


-- ----------------------------------------------------------------
-- 4. chat_sessions  （チャット履歴）
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.chat_sessions (
  id          BIGSERIAL   PRIMARY KEY,
  user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  session_id  TEXT        NOT NULL,
  messages    JSONB       NOT NULL DEFAULT '[]',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, session_id)
);

ALTER TABLE public.chat_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "chat_sessions: 本人のみ読み取り可"
  ON public.chat_sessions FOR SELECT
  USING (auth.uid() = user_id);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
  ON public.chat_sessions (user_id, session_id);


-- ----------------------------------------------------------------
-- 5. summaries  （保存された要約・履歴）
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.summaries (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  topic            TEXT        NOT NULL DEFAULT '',
  scene            TEXT        NOT NULL DEFAULT 'default',
  transcript       TEXT        NOT NULL DEFAULT '',
  summary          TEXT        NOT NULL DEFAULT '',
  duration_seconds INT         NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.summaries ENABLE ROW LEVEL SECURITY;

CREATE POLICY "summaries: 本人のみ読み取り可"
  ON public.summaries FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "summaries: 本人のみ作成可"
  ON public.summaries FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE INDEX IF NOT EXISTS idx_summaries_user_created
  ON public.summaries (user_id, created_at DESC);


-- ================================================================
-- 完了！
-- 5テーブル + 2RPC関数 + 新規ユーザートリガー
-- ================================================================
