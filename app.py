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
    if "ikeja" in m:
        return "Yes Boss, you can reach Ikeja. Ikeja is around 18km from you. Your battery can do about 45km."
    if "battery" in m:
        return "Your battery is at 78 percent. You can go about 45km more. You are good to go."
    if "lekki" in m:
        return "Lekki is about 25km. You can reach it, but you will have about 20km battery left."
    if "hello" in m or "hi" in m or "hey" in m:
        return "Hello Boss, I am Tabby, your e-bike JARVIS. Ask me if you can reach somewhere or check your battery."
    if "who are you" in m:
        return "I am Tabby, created by Faruq. I am the smart brain for your e-bike."
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
                    headers = {"Authorization": "Bearer " + OPENROUTER_API_KEY, "Content-Type": "application/json"}
                    payload = {"model": "openai/gpt-3.5-turbo", "messages": [{"role": "system", "content": "You are Tabby, a smart e-bike assistant created by Faruq. Answer short, friendly, like JARVIS. Help with battery, range, trips in Lagos."}, {"role": "user", "content": user_msg}]}
                    resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
                    resp.raise_for_status()
                    reply = resp.json()["choices"][0]["message"]["content"]
                except Exception as e:
                    print("OpenRouter failed:", e)
                    reply = "My AI brain is sleeping small, but I am still here. Try ask me: Can I reach Ikeja? Or Battery status?"
            else:
                reply = "I hear you Boss. I can help with battery, range, and trips. Try: Can I reach Ikeja? Or Battery status?"
        reply = reply.replace("**", "").replace("###", "").strip()
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)", (conversation_id, "assistant", reply))
        conn.commit()
        conn.close()
        return jsonify({"reply": reply})
    except Exception as e:
        print("Unexpected error:", e)
        return jsonify({"error": "Unexpected error: " + str(e)}), 500

@app.route("/history")
def history():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT role, content FROM messages ORDER BY id DESC LIMIT 50").fetchall()
        conn.close()
        return jsonify([dict(r) for r in reversed(rows)])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
