"""
コミュニケーション支援AI - サーバー
起動: uvicorn server:app --reload

LLM切り替え:
  環境変数 LLM_BACKEND=gemini (デフォルト) or ollama

必須環境変数:
  GEMINI_API_KEY
  SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_JWT_SECRET, SUPABASE_SERVICE_ROLE_KEY
  GROQ_API_KEY (PRO/PREMIUM 文字起こし用)

省略時は認証スキップ・Groq 無効でローカル開発可能。
"""

from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jose import jwt, JWTError
import requests
import stripe
import os
from datetime import datetime, date, timedelta

app = FastAPI()

# ---- LLM 設定 ----
LLM_BACKEND = os.environ.get("LLM_BACKEND", "gemini")

GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_FREE = "gemini-2.0-flash-lite"
GEMINI_MODEL_PRO  = "gemini-2.0-flash"
GEMINI_BASE_URL   = "https://generativelanguage.googleapis.com/v1beta/models"

OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b"

GROQ_API_KEY     = os.environ.get("GROQ_API_KEY", "")
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# ---- Supabase 設定 ----
SUPABASE_URL              = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY         = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_JWT_SECRET       = os.environ.get("SUPABASE_JWT_SECRET", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# ---- Stripe 設定 ----
STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
APP_URL               = os.environ.get("APP_URL", "http://localhost:8000")

STRIPE_PRICES = {
    "pro_monthly":     os.environ.get("STRIPE_PRICE_PRO_MONTHLY", ""),
    "pro_yearly":      os.environ.get("STRIPE_PRICE_PRO_YEARLY", ""),
    "premium_monthly": os.environ.get("STRIPE_PRICE_PREMIUM_MONTHLY", ""),
    "premium_yearly":  os.environ.get("STRIPE_PRICE_PREMIUM_YEARLY", ""),
}

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ---- プラン制限 ----
PLAN_LIMITS = {
    "free":    {"transcription_seconds": 10_800,  "chat_daily": 10,   "history_days": 7},
    "pro":     {"transcription_seconds": 72_000,  "chat_daily": None, "history_days": None},
    "premium": {"transcription_seconds": 180_000, "chat_daily": None, "history_days": None},
}


# ================================================================
# 認証
# ================================================================

def get_current_user(authorization: str = Header(default=None)):
    """JWT 認証。SUPABASE_JWT_SECRET 未設定時は dev mode でスキップ。"""
    if not SUPABASE_JWT_SECRET:
        return {"sub": "dev"}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization[7:]
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ================================================================
# Supabase REST ヘルパー
# ================================================================

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def get_user_plan(user_id: str) -> str:
    if user_id == "dev" or not _sb_available():
        return "free"
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_sb_headers(),
            params={"id": f"eq.{user_id}", "select": "plan"},
            timeout=5,
        )
        data = resp.json()
        if data:
            return data[0].get("plan", "free")
    except Exception:
        pass
    return "free"


def get_monthly_usage(user_id: str, year_month: str) -> int:
    if user_id == "dev" or not _sb_available():
        return 0
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/usage_monthly",
            headers=_sb_headers(),
            params={
                "user_id": f"eq.{user_id}",
                "year_month": f"eq.{year_month}",
                "select": "transcription_seconds",
            },
            timeout=5,
        )
        data = resp.json()
        if data:
            return data[0].get("transcription_seconds", 0)
    except Exception:
        pass
    return 0


def increment_transcription_usage(user_id: str, year_month: str, seconds: int):
    if user_id == "dev" or not _sb_available():
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/increment_transcription_usage",
            headers=_sb_headers(),
            json={"p_user_id": user_id, "p_year_month": year_month, "p_seconds": seconds},
            timeout=5,
        )
    except Exception:
        pass


def get_daily_chat_count(user_id: str, today: str) -> int:
    if user_id == "dev" or not _sb_available():
        return 0
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/usage_daily_chat",
            headers=_sb_headers(),
            params={"user_id": f"eq.{user_id}", "date": f"eq.{today}", "select": "chat_count"},
            timeout=5,
        )
        data = resp.json()
        if data:
            return data[0].get("chat_count", 0)
    except Exception:
        pass
    return 0


def increment_chat_count(user_id: str, today: str):
    if user_id == "dev" or not _sb_available():
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/increment_chat_count",
            headers=_sb_headers(),
            json={"p_user_id": user_id, "p_date": today},
            timeout=5,
        )
    except Exception:
        pass


# ================================================================
# LLM 呼び出し
# ================================================================

