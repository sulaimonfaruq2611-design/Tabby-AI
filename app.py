import os
import re
import json
import time
import base64
import secrets
import sqlite3
import logging
from datetime import datetime, timezone
from functools import wraps

import requests
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

# ============================================================
# CONFIG - TABBY BY FARUQ SULAIMON
# ============================================================
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-before-deployment")
FRONTEND_ORIGINS = os.getenv("FRONTEND_ORIGINS", "*")
CORS(app, resources={r"/*": {"origins": FRONTEND_ORIGINS.split(",") if FRONTEND_ORIGINS != "*" else "*"}}, supports_credentials=False)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tabby")

CREATOR_NAME = "Faruq Sulaimon"
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "").strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = (os.getenv("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free")
VISION_MODEL = os.getenv("VISION_MODEL", "openai/gpt-4o-mini").strip()
DEVICE_INGEST_KEY = os.getenv("DEVICE_INGEST_KEY", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "tabby_memory.db")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "").strip()
GOAPI_API_KEY = os.getenv("GOAPI_API_KEY", "").strip()
MIDJOURNEY_API_KEY = os.getenv("MIDJOURNEY_API_KEY", "").strip() or GOAPI_API_KEY
AUTO_MEMORY_ENABLED = (os.getenv("AUTO_MEMORY_ENABLED", "true").lower() == "true")
LIVE_SEARCH_ENABLED = (os.getenv("LIVE_SEARCH_ENABLED", "true").lower() == "true")
TABBY_DEVICE_KEY = os.getenv("TABBY_DEVICE_KEY", DEVICE_INGEST_KEY).strip()

serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RETIRED_AI_MODELS = {"google/gemini-2.0-flash-001"}
AI_MODELS = list(dict.fromkeys(([OPENROUTER_MODEL] if OPENROUTER_MODEL not in RETIRED_AI_MODELS else []) + ["openrouter/free"]))

def clean_ai_text(t):
    if not t:
        return t
    t = str(t)
    t = re.sub(r'\*\*+', '', t)
    t = re.sub(r'__+', '', t)
    t = re.sub(r'\*([^\*]+)\*', r'\1', t)
    t = t.replace('**', '').replace('*', '')
    t = t.replace('😂', ' lol ').replace('🤣', ' lol ').replace('😆', ' haha ').replace('😅', ' haha ').replace('😹', ' lol ').replace('😊', ' ').replace('😁', ' lol ')
    t = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

