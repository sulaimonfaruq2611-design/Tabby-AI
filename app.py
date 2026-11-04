import os
import sqlite3
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)
DB_PATH = "tabby.db"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

def init_database():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

init_database()

def local_brain(msg):
    m = msg.lower()
    if "who created you" in m or "who made you" in m or "your creator" in m:
        return "I was created by Faruq Sulaimon. He built me as his smart AI assistant and JARVIS for his e-bike."
    if "who are you" in m or "your name" in m or "what is your name" in m:
        return "My name is Tabby. I was created by Faruq Sulaimon. I am your smart AI assistant, like JARVIS. I can answer any question, help with your e-bike, school, coding, and anything else."
    if "ikeja" in m:
        return "Yes Boss, you can reach Ikeja. Ikeja is around 18km from you. Your battery can do about 45km."
    if "battery" in m:
        return "Your battery is at 78 percent. You can go about 45km more. You are good to go."
    return None

@app.route("/")
def index():
    if os.path.exists("templates/index.html"):
        return render_template("index.html")
    return "Tabby AI is Live!"

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json() or {}
        user_msg = data.get("message", "").strip()
        conversation_id = data.get("conversation_id", "default")
        if not user_msg:
            return jsonify({"error": "No message"}), 400
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)", (conversation_id, "user", user_msg))
        conn.commit()
        reply = local_brain(user_msg)
        if reply is None:
            if OPENROUTER_API_KEY:
                try:
                    headers = {"Authorization": "Bearer " + OPENROUTER_API_KEY, "Content-Type": "application/json", "HTTP-Referer": "https://tabby-ai.onrender.com", "X-Title": "Tabby AI"}
                    payload = {"model": "openai/gpt-3.5-turbo", "messages": [{"role": "system", "content": "You are Tabby, a smart AI assistant. Your creator is Faruq Sulaimon. You were created by Faruq Sulaimon. Always remember your name is Tabby and your creator is Faruq Sulaimon. Answer any question like ChatGPT - general knowledge, school, coding, life, e-bike, Lagos, anything. Be friendly, short, helpful, like JARVIS. If someone asks who created you, say Faruq Sulaimon."}, {"role": "user", "content": user_msg}]}
                    resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
                    resp.raise_for_status()
                    reply = resp.json()["choices"][0]["message"]["content"]
                except Exception as e:
                    print("OpenRouter failed:", e)
                    reply = "My main AI brain is sleeping small because my OpenRouter key is not set or it failed. But I am Tabby, created by Faruq Sulaimon. Ask me again after you set the key, or ask me about battery or who created me."
            else:
                reply = "I am Tabby, created by Faruq Sulaimon. My main AI key is not set yet for Render, so I can only answer simple things now. Set OPENROUTER_API_KEY in Render to unlock full AI for any question."
        reply = reply.replace("**", "").replace("###", "").strip()
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)", (conversation_id, "assistant", reply))
        conn.commit()
        conn.close()
        return jsonify({"reply": reply})
    except Exception as e:
        print("Unexpected error:", e)
        return jsonify({"error": "Unexpected error: " + str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