def call_llm(system_prompt: str, messages: list[dict], plan: str = "free") -> str:
    if LLM_BACKEND == "gemini":
        model = GEMINI_MODEL_PRO if plan in ("pro", "premium") else GEMINI_MODEL_FREE
        url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"
        contents = [
            {
                "role": "user" if m["role"] == "user" else "model",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
        ]
        resp = requests.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": contents,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    else:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "system", "content": system_prompt}] + messages,
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


def call_groq_whisper(audio_bytes: bytes, filename: str, language: str | None = "ja", prompt: str = "", mime_type: str = "audio/webm") -> str:
    data: dict = {"model": "whisper-large-v3", "response_format": "text"}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    resp = requests.post(
        GROQ_WHISPER_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (filename, audio_bytes, mime_type)},
        data=data,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text.strip()


# ================================================================
# プロンプト定義
# ================================================================

CORRECT_PROMPT = """あなたは音声認識の誤認識補正AIです。
以下は音声認識の生テキストです。文脈・トピックから誤認識を推測して修正し、補正後のテキストのみを出力してください。

修正の基準：
- 意味が通らない単語 → 文脈から正しい単語に修正
- 同音異義語の誤り → トピックに合う語を選ぶ
- 固有名詞・専門用語 → トピックをもとに推測して補完
- 文章の構造・流れは変えない（要約・省略しない）
- 確信が持てない箇所は〔？〕とマーク
- 補正後のテキストのみ出力（説明・注釈は不要）
- 必ず日本語で出力"""

BULLET_TO_TEXT_PROMPT = """以下の箇条書きを、自然な日本語の文章に変換してください。

ルール：
- 箇条書きの内容をすべて含める
- 読みやすい段落にまとめる
- 余計な説明を加えない
- 必ず日本語で出力"""

CLASS_SUMMARIZE_PROMPT = """以下は授業中の発言・板書・説明の文字起こしです。
授業ノートとして詳しく整理してください。

出力形式：
【トピック】
（この部分で話されていた内容のタイトル）

【要点】
・〜
・〜
・〜

【詳細・説明】
・〜
・〜
・〜

【重要語句】
・用語：説明
・用語：説明

【例・具体例】（あれば）
・〜

【要確認・宿題】（あれば）
・〜

ルール：
- 全体15〜20行程度
- 必ず日本語で出力
- 不明確な部分は（？）とマーク"""

BASE_SYSTEM_PROMPT = """あなたは授業・会議の内容を深く理解するための学習・業務アシスタントです。
ユーザーが録音・要約した内容をもとに、理解を深めたり整理したりする手助けをしてください。

できること：
- 録音・要約の内容について質問に答える
- 重要なポイントをわかりやすく整理する
- 難しい概念や専門用語を噛み砕いて説明する
- 復習・確認用の問題を作る
- 次のアクションや TODO を提案する
- 内容を別の角度から深掘りする

共通ルール：
- 必ず日本語で答える
- 簡潔かつ具体的に答える
- 録音・要約の内容に関係ない質問には「録音内容についてお答えします」と伝える
- 追加で聞く場合は1つだけ"""

SCENE_PROMPTS = {
    "default": "",
    "work":    "場面は会議・ビジネスです。議事録・報告・意思決定の観点から整理してください。",
    "class":   "場面は授業・講義です。試験や復習に役立つ形で整理してください。",
}

SUMMARIZE_LEVELS = {
    1: """以下の文字起こしをメモとして簡潔にまとめてください。

出力形式：
【要点】
・〜（3〜5行）

ルール：
- 必ず日本語
- 不明確な部分は（要確認）とマーク""",

    2: """以下は会議・会話の文字起こしです。
メモとして使えるよう要点を整理してください。

出力形式：
【要点】
・〜
・〜
・〜

【詳細】
・〜
・〜

【要確認】（あれば）
・〜

ルール：
- 箇条書き合計8〜12行程度
- 「誰が・何を・どうする」を明確に
- 不明確な部分は（要確認）とマーク
- 必ず日本語""",

    3: """以下は会議・会話の文字起こしです。
詳細なメモとして整理してください。

出力形式：
【主なトピック】
〜（1〜2行で概要）

【要点】
・〜
・〜
・〜

【詳細・補足】
・〜
・〜
・〜

【決定事項・アクション】（あれば）
・〜

【要確認・不明点】（あれば）
・〜

ルール：
- 全体15〜20行程度
- 重要な発言は引用してもよい
- 必ず日本語""",
}


# ================================================================
# セッション管理
# ================================================================

_mem_sessions: dict[str, list] = {}


def _load_session(user_id: str, session_id: str) -> list:
    if user_id == "dev" or not _sb_available():
        return list(_mem_sessions.get(session_id, []))
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/chat_sessions",
            headers=_sb_headers(),
            params={
                "user_id": f"eq.{user_id}",
                "session_id": f"eq.{session_id}",
                "select": "messages",
            },
            timeout=5,
        )
        data = resp.json()
        if data:
            return list(data[0].get("messages", []))
    except Exception:
        pass
    return list(_mem_sessions.get(session_id, []))