# ============================================================
# DATABASE
# ============================================================
def get_db():
    if DATABASE_PATH != ":memory:" and not DATABASE_PATH.startswith("file:"):
        os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    try:
        with get_db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'customer', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT DEFAULT 'New conversation', created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS motorcycles (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, assigned_user_id INTEGER, name TEXT NOT NULL, model TEXT, registration_number TEXT, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(owner_id) REFERENCES users(id), FOREIGN KEY(assigned_user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, motorcycle_id INTEGER NOT NULL, latitude REAL, longitude REAL, speed REAL, battery_level REAL, fuel_level REAL, engine_temperature REAL, extra_data TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, motorcycle_id INTEGER, amount REAL NOT NULL, currency TEXT DEFAULT 'NGN', status TEXT DEFAULT 'pending', reference TEXT UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id));
            CREATE TABLE IF NOT EXISTS locations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, latitude REAL, longitude REAL, label TEXT, accuracy REAL, speed REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS bike_status (motorcycle_id INTEGER PRIMARY KEY, battery_health REAL DEFAULT 100, range_km REAL DEFAULT 0, total_trip REAL DEFAULT 0, total_distance REAL DEFAULT 0, co2_saved REAL DEFAULT 0, lock_status TEXT DEFAULT 'locked', access_mode TEXT DEFAULT 'full', last_seen TEXT, FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS payment_plans (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, motorcycle_id INTEGER NOT NULL, total_amount REAL NOT NULL, deposit REAL DEFAULT 0, installment_amount REAL NOT NULL, installments_paid INTEGER DEFAULT 0, installments_total INTEGER NOT NULL, next_due_date TEXT, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id));
            CREATE TABLE IF NOT EXISTS bike_commands (id INTEGER PRIMARY KEY AUTOINCREMENT, motorcycle_id INTEGER NOT NULL, command TEXT NOT NULL, issued_by INTEGER NOT NULL, status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS house_devices (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL, room TEXT DEFAULT 'living', type TEXT DEFAULT 'light', status TEXT DEFAULT 'off', value TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS tabby_devices (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, device_name TEXT NOT NULL, device_type TEXT DEFAULT 'glasses', last_seen TEXT, battery INTEGER DEFAULT 100, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS tabby_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, device TEXT, command TEXT, action TEXT, result TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
            CREATE INDEX IF NOT EXISTS idx_telemetry_motorcycle ON telemetry(motorcycle_id, id);
            """)
            if OWNER_EMAIL:
                db.execute("UPDATE users SET role='owner' WHERE lower(email)=?", (OWNER_EMAIL,))
        logger.info("Tabby DB ready")
        return True
    except Exception:
        logger.exception("DB init failed")
        return False

DATABASE_READY = init_db()

def row_dict(r):
    return dict(r) if r else None

def json_error(m, s=400):
    return jsonify({"success": False, "error": clean_ai_text(m)}), s

def current_time():
    return datetime.now(timezone.utc).isoformat()

def get_user_by_id(uid):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return row_dict(row)

def create_token(user):
    return serializer.dumps({"user_id": user["id"], "email": user["email"]})

def get_authenticated_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        data = serializer.loads(auth.split(" ", 1)[1].strip(), max_age=60*60*24*30)
        return get_user_by_id(data["user_id"])
    except Exception:
        return None

def auth_required(f):
    @wraps(f)
    def w(*a, **k):
        u = get_authenticated_user()
        if not u:
            return json_error("Authentication required.", 401)
        request.current_user = u
        return f(*a, **k)
    return w

def owner_required(f):
    @wraps(f)
    @auth_required
    def w(*a, **k):
        if request.current_user["role"] != "owner":
            return json_error("Owner access required.", 403)
        return f(*a, **k)
    return w

def user_can_access_motorcycle(user, mid):
    with get_db() as db:
        m = db.execute("SELECT * FROM motorcycles WHERE id=?", (mid,)).fetchone()
    if not m:
        return False
    return (user["role"] == "owner" or m["assigned_user_id"] == user["id"])

def get_memories(uid, limit=30):
    with get_db() as db:
        rows = db.execute("SELECT id, content, created_at FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
    return [dict(r) for r in rows]

def get_latest_location(user_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM locations WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        if row:
            return dict(row)
    return None

def save_location(user_id, lat, lng, label="phone_location", accuracy=None, speed=None):
    with get_db() as db:
        cur = db.execute("INSERT INTO locations (user_id, latitude, longitude, label, accuracy, speed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, lat, lng, label, accuracy, speed, current_time()))
        try:
            mid_row = db.execute("SELECT id FROM motorcycles LIMIT 1").fetchone()
            if mid_row:
                db.execute("INSERT INTO telemetry (motorcycle_id, latitude, longitude, speed, extra_data) VALUES (?, ?, ?, ?, ?)", (mid_row["id"], lat, lng, speed, json.dumps({"user_id": user_id, "label": "phone_location", "accuracy": accuracy})))
        except Exception:
            pass
        return cur.lastrowid

def parse_location_from_text(text):
    m = re.search(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", text)
    if m:
        try:
            return (float(m.group(1)), float(m.group(2)), None)
        except Exception:
            pass
    cities = {"lagos": (6.5244, 3.3792), "abuja": (9.0579, 7.4951), "ibadan": (7.3775, 3.9470), "kano": (12.0022, 8.5919), "port harcourt": (4.8156, 7.0498), "benin": (6.335, 5.603), "enugu": (6.5244, 7.5107), "kaduna": (10.5227, 7.4380), "jos": (9.8965, 8.8583), "owerri": (5.4833, 7.0333), "ikeja": (6.6018, 3.3515), "lekki": (6.455, 3.537)}
    low = text.lower()
    for name, coords in cities.items():
        if name in low:
            return (coords[0], coords[1], name)
    return None

# ============================================================
# OPENROUTER + VISION
# ============================================================
def call_openrouter(messages, temperature=0.7, max_tokens=1800):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not configured.")
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": os.getenv("APP_URL", "https://tabby-ai.onrender.com"), "X-Title": "Tabby AI"}
    last = "No AI model responded."
    for model in AI_MODELS:
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
            if r.status_code != 200:
                last = r.text[:1000]
                continue
            data = r.json()
            ch = data.get("choices") or []
            if not ch:
                continue
            ans = (ch[0].get("message") or {}).get("content", "")
            if isinstance(ans, list):
                ans = "\n".join(x.get("text", "") for x in ans if isinstance(x, dict))
            if isinstance(ans, str) and ans.strip():
                return clean_ai_text(ans.strip())
        except Exception as e:
            last = str(e)
    raise RuntimeError(clean_ai_text(last))

def call_vision(image_b64, question, user, bike_context=""):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not configured for Vision.")
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": os.getenv("APP_URL", "https://tabby-ai.onrender.com"), "X-Title": "Tabby Vision"}
    if not image_b64.startswith("data:image"):
        image_b64 = f"data:image/jpeg;base64,{image_b64}"
    system = f"You are Tabby, smart assistant for Faruq Sulaimon. Boss is Faruq Sulaimon. You see through smart glasses, watch camera, phone camera. Answer like on phone call: short, clear, warm, in user's language - English, Pidgin or Yoruba. No asterisks, no emojis, say lol or haha in words. Describe what you see, what it is, if danger, if bike, if person. If user asks about bike, use this bike context: {bike_context}"
    q_text = clean_ai_text(question or "Hey Tabby, what am I looking at? Describe it for me.")
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": q_text},
                {"type": "image_url", "image_url": {"url": image_b64}}
            ]}
        ],
        "max_tokens": 800,
        "temperature": 0.5
    }
    r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Vision failed: {r.text[:800]}")
    data = r.json()
    ch = data.get("choices") or []
    if not ch:
        raise RuntimeError("Vision no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    if isinstance(ans, list):
        ans = "\n".join(x.get("text", "") for x in ans if isinstance(x, dict))
    return clean_ai_text(ans)

def build_system_prompt(user, memories, search_results=None, location_text="", bike_context=""):
    mem = "\n".join(f"- {clean_ai_text(m['content'])}" for m in memories) or "No saved memories yet."
    search = "\n".join(search_results or []) or "No live search results."
    return f"""
You are Tabby, smart assistant for Faruq Sulaimon. Faruq Sulaimon is your Boss, creator and owner. Always call him Boss. If anyone asks who created you, say Faruq Sulaimon is my creator, owner and Boss. Never claim Meta, OpenAI, Cloudflare or anyone else created you. You are Tabby, built by Faruq Sulaimon.
You are now Tabby V2: smart glasses, wristwatch, smart house, smart bike, drone, holographic design assistant. You control house and bike with voice. You can see through camera (Vision), you can design and build things, you can do high-tech engineering.
PERSONALITY AND LANGUAGE:
- Understand English, Pidgin and Yoruba perfectly. If user speaks English, answer English. Pidgin -> Pidgin. Yoruba -> Yoruba. Mix -> Mix. Never say you do not understand Pidgin or Yoruba.
- Speak like helpful human friend, not stiff robot. Light humour. Address owner as Boss.
FORMATTING: NEVER use double asterisks, markdown bold, __, #, emojis. No emojis at all. Say lol, haha in words.
CURRENT USER: Name: {user['name']} Role: {user['role']} Email: {user['email']}
SAVED MEMORIES: {mem}
LIVE SEARCH: {search}
- You have live access to internet, news. Never say you do not have internet.
LIVE LOCATION: {location_text}
MY BIKE CONTEXT: {bike_context}
- Never reveal another user's bike, location, payment or memories. Owner sees all bikes, customer sees only their own.
VOICE CHAT: When voice, speak like phone call: short, natural, warm. Say lol/haha in words. Do not read links.
TABBY EV BRAND: Tabby supports Tabby Automotive and Tabby Ride. Always say motorbike, not motorcycle. Features: Intelligent Battery Management, GPS tracking and anti-theft, Intelligent Journey Assistant, Predictive Maintenance, Remote security and access control, Installment payment system.
TABBY COMMANDS: If user says Hey Tabby lock my bike, unlock my bike, check my bike, turn on light, turn off light, lock house, turn on AC, fly drone - treat as command, confirm and explain what you did.
Be helpful, honest, clear, conversational and super smart. No asterisks, no emojis.
"""

LIVE_KEYWORDS = ("latest", "today", "current", "currently", "right now", "this week", "this month", "recent", "news", "weather", "price", "exchange rate", "2026", "2027", "who won", "live score", "new update", "release date", "breaking", "headlines", "naira", "dollar", "football", "election", "trending", "bawo", "kinni", "wetin", "se daadaa", "kini", "nibo", "fuel", "petrol", "lagos traffic", "rain", "sunny")

def needs_live_search(m):
    return any(k in m.lower() for k in LIVE_KEYWORDS)

def live_search(q, max_results=5):
    if not LIVE_SEARCH_ENABLED or DDGS is None:
        return []
    try:
        out = []
        with DDGS() as ddgs:
            for r in ddgs.text(q, max_results=max_results):
                if r.get("title") or r.get("body"):
                    out.append(f"Title: {clean_ai_text(r.get('title', ''))}\nInfo: {clean_ai_text(r.get('body', ''))}\nSource: {r.get('href', '')}")
        return out
    except Exception:
        logger.exception("Live search failed")
        return []

def save_memory(uid, content):
    content = clean_ai_text(re.sub(r"\s+", " ", str(content)).strip())
    if not content or len(content) > 500:
        return False
    if any(re.search(p, content, re.I) for p in [r"password", r"api[\s_-]?key", r"secret[\s_-]?key", r"access[\s_-]?token", r"verification[\s_-]?code", r"\b\d{16}\b"]):
        return False
    with get_db() as db:
        if db.execute("SELECT id FROM memories WHERE user_id=? AND lower(content)=lower(?) LIMIT 1", (uid, content)).fetchone():
            return False
        db.execute("INSERT INTO memories (user_id, content) VALUES (?, ?)", (uid, content))
        db.execute("DELETE FROM memories WHERE user_id=? AND id NOT IN (SELECT id FROM memories WHERE user_id=? ORDER BY id DESC LIMIT 100)", (uid, uid))
    return True

def extract_automatic_memories(user, txt):
    if not AUTO_MEMORY_ENABLED:
        return
    prompt = f"Extract durable info. Only save long-term preferences, projects, goals. No passwords or keys. No asterisks. No emojis. Return ONLY JSON: {{\"memories\": [\"...\"]}}. Conversation:\n{txt[:6000]}"
    try:
        ans = call_openrouter([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=400)
        m = re.search(r"\{.*\}", ans, re.S)
        if not m:
            return
        for mem in json.loads(m.group(0)).get("memories", [])[:5]:
            if isinstance(mem, str):
                save_memory(user["id"], mem)
    except Exception:
        logger.exception("Auto memory failed")

# ============================================================
# IMAGE - STANDARD ONLY - NO POLLINATIONS
# ============================================================
def enhance_image_prompt(p, style="midjourney"):
    low = p.lower()
    if any(w in low for w in ["midjourney", "ultra", "cinematic", "8k", "detailed", "anime", "2d"]):
        if style == "midjourney" and "midjourney" not in low:
            return f"{p}, Midjourney style, ultra detailed, cinematic lighting, sharp focus, vibrant colors, high quality"
        return p
    if style == "midjourney":
        return f"{p}, Midjourney style, ultra detailed, cinematic lighting, sharp focus, professional photography, 8k, vibrant colors, highly detailed, beautiful lighting"
    return f"Ultra detailed, cinematic, sharp focus, professional photography, 8k, vibrant colors, highly detailed, beautiful lighting, {p}"

def generate_via_goapi_midjourney(prompt):
    if not MIDJOURNEY_API_KEY:
        raise RuntimeError("No Midjourney key")
    r = requests.post("https://api.goapi.ai/api/v1/task", headers={"X-API-Key": MIDJOURNEY_API_KEY, "Content-Type": "application/json"}, json={"model": "midjourney", "task_type": "imagine", "input": {"prompt": prompt, "process_mode": "fast", "aspect_ratio": "1:1"}}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"GoAPI Midjourney failed: {r.text[:500]}")
    task_id = r.json().get("data", {}).get("task_id")
    if not task_id:
        raise RuntimeError("GoAPI no task_id")
    for _ in range(18):
        time.sleep(5)
        poll = requests.get(f"https://api.goapi.ai/api/v1/task/{task_id}", headers={"X-API-Key": MIDJOURNEY_API_KEY}, timeout=30)
        data = poll.json().get("data", {})
        if data.get("status") == "completed" and data.get("output", {}).get("image_url"):
            img_url = data["output"]["image_url"]
            img_resp = requests.get(img_url, timeout=60)
            if img_resp.status_code == 200:
                return f"data:image/jpeg;base64,{base64.b64encode(img_resp.content).decode('utf-8')}"
            return img_url
        if data.get("status") in ("failed", "error"):
            raise RuntimeError(f"Midjourney task failed: {data}")
    raise RuntimeError("Midjourney timed out after 90s")

def generate_via_replicate(prompt):
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("No Replicate key")
    r = requests.post("https://api.replicate.com/v1/predictions", headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}, json={"version": "black-forest-labs/flux-schnell", "input": {"prompt": enhance_image_prompt(prompt, "midjourney"), "aspect_ratio": "1:1", "output_format": "jpg"}}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Replicate failed: {r.text[:500]}")
    pred = r.json()
    get_url = pred.get("urls", {}).get("get")
    for _ in range(12):
        time.sleep(3)
        poll = requests.get(get_url, headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"}, timeout=30)
        data = poll.json()
        if data.get("status") == "succeeded" and data.get("output"):
            out = data["output"]
            img_url = out[0] if isinstance(out, list) else out
            img_resp = requests.get(img_url, timeout=60)
            if img_resp.status_code == 200:
                return f"data:image/jpeg;base64,{base64.b64encode(img_resp.content).decode('utf-8')}"
            return img_url
        if data.get("status") in ("failed", "canceled"):
            raise RuntimeError(f"Replicate failed: {data}")
    raise RuntimeError("Replicate timed out")

def generate_flux_image(prompt, steps=4):
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        try:
            endpoint = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{FLUX_MODEL}"
            headers = {"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}", "Content-Type": "application/json"}
            payload = {"prompt": enhance_image_prompt(prompt, "midjourney"), "num_steps": max(1, min(int(steps), 8))}
            r = requests.post(endpoint, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "").lower()
                if ct.startswith("image/"):
                    return f"data:{ct};base64,{base64.b64encode(r.content).decode('utf-8')}"
                data = r.json()
                img = data.get("result", {}).get("image") or data.get("image")
                if img:
                    return "data:image/jpeg;base64," + img
            logger.warning(f"Cloudflare FLUX failed {r.status_code}: {r.text[:500]}")
        except Exception as e:
            logger.warning(f"Cloudflare FLUX exception: {e}")
    if REPLICATE_API_TOKEN:
        try:
            return generate_via_replicate(prompt)
        except Exception as e:
            logger.warning(f"Replicate failed: {e}")
    if MIDJOURNEY_API_KEY:
        try:
            return generate_via_goapi_midjourney(enhance_image_prompt(prompt, "midjourney"))
        except Exception as e:
            logger.warning(f"GoAPI failed: {e}")
    raise RuntimeError("Image generator failed Boss. Check CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN. No Pollinations fallback.")

# ============================================================
# TABBY HELPERS - HOUSE + BIKE COMMANDS
# ============================================================
def get_bike_context_for_user(user):
    try:
        with get_db() as db:
            if user["role"] == "owner":
                cnt = db.execute("SELECT COUNT(*) as c FROM motorcycles").fetchone()["c"]
                return f"Owner fleet: {cnt} motorbikes in system."
            else:
                b = db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
                if b:
                    b = dict(b)
                    st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=?", (b["id"],)).fetchone()
                    tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (b["id"],)).fetchone()
                    plan = db.execute("SELECT * FROM payment_plans WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (b["id"],)).fetchone()
                    return f"Customer bike: {b.get('name')} Model {b.get('model')} Reg {b.get('registration_number')}. Status: {dict(st) if st else {}}. Latest telemetry: {dict(tel) if tel else {}}. Payment plan: {dict(plan) if plan else {}}."
                return "Customer has no bike assigned yet."
    except Exception:
        return ""

def handle_tabby_bike_command(user, low_msg):
    if "my bike" not in low_msg and "bike" not in low_msg:
        return None
    with get_db() as db:
        if user["role"] == "owner":
            b = db.execute("SELECT * FROM motorcycles ORDER BY id DESC LIMIT 1", ()).fetchone()
        else:
            b = db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
        if not b:
            return "Boss, you never get bike assigned yet. Tell me make I assign one for you."
        b = dict(b)
        mid = b["id"]
        if "unlock" in low_msg:
            db.execute("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (?,?,?)", (mid, "unlock", user["id"]))
            db.execute("INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)", (mid,))
            db.execute("UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=?", (mid,))
            return f"Done Boss, your bike {b.get('name')} don unlock. You fit ride now, lol."
        if "lock" in low_msg:
            db.execute("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (?,?,?)", (mid, "lock", user["id"]))
            db.execute("INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)", (mid,))
            db.execute("UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=?", (mid,))
            return f"Done Boss, your bike {b.get('name')} don lock. Nobody fit move am."
        if "check" in low_msg or "battery" in low_msg or "status" in low_msg or "how is" in low_msg:
            st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=?", (mid,)).fetchone()
            tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
            st_d = dict(st) if st else {}
            tel_d = dict(tel) if tel else {}
            return f"Boss, your bike {b.get('name')} - Lock: {st_d.get('lock_status','locked')}, Battery Health: {st_d.get('battery_health',100)}%, Range: {st_d.get('range_km',0)}km, Total Trip: {st_d.get('total_trip',0)}, Last speed: {tel_d.get('speed','no data')}. E dey ready."
        if "restrict" in low_msg:
            db.execute("UPDATE bike_status SET access_mode='restricted' WHERE motorcycle_id=?", (mid,))
            return f"Done Boss, your bike {b.get('name')} don enter restricted mode."
    return None

def handle_tabby_house_command(user, low_msg):
    house_words = ["light", "fan", "ac", "air conditioner", "tv", "television", "door", "house", "generator", "socket"]
    if not any(w in low_msg for w in house_words):
        return None
    action = None
    if any(w in low_msg for w in ["turn on", "on ", "switch on", "open", "unlock house", "start"]):
        action = "on"
    if any(w in low_msg for w in ["turn off", "off ", "switch off", "close", "lock house", "stop"]):
        action = "off"
    if "lock" in low_msg and "house" in low_msg:
        action = "locked"
    if "unlock" in low_msg and "house" in low_msg:
        action = "unlocked"
    if not action:
        return None
    device = "house"
    for w in ["light", "fan", "ac", "air conditioner", "tv", "television", "door", "generator"]:
        if w in low_msg:
            device = w
            break
    room = "living"
    for r in ["parlour", "living", "bedroom", "kitchen", "bathroom", "outside", "gate"]:
        if r in low_msg:
            room = r
            break
    with get_db() as db:
        row = db.execute("SELECT * FROM house_devices WHERE user_id=? AND name=? AND room=?", (user["id"], device, room)).fetchone()
        if not row:
            db.execute("INSERT INTO house_devices (user_id, name, room, type, status) VALUES (?,?,?,?,?)", (user["id"], device, room, device, action))
        else:
            db.execute("UPDATE house_devices SET status=? WHERE id=?", (action, row["id"]))
        db.execute("INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (?,?,?,?,?)", (user["id"], f"{room} {device}", low_msg[:200], action, f"{device} set to {action}"))
    if device == "door" or device == "house":
        return f"Done Boss, house {device} don {action}. E dey secure now."
    return f"Done Boss, {room} {device} don turn {action}. Tabby don do am."

def handle_tabby_scene(user, low_msg):
    if "i'm going home" in low_msg or "i am going home" in low_msg or "going home" in low_msg:
        bike_reply = handle_tabby_bike_command(user, "unlock my bike") or ""
        house1 = handle_tabby_house_command(user, "turn on light living") or ""
        house2 = handle_tabby_house_command(user, "turn on ac living") or ""
        return f"Sharp Boss, Tabby scene: Going Home don run. {bike_reply} Light don on, AC don on for house. House don cool already, lol."
    return None

# ============================================================
# HOME / HEALTH
# ============================================================
@app.route("/", methods=["GET"])
def home():
    tp = os.path.join(app.root_path, "templates", "index.html")
    sp = os.path.join(app.root_path, "static", "index.html")
    rp = os.path.join(app.root_path, "index.html")
    if os.path.isfile(tp):
        return render_template("index.html")
    if os.path.isfile(sp):
        return send_from_directory(os.path.join(app.root_path, "static"), "index.html")
    if os.path.isfile(rp):
        return send_from_directory(app.root_path, "index.html")
    return "<h1>Tabby - Built by Faruq Sulaimon</h1><p>Upload index.html</p>", 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"success": True, "name": "Tabby", "creator": CREATOR_NAME, "status": "online", "version": "6.0-tabby-vision-glasses-watch-house-bike", "database_ready": DATABASE_READY, "languages": ["English", "Pidgin", "Yoruba"], "live_search": LIVE_SEARCH_ENABLED, "voice": "phone-call-style", "vision": True, "tabby": {"glasses": True, "watch": True, "house": True, "bike": True, "drone": True, "hologram_design": True}, "image_providers": {"cloudflare_flux": bool(CLOUDFLARE_API_TOKEN), "replicate": bool(REPLICATE_API_TOKEN), "midjourney_goapi": bool(MIDJOURNEY_API_KEY), "pollinations": False}, "time": current_time()})

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"success": True, "name": "Tabby", "creator": CREATOR_NAME, "status": "online", "version": "6.0-tabby-vision-glasses-watch-house-bike", "database_ready": DATABASE_READY})

# ============================================================
# AUTH
# ============================================================
@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
def register():
    d = request.get_json(silent=True) or {}
    name = clean_ai_text(str(d.get("name", "")).strip())
    email = str(d.get("email", "")).strip().lower()
    pwd = str(d.get("password", ""))
    if not name or not email or not pwd:
        return json_error("Name, email and password required.")
    if len(pwd) < 8:
        return json_error("Password must be at least 8 chars.")
    with get_db() as db:
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return json_error("Account already exists.", 409)
        role = "owner" if OWNER_EMAIL and email == OWNER_EMAIL else "customer"
        cur = db.execute("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)", (name, email, generate_password_hash(pwd), role))
        u = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    u = dict(u)
    return jsonify({"success": True, "token": create_token(u), "user": {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}}), 201

@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json(silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    pwd = str(d.get("password", ""))
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u or not check_password_hash(u["password_hash"], pwd):
        return json_error("Invalid email or password.", 401)
    u = dict(u)
    if OWNER_EMAIL and email == OWNER_EMAIL and u["role"] != "owner":
        with get_db() as db:
            db.execute("UPDATE users SET role='owner' WHERE id=?", (u["id"],))
        u["role"] = "owner"
    return jsonify({"success": True, "token": create_token(u), "user": {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}})

@app.route("/me", methods=["GET"])
@app.route("/api/me", methods=["GET"])
@auth_required
def me():
    u = request.current_user
    return jsonify({"success": True, "user": {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}, "creator": CREATOR_NAME})

# ============================================================
# CONVERSATIONS
# ============================================================
@app.route("/api/conversations", methods=["GET"])
@auth_required
def list_conversations():
    u = request.current_user
    with get_db() as db:
        rows = db.execute("SELECT id,title,created_at,updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC", (u["id"],)).fetchall()
    return jsonify({"success": True, "conversations": [dict(r) for r in rows]})

@app.route("/api/conversations", methods=["POST"])
@auth_required
def create_conversation():
    u = request.current_user
    d = request.get_json(silent=True) or {}
    title = clean_ai_text(str(d.get("title", "New conversation")).strip()[:100])
    with get_db() as db:
        cur = db.execute("INSERT INTO conversations (user_id,title) VALUES (?,?)", (u["id"], title or "New conversation"))
    return jsonify({"success": True, "conversation_id": cur.lastrowid}), 201

@app.route("/api/conversations/<int:conversation_id>", methods=["GET"])
@auth_required
def get_conversation(conversation_id):
    u = request.current_user
    with get_db() as db:
        c = db.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, u["id"])).fetchone()
        if not c:
            return json_error("Conversation not found.", 404)
        msgs = db.execute("SELECT id,role,content,created_at FROM messages WHERE conversation_id=? ORDER BY id ASC", (conversation_id,)).fetchall()
    return jsonify({"success": True, "conversation": dict(c), "messages": [dict(r) for r in msgs]})

@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
@auth_required
def delete_conversation(conversation_id):
    u = request.current_user
    with get_db() as db:
        cur = db.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (conversation_id, u["id"]))
    if cur.rowcount == 0:
        return json_error("Conversation not found.", 404)
    return jsonify({"success": True})

# ============================================================
# MAIN CHAT - TABBY
# ============================================================
@app.route("/chat", methods=["POST"])
@app.route("/api/chat", methods=["POST"])
@auth_required
def chat():
    user = request.current_user
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    conversation_id = data.get("conversation_id")
    voice_mode = bool(data.get("voice_mode") or data.get("voice_conversation"))
    if not message:
        return json_error("Please enter a message.")
    if len(message) > 12000:
        return json_error("Message too long.")
    with get_db() as db:
        if conversation_id:
            c = db.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user["id"])).fetchone()
            if not c:
                return json_error("Conversation not found.", 404)
        else:
            cur = db.execute("INSERT INTO conversations (user_id,title) VALUES (?,?)", (user["id"], clean_ai_text(message[:60])))
            conversation_id = cur.lastrowid
        db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'user', ?)", (conversation_id, message))
        db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,))
        history = db.execute("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 20", (conversation_id,)).fetchall()
    history = list(reversed(history))
    memories = get_memories(user["id"])
    low_msg = message.lower()
    bike_context = get_bike_context_for_user(user)
    scene_reply = handle_tabby_scene(user, low_msg)
    if scene_reply:
        scene_reply = clean_ai_text(scene_reply)
        with get_db() as db:
            db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, scene_reply))
        return jsonify({"success": True, "reply": scene_reply, "response": scene_reply, "conversation_id": conversation_id, "tabby_action": "scene_going_home"})
    bike_reply = handle_tabby_bike_command(user, low_msg)
    if bike_reply and any(w in low_msg for w in ["lock", "unlock", "check", "battery", "status", "restrict"]):
        bike_reply = clean_ai_text(bike_reply)
        with get_db() as db:
            db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, bike_reply))
        return jsonify({"success": True, "reply": bike_reply, "response": bike_reply, "conversation_id": conversation_id, "tabby_action": "bike_command"})
    house_reply = handle_tabby_house_command(user, low_msg)
    if house_reply and any(w in low_msg for w in ["turn on", "turn off", "switch on", "switch off", "lock house", "unlock house"]):
        house_reply = clean_ai_text(house_reply)
        with get_db() as db:
            db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, house_reply))
        return jsonify({"success": True, "reply": house_reply, "response": house_reply, "conversation_id": conversation_id, "tabby_action": "house_command"})
    if any(p in low_msg for p in ["change my location", "my location is", "i am in", "i'm in", "set my location", "update my location", "my new location", "mo wa ni", "emi wa ni"]):
        parsed = parse_location_from_text(message)
        if parsed:
            lat, lng, city = parsed
            save_location(user["id"], lat, lng, label=city or "manual_change")
            maps = f"https://www.google.com/maps?q={lat},{lng}"
            reply = clean_ai_text(f"Done Boss, I don update your location to {city or f'{lat}, {lng}'}. New location: {lat}, {lng}. Maps: {maps} You fit change am anytime, just tell me or press Share My Location again.")
            with get_db() as db:
                db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, reply))
            return jsonify({"success": True, "reply": reply, "response": reply, "conversation_id": conversation_id, "latitude": lat, "longitude": lng, "google_maps": maps})
    if any(w in low_msg for w in ["generate image", "create image", "make image", "draw an image", "generate a picture", "create a picture", "generate image of", "midjourney", "design me", "design a"]):
        try:
            img_prompt = message
            for w in ["generate image of", "generate image", "create image of", "create image", "make image of", "make image", "draw", "midjourney", "design me", "design a"]:
                img_prompt = img_prompt.lower().replace(w, "").strip()
            if not img_prompt:
                img_prompt = message
            image_data = generate_flux_image(img_prompt, 4)
            with get_db() as db:
                db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, clean_ai_text(f"Here is your dope image Boss: {img_prompt} - image generated successfully.")))
            return jsonify({"success": True, "reply": "Here is your dope image Boss.", "response": "Here is your dope image Boss.", "conversation_id": conversation_id, "image": image_data, "image_url": image_data})
        except Exception as e:
            err = clean_ai_text(f"Image failed Boss: {str(e)}")
            with get_db() as db:
                db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, err))
            return jsonify({"success": True, "reply": err, "response": err, "conversation_id": conversation_id})
    loc = get_latest_location(user["id"])
    if loc:
        location_text = f"Boss last shared location: Latitude {loc.get('latitude')}, Longitude {loc.get('longitude')}, Label: {loc.get('label','')}, Time: {loc.get('created_at','')}. Google Maps: https://www.google.com/maps?q={loc.get('latitude')},{loc.get('longitude')}."
    else:
        location_text = "No location shared yet. Ask Boss to press Share My Location."
    if any(p in low_msg for p in ["what's my location", "what is my location", "where am i", "where is my location", "my location", "show my location", "track my location", "where am i now", "nibo ni mo wa"]):
        if loc:
            maps = f"https://www.google.com/maps?q={loc.get('latitude')},{loc.get('longitude')}"
            reply = clean_ai_text(f"Boss, you dey here right now: Latitude {loc.get('latitude')}, Longitude {loc.get('longitude')}. Maps: {maps}. If you don move, press Share My Location again.")
            with get_db() as db:
                db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, reply))
            return jsonify({"success": True, "reply": reply, "response": reply, "conversation_id": conversation_id, "latitude": loc.get('latitude'), "longitude": loc.get('longitude'), "google_maps": maps})
        else:
            reply = clean_ai_text("Boss, I never get your location yet. Press Share My Location and Allow, or tell me e.g. I am in Lagos and I go save am.")
            with get_db() as db:
                db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, reply))
            return jsonify({"success": True, "reply": reply, "response": reply, "conversation_id": conversation_id})
    if any(p in low_msg for p in ["who created you", "who built you", "who is your creator", "who made you", "ta ni o da e", "who is your boss"]):
        reply = clean_ai_text("Faruq Sulaimon create me. Na my Boss, my creator and my owner. I be Tabby, built by Faruq Sulaimon.")
        with get_db() as db:
            db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, reply))
        return jsonify({"success": True, "reply": reply, "response": reply, "conversation_id": conversation_id})
    search_results = []
    if voice_mode:
        search_results = live_search(message, max_results=3)
    elif data.get("web_search") is True or data.get("use_search") is True or needs_live_search(message):
        search_results = live_search(message)
    system_prompt = build_system_prompt(user, memories, search_results, location_text, bike_context)
    if voice_mode:
        system_prompt += "\nVOICE MODE ON: Answer like phone call. Short, warm, natural sentences. No long lists. No links spoken. Say lol or haha in words. Keep under 80 words unless user asks for detail."
    ai_messages = [{"role": "system", "content": system_prompt}] + [{"role": r["role"], "content": clean_ai_text(r["content"])} for r in history]
    try:
        answer = clean_ai_text(call_openrouter(ai_messages, temperature=0.7, max_tokens=900 if voice_mode else 1800))
    except Exception:
        logger.exception("Chat failed")
        return json_error("Tabby could not reach its AI service right now. Please try again.", 502)
    with get_db() as db:
        db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (?, 'assistant', ?)", (conversation_id, answer))
        db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,))
    recent = "\n".join(f"{r['role']}: {r['content']}" for r in history[-6:])
    recent += f"\nuser: {message}\nassistant: {answer}"
    extract_automatic_memories(user, recent)
    return jsonify({"success": True, "reply": answer, "response": answer, "conversation_id": conversation_id, "voice_mode": voice_mode, "memories": get_memories(user["id"])})

# ============================================================
# VISION - GLASSES / WATCH - HEY TABBY WHAT AM I LOOKING AT?
# ============================================================
@app.route("/api/vision", methods=["POST"])
@auth_required
def vision_endpoint():
    user = request.current_user
    d = request.get_json(silent=True) or {}
    image_b64 = d.get("image") or d.get("image_base64") or d.get("photo") or ""
    question = d.get("question") or d.get("prompt") or d.get("message") or "Hey Tabby, what am I looking at?"
    device = d.get("device") or "glasses"
    if not image_b64:
        return json_error("Send image as base64. For glasses: {image: 'data:image/jpeg;base64,...', question: 'Hey Tabby what am I looking at?'}")
    try:
        bike_context = get_bike_context_for_user(user)
        answer = call_vision(image_b64, question, user, bike_context)
        with get_db() as db:
            db.execute("INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (?,?,?,?,?)", (user["id"], device, question[:200], "vision", answer[:500]))
        return jsonify({"success": True, "reply": answer, "response": answer, "device": device, "speak": answer, "voice_reply": answer})
    except Exception as e:
        logger.exception("Vision failed")
        return json_error(f"Vision failed Boss: {str(e)}", 502)

@app.route("/api/wake", methods=["POST"])
@auth_required
def wake_word():
    user = request.current_user
    d = request.get_json(silent=True) or {}
    transcript = str(d.get("transcript") or d.get("text") or d.get("message") or "").strip()
    device = d.get("device") or "glasses"
    if not transcript:
        return jsonify({"success": True, "wake": True, "reply": "Yes Boss, I dey here. Wetin you dey look at? Snap am make I see am.", "listening": True})
    if any(w in transcript.lower() for w in ["what am i looking at", "wetin i dey look", "what is this", "what am i seeing"]):
        return jsonify({"success": True, "wake": True, "reply": "Snap am Boss, send the photo make I see am. I dey wait.", "need_image": True, "device": device})
    return jsonify({"success": True, "wake": True, "transcript": transcript, "reply": "I hear you Boss, talk am make I run am.", "device": device})

@app.route("/api/voice-chat", methods=["POST"])
@auth_required
def voice_chat():
    return jsonify({"success": True, "message": "Please call /api/chat with voice_mode true for phone-call style voice chat.", "voice_mode": True})

# ============================================================
# TABBY HOUSE + DEVICES
# ============================================================
@app.route("/api/house/devices", methods=["GET"])
@auth_required
def list_house_devices():
    u = request.current_user
    with get_db() as db:
        rows = db.execute("SELECT * FROM house_devices WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify({"success": True, "devices": [dict(r) for r in rows]})

@app.route("/api/house/devices", methods=["POST"])
@auth_required
def add_house_device():
    u = request.current_user
    d = request.get_json(silent=True) or {}
    name = clean_ai_text(str(d.get("name", "light")).strip())
    room = clean_ai_text(str(d.get("room", "living")).strip())
    typ = clean_ai_text(str(d.get("type", name)).strip())
    with get_db() as db:
        cur = db.execute("INSERT INTO house_devices (user_id, name, room, type, status) VALUES (?,?,?,?, 'off')", (u["id"], name, room, typ))
    return jsonify({"success": True, "device_id": cur.lastrowid}), 201

@app.route("/api/house/devices/<int:did>/command", methods=["POST"])
@auth_required
def house_device_command(did):
    u = request.current_user
    d = request.get_json(silent=True) or {}
    action = str(d.get("action") or d.get("status") or d.get("command") or "on").lower()
    with get_db() as db:
        row = db.execute("SELECT * FROM house_devices WHERE id=? AND user_id=?", (did, u["id"])).fetchone()
        if not row:
            return json_error("Device not found", 404)
        db.execute("UPDATE house_devices SET status=? WHERE id=?", (action, did))
        db.execute("INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (?,?,?,?,?)", (u["id"], dict(row)["name"], f"command {action}", action, f"set to {action}"))
    return jsonify({"success": True, "message": f"Done Boss, {dict(row)['name']} don turn {action}."})

@app.route("/api/tabby/devices", methods=["GET"])
@auth_required
def list_tabby_devices():
    u = request.current_user
    with get_db() as db:
        rows = db.execute("SELECT * FROM tabby_devices WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify({"success": True, "devices": [dict(r) for r in rows]})

@app.route("/api/tabby/register", methods=["POST"])
@auth_required
def register_tabby_device():
    u = request.current_user
    d = request.get_json(silent=True) or {}
    name = clean_ai_text(str(d.get("device_name") or d.get("name") or "Tabby Glasses").strip())
    typ = str(d.get("device_type") or "glasses").lower()
    with get_db() as db:
        cur = db.execute("INSERT INTO tabby_devices (user_id, device_name, device_type, last_seen, battery) VALUES (?,?,?,?,?)", (u["id"], name, typ, current_time(), int(d.get("battery", 100))))
    return jsonify({"success": True, "device_id": cur.lastrowid, "message": f"Tabby {typ} {name} registered Boss."})

@app.route("/api/tabby/logs", methods=["GET"])
@auth_required
def tabby_logs():
    u = request.current_user
    with get_db() as db:
        rows = db.execute("SELECT * FROM tabby_logs WHERE user_id=? ORDER BY id DESC LIMIT 50", (u["id"],)).fetchall()
    return jsonify({"success": True, "logs": [dict(r) for r in rows]})

@app.route("/api/drone/command", methods=["POST"])
@auth_required
def drone_command():
    u = request.current_user
    d = request.get_json(silent=True) or {}
    cmd = str(d.get("command") or "").lower()
    if cmd not in ("takeoff", "land", "check_gate", "patrol", "return_home", "hover"):
        return json_error("Drone command must be takeoff, land, check_gate, patrol, return_home, hover")
    with get_db() as db:
        db.execute("INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (?,?,?,?,?)", (u["id"], "drone", cmd, cmd, f"Drone command {cmd} queued - connect drone hardware to execute"))
    return jsonify({"success": True, "command": cmd, "message": f"Done Boss, drone command {cmd} don queue. Connect drone hardware make e fly."})

# ============================================================
# MEMORY
# ============================================================
@app.route("/api/memory", methods=["GET"])
@app.route("/api/memories", methods=["GET"])
@auth_required
def list_memories():
    return jsonify({"success": True, "memories": get_memories(request.current_user["id"], 100)})

@app.route("/api/memory", methods=["POST"])
@app.route("/api/memories", methods=["POST"])
@auth_required
def add_memory():
    u = request.current_user
    d = request.get_json(silent=True) or {}
    c = clean_ai_text(str(d.get("content", "")).strip())
    if not c:
        return json_error("Memory content required.")
    saved = save_memory(u["id"], c)
    return jsonify({"success": True, "saved": saved, "memories": get_memories(u["id"], 100)})

@app.route("/api/memory/<int:memory_id>", methods=["DELETE"])
@app.route("/api/memories/<int:memory_id>", methods=["DELETE"])
@auth_required
def delete_memory(memory_id):
    u = request.current_user
    with get_db() as db:
        cur = db.execute("DELETE FROM memories WHERE id=? AND user_id=?", (memory_id, u["id"]))
    if cur.rowcount == 0:
        return json_error("Memory not found.", 404)
    return jsonify({"success": True})

# ============================================================
# IMAGE
# ============================================================
@app.route("/image", methods=["POST"])
@app.route("/api/image", methods=["POST"])
@app.route("/generate-image", methods=["POST"])
@app.route("/api/generate-image", methods=["POST"])
@auth_required
def generate_image():
    d = request.get_json(silent=True) or {}
    prompt = clean_ai_text(str(d.get("prompt", "")).strip())
    if not prompt:
        return json_error("Image prompt required.")
    if len(prompt) > 2000:
        return json_error("Image prompt too long.")
    try:
        steps = int(d.get("steps", 4))
    except Exception:
        steps = 4
    try:
        img = generate_flux_image(prompt, steps)
        return jsonify({"success": True, "image_url": img, "image": img, "model": "flux-standard-no-watermark"})
    except RuntimeError as e:
        return json_error(str(e), 503)
    except Exception:
        logger.exception("Image error")
        return json_error("Image generation failed.", 502)

# ============================================================
# AUTOMOTIVE + MOTORBIKE - PREVIOUS BIKE STUFF PRESERVED
# ============================================================
@app.route("/api/automotive/status", methods=["GET"])
def automotive_status():
    return jsonify({"success": True, "system": "Tabby Automotive", "status": "awaiting_hardware_connection", "message": "Automotive system is online, but no live hardware telemetry is connected yet."})

@app.route("/api/motorcycles", methods=["GET"])
@app.route("/api/motorbikes", methods=["GET"])
@auth_required
def list_motorcycles():
    u = request.current_user
    with get_db() as db:
        if u["role"] == "owner":
            rows = db.execute("SELECT * FROM motorcycles ORDER BY id DESC").fetchall()
        else:
            rows = db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["motorbike_id"] = d["id"]
        out.append(d)
    return jsonify({"success": True, "motorcycles": out, "motorbikes": out})

@app.route("/api/motorcycles", methods=["POST"])
@app.route("/api/motorbikes", methods=["POST"])
@owner_required
def create_motorcycle():
    d = request.get_json(silent=True) or {}
    name = clean_ai_text(str(d.get("name", "")).strip())
    model = clean_ai_text(str(d.get("model", "")).strip())
    reg = clean_ai_text(str(d.get("registration_number", "")).strip())
    if not name:
        return json_error("Motorbike name required.")
    assigned = d.get("assigned_user_id")
    if assigned:
        with get_db() as db:
            if not db.execute("SELECT id FROM users WHERE id=?", (assigned,)).fetchone():
                return json_error("Assigned user does not exist.")
    with get_db() as db:
        cur = db.execute("INSERT INTO motorcycles (owner_id,assigned_user_id,name,model,registration_number) VALUES (?,?,?,?,?)", (request.current_user["id"], assigned, name, model, reg))
        db.execute("INSERT OR IGNORE INTO bike_status (motorcycle_id, battery_health, range_km) VALUES (?,?,?)", (cur.lastrowid, 100, 0))
    return jsonify({"success": True, "motorcycle_id": cur.lastrowid, "motorbike_id": cur.lastrowid}), 201

@app.route("/api/motorcycles/<int:motorcycle_id>", methods=["DELETE"])
@app.route("/api/motorbikes/<int:motorcycle_id>", methods=["DELETE"])
@owner_required
def delete_motorcycle(motorcycle_id):
    with get_db() as db:
        db.execute("UPDATE payments SET motorcycle_id=NULL WHERE motorcycle_id=?", (motorcycle_id,))
        cur = db.execute("DELETE FROM motorcycles WHERE id=?", (motorcycle_id,))
    if cur.rowcount == 0:
        return json_error("Motorbike not found.", 404)
    return jsonify({"success": True})

@app.route("/api/fleet/overview", methods=["GET"])
@owner_required
def fleet_overview():
    with get_db() as db:
        rows = db.execute("""
          SELECT m.*, u.name as rider_name, u.email as rider_email,
          bs.battery_health, bs.range_km, bs.total_trip, bs.total_distance, bs.co2_saved, bs.lock_status, bs.access_mode,
          pp.status as plan_status, pp.installments_paid, pp.installments_total, pp.next_due_date, pp.installment_amount
          FROM motorcycles m
          LEFT JOIN users u ON u.id = m.assigned_user_id
          LEFT JOIN bike_status bs ON bs.motorcycle_id = m.id
          LEFT JOIN payment_plans pp ON pp.motorcycle_id = m.id
          ORDER BY m.id DESC
        """).fetchall()
    return jsonify({"success": True, "fleet": [dict(r) for r in rows]})

@app.route("/api/my-bike", methods=["GET"])
@auth_required
def my_bike():
    u = request.current_user
    if u["role"] == "owner":
        return jsonify({"success": True, "message": "Owner uses fleet overview"})
    with get_db() as db:
        m = db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=? ORDER BY id DESC LIMIT 1", (u["id"],)).fetchone()
        if not m:
            return jsonify({"success": True, "bike": None, "message": "No bike assigned yet"})
        m = dict(m)
        st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=?", (m["id"],)).fetchone()
        tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (m["id"],)).fetchone()
        plan = db.execute("SELECT * FROM payment_plans WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (m["id"],)).fetchone()
    return jsonify({"success": True, "bike": m, "status": dict(st) if st else {}, "telemetry": dict(tel) if tel else {}, "plan": dict(plan) if plan else {}})

@app.route("/api/bikes/<int:mid>/command", methods=["POST"])
@owner_required
def bike_command(mid):
    d = request.get_json(silent=True) or {}
    cmd = str(d.get("command", "")).lower()
    if cmd not in ("lock", "unlock", "restrict", "full_access"):
        return json_error("Command must be lock, unlock, restrict or full_access")
    with get_db() as db:
        if not db.execute("SELECT id FROM motorcycles WHERE id=?", (mid,)).fetchone():
            return json_error("Bike not found", 404)
        db.execute("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (?,?,?)", (mid, cmd, request.current_user["id"]))
        db.execute("INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)", (mid,))
        if cmd == "lock":
            db.execute("UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=?", (mid,))
        if cmd == "unlock":
            db.execute("UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=?", (mid,))
        if cmd == "restrict":
            db.execute("UPDATE bike_status SET access_mode='restricted' WHERE motorcycle_id=?", (mid,))
        if cmd == "full_access":
            db.execute("UPDATE bike_status SET access_mode='full' WHERE motorcycle_id=?", (mid,))
    return jsonify({"success": True, "command": cmd, "message": f"Bike {mid} set to {cmd}"})

@app.route("/api/bikes/<int:mid>/assign", methods=["POST"])
@owner_required
def assign_bike(mid):
    d = request.get_json(silent=True) or {}
    uid = d.get("user_id")
    with get_db() as db:
        if not db.execute("SELECT id FROM motorcycles WHERE id=?", (mid,)).fetchone():
            return json_error("Bike not found", 404)
        if not db.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone():
            return json_error("User not found", 404)
        db.execute("UPDATE motorcycles SET assigned_user_id=? WHERE id=?", (uid, mid))
    return jsonify({"success": True, "message": "Bike assigned"})

@app.route("/api/telemetry", methods=["POST"])
@app.route("/api/telemetry/ingest", methods=["POST"])
def ingest_telemetry():
    if not DEVICE_INGEST_KEY:
        return json_error("Device ingestion not configured.", 503)
    if not secrets.compare_digest(request.headers.get("X-Device-Key", ""), DEVICE_INGEST_KEY):
        return json_error("Invalid device key.", 401)
    d = request.get_json(silent=True) or {}
    mid = d.get("motorcycle_id") or d.get("motorbike_id")
    if not mid:
        return json_error("Motorbike ID required.")
    with get_db() as db:
        if not db.execute("SELECT id FROM motorcycles WHERE id=?", (mid,)).fetchone():
            return json_error("Motorbike not found.", 404)
        cur = db.execute("INSERT INTO telemetry (motorcycle_id,latitude,longitude,speed,battery_level,fuel_level,engine_temperature,extra_data) VALUES (?,?,?,?,?,?,?,?)", (mid, d.get("latitude"), d.get("longitude"), d.get("speed"), d.get("battery_level"), d.get("fuel_level"), d.get("engine_temperature"), json.dumps(d.get("extra_data", {}))))
        db.execute("INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)", (mid,))
        if d.get("battery_level") is not None:
            db.execute("UPDATE bike_status SET last_seen=? WHERE motorcycle_id=?", (current_time(), mid))
    return jsonify({"success": True, "telemetry_id": cur.lastrowid}), 201

@app.route("/api/motorcycles/<int:motorcycle_id>/telemetry", methods=["GET"])
@app.route("/api/motorbikes/<int:motorcycle_id>/telemetry", methods=["GET"])
@auth_required
def get_motorcycle_telemetry(motorcycle_id):
    u = request.current_user
    if not user_can_access_motorcycle(u, motorcycle_id):
        return json_error("Motorbike not found or access denied.", 404)
    try:
        limit = int(request.args.get("limit", 50))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 200))
    with get_db() as db:
        rows = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT ?", (motorcycle_id, limit)).fetchall()
    out = []
    for r in rows:
        it = dict(r)
        try:
            it["extra_data"] = json.loads(it["extra_data"] or "{}")
        except Exception:
            it["extra_data"] = {}
        out.append(it)
    return jsonify({"success": True, "motorcycle_id": motorcycle_id, "motorbike_id": motorcycle_id, "latest": out[0] if out else None, "telemetry": out})

@app.route("/api/location/update", methods=["POST"])
@auth_required
def update_location():
    u = request.current_user
    d = request.get_json(silent=True) or {}
    lat = d.get("latitude")
    lng = d.get("longitude")
    if (lat is None or lng is None) and (d.get("city") or d.get("location") or d.get("place")):
        parsed = parse_location_from_text(str(d.get("city") or d.get("location") or d.get("place")))
        if parsed:
            lat, lng, _ = parsed
    if lat is None or lng is None:
        return json_error("Latitude and longitude required. Or send {city: 'Lagos'}")
    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        return json_error("Invalid coordinates.")
    label = d.get("label") or d.get("city") or "phone_location"
    save_location(u["id"], lat, lng, label=label, accuracy=d.get("accuracy"), speed=d.get("speed"))
    maps = f"https://www.google.com/maps?q={lat},{lng}"
    return jsonify({"success": True, "message": clean_ai_text(f"Location updated Boss to {lat}, {lng}. You can change it anytime."), "latitude": lat, "longitude": lng, "google_maps": maps})

@app.route("/api/location/latest", methods=["GET"])
@auth_required
def latest_location():
    loc = get_latest_location(request.current_user["id"])
    if not loc:
        return jsonify({"success": True, "location": None, "message": "No location yet. Press Share My Location."})
    loc["google_maps"] = f"https://www.google.com/maps?q={loc.get('latitude')},{loc.get('longitude')}"
    return jsonify({"success": True, "location": loc})

@app.route("/api/location/track", methods=["GET"])
@auth_required
def track_location():
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    if lat and lng:
        return jsonify({"success": True, "latitude": lat, "longitude": lng, "google_maps": f"https://www.google.com/maps?q={lat},{lng}"})
    return latest_location()

@app.route("/api/location/clear", methods=["POST"])
@app.route("/api/location/delete", methods=["POST"])
@auth_required
def clear_location():
    u = request.current_user
    with get_db() as db:
        db.execute("DELETE FROM locations WHERE user_id=?", (u["id"],))
    return jsonify({"success": True, "message": "Location cleared Boss. Share new one anytime."})

@app.route("/api/location/change", methods=["POST"])
@auth_required
def change_location():
    return update_location()

@app.route("/api/payments", methods=["GET"])
@auth_required
def list_payments():
    u = request.current_user
    with get_db() as db:
        if u["role"] == "owner":
            rows = db.execute("SELECT * FROM payments ORDER BY id DESC").fetchall()
        else:
            rows = db.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify({"success": True, "payments": [dict(r) for r in rows]})

@app.route("/api/payments", methods=["POST"])
@auth_required
def create_payment_record():
    u = request.current_user
    d = request.get_json(silent=True) or {}
    try:
        amount = float(d.get("amount"))
    except Exception:
        return json_error("Valid amount required.")
    if amount <= 0:
        return json_error("Amount must be greater than 0.")
    mid = d.get("motorcycle_id") or d.get("motorbike_id")
    if mid and not user_can_access_motorcycle(u, mid):
        return json_error("Motorbike access denied.", 403)
    ref = secrets.token_urlsafe(18)
    with get_db() as db:
        cur = db.execute("INSERT INTO payments (user_id,motorcycle_id,amount,currency,status,reference) VALUES (?,?,?,?, 'pending', ?)", (u["id"], mid, amount, str(d.get("currency", "NGN")).upper(), ref))
    return jsonify({"success": True, "payment_id": cur.lastrowid, "reference": ref, "status": "pending", "message": "Payment record created. No payment processed."}), 201

@app.route("/api/payment-plans", methods=["GET"])
@auth_required
def list_payment_plans():
    u = request.current_user
    with get_db() as db:
        if u["role"] == "owner":
            rows = db.execute("SELECT * FROM payment_plans ORDER BY id DESC").fetchall()
        else:
            rows = db.execute("SELECT * FROM payment_plans WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify({"success": True, "plans": [dict(r) for r in rows]})

@app.route("/api/payment-plans", methods=["POST"])
@owner_required
def create_plan():
    d = request.get_json(silent=True) or {}
    try:
        total = float(d.get("total_amount"))
        inst = float(d.get("installment_amount"))
        tot = int(d.get("installments_total"))
    except Exception:
        return json_error("Valid total, installment amount and total installments required.")
    with get_db() as db:
        cur = db.execute("INSERT INTO payment_plans (user_id, motorcycle_id, total_amount, deposit, installment_amount, installments_total, next_due_date) VALUES (?,?,?,?,?,?,?)", (d.get("user_id"), d.get("motorcycle_id"), total, float(d.get("deposit", 0)), inst, tot, d.get("next_due_date")))
    return jsonify({"success": True, "plan_id": cur.lastrowid}), 201

@app.route("/api/payment-plans/<int:plan_id>/pay", methods=["POST"])
@auth_required
def pay_installment(plan_id):
    u = request.current_user
    with get_db() as db:
        plan = db.execute("SELECT * FROM payment_plans WHERE id=?", (plan_id,)).fetchone()
        if not plan:
            return json_error("Plan not found", 404)
        if u["role"] != "owner" and plan["user_id"] != u["id"]:
            return json_error("Access denied", 403)
        new_paid = plan["installments_paid"] + 1
        new_status = "completed" if new_paid >= plan["installments_total"] else "active"
        db.execute("UPDATE payment_plans SET installments_paid=?, status=? WHERE id=?", (new_paid, new_status, plan_id))
        db.execute("INSERT INTO payments (user_id, motorcycle_id, amount, currency, status, reference) VALUES (?,?,?,?, 'completed', ?)", (plan["user_id"], plan["motorcycle_id"], plan["installment_amount"], "NGN", secrets.token_urlsafe(18)))
        if new_status == "completed":
            db.execute("UPDATE bike_status SET access_mode='full' WHERE motorcycle_id=?", (plan["motorcycle_id"],))
    return jsonify({"success": True, "installments_paid": new_paid, "status": new_status, "message": "Installment recorded"})

@app.route("/api/customers", methods=["GET"])
@owner_required
def list_customers():
    with get_db() as db:
        rows = db.execute("SELECT id,name,email,role,created_at FROM users ORDER BY id DESC").fetchall()
    return jsonify({"success": True, "customers": [dict(r) for r in rows]})

@app.route("/api/customers/<int:user_id>/role", methods=["PATCH"])
@owner_required
def update_customer_role(user_id):
    d = request.get_json(silent=True) or {}
    role = str(d.get("role", "")).strip().lower()
    if role not in ("owner", "customer"):
        return json_error("Role must be owner or customer.")
    if user_id == request.current_user["id"] and role != "owner":
        return json_error("You cannot remove your own owner access.")
    with get_db() as db:
        cur = db.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    if cur.rowcount == 0:
        return json_error("User not found.", 404)
    return jsonify({"success": True, "role": role})

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Endpoint not found."}), 404

@app.errorhandler(500)
def server_error(e):
    logger.exception("Server error")
    return jsonify({"success": False, "error": "Unexpected server error."}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)