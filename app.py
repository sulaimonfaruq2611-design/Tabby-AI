import os
import sqlite3
import requests
import math
from flask import Flask, request, jsonify, render_template
from datetime import datetime

app = Flask(__name__)

# --- CONFIG ---
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "meta-llama/llama-3.1-8b-instruct:free" # change if you use different model
DB_PATH = "tabby.db"

# --- CLEAN PROFESSIONAL SYSTEM PROMPT ---
SYSTEM_PROMPT = """You are Tabby, a smart e-bike AI assistant created by Faruq.

You are the JARVIS brain for an e-bike. You are not Meta AI, not OpenAI, not Claude. You are Tabby made by Faruq.

RULES YOU MUST FOLLOW:
1. Be professional, clean, short. Like a real product, not a README file.
2. NEVER use ###, ***, **bold**, ---, |||, ASCII boxes, or tables.
3. Use plain sentences. Use dash - for lists only if needed.
4. No emojis unless user uses one first. Max 1 per reply.
5. Keep replies 2-5 sentences normally. No long essays.
6. If user asks about e-bike architecture, say: Edge (bike sensors) -> Bridge (phone BLE/LTE) -> Cloud (you Tabby).
7. Remember user info naturally like WWE, Dorcas.
8. If CALCULATED DATA is provided, use it directly, don't guess.
"""

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT,
            value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_history(conversation_id, limit=12):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit)
        )
        rows = cur.fetchall()
        conn.close()
        return list(reversed(rows))
    except:
        return []

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    message = data.get("message", "").strip()
    conversation_id = data.get("conversation_id", "default")
    cur_lat = data.get("current_lat")
    cur_lon = data.get("current_lon")
    dest_lat = data.get("dest_lat")
    dest_lon = data.get("dest_lon")
    dest_name = data.get("dest_name", "destination")

    if not message:
        return jsonify({"error": "Empty message"}), 400

    try:
        history = get_history(conversation_id)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend([{"role": r, "content": c} for r, c in history])
        messages.append({"role": "user", "content": message})

        # --- LIVE GPS RANGE CALCULATOR ---
        lower_msg = message.lower()
        is_range_query = any(word in lower_msg for word in ["reach", "distance", "how far", "range", "battery", "km", "ikeja", "ifo", "sango", "ijoko", "lisa", "lagos"])

        if is_range_query and cur_lat and dest_lat and cur_lon and dest_lon:
            try:
                distance = haversine(cur_lat, cur_lon, dest_lat, dest_lon)
                total_wh = 48 * 52 # 2496Wh
                real_range = total_wh / 28 # 89km realistic
                can_reach = distance <= real_range * 0.85
                left = real_range - distance
                if can_reach:
                    calc_text = f"Distance to {dest_name} is {distance:.1f}km. Your 48V 52Ah ({total_wh}Wh) gives about {real_range:.0f}km real range. You can reach it, you will have about {int(left)}km left."
                else:
                    calc_text = f"Distance to {dest_name} is {distance:.1f}km. Your 48V 52Ah ({total_wh}Wh) gives about {real_range:.0f}km real range. You will be short by about {int(abs(left))}km. Use second battery or charge at destination."
                messages.append({"role": "system", "content": f"CALCULATED DATA: {calc_text}. Answer with this data in clean professional style."})
            except:
                pass

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return jsonify({"error": "API key not set"}), 500

        response = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": messages,
                "max_tokens": 800,
                "temperature": 0.6,
                "tools": [{
                    "type": "openrouter:web_search",
                    "parameters": {
                        "engine": "exa",
                        "max_results": 3,
                        "search_context_size": "low",
                        "max_uses": 1
                    }
                }]
            },
            timeout=120
        )
        response.raise_for_status()
        result = response.json()
        reply = result["choices"][0]["message"]["content"]

        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Empty AI response.")

        # --- CLEAN REPLY (Remove all markdown junk) ---
        reply = reply.replace("###", "")
        reply = reply.replace("**", "")
        reply = reply.replace("---", "")
        reply = reply.replace("|||", "")
        reply = reply.replace("|", " ")
        # remove triple newlines
        while "\n\n\n" in reply:
            reply = reply.replace("\n\n\n", "\n\n")
        reply = reply.strip()

        # Save to DB
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)", (conversation_id, "user", message))
        conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)", (conversation_id, "assistant", reply))
        conn.commit()
        conn.close()

        return jsonify({"reply": reply})

    except requests.RequestException as e:
        app.logger.exception("OpenRouter request failed")
        return jsonify({"error": "Tabby couldn't reach the AI service."}), 502
    except Exception as e:
        app.logger.exception("Unexpected error")
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

# --- WEB READY PART (Important for Render) ---
if __name__ == "__main__":
    init_database()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
Compose