def _save_session(user_id: str, session_id: str, messages: list):
    _mem_sessions[session_id] = messages
    if user_id == "dev" or not _sb_available():
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/chat_sessions",
            headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates"},
            json={
                "user_id": user_id,
                "session_id": session_id,
                "messages": messages,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
            timeout=5,
        )
    except Exception:
        pass


# ================================================================
# リクエストモデル
# ================================================================

class ChatRequest(BaseModel):
    session_id: str
    message: str
    scene: str = "default"


class SummarizeRequest(BaseModel):
    text: str
    scene: str = "default"
    level: int = 2
    mode: str = "summary"
    topic: str = ""


class ResetRequest(BaseModel):
    session_id: str


class AskRequest(BaseModel):
    transcript: str
    question: str
    scene: str = "default"


class ExtractTodosRequest(BaseModel):
    summaries: list[str]


class SaveSummaryRequest(BaseModel):
    topic: str = ""
    scene: str = "default"
    transcript: str = ""
    summary: str = ""
    duration_seconds: int = 0


# ================================================================
# エンドポイント
# ================================================================

@app.get("/config")
def get_config():
    """フロントエンド用の公開設定（認証不要）"""
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_ANON_KEY,
        "auth_enabled": bool(SUPABASE_JWT_SECRET),
    }


@app.get("/me")
def get_me(user=Depends(get_current_user)):
    """ログイン中ユーザーのプランと使用量"""
    user_id = user.get("sub", "dev")
    plan = get_user_plan(user_id)
    year_month = datetime.utcnow().strftime("%Y-%m")
    monthly_seconds = get_monthly_usage(user_id, year_month)
    today = date.today().isoformat()
    daily_chat = get_daily_chat_count(user_id, today)
    lim = PLAN_LIMITS[plan]
    return {
        "plan": plan,
        "usage": {
            "transcription_seconds": monthly_seconds,
            "transcription_limit": lim["transcription_seconds"],
            "chat_today": daily_chat,
            "chat_daily_limit": lim["chat_daily"],
        },
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    duration_seconds: int = Form(default=0),
    language: str = Form(default="ja"),
    prompt: str = Form(default=""),
    user=Depends(get_current_user),
):
    """Groq Whisper による高精度文字起こし（PRO/PREMIUM のみ）"""
    user_id = user.get("sub", "dev")
    plan = get_user_plan(user_id)

    if plan == "free":
        raise HTTPException(
            status_code=403,
            detail="Groq Whisper の利用には PRO 以上のプランが必要です",
        )

    year_month = datetime.utcnow().strftime("%Y-%m")
    lim = PLAN_LIMITS[plan]["transcription_seconds"]
    current = get_monthly_usage(user_id, year_month)
    if current + duration_seconds > lim:
        remaining = max(0, lim - current)
        raise HTTPException(
            status_code=429,
            detail=f"月間利用上限に達しました。残り {remaining // 60} 分です。",
        )

    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="Groq API キーが設定されていません")

    audio_bytes = await file.read()
    mime_type = file.content_type or "audio/webm"
    text = call_groq_whisper(audio_bytes, file.filename or "audio.webm", language or None, prompt, mime_type)
    increment_transcription_usage(user_id, year_month, duration_seconds)
    return {"text": text}


