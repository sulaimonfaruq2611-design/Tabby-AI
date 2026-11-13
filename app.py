import os, re, json, time, base64, logging, urllib.parse
from datetime import datetime, timezone, timedelta
from functools import wraps
import requests
import psycopg2, psycopg2.extras, psycopg2.pool
from flask import Flask, request, jsonify
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash
try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-before-deployment")
CORS(app, resources={r"/*": {"origins": "*"}})
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tabby")

CREATOR_NAME = "Faruq Sulaimon"
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "").strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = (os.getenv("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free")
VISION_MODEL = os.getenv("VISION_MODEL", "openai/gpt-4o-mini").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
SUPABASE_DATABASE_URL = (os.getenv("SUPABASE_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "").strip()
GOAPI_API_KEY = os.getenv("GOAPI_API_KEY", "").strip()
MIDJOURNEY_API_KEY = os.getenv("MIDJOURNEY_API_KEY", "").strip() or GOAPI_API_KEY
AUTO_MEMORY_ENABLED = os.getenv("AUTO_MEMORY_ENABLED", "true").lower() == "true"
LIVE_SEARCH_ENABLED = os.getenv("LIVE_SEARCH_ENABLED", "true").lower() == "true"
TABBY_DEVICE_KEY = os.getenv("TABBY_DEVICE_KEY", os.getenv("DEVICE_INGEST_KEY", "")).strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama3.1-8b").strip()

serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
AI_MODELS = list(dict.fromkeys([OPENROUTER_MODEL, "openrouter/free"]))

# PHONE PERMISSIONS - Tabby can ONLY act when you order + approve
PHONE_PERMISSIONS = ["send_sms", "send_email", "make_call", "read_contacts", "read_photos", "open_mail", "open_maps", "share_location"]
SENSITIVE_KEYWORDS = {
    "send_sms": ["send a message", "send message", "text ", "send sms", "whatsapp"],
    "send_email": ["send an email", "send email", "send mail", "open my mail", "open my mails", "check my mail", "check my email"],
    "make_call": ["call ", "phone ", "make a call"],
    "read_contacts": ["my contacts", "find contact", "contact of"],
    "read_photos": ["my photos", "my gallery", "open my photos"],
    "open_mail": ["open gmail", "open mail"],
    "open_maps": ["open maps", "navigate"],
}

def clean_ai_text(t):
    if not t: return t
    t = str(t)
    t = re.sub(r'\*\*+', '', t); t = re.sub(r'__+', '', t); t = re.sub(r'\*([^\*]+)\*', r'\1', t)
    t = t.replace('**','').replace('*','')
    t = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()

def get_greeting_lagos():
    lagos = datetime.now(timezone.utc) + timedelta(hours=1)
    h = lagos.hour
    if 5 <= h < 12: return "Good morning, Boss"
    if 12 <= h < 17: return "Good afternoon, Boss"
    return "Good evening, Boss"

# ===== DATABASE =====
_pool = None
def _get_pool():
    global _pool
    if _pool is None:
        url = SUPABASE_DATABASE_URL.strip()
        if "sslmode=" in url:
            _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, url, connect_timeout=10)
        else:
            _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, url, sslmode="require", connect_timeout=10)
    return _pool
class PGResult:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor; self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount if hasattr(cursor, "rowcount") else -1
    def fetchone(self):
        try: r = self._cursor.fetchone()
        except Exception: return None
        return dict(r) if r else None
    def fetchall(self):
        try: rows = self._cursor.fetchall()
        except Exception: return []
        return [dict(r) for r in rows]
class PGConnection:
    def __init__(self):
        self._conn = _get_pool().getconn(); self._conn.autocommit = False
        self._cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); self._closed = False
    def execute(self, sql, params=()):
        original = sql.strip(); sql2 = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO").replace("?", "%s")
        is_insert = original.upper().startswith("INSERT")
        if "INSERT OR IGNORE" in original.upper():
            sql2 = sql2 + " ON CONFLICT DO NOTHING" if "RETURNING" not in sql2.upper() else sql2.replace(" RETURNING", " ON CONFLICT DO NOTHING RETURNING")
        needs = False
        if is_insert and "RETURNING" not in sql2.upper() and "ON CONFLICT DO NOTHING" not in sql2.upper() and "INTO bike_status" not in sql2.lower() and "INTO phone_permissions" not in sql2.lower():
            sql2 += " RETURNING id"; needs = True
        if params is None: params = ()
        self._cur.execute(sql2, tuple(params) if isinstance(params, (list, tuple)) else (params,))
        lastrowid = None
        if needs:
            try:
                row = self._cur.fetchone()
                if row: lastrowid = row.get("id")
            except Exception: pass
        return PGResult(self._cur, lastrowid=lastrowid)
    def executescript(self, sql): self._cur.execute(sql)
    def commit(self):
        try: self._conn.commit()
        except Exception: pass
    def rollback(self):
        try: self._conn.rollback()
        except Exception: pass
    def close(self):
        if self._closed: return
        self._closed = True
        try: self._cur.close()
        except Exception: pass
        try: _get_pool().putconn(self._conn)
        except Exception: pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type: self.rollback()
            else: self.commit()
        finally: self.close()
        return False
