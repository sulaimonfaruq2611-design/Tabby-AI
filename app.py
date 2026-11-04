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

# FIX: Run am immediately, not only in __main__ - Render needs this
init_database()

def local_brain(msg):
    msg = msg.lower()
    if "ikeja" in msg:
        return "Yes Boss! You can reach Ikeja. Ikeja is around 18km from you. Your battery can do about 45km, you go still get battery remain."
    if "battery" in msg:
        return "Your battery is at 78%. You can go about 45km more. You are good to go!"
    if "lekki" in msg:
        return "Lekki is about 25km. You can reach it, but you will have about 20km battery left. Charge when you reach."
    if "hello" in msg or "hi" in msg or "hey" in msg:
        return "Hello Boss! I'm Tabby, your e-bike JARVIS. Ask me if you can reach somewhere or check your battery."
    if "who are you" in msg:
        return "I'm Tabby, created by Faruq. I'm the smart brain for your e-bike."
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
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                     (conversation_id, "user", user_msg))
        conn.commit()

        reply = None

        # 1. Try local brain first - fast, no money
        reply = local_brain(user_msg)

        # 2. If local no know, try OpenRouter
        if reply is None:
            if OPENROUTER_API_KEY:
                try:
                    headers = {
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": "openai/gpt-3.5-turbo",
                        "messages": [
                            {"role": "system", "content": "You are Tabby, a smart e-bike assistant created by Faruq. Answer short, friendly, like JARVIS. Help with battery, range, trips in Lagos."},
                            {"role": "user", "content": user_msg}
                        ]
                    }
                    resp = requests.post("https://openrouter.ai/api