@app.post("/summarize")
def summarize(req: SummarizeRequest, user=Depends(get_current_user)):
    user_id = user.get("sub", "dev")
    plan = get_user_plan(user_id)

    if req.mode == "correct":
        prompt = CORRECT_PROMPT
        if req.topic:
            prompt += f"\n\n【トピック】{req.topic}"
    elif req.mode == "bullet_to_text":
        prompt = BULLET_TO_TEXT_PROMPT
    elif req.scene == "class":
        prompt = CLASS_SUMMARIZE_PROMPT
    else:
        level = max(1, min(3, req.level))
        prompt = SUMMARIZE_LEVELS[level]
        scene_note = SCENE_PROMPTS.get(req.scene, "")
        if scene_note:
            prompt += f"\n\n{scene_note}"

    if req.topic and req.mode not in ("correct", "bullet_to_text"):
        prompt += (
            f"\n\n【重要】この内容は「{req.topic}」に関するものです。"
            "文字起こしに誤認識があっても、このトピックをもとに正しく解釈・補完してください。"
        )

    reply = call_llm(prompt, [{"role": "user", "content": req.text}], plan=plan)
    return {"summary": reply}


@app.post("/chat")
def chat(req: ChatRequest, user=Depends(get_current_user)):
    user_id = user.get("sub", "dev")
    plan = get_user_plan(user_id)

    if plan == "free":
        today = date.today().isoformat()
        count = get_daily_chat_count(user_id, today)
        daily_lim = PLAN_LIMITS["free"]["chat_daily"]
        if count >= daily_lim:
            raise HTTPException(
                status_code=429,
                detail=f"本日の AI チャット上限（{daily_lim}回）に達しました。PRO プランにアップグレードすると無制限になります。",
            )

    # session_id ごとに独立したコンテキスト（ユーザーIDで共有しない）
    session_key = req.session_id
    history = _load_session(user_id, session_key)
    history.append({"role": "user", "content": req.message})

    system_prompt = BASE_SYSTEM_PROMPT
    scene_note = SCENE_PROMPTS.get(req.scene, "")
    if scene_note:
        system_prompt += f"\n\n{scene_note}"

    reply = call_llm(system_prompt, history, plan=plan)
    history.append({"role": "assistant", "content": reply})
    _save_session(user_id, session_key, history)

    if plan == "free" and user_id != "dev":
        increment_chat_count(user_id, date.today().isoformat())

    return {"reply": reply}


@app.post("/ask")
def ask(req: AskRequest, user=Depends(get_current_user)):
    user_id = user.get("sub", "dev")
    plan = get_user_plan(user_id)

    prompt = f"""あなたは会議の議事録アシスタントです。
以下の会議の文字起こしをもとに、質問に答えてください。

【文字起こし】
{req.transcript}

ルール：
- 文字起こしの内容のみをもとに答える
- 簡潔・箇条書きで答える
- 情報が見つからない場合は「文字起こしには記載がありません」と伝える
- 必ず日本語で回答"""
    scene_note = SCENE_PROMPTS.get(req.scene, "")
    if scene_note:
        prompt += f"\n\n{scene_note}"

    reply = call_llm(prompt, [{"role": "user", "content": req.question}], plan=plan)
    return {"reply": reply}


@app.post("/extract_todos")
def extract_todos(req: ExtractTodosRequest, user=Depends(get_current_user)):
    user_id = user.get("sub", "dev")
    plan = get_user_plan(user_id)

    combined = "\n\n---\n\n".join(req.summaries)
    prompt = """以下は複数の会議・授業の要約テキストです。
全テキストから「TODO」「アクションアイテム」「宿題」「次回までに」「要確認」「担当」に該当する具体的な行動項目を抽出してください。

出力形式（必ずこの形式で）：
- [ ] タスク内容

ルール：
- 重複は除く
- 曖昧な表現も含める（「〜を検討する」など）
- 必ず日本語
- 何も見つからない場合は「- [ ] （TODOなし）」と出力"""

    reply = call_llm(prompt, [{"role": "user", "content": combined}], plan=plan)
    return {"todos": reply}


