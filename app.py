import os
import sqlite3
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

DB_PATH = "tabby.db"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

def init_database():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            role TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

@app.route("/")
def index():
    return render_template("index.html") if os.path.exists("templates/index.html") else "Tabby AI is Live!"

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        user_msg = data.get("message", "")
        conversation_id = data.get("conversation_id", "default")

        if not user_msg:
            return jsonify({"error": "No message"}), 400

        # Save user message
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                     (conversation_id, "user", user_msg))
        conn.commit()

        # Call AI
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "openai/gpt-3.5-turbo",
            "messages": [{"role": "user", "content": user_msg}]
        }
        
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        ai_data = resp.json()
        reply = ai_data["choices"][0]["message"]["content"]

        # Clean reply
        reply = reply.replace("**", "").replace("###", "").strip()
        while "\n\n\n" in reply:
            reply = reply.replace("\n\n\n", "\n\n")

        # Save AI reply
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                     (conversation_id, "assistant", reply))
        conn.commit()
        conn.close()

        return jsonify({"reply": reply})

    except requests.RequestException as e:
        print(f"OpenRouter request failed: {e}")
        return jsonify({"error": "Tabby couldn't reach the AI service."}), 502
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

# --- WEB READY PART (Important for Render) ---
if __name__ == "__main__":
    init_database()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
