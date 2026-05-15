"""
コミュニケーション支援AI - キーワード→文章化モード（Ollama版）
使い方: python keyword_to_message.py
"""

import requests
import json

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:7b"

SYSTEM_PROMPT = """あなたは会話・発言が苦手な人の「通訳AI」です。
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


def chat(message: str, history: list) -> str:
    history.append({"role": "user", "content": message})
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "stream": False,
    })
    reply = response.json()["message"]["content"]
    history.append({"role": "assistant", "content": reply})
    return reply


def main():
    print("=" * 50)
    print("  コミュニケーション支援AI - キーワードモード")
    print("=" * 50)
    print("言いたいことをキーワードで入力してください。")
    print("（例：「会議 資料 まだ できてない」）")
    print("終了するには 'q' を入力")
    print()

    while True:
        history = []

        keywords = input("キーワード入力 > ").strip()
        if keywords.lower() == "q":
            print("終了します。")
            break
        if not keywords:
            continue

        print("\nAI が推測中...\n")
        reply = chat(keywords, history)
        print(reply)
        print()

        while True:
            follow = input("番号を選ぶ / 追加キーワード / 新しい話題は Enter > ").strip()
            if not follow:
                break
            if follow.lower() == "q":
                return

            print("\nAI が処理中...\n")
            reply = chat(follow, history)
            print(reply)
            print()


if __name__ == "__main__":
    main()