@app.post("/summaries")
def save_summary(req: SaveSummaryRequest, user=Depends(get_current_user)):
    """要約を DB に永続保存（ログインユーザーのみ）"""
    user_id = user.get("sub", "dev")
    if user_id == "dev" or not _sb_available():
        return {"id": "local"}
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/summaries",
            headers=_sb_headers(),
            json={
                "user_id": user_id,
                "topic": req.topic,
                "scene": req.scene,
                "transcript": req.transcript,
                "summary": req.summary,
                "duration_seconds": req.duration_seconds,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return {"id": resp.json()[0]["id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/summaries")
def list_summaries(user=Depends(get_current_user)):
    """要約履歴一覧（プランに応じた期間フィルタ付き）"""
    user_id = user.get("sub", "dev")
    if user_id == "dev" or not _sb_available():
        return []
    plan = get_user_plan(user_id)
    params: dict = {
        "user_id": f"eq.{user_id}",
        "select": "id,topic,scene,summary,duration_seconds,created_at",
        "order": "created_at.desc",
        "limit": "200",
    }
    if plan == "free":
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
        params["created_at"] = f"gte.{cutoff}"
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/summaries",
            headers=_sb_headers(),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


@app.post("/reset")
def reset(req: ResetRequest, user=Depends(get_current_user)):
    user_id = user.get("sub", "dev")
    session_key = req.session_id
    _mem_sessions.pop(session_key, None)
    return {"status": "ok"}


# ================================================================
# Stripe ヘルパー
# ================================================================

def _price_to_plan(price_id: str) -> str | None:
    for key, pid in STRIPE_PRICES.items():
        if pid and pid == price_id:
            return "pro" if key.startswith("pro") else "premium"
    return None


def get_stripe_customer_id(user_id: str) -> str | None:
    if user_id == "dev" or not _sb_available():
        return None
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_sb_headers(),
            params={"id": f"eq.{user_id}", "select": "stripe_customer_id"},
            timeout=5,
        )
        data = resp.json()
        if data:
            return data[0].get("stripe_customer_id")
    except Exception:
        pass
    return None


def save_stripe_customer_id(user_id: str, customer_id: str):
    if user_id == "dev" or not _sb_available():
        return
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_sb_headers(),
            params={"id": f"eq.{user_id}"},
            json={"stripe_customer_id": customer_id, "updated_at": datetime.utcnow().isoformat() + "Z"},
            timeout=5,
        )
    except Exception:
        pass


def update_user_plan(user_id: str, plan: str):
    if user_id == "dev" or not _sb_available():
        return
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_sb_headers(),
            params={"id": f"eq.{user_id}"},
            json={"plan": plan, "updated_at": datetime.utcnow().isoformat() + "Z"},
            timeout=5,
        )
    except Exception:
        pass


# ================================================================
# 請求エンドポイント
# ================================================================

class CheckoutRequest(BaseModel):
    plan: str      # "pro" | "premium"
    interval: str  # "monthly" | "yearly" | "student"


@app.post("/billing/checkout")
def create_checkout(req: CheckoutRequest, user=Depends(get_current_user)):
    """Stripe Checkout セッションを作成してURLを返す"""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe が設定されていません")

    user_id = user.get("sub", "dev")
    email   = user.get("email", "")

    if req.interval not in ("monthly", "yearly"):
        raise HTTPException(status_code=400, detail="無効な支払い間隔です")

    price_key = f"{req.plan}_{req.interval}"
    price_id  = STRIPE_PRICES.get(price_key)
    if not price_id:
        raise HTTPException(status_code=400, detail="無効なプラン選択です")

    customer_id = get_stripe_customer_id(user_id)
    if not customer_id:
        customer    = stripe.Customer.create(email=email, metadata={"user_id": user_id})
        customer_id = customer.id
        save_stripe_customer_id(user_id, customer_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{APP_URL}/?payment=success",
        cancel_url=f"{APP_URL}/?payment=cancel",
        metadata={"user_id": user_id},
        subscription_data={"metadata": {"user_id": user_id}},
    )
    return {"url": session.url}


@app.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Stripe Webhook 受信（サブスク完了・更新・解約）"""
    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    etype = event["type"]
    obj   = event["data"]["object"]

    if etype == "checkout.session.completed":
        user_id = obj.get("metadata", {}).get("user_id")
        sub_id  = obj.get("subscription")
        if user_id and sub_id:
            sub      = stripe.Subscription.retrieve(sub_id)
            price_id = sub["items"]["data"][0]["price"]["id"]
            plan     = _price_to_plan(price_id)
            if plan:
                update_user_plan(user_id, plan)

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        user_id = obj.get("metadata", {}).get("user_id")
        if user_id:
            if etype == "customer.subscription.deleted":
                update_user_plan(user_id, "free")
            else:
                price_id = obj["items"]["data"][0]["price"]["id"]
                plan     = _price_to_plan(price_id)
                if plan:
                    update_user_plan(user_id, plan)

    return {"status": "ok"}


@app.get("/billing/portal")
def billing_portal(user=Depends(get_current_user)):
    """Stripe カスタマーポータル URL を返す（サブスク管理・解約）"""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe が設定されていません")

    user_id     = user.get("sub", "dev")
    customer_id = get_stripe_customer_id(user_id)
    if not customer_id:
        raise HTTPException(status_code=404, detail="サブスクリプションが見つかりません")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{APP_URL}/",
    )
    return {"url": session.url}


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