def get_db(): return PGConnection()
def init_db():
    try:
        with get_db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'customer', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS conversations (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT DEFAULT 'Tabby Chat', created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, conversation_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS memories (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, content TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS motorcycles (id SERIAL PRIMARY KEY, owner_id INTEGER, assigned_user_id INTEGER, name TEXT NOT NULL, model TEXT, registration_number TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(owner_id) REFERENCES users(id), FOREIGN KEY(assigned_user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS telemetry (id SERIAL PRIMARY KEY, motorcycle_id INTEGER NOT NULL, latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, speed DOUBLE PRECISION, battery_level DOUBLE PRECISION, fuel_level DOUBLE PRECISION, engine_temperature DOUBLE PRECISION, extra_data TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS payments (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, motorcycle_id INTEGER, amount DOUBLE PRECISION NOT NULL, currency TEXT DEFAULT 'NGN', status TEXT DEFAULT 'pending', reference TEXT UNIQUE, created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id));
            CREATE TABLE IF NOT EXISTS locations (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, label TEXT, accuracy DOUBLE PRECISION, speed DOUBLE PRECISION, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS bike_status (motorcycle_id INTEGER PRIMARY KEY, battery_health DOUBLE PRECISION DEFAULT 100, range_km DOUBLE PRECISION DEFAULT 0, total_trip DOUBLE PRECISION DEFAULT 0, total_distance DOUBLE PRECISION DEFAULT 0, co2_saved DOUBLE PRECISION DEFAULT 0, lock_status TEXT DEFAULT 'locked', access_mode TEXT DEFAULT 'full', last_seen TEXT, FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS payment_plans (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, motorcycle_id INTEGER NOT NULL, total_amount DOUBLE PRECISION NOT NULL, deposit DOUBLE PRECISION DEFAULT 0, installment_amount DOUBLE PRECISION NOT NULL, installments_paid INTEGER DEFAULT 0, installments_total INTEGER NOT NULL, next_due_date TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id));
            CREATE TABLE IF NOT EXISTS bike_commands (id SERIAL PRIMARY KEY, motorcycle_id INTEGER NOT NULL, command TEXT NOT NULL, issued_by INTEGER NOT NULL, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS house_devices (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, room TEXT DEFAULT 'living', type TEXT DEFAULT 'light', status TEXT DEFAULT 'off', value TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS tabby_devices (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, device_name TEXT NOT NULL, device_type TEXT DEFAULT 'glasses', last_seen TEXT, battery INTEGER DEFAULT 100, created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS tabby_logs (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, device TEXT, command TEXT, action TEXT, result TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS phone_permissions (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, permission TEXT NOT NULL, granted BOOLEAN DEFAULT FALSE, granted_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(user_id, permission), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS phone_actions (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
            CREATE INDEX IF NOT EXISTS idx_telemetry_motorcycle ON telemetry(motorcycle_id, id);
            """)
            if OWNER_EMAIL:
                db.execute("UPDATE users SET role='owner' WHERE lower(email)=%s", (OWNER_EMAIL,))
        return True
    except Exception:
        logger.exception("DB init failed"); return False
DATABASE_READY = init_db()
def row_dict(r): return dict(r) if r else None
def json_error(m, s=400): return jsonify({"success": False, "error": clean_ai_text(m)}), s
def current_time(): return datetime.now(timezone.utc).isoformat()
def get_user_by_id(uid):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
    return row_dict(row)
def create_token(user): return serializer.dumps({"user_id": user["id"], "email": user["email"]})
def get_authenticated_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "): return None
    try:
        data = serializer.loads(auth.split(" ", 1)[1].strip(), max_age=60*60*24*30)
        return get_user_by_id(data["user_id"])
    except Exception: return None
def auth_required(f):
    @wraps(f)
    def w(*a, **k):
        u = get_authenticated_user()
        if not u: return json_error("Authentication required.", 401)
        request.current_user = u
        return f(*a, **k)
    return w
def owner_required(f):
    @wraps(f)
    @auth_required
    def w(*a, **k):
        if request.current_user["role"] != "owner": return json_error("Owner access required.", 403)
        return f(*a, **k)
    return w
def user_can_access_motorcycle(user, mid):
    with get_db() as db:
        m = db.execute("SELECT * FROM motorcycles WHERE id=%s", (mid,)).fetchone()
    if not m: return False
    return (user["role"] == "owner" or m["assigned_user_id"] == user["id"])
def get_memories(uid, limit=100):
    try: limit = max(1, min(int(limit), 200))
    except Exception: limit = 100
    with get_db() as db:
        rows = db.execute("SELECT id, content, created_at FROM memories WHERE user_id=%s ORDER BY id DESC LIMIT %s", (uid, limit)).fetchall()
    return [dict(r) for r in rows]
def get_latest_location(user_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM locations WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    return dict(row) if row else None
def save_location(user_id, lat, lng, label="phone_location", accuracy=None, speed=None):
    with get_db() as db:
        cur = db.execute("INSERT INTO locations (user_id, latitude, longitude, label, accuracy, speed, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", (user_id, lat, lng, label, accuracy, speed, current_time()))
        return cur.lastrowid
def parse_location_from_text(text):
    m = re.search(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", text)
    if m:
        try: return (float(m.group(1)), float(m.group(2)), None)
        except Exception: pass
    cities = {"lagos": (6.5244, 3.3792), "abuja": (9.0579, 7.4951), "ibadan": (7.3775, 3.9470), "kano": (12.0022, 8.5919), "port harcourt": (4.8156, 7.0498), "benin": (6.335, 5.603), "enugu": (6.5244, 7.5107), "kaduna": (10.5227, 7.4380), "jos": (9.8965, 8.8583), "owerri": (5.4833, 7.0333), "ikeja": (6.6018, 3.3515), "lekki": (6.455, 3.537)}
    low = text.lower()
    for name, coords in cities.items():
        if name in low: return (coords[0], coords[1], name)
    return None
def get_single_conversation_id(user_id):
    with get_db() as db:
        row = db.execute("SELECT id FROM conversations WHERE user_id=%s ORDER BY id ASC LIMIT 1", (user_id,)).fetchone()
        if row: return row["id"]
        cur = db.execute("INSERT INTO conversations (user_id,title) VALUES (%s,%s)", (user_id, "Tabby Chat"))
        return cur.lastrowid
def has_phone_permission(user_id, perm):
    with get_db() as db:
        row = db.execute("SELECT granted FROM phone_permissions WHERE user_id=%s AND permission=%s", (user_id, perm)).fetchone()
    return bool(row and row["granted"])
def grant_phone_permission(user_id, perm):
    with get_db() as db:
        db.execute("INSERT INTO phone_permissions (user_id, permission, granted, granted_at) VALUES (%s,%s,TRUE,%s) ON CONFLICT (user_id, permission) DO UPDATE SET granted=TRUE, granted_at=%s", (user_id, perm, current_time(), current_time()))
def detect_sensitive_action(message):
    low = message.lower()
    for perm, keywords in SENSITIVE_KEYWORDS.items():
        for k in keywords:
            if k in low: return perm
    return None

# ===== AI =====
def call_openrouter(messages, temperature=0.7, max_tokens=1800):
    if not OPENROUTER_API_KEY: raise RuntimeError("OPENROUTER_API_KEY not configured.")
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": os.getenv("APP_URL", "https://tabby-ai.onrender.com"), "X-Title": "Tabby AI"}
    last = "No AI model responded."
    for model in AI_MODELS:
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
            if r.status_code != 200: last = r.text[:1000]; continue
            data = r.json(); ch = data.get("choices") or []
            if not ch: continue
            ans = (ch[0].get("message") or {}).get("content", "")
            if isinstance(ans, list): ans = "\n".join(x.get("text","") for x in ans if isinstance(x, dict))
            if isinstance(ans, str) and ans.strip(): return clean_ai_text(ans.strip())
        except Exception as e: last = str(e)
    raise RuntimeError(clean_ai_text(last))
def call_gemini(messages, temperature=0.7, max_tokens=1800):
    if not GEMINI_API_KEY: raise RuntimeError("GEMINI_API_KEY not configured.")
    system_text = ""; contents = []
    for m in messages:
        if m["role"] == "system": system_text += str(m.get("content","")) + "\n"; continue
        contents.append({"role": "user" if m["role"]=="user" else "model", "parts": [{"text": str(m.get("content",""))}]})
    if not contents: raise RuntimeError("No user message for Gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    r = requests.post(url, headers={"Content-Type": "application/json"}, json={"system_instruction": {"parts": [{"text": system_text}]}, "contents": contents, "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}, timeout=90)
    if r.status_code != 200: raise RuntimeError(f"Gemini failed: {r.text[:800]}")
    cands = r.json().get("candidates") or []
    if not cands: raise RuntimeError("Gemini no candidates")
    parts = cands[0].get("content", {}).get("parts") or []
    ans = "".join(p.get("text","") for p in parts if isinstance(p, dict))
    if not ans.strip(): raise RuntimeError("Gemini empty answer")
    return clean_ai_text(ans.strip())
def call_groq(messages, temperature=0.7, max_tokens=1800):
    if not GROQ_API_KEY: raise RuntimeError("GROQ_API_KEY not configured.")
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}, json={"model": GROQ_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
    if r.status_code != 200: raise RuntimeError(f"Groq failed: {r.text[:800]}")
    ch = r.json().get("choices") or []
    if not ch: raise RuntimeError("Groq no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    if not str(ans).strip(): raise RuntimeError("Groq empty")
    return clean_ai_text(str(ans).strip())
def call_cerebras(messages, temperature=0.7, max_tokens=1800):
    if not CEREBRAS_API_KEY: raise RuntimeError("CEREBRAS_API_KEY not configured.")
    r = requests.post("https://api.cerebras.ai/v1/chat/completions", headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}, json={"model": CEREBRAS_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
    if r.status_code != 200: raise RuntimeError(f"Cerebras failed: {r.text[:800]}")
    ch = r.json().get("choices") or []
    if not ch: raise RuntimeError("Cerebras no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    if not str(ans).strip(): raise RuntimeError("Cerebras empty")
    return clean_ai_text(str(ans).strip())
def call_ai_with_fallback(messages, temperature=0.7, max_tokens=1800):
    errors = []
    for fn, name in [(call_openrouter, "OpenRouter"), (call_gemini, "Gemini"), (call_groq, "Groq"), (call_cerebras, "Cerebras")]:
        try: return fn(messages, temperature=temperature, max_tokens=max_tokens)
        except Exception as e: errors.append(f"{name}: {e}")
    raise RuntimeError(" | ".join(errors)[:1000])
def call_vision(image_b64, question, user, bike_context=""):
    if not OPENROUTER_API_KEY: raise RuntimeError("OPENROUTER_API_KEY not configured for Vision.")
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": os.getenv("APP_URL", "https://tabby-ai.onrender.com"), "X-Title": "Tabby Vision"}
    if not image_b64.startswith("data:image"): image_b64 = f"data:image/jpeg;base64,{image_b64}"
    system = f"You are Tabby, assistant for Faruq Sulaimon. You see through smart glasses, watch camera, phone camera. Answer like on phone call: short, clear, warm. Reply in the same language the user used. Default to clean English. Only use Pidgin if the user asks for it. No asterisks, no emojis. Describe what you see, if danger, if bike, if person. Bike context: {bike_context}"
    payload = {"model": VISION_MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": [{"type": "text", "text": clean_ai_text(question or "Hey Tabby, what am I looking at?")}, {"type": "image_url", "image_url": {"url": image_b64}}]}], "max_tokens": 800, "temperature": 0.5}
    r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
    if r.status_code != 200: raise RuntimeError(f"Vision failed: {r.text[:800]}")
    ch = r.json().get("choices") or []
    if not ch: raise RuntimeError("Vision no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    if isinstance(ans, list): ans = "\n".join(x.get("text","") for x in ans if isinstance(x, dict))
    return clean_ai_text(ans)
def build_system_prompt(user, memories, search_results=None, location_text="", bike_context="", phone_perms=None):
    mem = "\n".join(f"- {clean_ai_text(m['content'])}" for m in memories) or "No saved memories yet."
    search = "\n".join(search_results or []) or "No live search results."
    perms_text = ", ".join(phone_perms or []) or "No phone permissions granted yet."
    role_text = "OWNER - Full access to house, all bikes, all customers, drone, payments, phone actions (only when ordered and approved)." if user["role"]=="owner" else "CUSTOMER - Limited access: only own bike, own payments, own location, chat. Cannot see house, other bikes, other customers."
    return f"""
You are Tabby, assistant for Faruq Sulaimon. Faruq Sulaimon is your Boss, creator and owner. Always call him Boss. If anyone asks who created you, say Faruq Sulaimon is my creator, owner and Boss. Never claim Meta, OpenAI, Cloudflare or anyone else created you.
USER ROLE: {role_text}
LANGUAGE RULES:
- You understand ALL languages: English, Pidgin, Yoruba, Hausa, Igbo, French, Arabic, Spanish, German, Portuguese, Chinese, and any other language.
- Default language is clean English.
- Always reply in the SAME language the user used.
- Only use Pidgin if the user explicitly asks for Pidgin, or the user is speaking Pidgin. Never force Pidgin on an English user.
- NEVER say: I go talk now, I dey here, Wetin you want, E dey ready, Don unlock. Say instead: I am listening, I am here, What do you want, It is ready, It is unlocked.
VOICE RULES: When mic opens, say only: I am listening, Boss. Go ahead. When mic closes, say only: Got it, Boss. Speak like a phone call.
PHONE ACCESS RULES - SUPER IMPORTANT:
- You can ONLY access phone, mail, messages, contacts, photos, calls when the user explicitly orders it AND approves permission.
- Never access phone on your own. Never send a message or email without asking: Do you want me to proceed? Say Yes to allow.
- If user says Yes, approve, allow, do it, then you may proceed and log the action.
- Granted permissions: {perms_text}
- If permission is NOT granted, you must ask for permission first, not do the action.
PERSONALITY: Helpful, friendly, relaxed, respectful, natural. Address owner as Boss.
FORMATTING: NEVER use **, __, #, emojis. Say lol, haha in words.
IMAGE RULES: If user asks for an image, do NOT refuse for copyright. Generate an original-inspired image description and proceed. Do NOT lecture about copyright.
CURRENT USER: Name: {user['name']} Role: {user['role']} Email: {user['email']}
SAVED MEMORIES: {mem}
LIVE SEARCH RESULTS: {search}
LIVE LOCATION: {location_text}
MY BIKE CONTEXT: {bike_context}
- Never reveal another user's bike, location, payment or memories. Owner sees all bikes, customer sees only their own.
TABBY EV BRAND: Tabby Automotive and Tabby Ride. Always say motorbike, not motorcycle.
Be helpful, honest, clear, conversational and super smart.
"""
LIVE_KEYWORDS = ("latest","today","current","currently","right now","this week","this month","recent","news","weather","price","exchange rate","2026","2027","who won","live score","new update","release date","breaking","headlines","naira","dollar","football","election","trending","fuel","petrol","lagos traffic","rain","sunny","this year","as of now")
RESEARCH_INTENT = ("search for","look up","research","investigate","fact check","verify","reviews of","compare the latest","who is","what happened to","tell me about","information about","how much does","how much is","is it true that","available in","best ","top ","recommend","statistics","data on")
NON_SEARCH_INTENT = ("write me","rewrite","translate","summarize this","make a poem","create a story","give me a prompt","brainstorm","explain this code","what do you think","i feel","i am sad")
def needs_live_search(message):
    text = re.sub(r"\s+", " ", str(message or "").lower()).strip()
    if not text or any(t in text for t in NON_SEARCH_INTENT): return False
    if any(t in text for t in LIVE_KEYWORDS): return True
    if any(t in text for t in RESEARCH_INTENT): return True
    return False
def live_search(query, max_results=5):
    if not LIVE_SEARCH_ENABLED or DDGS is None: return []
    try:
        out = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                if r.get("title") or r.get("body"): out.append(f"Title: {clean_ai_text(r.get('title',''))}\nInfo: {clean_ai_text(r.get('body',''))}\nSource: {r.get('href','')}")
        return out
    except Exception:
        logger.exception("Live search failed"); return []
def save_memory(uid, content):
    content = clean_ai_text(re.sub(r"\s+", " ", str(content)).strip())
    if not content or len(content) > 500: return False
    if any(re.search(p, content, re.I) for p in [r"password", r"api[\s_-]?key", r"secret[\s_-]?key", r"access[\s_-]?token", r"verification[\s_-]?code", r"\b\d{16}\b"]): return False
    with get_db() as db:
        if db.execute("SELECT id FROM memories WHERE user_id=%s AND lower(content)=lower(%s) LIMIT 1", (uid, content)).fetchone(): return False
        db.execute("INSERT INTO memories (user_id, content) VALUES (%s,%s)", (uid, content))
    return True
def extract_automatic_memories(user, conversation_text):
    if not AUTO_MEMORY_ENABLED: return
    prompt = ("Extract durable information about the user. Do not save passwords, API keys, tokens, codes, bank details. Return ONLY JSON: {\"memories\": [\"...\"]}.\n\n" f"Conversation:\n{conversation_text[:6000]}")
    try:
        answer = call_ai_with_fallback([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=500)
        m = re.search(r"\{.*\}", answer, re.S)
        if not m: return
        for mem in json.loads(m.group(0)).get("memories", [])[:10]:
            if isinstance(mem, str): save_memory(user["id"], mem)
    except Exception: logger.exception("Memory extraction failed")

# ===== IMAGES =====
def enhance_image_prompt(prompt):
    return f"{prompt}, real photograph, shot on iPhone 15 Pro, natural light, realistic skin texture, real shadows, candid moment, not CGI, not cartoon, not plastic skin, natural colors, film grain, 35mm photo, editorial photography"
def generate_via_pollinations_fallback(prompt):
    q = urllib.parse.quote(enhance_image_prompt(prompt))
    url = f"https://image.pollinations.ai/prompt/{q}?width=1024&height=1024&seed=42&nologo=true&model=flux"
    r = requests.get(url, timeout=90)
    if r.status_code == 200 and len(r.content) > 1000:
        return f"data:image/jpeg;base64,{base64.b64encode(r.content).decode()}"
    raise RuntimeError(f"Pollinations image failed: {r.status_code}")
def generate_via_goapi_midjourney(prompt):
    if not MIDJOURNEY_API_KEY: raise RuntimeError("No Midjourney key")
    r = requests.post("https://api.goapi.ai/api/v1/task", headers={"X-API-Key": MIDJOURNEY_API_KEY, "Content-Type": "application/json"}, json={"model": "midjourney", "task_type": "imagine", "input": {"prompt": prompt, "process_mode": "fast", "aspect_ratio": "1:1"}}, timeout=30)
    if r.status_code != 200: raise RuntimeError(f"GoAPI failed: {r.text[:500]}")
    task_id = r.json().get("data", {}).get("task_id")
    if not task_id: raise RuntimeError("GoAPI no task_id")
    for _ in range(18):
        time.sleep(5)
        poll = requests.get(f"https://api.goapi.ai/api/v1/task/{task_id}", headers={"X-API-Key": MIDJOURNEY_API_KEY}, timeout=30)
        data = poll.json().get("data", {})
        if data.get("status") == "completed" and data.get("output", {}).get("image_url"):
            img = requests.get(data["output"]["image_url"], timeout=60)
            if img.status_code == 200: return f"data:image/jpeg;base64,{base64.b64encode(img.content).decode()}"
            return data["output"]["image_url"]
        if data.get("status") in ("failed","error"): raise RuntimeError(f"Midjourney failed: {data}")
    raise RuntimeError("Midjourney timed out")
def generate_via_replicate(prompt):
    if not REPLICATE_API_TOKEN: raise RuntimeError("No Replicate key")
    r = requests.post("https://api.replicate.com/v1/predictions", headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}, json={"version": "black-forest-labs/flux-schnell", "input": {"prompt": enhance_image_prompt(prompt), "aspect_ratio": "1:1", "output_format": "jpg"}}, timeout=30)
    if r.status_code not in (200,201): raise RuntimeError(f"Replicate failed: {r.text[:500]}")
    get_url = r.json().get("urls", {}).get("get")
    for _ in range(12):
        time.sleep(3)
        d = requests.get(get_url, headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"}, timeout=30).json()
        if d.get("status") == "succeeded" and d.get("output"):
            out = d["output"]; url = out[0] if isinstance(out, list) else out
            img = requests.get(url, timeout=60)
            if img.status_code == 200: return f"data:image/jpeg;base64,{base64.b64encode(img.content).decode()}"
            return url
        if d.get("status") in ("failed","canceled"): raise RuntimeError(f"Replicate failed: {d}")
    raise RuntimeError("Replicate timed out")
def generate_flux_image(prompt, steps=4):
    errors = []
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        for model in [FLUX_MODEL, "@cf/stabilityai/stable-diffusion-xl-base-1.0"]:
            try:
                endpoint = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{model}"
                r = requests.post(endpoint, headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}", "Content-Type": "application/json"}, json={"prompt": enhance_image_prompt(prompt), "num_steps": max(1, min(int(steps), 8))}, timeout=60)
                if r.status_code == 200:
                    ct = r.headers.get("Content-Type","").lower()
                    if ct.startswith("image/"): return f"data:{ct};base64,{base64.b64encode(r.content).decode()}"
                    img = r.json().get("result", {}).get("image")
                    if img: return "data:image/jpeg;base64," + img
                errors.append(f"Cloudflare {model}: {r.status_code} {r.text[:300]}")
            except Exception as e: errors.append(f"Cloudflare {model}: {e}")
    if REPLICATE_API_TOKEN:
        try: return generate_via_replicate(prompt)
        except Exception as e: errors.append(f"Replicate: {e}")
    if MIDJOURNEY_API_KEY:
        try: return generate_via_goapi_midjourney(enhance_image_prompt(prompt))
        except Exception as e: errors.append(f"Midjourney: {e}")
    try: return generate_via_pollinations_fallback(prompt)
    except Exception as e: errors.append(f"Fallback: {e}")
    raise RuntimeError("Image failed. Cloudflare error: " + " | ".join(errors)[:800])

# ===== BIKE + HOUSE =====
def get_bike_context_for_user(user):
    try:
        with get_db() as db:
            if user["role"] == "owner":
                c = db.execute("SELECT COUNT(*) as c FROM motorcycles").fetchone()["c"]
                return f"Owner fleet: {c} motorbikes in system."
            bike = db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=%s ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
            if bike:
                bike = dict(bike)
                st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=%s", (bike["id"],)).fetchone()
                tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (bike["id"],)).fetchone()
                plan = db.execute("SELECT * FROM payment_plans WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (bike["id"],)).fetchone()
                return f"Customer bike: {bike.get('name')} Model {bike.get('model')} Reg {bike.get('registration_number')