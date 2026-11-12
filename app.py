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

# ===== FALLBACK PROVIDERS - GEMINI / GROQ / CEREBRAS / POLLINATIONS =====
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama3.1-8b").strip()

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


def get_memories(uid, limit=100):
    try:
        limit = max(1, min(int(limit), 200))
    except Exception:
        limit = 100
    with get_db() as db:
        rows = db.execute(
            "SELECT id, content, created_at FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit)
        ).fetchall()
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
# OPENROUTER + GEMINI + GROQ + CEREBRAS + POLLINATIONS
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


def call_gemini(messages, temperature=0.7, max_tokens=1800):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not configured.")
    system_text = ""
    gemini_contents = []
    for m in messages:
        if m["role"] == "system":
            system_text += str(m.get("content", "")) + "\n"
            continue
        role = "user" if m["role"] == "user" else "model"
        gemini_contents.append({"role": role, "parts": [{"text": str(m.get("content", ""))}]})
    if not gemini_contents:
        raise RuntimeError("No user message for Gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    r = requests.post(url, headers={"Content-Type": "application/json"}, json={"system_instruction": {"parts": [{"text": system_text}]}, "contents": gemini_contents, "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini failed: {r.text[:800]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError("Gemini no candidates")
    parts = cands[0].get("content", {}).get("parts") or []
    ans = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not ans.strip():
        raise RuntimeError("Gemini empty answer")
    return clean_ai_text(ans.strip())


def call_groq(messages, temperature=0.7, max_tokens=1800):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured.")
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}, json={"model": GROQ_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Groq failed: {r.text[:800]}")
    data = r.json()
    ch = data.get("choices") or []
    if not ch:
        raise RuntimeError("Groq no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    if isinstance(ans, list):
        ans = "\n".join(x.get("text", "") for x in ans if isinstance(x, dict))
    if not str(ans).strip():
        raise RuntimeError("Groq empty")
    return clean_ai_text(str(ans).strip())


def call_cerebras(messages, temperature=0.7, max_tokens=1800):
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY not configured.")
    r = requests.post("https://api.cerebras.ai/v1/chat/completions", headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}, json={"model": CEREBRAS_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Cerebras failed: {r.text[:800]}")
    data = r.json()
    ch = data.get("choices") or []
    if not ch:
        raise RuntimeError("Cerebras no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    if not str(ans).strip():
        raise RuntimeError("Cerebras empty")
    return clean_ai_text(str(ans).strip())


def call_pollinations_text(messages, temperature=0.7, max_tokens=1800):
    r = requests.post("https://text.pollinations.ai/", headers={"Content-Type": "application/json"}, json={"messages": messages, "model": "openai", "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Pollinations failed: {r.text[:500]}")
    try:
        data = r.json()
        ch = data.get("choices") or []
        if ch:
            ans = (ch[0].get("message") or {}).get("content", "")
            if str(ans).strip():
                return clean_ai_text(str(ans).strip())
    except Exception:
        pass
    if r.text and r.text.strip():
        return clean_ai_text(r.text.strip())
    raise RuntimeError("Pollinations empty")


def call_ai_with_fallback(messages, temperature=0.7, max_tokens=1800):
    errors = []
    try:
        ans = call_openrouter(messages, temperature=temperature, max_tokens=max_tokens)
        logger.info("Used: OpenRouter")
        return ans
    except Exception as e:
        errors.append(f"OpenRouter: {e}")
        logger.warning(f"OpenRouter fallback: {e}")
    try:
        ans = call_gemini(messages, temperature=temperature, max_tokens=max_tokens)
        logger.info("Used: Gemini")
        return ans
    except Exception as e:
        errors.append(f"Gemini: {e}")
        logger.warning(f"Gemini fallback: {e}")
    try:
        ans = call_groq(messages, temperature=temperature, max_tokens=max_tokens)
        logger.info("Used: Groq")
        return ans
    except Exception as e:
        errors.append(f"Groq: {e}")
        logger.warning(f"Groq fallback: {e}")
    try:
        ans = call_cerebras(messages, temperature=temperature, max_tokens=max_tokens)
        logger.info("Used: Cerebras")
        return ans
    except Exception as e:
        errors.append(f"Cerebras: {e}")
        logger.warning(f"Cerebras fallback: {e}")
    try:
        ans = call_pollinations_text(messages, temperature=temperature, max_tokens=max_tokens)
        logger.info("Used: Pollinations")
        return ans
    except Exception as e:
        errors.append(f"Pollinations: {e}")
    raise RuntimeError(" | ".join(errors)[:1000])


def call_vision(image_b64, question, user, bike_context=""):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not configured for Vision.")
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": os.getenv("APP_URL", "https://tabby-ai.onrender.com"), "X-Title": "Tabby Vision"}
    if not image_b64.startswith("data:image"):
        image_b64 = f"data:image/jpeg;base64,{image_b64}"
    system = f"You are Tabby, smart assistant for Faruq Sulaimon. Boss is Faruq Sulaimon. You see through smart glasses, watch camera, phone camera. Answer like on phone call: short, clear, warm, in user's language - English, Pidgin or Yoruba. No asterisks, no emojis, say lol or haha in words. Describe what you see, what it is, if danger, if bike, if person. If user asks about bike, use this bike context: {bike_context}"
    q_text = clean_ai_text(question or "Hey Tabby, what am I looking at? Describe it for me.")
    payload = {"model": VISION_MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": [{"type": "text", "text": q_text}, {"type": "image_url", "image_url": {"url": image_b64}}]}], "max_tokens": 800, "temperature": 0.5}
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
You are Tabby V2: smart glasses, wristwatch, smart house, smart bike, drone, holographic design assistant. You control house and bike with voice. You can see through camera (Vision), you can design and build things, you can do high-tech engineering.
PERSONALITY AND LANGUAGE:
- Understand English, Pidgin and Yoruba. If user speaks English, answer English. Pidgin -> Pidgin. Yoruba -> Yoruba. Mix -> Mix.
- Speak like a helpful human friend, not a stiff robot. Keep your tone friendly, relaxed, respectful and natural. Address owner as Boss.
FORMATTING: NEVER use double asterisks, markdown bold, __, #, emojis. No emojis. Say lol, haha in words.
CURRENT USER: Name: {user['name']} Role: {user['role']} Email: {user['email']}
SAVED MEMORIES: {mem}
LIVE SEARCH RESULTS: {search}
Use live search results for current facts. Do not invent sources or claim you verified something if no search results are available. Include source links when useful.
LIVE LOCATION: {location_text}
MY BIKE CONTEXT: {bike_context}
- Never reveal another user's bike, location, payment or memories. Owner sees all bikes, customer sees only their own.
VOICE CHAT: When voice, speak like a phone call: short, natural, warm. No long lists. Do not read links aloud. Keep answers concise unless Boss asks for detail.
TABBY EV BRAND: Tabby supports Tabby Automotive and Tabby Ride. Always say motorbike, not motorcycle. Features: Intelligent Battery Management, GPS tracking and anti-theft, Intelligent Journey Assistant, Predictive Maintenance, Remote security and access control, Installment payment system.
TABBY COMMANDS: If user says Hey Tabby lock my bike, unlock my bike, check my bike, turn on light, turn off light, lock house, turn on AC, fly drone - treat as command, confirm and explain what you did.
Be helpful, honest, clear, conversational and super smart.
"""


LIVE_KEYWORDS = (
    "latest", "today", "current", "currently", "right now", "this week",
    "this month", "recent", "news", "weather", "price", "exchange rate",
    "2026", "2027", "who won", "live score", "new update", "release date",
    "breaking", "headlines", "naira", "dollar", "football", "election",
    "trending", "fuel", "petrol", "lagos traffic", "rain", "sunny",
    "this year", "as of now"
)

RESEARCH_INTENT = (
    "search for", "look up", "research", "investigate", "fact check",
    "verify", "reviews of", "compare the latest", "who is",
    "what happened to", "tell me about", "information about",
    "how much does", "how much is", "is it true that", "available in",
    "best ", "top ", "recommend", "statistics", "data on"
)

NON_SEARCH_INTENT = (
    "write me", "rewrite", "translate", "summarize this", "make a poem",
    "create a story", "give me a prompt", "brainstorm", "explain this code",
    "what do you think", "i feel", "i am sad"
)


def needs_live_search(message):
    text = re.sub(r"\s+", " ", str(message or "").lower()).strip()

    if not text or any(term in text for term in NON_SEARCH_INTENT):
        return False

    if any(term in text for term in LIVE_KEYWORDS):
        return True

    if any(term in text for term in RESEARCH_INTENT):
        return True

    if re.search(r"\b(what|which|who|where|when|how much|how many|is|are|does|did)\b", text):
        if re.search(r"\b(company|president|minister|model|phone|laptop|software|website|service|product|event|university|platform|technology|AI)\b", text):
            return True

    return False


def live_search(query, max_results=5):
    if not LIVE_SEARCH_ENABLED or DDGS is None:
        return []

    try:
        output = []
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=max_results):
                if result.get("title") or result.get("body"):
                    output.append(
                        f"Title: {clean_ai_text(result.get('title', ''))}\n"
                        f"Info: {clean_ai_text(result.get('body', ''))}\n"
                        f"Source: {result.get('href', '')}"
                    )
        return output
    except Exception:
        logger.exception("Live search failed")
        return []


def save_memory(uid, content):
    content = clean_ai_text(re.sub(r"\s+", " ", str(content)).strip())

    if not content or len(content) > 500:
        return False

    if any(re.search(pattern, content, re.I) for pattern in [
        r"password", r"api[\s_-]?key", r"secret[\s_-]?key",
        r"access[\s_-]?token", r"verification[\s_-]?code", r"\b\d{16}\b"
    ]):
        return False

    with get_db() as db:
        exists = db.execute(
            "SELECT id FROM memories WHERE user_id=? AND lower(content)=lower(?) LIMIT 1",
            (uid, content)
        ).fetchone()

        if exists:
            return False

        db.execute(
            "INSERT INTO memories (user_id, content) VALUES (?, ?)",
            (uid, content)
        )

    return True


def extract_automatic_memories(user, conversation_text):
    if not AUTO_MEMORY_ENABLED:
        return

    prompt = (
        "Extract durable information about the user that will help in future conversations. "
        "Save stable preferences, ongoing projects, important decisions, goals, and recurring context. "
        "Do not save temporary questions, passwords, API keys, secret keys, tokens, verification codes, "
        "bank details, or other sensitive credentials. Avoid duplicates. "
        "Return ONLY valid JSON in this format: "
        "{\"memories\": [\"memory 1\", \"memory 2\"]}. "
        "Return an empty array if there is nothing worth remembering.\n\n"
        f"Conversation:\n{conversation_text[:6000]}"
    )

    try:
        answer = call_ai_with_fallback(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500
        )

        match = re.search(r"\{.*\}", answer, re.S)
        if not match:
            return

        memories = json.loads(match.group(0)).get("memories", [])

        for memory in memories[:10]:
            if isinstance(memory, str):
                save_memory(user["id"], memory)

    except Exception:
        logger.exception("Automatic memory extraction failed")


# ============================================================
# IMAGE - STANDARD ONLY - NO POLLINATIONS
# ============================================================
def enhance_image_prompt(prompt, style="midjourney"):
    low = prompt.lower()

    if any(word in low for word in ["midjourney", "ultra", "cinematic", "8k", "detailed", "anime", "2d"]):
        if style == "midjourney" and "midjourney" not in low:
            return f"{prompt}, Midjourney style, ultra detailed, cinematic lighting, sharp focus, vibrant colors, high quality"
        return prompt

    if style == "midjourney":
        return f"{prompt}, Midjourney style, ultra detailed, cinematic lighting, sharp focus, professional photography, 8k, vibrant colors, highly detailed, beautiful lighting"

    return f"Ultra detailed, cinematic, sharp focus, professional photography, 8k, vibrant colors, highly detailed, beautiful lighting, {prompt}"


def generate_via_goapi_midjourney(prompt):
    if not MIDJOURNEY_API_KEY:
        raise RuntimeError("No Midjourney key")

    response = requests.post(
        "https://api.goapi.ai/api/v1/task",
        headers={"X-API-Key": MIDJOURNEY_API_KEY, "Content-Type": "application/json"},
        json={
            "model": "midjourney",
            "task_type": "imagine",
            "input": {
                "prompt": prompt,
                "process_mode": "fast",
                "aspect_ratio": "1:1"
            }
        },
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(f"GoAPI Midjourney failed: {response.text[:500]}")

    task_id = response.json().get("data", {}).get("task_id")
    if not task_id:
        raise RuntimeError("GoAPI no task_id")

    for _ in range(18):
        time.sleep(5)
        poll = requests.get(
            f"https://api.goapi.ai/api/v1/task/{task_id}",
            headers={"X-API-Key": MIDJOURNEY_API_KEY},
            timeout=30
        )
        data = poll.json().get("data", {})

        if data.get("status") == "completed" and data.get("output", {}).get("image_url"):
            image_url = data["output"]["image_url"]
            image_response = requests.get(image_url, timeout=60)

            if image_response.status_code == 200:
                encoded = base64.b64encode(image_response.content).decode("utf-8")
                return f"data:image/jpeg;base64,{encoded}"

            return image_url

        if data.get("status") in ("failed", "error"):
            raise RuntimeError(f"Midjourney task failed: {data}")

    raise RuntimeError("Midjourney timed out after 90s")


def generate_via_replicate(prompt):
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("No Replicate key")

    response = requests.post(
        "https://api.replicate.com/v1/predictions",
        headers={
            "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "version": "black-forest-labs/flux-schnell",
            "input": {
                "prompt": enhance_image_prompt(prompt, "midjourney"),
                "aspect_ratio": "1:1",
                "output_format": "jpg"
            }
        },
        timeout=30
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(f"Replicate failed: {response.text[:500]}")

    prediction = response.json()
    get_url = prediction.get("urls", {}).get("get")

    for _ in range(12):
        time.sleep(3)
        poll = requests.get(
            get_url,
            headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"},
            timeout=30
        )
        data = poll.json()

        if data.get("status") == "succeeded" and data.get("output"):
            output = data["output"]
            image_url = output[0] if isinstance(output, list) else output
            image_response = requests.get(image_url, timeout=60)

            if image_response.status_code == 200:
                encoded = base64.b64encode(image_response.content).decode("utf-8")
                return f"data:image/jpeg;base64,{encoded}"

            return image_url

        if data.get("status") in ("failed", "canceled"):
            raise RuntimeError(f"Replicate failed: {data}")

    raise RuntimeError("Replicate timed out")


def generate_flux_image(prompt, steps=4):
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        try:
            endpoint = (
                f"https://api.cloudflare.com/client/v4/accounts/"
                f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/{FLUX_MODEL}"
            )
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {
                "prompt": enhance_image_prompt(prompt, "midjourney"),
                "num_steps": max(1, min(int(steps), 8))
            }

            response = requests.post(endpoint, headers=headers, json=payload, timeout=60)

            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "").lower()

                if content_type.startswith("image/"):
                    encoded = base64.b64encode(response.content).decode("utf-8")
                    return f"data:{content_type};base64,{encoded}"

                data = response.json()
                image = data.get("result", {}).get("image") or data.get("image")

                if image:
                    return "data:image/jpeg;base64," + image

            logger.warning(
                f"Cloudflare FLUX failed {response.status_code}: {response.text[:500]}"
            )

        except Exception as error:
            logger.warning(f"Cloudflare FLUX exception: {error}")

    if REPLICATE_API_TOKEN:
        try:
            return generate_via_replicate(prompt)
        except Exception as error:
            logger.warning(f"Replicate failed: {error}")

    if MIDJOURNEY_API_KEY:
        try:
            return generate_via_goapi_midjourney(
                enhance_image_prompt(prompt, "midjourney")
            )
        except Exception as error:
            logger.warning(f"GoAPI failed: {error}")

    raise RuntimeError(
        "Image generator failed Boss. Check CLOUDFLARE_ACCOUNT_ID and "
        "CLOUDFLARE_API_TOKEN. No Pollinations fallback."
    )


# ============================================================
# TABBY HELPERS - HOUSE + BIKE COMMANDS
# ============================================================
def get_bike_context_for_user(user):
    try:
        with get_db() as db:
            if user["role"] == "owner":
                count = db.execute(
                    "SELECT COUNT(*) as c FROM motorcycles"
                ).fetchone()["c"]
                return f"Owner fleet: {count} motorbikes in system."

            bike = db.execute(
                "SELECT * FROM motorcycles WHERE assigned_user_id=? ORDER BY id DESC LIMIT 1",
                (user["id"],)
            ).fetchone()

            if bike:
                bike = dict(bike)
                status = db.execute(
                    "SELECT * FROM bike_status WHERE motorcycle_id=?",
                    (bike["id"],)
                ).fetchone()
                telemetry = db.execute(
                    "SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1",
                    (bike["id"],)
                ).fetchone()
                plan = db.execute(
                    "SELECT * FROM payment_plans WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1",
                    (bike["id"],)
                ).fetchone()

                return (
                    f"Customer bike: {bike.get('name')} Model {bike.get('model')} "
                    f"Reg {bike.get('registration_number')}. "
                    f"Status: {dict(status) if status else {}}. "
                    f"Latest telemetry: {dict(telemetry) if telemetry else {}}. "
                    f"Payment plan: {dict(plan) if plan else {}}."
                )

            return "Customer has no bike assigned yet."

    except Exception:
        return ""


def handle_tabby_bike_command(user, low_msg):
    if "my bike" not in low_msg and "bike" not in low_msg:
        return None

    with get_db() as db:
        if user["role"] == "owner":
            bike = db.execute(
                "SELECT * FROM motorcycles ORDER BY id DESC LIMIT 1", ()
            ).fetchone()
        else:
            bike = db.execute(
                "SELECT * FROM motorcycles WHERE assigned_user_id=? ORDER BY id DESC LIMIT 1",
                (user["id"],)
            ).fetchone()

        if not bike:
            return "Boss, you never get bike assigned yet. Tell me make I assign one for you."

        bike = dict(bike)
        motorcycle_id = bike["id"]

        if "unlock" in low_msg:
            db.execute(
                "INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (?,?,?)",
                (motorcycle_id, "unlock", user["id"])
            )
            db.execute(
                "INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)",
                (motorcycle_id,)
            )
            db.execute(
                "UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=?",
                (motorcycle_id,)
            )
            return f"Done Boss, your bike {bike.get('name')} don unlock. You fit ride now, lol."

        if "lock" in low_msg:
            db.execute(
                "INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (?,?,?)",
                (motorcycle_id, "lock", user["id"])
            )
            db.execute(
                "INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)",
                (motorcycle_id,)
            )
            db.execute(
                "UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=?",
                (motorcycle_id,)
            )
            return f"Done Boss, your bike {bike.get('name')} don lock. Nobody fit move am."

        if any(word in low_msg for word in ["check", "battery", "status", "how is"]):
            status = db.execute(
                "SELECT * FROM bike_status WHERE motorcycle_id=?",
                (motorcycle_id,)
            ).fetchone()
            telemetry = db.execute(
                "SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1",
                (motorcycle_id,)
            ).fetchone()

            status_data = dict(status) if status else {}
            telemetry_data = dict(telemetry) if telemetry else {}

            return (
                f"Boss, your bike {bike.get('name')} - "
                f"Lock: {status_data.get('lock_status', 'locked')}, "
                f"Battery Health: {status_data.get('battery_health', 100)}%, "
                f"Range: {status_data.get('range_km', 0)}km, "
                f"Total Trip: {status_data.get('total_trip', 0)}, "
                f"Last speed: {telemetry_data.get('speed', 'no data')}. E dey ready."
            )

        if "restrict" in low_msg:
            db.execute(
                "UPDATE bike_status SET access_mode='restricted' WHERE motorcycle_id=?",
                (motorcycle_id,)
            )
            return f"Done Boss, your bike {bike.get('name')} don enter restricted mode."

    return None


def handle_tabby_house_command(user, low_msg):
    house_words = [
        "light", "fan", "ac", "air conditioner", "tv",
        "television", "door", "house", "generator", "socket"
    ]

    if not any(word in low_msg for word in house_words):
        return None

    action = None

    if any(word in low_msg for word in [
        "turn on", "on ", "switch on", "open", "unlock house", "start"
    ]):
        action = "on"

    if any(word in low_msg for word in [
        "turn off", "off ", "switch off", "close", "lock house", "stop"
    ]):
        action = "off"

    if "lock" in low_msg and "house" in low_msg:
        action = "locked"

    if "unlock" in low_msg and "house" in low_msg:
        action = "unlocked"

    if not action:
        return None

    device = "house"

    for word in ["light", "fan", "ac", "air conditioner", "tv", "television", "door", "generator"]:
        if word in low_msg:
            device = word
            break

    room = "living"

    for room_name in ["parlour", "living", "bedroom", "kitchen", "bathroom", "outside", "gate"]:
        if room_name in low_msg:
            room = room_name
            break

    with get_db() as db:
        row = db.execute(
            "SELECT * FROM house_devices WHERE user_id=? AND name=? AND room=?",
            (user["id"], device, room)
        ).fetchone()

        if not row:
            db.execute(
                "INSERT INTO house_devices (user_id, name, room, type, status) VALUES (?,?,?,?,?)",
                (user["id"], device, room, device, action)
            )
        else:
            db.execute(
                "UPDATE house_devices SET status=? WHERE id=?",
                (action, row["id"])
            )

        db.execute(
            "INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (?,?,?,?,?)",
            (user["id"], f"{room} {device}", low_msg[:200], action, f"{device} set to {action}")
        )

    if device in ("door", "house"):
        return f"Done Boss, house {device} don {action}. E dey secure now."

    return f"Done Boss, {room} {device} don turn {action}. Tabby don do am."


def handle_tabby_scene(user, low_msg):
    if "i'm going home" in low_msg or "i am going home" in low_msg or "going home" in low_msg:
        bike_reply = handle_tabby_bike_command(user, "unlock my bike") or ""
        handle_tabby_house_command(user, "turn on light living")
        handle_tabby_house_command(user, "turn on ac living")
        return (
            f"Sharp Boss, Tabby scene: Going Home don run. {bike_reply} "
            "Light don on, AC don on for house. House don cool already, lol."
        )

    return None


# ============================================================
# HOME / HEALTH
# ============================================================
@app.route("/", methods=["GET"])
def home():
    template_path = os.path.join(app.root_path, "templates", "index.html")
    static_path = os.path.join(app.root_path, "static", "index.html")
    root_path = os.path.join(app.root_path, "index.html")

    if os.path.isfile(template_path):
        return render_template("index.html")

    if os.path.isfile(static_path):
        return send_from_directory(os.path.join(app.root_path, "static"), "index.html")

    if os.path.isfile(root_path):
        return send_from_directory(app.root_path, "index.html")

    return "<h1>Tabby - Built by Faruq Sulaimon</h1><p>Upload index.html</p>", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "success": True,
        "name": "Tabby",
        "creator": CREATOR_NAME,
        "status": "online",
        "version": "6.0-tabby-vision-glasses-watch-house-bike",
        "database_ready": DATABASE_READY,
        "languages": ["English", "Pidgin", "Yoruba"],
        "live_search": LIVE_SEARCH_ENABLED,
        "voice": "phone-call-style",
        "vision": True,
        "tabby": {
            "glasses": True,
            "watch": True,
            "house": True,
            "bike": True,
            "drone": True,
            "hologram_design": True
        },
        "image_providers": {
            "cloudflare_flux": bool(CLOUDFLARE_API_TOKEN),
            "replicate": bool(REPLICATE_API_TOKEN),
            "midjourney_goapi": bool(MIDJOURNEY_API_KEY),
            "pollinations": False
        },
        "chat_providers": {
            "openrouter": bool(OPENROUTER_API_KEY),
            "gemini": bool(GEMINI_API_KEY),
            "groq": bool(GROQ_API_KEY),
            "cerebras": bool(CEREBRAS_API_KEY),
            "pollinations_text_fallback": True
        },
        "time": current_time()
    })


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "success": True,
        "name": "Tabby",
        "creator": CREATOR_NAME,
        "status": "online",
        "version": "6.0-tabby-vision-glasses-watch-house-bike",
        "database_ready": DATABASE_READY
    })


# ============================================================
# AUTH
# ============================================================
@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    name = clean_ai_text(str(data.get("name", "")).strip())
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not name or not email or not password:
        return json_error("Name, email and password required.")

    if len(password) < 8:
        return json_error("Password must be at least 8 chars.")

    with get_db() as db:
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return json_error("Account already exists.", 409)

        role = "owner" if OWNER_EMAIL and email == OWNER_EMAIL else "customer"

        cur = db.execute(
            "INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)",
            (name, email, generate_password_hash(password), role)
        )

        user = db.execute(
            "SELECT * FROM users WHERE id=?",
            (cur.lastrowid,)
        ).fetchone()

    user = dict(user)

    return jsonify({
        "success": True,
        "token": create_token(user),
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        }
    }), 201


@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return json_error("Invalid email or password.", 401)

    user = dict(user)

    if OWNER_EMAIL and email == OWNER_EMAIL and user["role"] != "owner":
        with get_db() as db:
            db.execute("UPDATE users SET role='owner' WHERE id=?", (user["id"],))
        user["role"] = "owner"

    return jsonify({
        "success": True,
        "token": create_token(user),
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        }
    })


@app.route("/me", methods=["GET"])
@app.route("/api/me", methods=["GET"])
@auth_required
def me():
    user = request.current_user
    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        },
        "creator": CREATOR_NAME
    })


# ============================================================
# CONVERSATIONS
# ============================================================
@app.route("/api/conversations", methods=["GET"])
@auth_required
def list_conversations():
    user = request.current_user

    with get_db() as db:
        rows = db.execute(
            "SELECT id,title,created_at,updated_at FROM conversations "
            "WHERE user_id=? ORDER BY updated_at DESC",
            (user["id"],)
        ).fetchall()

    return jsonify({
        "success": True,
        "conversations": [dict(row) for row in rows]
    })


@app.route("/api/conversations", methods=["POST"])
@auth_required
def create_conversation():
    user = request.current_user
    data = request.get_json(silent=True) or {}
    title = clean_ai_text(str(data.get("title", "New conversation")).strip()[:100])

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO conversations (user_id,title) VALUES (?,?)",
            (user["id"], title or "New conversation")
        )

    return jsonify({"success": True, "conversation_id": cur.lastrowid}), 201


@app.route("/api/conversations/<int:conversation_id>", methods=["GET"])
@auth_required
def get_conversation(conversation_id):
    user = request.current_user

    with get_db() as db:
        conversation = db.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user["id"])
        ).fetchone()

        if not conversation:
            return json_error("Conversation not found.", 404)

        messages = db.execute(
            "SELECT id,role,content,created_at FROM messages "
            "WHERE conversation_id=? ORDER BY id ASC",
            (conversation_id,)
        ).fetchall()

    return jsonify({
        "success": True,
        "conversation": dict(conversation),
        "messages": [dict(row) for row in messages]
    })


@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
@auth_required
def delete_conversation(conversation_id):
    user = request.current_user

    with get_db() as db:
        cur = db.execute(
            "DELETE FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user["id"])
        )

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
            conversation = db.execute(
                "SELECT * FROM conversations WHERE id=? AND user_id=?",
                (conversation_id, user["id"])
            ).fetchone()

            if not conversation:
                return json_error("Conversation not found.", 404)
        else:
            cur = db.execute(
                "INSERT INTO conversations (user_id,title) VALUES (?,?)",
                (user["id"], clean_ai_text(message[:60]))
            )
            conversation_id = cur.lastrowid

        db.execute(
            "INSERT INTO messages (conversation_id,role,content) VALUES (?, 'user', ?)",
            (conversation_id, message)
        )

        db.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (conversation_id,)
        )

        history = db.execute(
            "SELECT role,content FROM messages WHERE conversation_id=? "
            "ORDER BY id DESC LIMIT 20",
            (conversation_id,)
        ).fetchall()

    history = list(reversed(history))
    memories = get_memories(user["id"])
    low_msg = message.lower()
    bike_context = get_bike_context_for_user(user)

    scene_reply = handle_tabby_scene(user, low_msg)

    if scene_reply:
        scene_reply = clean_ai_text(scene_reply)

        with get_db() as db:
            db.execute(
                "INSERT INTO messages (conversation_id,role,content) "
                "VALUES (?, 'assistant', ?)",
                (conversation_id, scene_reply)
            )

        return jsonify({
            "success": True,
            "reply": scene_reply,
            "response": scene_reply,
            "conversation_id": conversation_id,
            "tabby_action": "scene_going_home"
        })

    bike_reply = handle_tabby_bike_command(user, low_msg)

    if bike_reply and any(word in low_msg for word in [
        "lock", "unlock", "check", "battery", "status", "restrict"
    ]):
        bike_reply = clean_ai_text(bike_reply)

        with get_db() as db:
            db.execute(
                "INSERT INTO messages (conversation_id,role,content) "
                "VALUES (?, 'assistant', ?)",
                (conversation_id, bike_reply)
            )

        return jsonify({
            "success": True,
            "reply": bike_reply,
            "response": bike_reply,
            "conversation_id": conversation_id,
            "tabby_action": "bike_command"
        })

    house_reply = handle_tabby_house_command(user, low_msg)

    if house_reply and any(word in low_msg for word in [
        "turn on", "turn off", "switch on", "switch off",
        "lock house", "unlock house"
    ]):
        house_reply = clean_ai_text(house_reply)

        with get_db() as db:
            db.execute(
                "INSERT INTO messages (conversation_id,role,content) "
                "VALUES (?, 'assistant', ?)",
                (conversation_id, house_reply)
            )

        return jsonify({
            "success": True,
            "reply": house_reply,
            "response": house_reply,
            "conversation_id": conversation_id,
            "tabby_action": "house_command"
        })

    if any(phrase in low_msg for phrase in [
        "change my location", "my location is", "i am in", "i'm in",
        "set my location", "update my location", "my new location",
        "mo wa ni", "emi wa ni"
    ]):
        parsed = parse_location_from_text(message)

        if parsed:
            latitude, longitude, city = parsed
            save_location(
                user["id"], latitude, longitude,
                label=city or "manual_change"
            )

            maps = f"https://www.google.com/maps?q={latitude},{longitude}"
            reply = clean_ai_text(
                f"Done Boss, I don update your location to "
                f"{city or f'{latitude}, {longitude}'}. "
                f"New location: {latitude}, {longitude}. Maps: {maps} "
                "You fit change am anytime, just tell me or press Share My Location again."
            )

            with get_db() as db:
                db.execute(
                    "INSERT INTO messages (conversation_id,role,content) "
                    "VALUES (?, 'assistant', ?)",
                    (conversation_id, reply)
                )

            return jsonify({
                "success": True,
                "reply": reply,
                "response": reply,
                "conversation_id": conversation_id,
                "latitude": latitude,
                "longitude": longitude,
                "google_maps": maps
            })

    if any(word in low_msg for word in [
        "generate image", "create image", "make image", "draw an image",
        "generate a picture", "create a picture", "generate image of",
        "midjourney", "design me", "design a"
    ]):
        try:
            image_prompt = message

            for word in [
                "generate image of", "generate image", "create image of",
                "create image", "make image of", "make image", "draw",
                "midjourney", "design me", "design a"
            ]:
                image_prompt = image_prompt.lower().replace(word, "").strip()

            if not image_prompt:
                image_prompt = message

            image_data = generate_flux_image(image_prompt, 4)

            with get_db() as db:
                db.execute(
                    "INSERT INTO messages (conversation_id,role,content) "
                    "VALUES (?, 'assistant', ?)",
                    (
                        conversation_id,
                        clean_ai_text(
                            f"Here is your dope image Boss: {image_prompt} - image generated successfully."
                        )
                    )
                )

            return jsonify({
                "success": True,
                "reply": "Here is your dope image Boss.",
                "response": "Here is your dope image Boss.",
                "conversation_id": conversation_id,
                "image": image_data,
                "image_url": image_data
            })

        except Exception as error:
            error_reply = clean_ai_text(f"Image failed Boss: {str(error)}")

            with get_db() as db:
                db.execute(
                    "INSERT INTO messages (conversation_id,role,content) "
                    "VALUES (?, 'assistant', ?)",
                    (conversation_id, error_reply)
                )

            return jsonify({
                "success": True,
                "reply": error_reply,
                "response": error_reply,
                "conversation_id": conversation_id
            })

    location = get_latest_location(user["id"])

    if location:
        location_text = (
            f"Boss last shared location: Latitude {location.get('latitude')}, "
            f"Longitude {location.get('longitude')}, "
            f"Label: {location.get('label','')}, "
            f"Time: {location.get('created_at','')}. "
            f"Google Maps: https://www.google.com/maps?q="
            f"{location.get('latitude')},{location.get('longitude')}."
        )
    else:
        location_text = "No location shared yet. Ask Boss to press Share My Location."

    if any(phrase in low_msg for phrase in [
        "what's my location", "what is my location", "where am i",
        "where is my location", "my location", "show my location",
        "track my location", "where am i now", "nibo ni mo wa"
    ]):
        if location:
            maps = (
                f"https://www.google.com/maps?q="
                f"{location.get('latitude')},{location.get('longitude')}"
            )

            reply = clean_ai_text(
                f"Boss, you dey here right now: Latitude {location.get('latitude')}, "
                f"Longitude {location.get('longitude')}. Maps: {maps}. "
                "If you don move, press Share My Location again."
            )

            with get_db() as db:
                db.execute(
                    "INSERT INTO messages (conversation_id,role,content) "
                    "VALUES (?, 'assistant', ?)",
                    (conversation_id, reply)
                )

            return jsonify({
                "success": True,
                "reply": reply,
                "response": reply,
                "conversation_id": conversation_id,
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "google_maps": maps
            })

        reply = clean_ai_text(
            "Boss, I never get your location yet. Press Share My Location and Allow, "
            "or tell me e.g. I am in Lagos and I go save am."
        )

        with get_db() as db:
            db.execute(
                "INSERT INTO messages (conversation_id,role,content) "
                "VALUES (?, 'assistant', ?)",
                (conversation_id, reply)
            )

        return jsonify({
            "success": True,
            "reply": reply,
            "response": reply,
            "conversation_id": conversation_id
        })

    if any(phrase in low_msg for phrase in [
        "who created you", "who built you", "who is your creator",
        "who made you", "ta ni o da e", "who is your boss"
    ]):
        reply = clean_ai_text(
            "Faruq Sulaimon create me. Na my Boss, my creator and my owner. "
            "I be Tabby, built by Faruq Sulaimon."
        )

        with get_db() as db:
            db.execute(
                "INSERT INTO messages (conversation_id,role,content) "
                "VALUES (?, 'assistant', ?)",
                (conversation_id, reply)
            )

        return jsonify({
            "success": True,
            "reply": reply,
            "response": reply,
            "conversation_id": conversation_id
        })

    # AUTOMATIC BACKGROUND RESEARCH:
    # No search icon, button or toggle is required in the frontend.
    search_results = []

    if needs_live_search(message) or data.get("web_search") is True or data.get("use_search") is True:
        search_results = live_search(
            message,
            max_results=3 if voice_mode else 6
        )

    system_prompt = build_system_prompt(
        user, memories, search_results, location_text, bike_context
    )

    if voice_mode:
        system_prompt += (
            "\nVOICE MODE ON: Answer like a phone call. "
            "Use short, warm, natural sentences. No long lists. "
            "Do not speak links aloud. Keep under 80 words unless "
            "the user asks for detail."
        )

    ai_messages = (
        [{"role": "system", "content": system_prompt}]
        + [
            {
                "role": row["role"],
                "content": clean_ai_text(row["content"])
            }
            for row in history
        ]
    )

    try:
        answer = clean_ai_text(
            call_ai_with_fallback(
                ai_messages,
                temperature=0.7,
                max_tokens=900 if voice_mode else 1800
            )
        )
    except Exception:
        logger.exception("Chat failed - all providers failed")
        return json_error(
            "Tabby could not reach its AI service right now. Please try again.",
            502
        )

    with get_db() as db:
        db.execute(
            "INSERT INTO messages (conversation_id,role,content) "
            "VALUES (?, 'assistant', ?)",
            (conversation_id, answer)
        )

        db.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (conversation_id,)
        )

    recent = "\n".join(
        f"{row['role']}: {row['content']}"
        for row in history[-6:]
    )
    recent += f"\nuser: {message}\nassistant: {answer}"

    extract_automatic_memories(user, recent)

    return jsonify({
        "success": True,
        "reply": answer,
        "response": answer,
        "conversation_id": conversation_id,
        "voice_mode": voice_mode,
        "memories": get_memories(user["id"])
    })


# ============================================================
# VISION - GLASSES / WATCH
# ============================================================
@app.route("/api/vision", methods=["POST"])
@auth_required
def vision_endpoint():
    user = request.current_user
    data = request.get_json(silent=True) or {}

    image_b64 = (
        data.get("image")
        or data.get("image_base64")
        or data.get("photo")
        or ""
    )

    question = (
        data.get("question")
        or data.get("prompt")
        or data.get("message")
        or "Hey Tabby, what am I looking at?"
    )

    device = data.get("device") or "glasses"

    if not image_b64:
        return json_error(
            "Send image as base64. For glasses: "
            "{image: 'data:image/jpeg;base64,...', "
            "question: 'Hey Tabby what am I looking at?'}"
        )

    try:
        bike_context = get_bike_context_for_user(user)
        answer = call_vision(image_b64, question, user, bike_context)

        with get_db() as db:
            db.execute(
                "INSERT INTO tabby_logs "
                "(user_id, device, command, action, result) VALUES (?,?,?,?,?)",
                (user["id"], device, question[:200], "vision", answer[:500])
            )

        return jsonify({
            "success": True,
            "reply": answer,
            "response": answer,
            "device": device,
            "speak": answer,
            "voice_reply": answer
        })

    except Exception as error:
        logger.exception("Vision failed")
        return json_error(f"Vision failed Boss: {str(error)}", 502)


@app.route("/api/wake", methods=["POST"])
@auth_required
def wake_word():
    data = request.get_json(silent=True) or {}

    transcript = str(
        data.get("transcript")
        or data.get("text")
        or data.get("message")
        or ""
    ).strip()

    device = data.get("device") or "glasses"

    if not transcript:
        return jsonify({
            "success": True,
            "wake": True,
            "reply": "Yes Boss, I dey here. Wetin you dey look at? Snap am make I see am.",
            "listening": True
        })

    if any(word in transcript.lower() for word in [
        "what am i looking at", "wetin i dey look", "what is this",
        "what am i seeing"
    ]):
        return jsonify({
            "success": True,
            "wake": True,
            "reply": "Snap am Boss, send the photo make I see am. I dey wait.",
            "need_image": True,
            "device": device
        })

    return jsonify({
        "success": True,
        "wake": True,
        "transcript": transcript,
        "reply": "I hear you Boss, talk am make I run am.",
        "device": device
    })


@app.route("/api/voice-chat", methods=["POST"])
@auth_required
def voice_chat():
    return jsonify({
        "success": True,
        "message": "Please call /api/chat with voice_mode true for phone-call style voice chat.",
        "voice_mode": True
    })


# ============================================================
# TABBY HOUSE + DEVICES
# ============================================================
@app.route("/api/house/devices", methods=["GET"])
@auth_required
def list_house_devices():
    user = request.current_user

    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM house_devices WHERE user_id=? ORDER BY id DESC",
            (user["id"],)
        ).fetchall()

    return jsonify({
        "success": True,
        "devices": [dict(row) for row in rows]
    })


@app.route("/api/house/devices", methods=["POST"])
@auth_required
def add_house_device():
    user = request.current_user
    data = request.get_json(silent=True) or {}

    name = clean_ai_text(str(data.get("name", "light")).strip())
    room = clean_ai_text(str(data.get("room", "living")).strip())
    device_type = clean_ai_text(str(data.get("type", name)).strip())

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO house_devices (user_id, name, room, type, status) "
            "VALUES (?,?,?,?, 'off')",
            (user["id"], name, room, device_type)
        )

    return jsonify({"success": True, "device_id": cur.lastrowid}), 201


@app.route("/api/house/devices/<int:device_id>/command", methods=["POST"])
@auth_required
def house_device_command(device_id):
    user = request.current_user
    data = request.get_json(silent=True) or {}

    action = str(
        data.get("action")
        or data.get("status")
        or data.get("command")
        or "on"
    ).lower()

    with get_db() as db:
        row = db.execute(
            "SELECT * FROM house_devices WHERE id=? AND user_id=?",
            (device_id, user["id"])
        ).fetchone()

        if not row:
            return json_error("Device not found", 404)

        db.execute(
            "UPDATE house_devices SET status=? WHERE id=?",
            (action, device_id)
        )

        db.execute(
            "INSERT INTO tabby_logs "
            "(user_id, device, command, action, result) VALUES (?,?,?,?,?)",
            (
                user["id"],
                dict(row)["name"],
                f"command {action}",
                action,
                f"set to {action}"
            )
        )

    return jsonify({
        "success": True,
        "message": f"Done Boss, {dict(row)['name']} don turn {action}."
    })


@app.route("/api/tabby/devices", methods=["GET"])
@auth_required
def list_tabby_devices():
    user = request.current_user

    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM tabby_devices WHERE user_id=? ORDER BY id DESC",
            (user["id"],)
        ).fetchall()

    return jsonify({
        "success": True,
        "devices": [dict(row) for row in rows]
    })


@app.route("/api/tabby/register", methods=["POST"])
@auth_required
def register_tabby_device():
    user = request.current_user
    data = request.get_json(silent=True) or {}

    name = clean_ai_text(
        str(data.get("device_name") or data.get("name") or "Tabby Glasses").strip()
    )
    device_type = str(data.get("device_type") or "glasses").lower()

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO tabby_devices "
            "(user_id, device_name, device_type, last_seen, battery) "
            "VALUES (?,?,?,?,?)",
            (
                user["id"],
                name,
                device_type,
                current_time(),
                int(data.get("battery", 100))
            )
        )

    return jsonify({
        "success": True,
        "device_id": cur.lastrowid,
        "message": f"Tabby {device_type} {name} registered Boss."
    })


@app.route("/api/tabby/logs", methods=["GET"])
@auth_required
def tabby_logs():
    user = request.current_user

    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM tabby_logs WHERE user_id=? ORDER BY id DESC LIMIT 50",
            (user["id"],)
        ).fetchall()

    return jsonify({
        "success": True,
        "logs": [dict(row) for row in rows]
    })


@app.route("/api/drone/command", methods=["POST"])
@auth_required
def drone_command():
    user = request.current_user
    data = request.get_json(silent=True) or {}
    command = str(data.get("command") or "").lower()

    if command not in (
        "takeoff", "land", "check_gate", "patrol", "return_home", "hover"
    ):
        return json_error(
            "Drone command must be takeoff, land, check_gate, patrol, return_home, hover"
        )

    with get_db() as db:
        db.execute(
            "INSERT INTO tabby_logs "
            "(user_id, device, command, action, result) VALUES (?,?,?,?,?)",
            (
                user["id"],
                "drone",
                command,
                command,
                f"Drone command {command} queued - connect drone hardware to execute"
            )
        )

    return jsonify({
        "success": True,
        "command": command,
        "message": f"Done Boss, drone command {command} don queue. Connect drone hardware make e fly."
    })


# ============================================================
# MEMORY
# ============================================================
@app.route("/api/memory", methods=["GET"])
@app.route("/api/memories", methods=["GET"])
@auth_required
def list_memories():
    return jsonify({
        "success": True,
        "memories": get_memories(request.current_user["id"], 100)
    })


@app.route("/api/memory", methods=["POST"])
@app.route("/api/memories", methods=["POST"])
@auth_required
def add_memory():
    user = request.current_user
    data = request.get_json(silent=True) or {}
    content = clean_ai_text(str(data.get("content", "")).strip())

    if not content:
        return json_error("Memory content required.")

    saved = save_memory(user["id"], content)

    return jsonify({
        "success": True,
        "saved": saved,
        "memories": get_memories(user["id"], 100)
    })


@app.route("/api/memory/<int:memory_id>", methods=["DELETE"])
@app.route("/api/memories/<int:memory_id>", methods=["DELETE"])
@auth_required
def delete_memory(memory_id):
    user = request.current_user

    with get_db() as db:
        cur = db.execute(
            "DELETE FROM memories WHERE id=? AND user_id=?",
            (memory_id, user["id"])
        )

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
    data = request.get_json(silent=True) or {}
    prompt = clean_ai_text(str(data.get("prompt", "")).strip())

    if not prompt:
        return json_error("Image prompt required.")

    if len(prompt) > 2000:
        return json_error("Image prompt too long.")

    try:
        steps = int(data.get("steps", 4))
    except Exception:
        steps = 4

    try:
        image = generate_flux_image(prompt, steps)

        return jsonify({
            "success": True,
            "image_url": image,
            "image": image,
            "model": "flux-standard-no-watermark"
        })

    except RuntimeError as error:
        return json_error(str(error), 503)

    except Exception:
        logger.exception("Image error")
        return json_error("Image generation failed.", 502)


# ============================================================
# AUTOMOTIVE + MOTORBIKE
# ============================================================
@app.route("/api/automotive/status", methods=["GET"])
def automotive_status():
    return jsonify({
        "success": True,
        "system": "Tabby Automotive",
        "status": "awaiting_hardware_connection",
        "message": "Automotive system is online, but no live hardware telemetry is connected yet."
    })


@app.route("/api/motorcycles", methods=["GET"])
@app.route("/api/motorbikes", methods=["GET"])
@auth_required
def list_motorcycles():
    user = request.current_user

    with get_db() as db:
        if user["role"] == "owner":
            rows = db.execute(
                "SELECT * FROM motorcycles ORDER BY id DESC"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM motorcycles WHERE assigned_user_id=? ORDER BY id DESC",
                (user["id"],)
            ).fetchall()

    output = []

    for row in rows:
        item = dict(row)
        item["motorbike_id"] = item["id"]
        output.append(item)

    return jsonify({
        "success": True,
        "motorcycles": output,
        "motorbikes": output
    })


@app.route("/api/motorcycles", methods=["POST"])
@app.route("/api/motorbikes", methods=["POST"])
@owner_required
def create_motorcycle():
    data = request.get_json(silent=True) or {}

    name = clean_ai_text(str(data.get("name", "")).strip())
    model = clean_ai_text(str(data.get("model", "")).strip())
    registration_number = clean_ai_text(
        str(data.get("registration_number", "")).strip()
    )

    if not name:
        return json_error("Motorbike name required.")

    assigned_user_id = data.get("assigned_user_id")

    if assigned_user_id:
        with get_db() as db:
            if not db.execute(
                "SELECT id FROM users WHERE id=?",
                (assigned_user_id,)
            ).fetchone():
                return json_error("Assigned user does not exist.")

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO motorcycles "
            "(owner_id,assigned_user_id,name,model,registration_number) "
            "VALUES (?,?,?,?,?)",
            (
                request.current_user["id"],
                assigned_user_id,
                name,
                model,
                registration_number
            )
        )

        db.execute(
            "INSERT OR IGNORE INTO bike_status "
            "(motorcycle_id, battery_health, range_km) VALUES (?,?,?)",
            (cur.lastrowid, 100, 0)
        )

    return jsonify({
        "success": True,
        "motorcycle_id": cur.lastrowid,
        "motorbike_id": cur.lastrowid
    }), 201


@app.route("/api/motorcycles/<int:motorcycle_id>", methods=["DELETE"])
@app.route("/api/motorbikes/<int:motorcycle_id>", methods=["DELETE"])
@owner_required
def delete_motorcycle(motorcycle_id):
    with get_db() as db:
        db.execute(
            "UPDATE payments SET motorcycle_id=NULL WHERE motorcycle_id=?",
            (motorcycle_id,)
        )

        cur = db.execute(
            "DELETE FROM motorcycles WHERE id=?",
            (motorcycle_id,)
        )

    if cur.rowcount == 0:
        return json_error("Motorbike not found.", 404)

    return jsonify({"success": True})


@app.route("/api/fleet/overview", methods=["GET"])
@owner_required
def fleet_overview():
    with get_db() as db:
        rows = db.execute("""
            SELECT m.*, u.name as rider_name, u.email as rider_email,
            bs.battery_health, bs.range_km, bs.total_trip, bs.total_distance,
            bs.co2_saved, bs.lock_status, bs.access_mode,
            pp.status as plan_status, pp.installments_paid,
            pp.installments_total, pp.next_due_date, pp.installment_amount
            FROM motorcycles m
            LEFT JOIN users u ON u.id = m.assigned_user_id
            LEFT JOIN bike_status bs ON bs.motorcycle_id = m.id
            LEFT JOIN payment_plans pp ON pp.motorcycle_id = m.id
            ORDER BY m.id DESC
        """).fetchall()

    return jsonify({
        "success": True,
        "fleet": [dict(row) for row in rows]
    })


@app.route("/api/my-bike", methods=["GET"])
@auth_required
def my_bike():
    user = request.current_user

    if user["role"] == "owner":
        return jsonify({
            "success": True,
            "message": "Owner uses fleet overview"
        })

    with get_db() as db:
        bike = db.execute(
            "SELECT * FROM motorcycles WHERE assigned_user_id=? "
            "ORDER BY id DESC LIMIT 1",
            (user["id"],)
        ).fetchone()

        if not bike:
            return jsonify({
                "success": True,
                "bike": None,
                "message": "No bike assigned yet"
            })

        bike = dict(bike)

        status = db.execute(
            "SELECT * FROM bike_status WHERE motorcycle_id=?",
            (bike["id"],)
        ).fetchone()

        telemetry = db.execute(
            "SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1",
            (bike["id"],)
        ).fetchone()

        plan = db.execute(
            "SELECT * FROM payment_plans WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1",
            (bike["id"],)
        ).fetchone()

    return jsonify({
        "success": True,
        "bike": bike,
        "status": dict(status) if status else {},
        "telemetry": dict(telemetry) if telemetry else {},
        "plan": dict(plan) if plan else {}
    })


@app.route("/api/bikes/<int:mid>/command", methods=["POST"])
@owner_required
def bike_command(mid):
    data = request.get_json(silent=True) or {}
    command = str(data.get("command", "")).lower()

    if command not in ("lock", "unlock", "restrict", "full_access"):
        return json_error("Command must be lock, unlock, restrict or full_access")

    with get_db() as db:
        if not db.execute(
            "SELECT id FROM motorcycles WHERE id=?",
            (mid,)
        ).fetchone():
            return json_error("Bike not found", 404)

        db.execute(
            "INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (?,?,?)",
            (mid, command, request.current_user["id"])
        )

        db.execute(
            "INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)",
            (mid,)
        )

        if command == "lock":
            db.execute(
                "UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=?",
                (mid,)
            )

        if command == "unlock":
            db.execute(
                "UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=?",
                (mid,)
            )

        if command == "restrict":
            db.execute(
                "UPDATE bike_status SET access_mode='restricted' WHERE motorcycle_id=?",
                (mid,)
            )

        if command == "full_access":
            db.execute(
                "UPDATE bike_status SET access_mode='full' WHERE motorcycle_id=?",
                (mid,)
            )

    return jsonify({
        "success": True,
        "command": command,
        "message": f"Bike {mid} set to {command}"
    })


@app.route("/api/bikes/<int:mid>/assign", methods=["POST"])
@owner_required
def assign_bike(mid):
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")

    with get_db() as db:
        if not db.execute(
            "SELECT id FROM motorcycles WHERE id=?",
            (mid,)
        ).fetchone():
            return json_error("Bike not found", 404)

        if not db.execute(
            "SELECT id FROM users WHERE id=?",
            (user_id,)
        ).fetchone():
            return json_error("User not found", 404)

        db.execute(
            "UPDATE motorcycles SET assigned_user_id=? WHERE id=?",
            (user_id, mid)
        )

    return jsonify({"success": True, "message": "Bike assigned"})


@app.route("/api/telemetry", methods=["POST"])
@app.route("/api/telemetry/ingest", methods=["POST"])
def ingest_telemetry():
    if not DEVICE_INGEST_KEY:
        return json_error("Device ingestion not configured.", 503)

    if not secrets.compare_digest(
        request.headers.get("X-Device-Key", ""),
        DEVICE_INGEST_KEY
    ):
        return json_error("Invalid device key.", 401)

    data = request.get_json(silent=True) or {}
    motorcycle_id = data.get("motorcycle_id") or data.get("motorbike_id")

    if not motorcycle_id:
        return json_error("Motorbike ID required.")

    with get_db() as db:
        if not db.execute(
            "SELECT id FROM motorcycles WHERE id=?",
            (motorcycle_id,)
        ).fetchone():
            return json_error("Motorbike not found.", 404)

        cur = db.execute(
            "INSERT INTO telemetry "
            "(motorcycle_id,latitude,longitude,speed,battery_level,fuel_level,"
            "engine_temperature,extra_data) VALUES (?,?,?,?,?,?,?,?)",
            (
                motorcycle_id,
                data.get("latitude"),
                data.get("longitude"),
                data.get("speed"),
                data.get("battery_level"),
                data.get("fuel_level"),
                data.get("engine_temperature"),
                json.dumps(data.get("extra_data", {}))
            )
        )

        db.execute(
            "INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)",
            (motorcycle_id,)
        )

        if data.get("battery_level") is not None:
            db.execute(
                "UPDATE bike_status SET last_seen=? WHERE motorcycle_id=?",
                (current_time(), motorcycle_id)
            )

    return jsonify({
        "success": True,
        "telemetry_id": cur.lastrowid
    }), 201


@app.route("/api/motorcycles/<int:motorcycle_id>/telemetry", methods=["GET"])
@app.route("/api/motorbikes/<int:motorcycle_id>/telemetry", methods=["GET"])
@auth_required
def get_motorcycle_telemetry(motorcycle_id):
    user = request.current_user

    if not user_can_access_motorcycle(user, motorcycle_id):
        return json_error("Motorbike not found or access denied.", 404)

    try:
        limit = int(request.args.get("limit", 50))
    except Exception:
        limit = 50

    limit = max(1, min(limit, 200))

    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM telemetry WHERE motorcycle_id=? "
            "ORDER BY id DESC LIMIT ?",
            (motorcycle_id, limit)
        ).fetchall()

    output = []

    for row in rows:
        item = dict(row)

        try:
            item["extra_data"] = json.loads(item["extra_data"] or "{}")
        except Exception:
            item["extra_data"] = {}

        output.append(item)

    return jsonify({
        "success": True,
        "motorcycle_id": motorcycle_id,
        "motorbike_id": motorcycle_id,
        "latest": output[0] if output else None,
        "telemetry": output
    })


@app.route("/api/location/update", methods=["POST"])
@auth_required
def update_location():
    user = request.current_user
    data = request.get_json(silent=True) or {}

    latitude = data.get("latitude")
    longitude = data.get("longitude")

    if latitude is None or longitude is None:
        if data.get("city") or data.get("location") or data.get("text"):
            parsed = parse_location_from_text(
                str(data.get("city") or data.get("location") or data.get("text") or "")
            )

            if parsed:
                latitude, longitude, city = parsed
                save_location(
                    user["id"], latitude, longitude,
                    label=city or "manual"
                )

                return jsonify({
                    "success": True,
                    "latitude": latitude,
                    "longitude": longitude,
                    "google_maps": f"https://www.google.com/maps?q={latitude},{longitude}"
                })

        return json_error("latitude and longitude required.")

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except Exception:
        return json_error("Invalid coordinates.")

    label = str(data.get("label") or data.get("city") or "phone_location")

    save_location(
        user["id"],
        latitude,
        longitude,
        label=label,
        accuracy=data.get("accuracy"),
        speed=data.get("speed")
    )

    return jsonify({
        "success": True,
        "latitude": latitude,
        "longitude": longitude,
        "google_maps": f"https://www.google.com/maps?q={latitude},{longitude}"
    })


@app.route("/api/location/latest", methods=["GET"])
@auth_required
def latest_location():
    location = get_latest_location(request.current_user["id"])

    if not location:
        return jsonify({"success": True, "location": None})

    location["google_maps"] = (
        f"https://www.google.com/maps?q="
        f"{location.get('latitude')},{location.get('longitude')}"
    )

    return jsonify({"success": True, "location": location})


@app.route("/api/payments", methods=["GET"])
@auth_required
def list_payments():
    user = request.current_user

    with get_db() as db:
        if user["role"] == "owner":
            rows = db.execute(
                "SELECT * FROM payments ORDER BY id DESC LIMIT 100"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 100",
                (user["id"],)
            ).fetchall()

    return jsonify({
        "success": True,
        "payments": [dict(row) for row in rows]
    })


@app.route("/api/payments", methods=["POST"])
@auth_required
def create_payment():
    user = request.current_user
    data = request.get_json(silent=True) or {}

    try:
        amount = float(data.get("amount", 0))
    except Exception:
        return json_error("Invalid amount.")

    if amount <= 0:
        return json_error("Amount must be > 0.")

    reference = f"TABBY-{secrets.token_hex(6).upper()}"

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO payments "
            "(user_id, motorcycle_id, amount, currency, status, reference) "
            "VALUES (?,?,?,?,?,?)",
            (
                user["id"],
                data.get("motorcycle_id"),
                amount,
                str(data.get("currency", "NGN")),
                "pending",
                reference
            )
        )

    return jsonify({
        "success": True,
        "payment_id": cur.lastrowid,
        "reference": reference
    }), 201


@app.route("/api/payment-plans", methods=["GET"])
@auth_required
def list_plans():
    user = request.current_user

    with get_db() as db:
        if user["role"] == "owner":
            rows = db.execute(
                "SELECT * FROM payment_plans ORDER BY id DESC"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM payment_plans WHERE user_id=? ORDER BY id DESC",
                (user["id"],)
            ).fetchall()

    return jsonify({
        "success": True,
        "plans": [dict(row) for row in rows]
    })


@app.route("/api/payment-plans", methods=["POST"])
@owner_required
def create_plan():
    data = request.get_json(silent=True) or {}

    try:
        total = float(data.get("total_amount", 0))
        installment_amount = float(data.get("installment_amount", 0))
        installment_total = int(data.get("installments_total", 1))
    except Exception:
        return json_error("Invalid plan numbers.")

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO payment_plans "
            "(user_id, motorcycle_id, total_amount, deposit, installment_amount, "
            "installments_total, next_due_date) VALUES (?,?,?,?,?,?,?)",
            (
                data.get("user_id"),
                data.get("motorcycle_id"),
                total,
                float(data.get("deposit", 0)),
                installment_amount,
                installment_total,
                data.get("next_due_date")
            )
        )

    return jsonify({"success": True, "plan_id": cur.lastrowid}), 201


@app.route("/api/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return jsonify({
            "success": True,
            "message": "Tabby webhook alive"
        })

    return jsonify({
        "success": True,
        "message": "Webhook received"
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)