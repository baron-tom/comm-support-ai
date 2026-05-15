"""
コミュニケーション支援AI - サーバー
起動: uvicorn server:app --reload

LLM切り替え:
  環境変数 LLM_BACKEND=gemini (デフォルト) or ollama
  Gemini使用時: 環境変数 GEMINI_API_KEY を設定

認証:
  SUPABASE_JWT_SECRET が未設定の場合は認証スキップ（ローカル開発用）
"""

from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jose import jwt, JWTError
import requests
import os

app = FastAPI()

# ---- LLM設定 ----
LLM_BACKEND = os.environ.get("LLM_BACKEND", "gemini")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.0-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b"

# ---- Supabase認証設定 ----
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")


def get_current_user(authorization: str = Header(default=None)):
    """JWT認証。SUPABASE_JWT_SECRET未設定時はdev modeでスキップ。"""
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


def call_llm(system_prompt: str, messages: list[dict]) -> str:
    """
    messages: [{"role": "user"/"assistant", "content": str}, ...]
    """
    if LLM_BACKEND == "gemini":
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
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


# ---- プロンプト定義 ----

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

BASE_SYSTEM_PROMPT = """あなたは会話・発言が苦手な人の「通訳AI」です。
ユーザーはADHD・APD・会話困難を持つ人です。
入力の種類によって自動でモードを切り替えてください。

---
【キーワードモード】入力が短い・断片的なキーワードの場合
ステップ1: 「言いたいこと」を3つ推測し、番号リストで提示する
ステップ2: 番号が選ばれたら、相手に伝えられる自然な文章を作る

---
【まとめモード】入力が長い文章・話し言葉・メモの場合
以下の形式で出力する：

■ 要点
・〜
・〜

■ 伝えるべきこと（あれば）
〜

---
共通ルール：
- 必ず日本語で答える
- 出力は短く明確に
- 追加で聞くなら1つだけ
- 批判・急かしをしない"""

SCENE_PROMPTS = {
    "default": "",
    "work": "場面は職場です。上司・同僚・取引先への言葉として適切な表現を使ってください。",
    "medical": "場面は医療機関です。医師・看護師に症状や状況を正確に伝える表現を使ってください。",
    "daily": "場面は日常・家族・友人との会話です。自然でやわらかい表現を使ってください。",
    "class": "場面は学校の授業です。",
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
- 必ず日本語"""
}


# ---- リクエストモデル ----

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


sessions: dict[str, list] = {}


# ---- エンドポイント ----

@app.get("/config")
def get_config():
    """フロントエンド用の公開設定を返す（認証不要）"""
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_ANON_KEY,
        "auth_enabled": bool(SUPABASE_JWT_SECRET),
    }


@app.post("/summarize")
def summarize(req: SummarizeRequest, user=Depends(get_current_user)):
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
        prompt += f"\n\n【重要】この内容は「{req.topic}」に関するものです。文字起こしに誤認識があっても、このトピックをもとに正しく解釈・補完してください。"

    reply = call_llm(prompt, [{"role": "user", "content": req.text}])
    return {"summary": reply}


@app.post("/chat")
def chat(req: ChatRequest, user=Depends(get_current_user)):
    # 認証済みの場合はユーザーIDをセッションキーに使う
    session_key = user.get("sub", req.session_id)
    if session_key == "dev":
        session_key = req.session_id

    if session_key not in sessions:
        sessions[session_key] = []

    history = sessions[session_key]
    history.append({"role": "user", "content": req.message})

    scene_note = SCENE_PROMPTS.get(req.scene, "")
    system_prompt = BASE_SYSTEM_PROMPT
    if scene_note:
        system_prompt += f"\n\n{scene_note}"

    reply = call_llm(system_prompt, history)
    history.append({"role": "assistant", "content": reply})
    return {"reply": reply}


@app.post("/ask")
def ask(req: AskRequest, user=Depends(get_current_user)):
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
    reply = call_llm(prompt, [{"role": "user", "content": req.question}])
    return {"reply": reply}


@app.post("/reset")
def reset(req: ResetRequest, user=Depends(get_current_user)):
    session_key = user.get("sub", req.session_id)
    if session_key == "dev":
        session_key = req.session_id
    sessions.pop(session_key, None)
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
