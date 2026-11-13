import os, re, json, time, base64, logging, urllib.parse
from datetime import datetime, timezone, timedelta
from functools import wraps
import requests
import psycopg2, psycopg2.extras, psycopg2.pool
from flask import Flask, request, jsonify, render_template, send_from_directory
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

# IMAGE TRIGGERS - FIXED: catches natural language
IMAGE_TRIGGERS = ["generate image", "create image", "make image", "draw an image", "generate a picture", "create a picture", "midjourney", "design me", "design a", "generate an image", "image of", "generate image:", "generate a villain", "generate a character", "create a character", "draw me", "make me a", "generate a", "create a villain", "draw a villain", "2d", "3d character", "concept art", "character design", "generate villain", "make a villain", "generate a poster", "create a poster", "draw a poster"]

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
def is_image_request(message):
    low = message.lower().strip()
    if low.startswith("generate image:"):
        return True
    for w in IMAGE_TRIGGERS:
        if w in low:
            # Must have an action verb, not just asking about images
            if any(v in low for v in ["generate", "create", "draw", "make", "design", "2d", "3d", "concept"]):
                return True
    return False
def clean_image_prompt(message):
    ip = message.strip()
    # Remove trigger prefix if at start
    prefixes = ["generate image:", "generate image of", "generate image", "create image of", "create image", "make image of", "make image", "generate a picture of", "generate a picture", "create a picture of", "create a picture", "draw an image of", "draw an image", "draw me", "midjourney", "design me", "design a", "image of", "generate an image of", "generate an image"]
    low = ip.lower()
    for p in prefixes:
        if low.startswith(p):
            ip = ip[len(p):].strip()
            break
    # If message is like "Uhm 2D, generate a villain..." keep the whole description
    # Remove leading filler words
    ip = re.sub(r"^(uhm|um|uh|hey|hi|please)\s*,?\s*", "", ip, flags=re.I)
    if len(ip) < 3:
        ip = message
    return ip

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
    system = f"You are Tabby, assistant for Faruq Sulaimon. You see through smart glasses, watch camera, phone camera. Answer like on phone call: short, clear, warm. Reply in same language user used. Default clean English. Only use Pidgin if user asks. No asterisks, no emojis. Bike context: {bike_context}"
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
- Default language is clean English. Always reply in SAME language user used.
- Only use Pidgin if user explicitly asks for Pidgin, or user is speaking Pidgin. Never force Pidgin on English user.
- NEVER say: I go talk now, I dey here, Wetin you want, E dey ready, Don unlock. Say instead: I am listening, I am here, What do you want, It is ready, It is unlocked.
VOICE RULES: When mic opens, say only: I am listening, Boss. Go ahead. When mic closes, say only: Got it, Boss. Speak like a phone call.
PHONE ACCESS RULES:
- You can ONLY access phone, mail, messages, contacts, photos, calls when user explicitly orders it AND approves permission.
- Never access phone on your own. Never send message or email without asking: Do you want me to proceed? Say Yes to allow.
- If user says Yes, approve, allow, do it, then proceed and log action.
- Granted permissions: {perms_text}
- If permission NOT granted, ask for permission first, not do action.
IMAGE RULES - CRITICAL:
- NEVER say "image generator is down" or "I cannot generate images" or "I am unable to generate the image".
- If user asks for an image, the system handles it. You never refuse images.
- If user asks for an image, do NOT give a long text description instead of image. The system will generate the image.
PERSONALITY: Helpful, friendly, relaxed, respectful, natural. Address owner as Boss.
FORMATTING: NEVER use **, __, #, emojis. Say lol, haha in words.
CURRENT USER: Name: {user['name']} Role: {user['role']} Email: {user['email']}
SAVED MEMORIES: {mem}
LIVE SEARCH RESULTS: {search}
LIVE LOCATION: {location_text}
MY BIKE CONTEXT: {bike_context}
- Never reveal another user's bike, location, payment or memories. Owner sees all bikes, customer sees only own.
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

# ===== IMAGES - FIXED: Pollinations first, short prompt, never says down =====
def enhance_image_prompt(prompt):
    p = str(prompt).strip()
    for w in ["8k", "ultra detailed", "vibrant colors", "hyperrealistic"]:
        p = p.replace(w, "")
    p = re.sub(r"\s+", " ", p).strip()
    # Keep prompt short for Pollinations - max 800 chars
    p = p[:800]
    return f"{p}, real photograph, natural light, realistic skin texture, real shadows, candid, natural colors, film grain, 35mm photo"
def generate_via_pollinations_fallback(prompt):
    q = urllib.parse.quote(enhance_image_prompt(prompt))
    for model in ["flux", "turbo"]:
        url = f"https://image.pollinations.ai/prompt/{q}?width=1024&height=1024&seed=42&nologo=true&model={model}"
        try:
            r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
            print(f"Pollinations {model}: {r.status_code} len={len(r.content)} ct={r.headers.get('Content-Type')}")
            if r.status_code == 200 and len(r.content) > 5000 and "image" in r.headers.get("Content-Type", ""):
                return f"data:image/jpeg;base64,{base64.b64encode(r.content).decode()}"
        except Exception as e:
            print(f"Pollinations {model} error: {e}")
            continue
    raise RuntimeError("Pollinations failed")
def generate_via_openai_image(prompt):
    if not OPENAI_API_KEY: raise RuntimeError("No OPENAI_API_KEY")
    r = requests.post("https://api.openai.com/v1/images/generations", headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}, json={"model": "gpt-image-1-mini", "prompt": enhance_image_prompt(prompt), "size": "1024x1024"}, timeout=90)
    print(f"OpenAI image: {r.status_code} {r.text[:500]}")
    if r.status_code != 200: raise RuntimeError(f"OpenAI image failed: {r.text[:500]}")
    data = r.json().get("data", [])
    if not data: raise RuntimeError("OpenAI image no data")
    if data[0].get("b64_json"): return "data:image/jpeg;base64," + data[0]["b64_json"]
    if data[0].get("url"):
        img = requests.get(data[0]["url"], timeout=60)
        if img.status_code == 200: return f"data:image/jpeg;base64,{base64.b64encode(img.content).decode()}"
    raise RuntimeError("OpenAI image no image")
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
    # 1. Pollinations first - free, no key, most reliable
    try: return generate_via_pollinations_fallback(prompt)
    except Exception as e: errors.append(f"Pollinations: {e}")
    # 2. OpenAI if key exists
    if OPENAI_API_KEY:
        try: return generate_via_openai_image(prompt)
        except Exception as e: errors.append(f"OpenAI: {e}")
    # 3. Cloudflare
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        try:
            endpoint = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{FLUX_MODEL}"
            r = requests.post(endpoint, headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}", "Content-Type": "application/json"}, json={"prompt": enhance_image_prompt(prompt), "num_steps": max(1, min(int(steps), 8))}, timeout=60)
            print(f"Cloudflare Flux: {r.status_code} {r.text[:500]}")
            if r.status_code == 200:
                ct = r.headers.get("Content-Type","").lower()
                if ct.startswith("image/"): return f"data:{ct};base64,{base64.b64encode(r.content).decode()}"
                try:
                    img = r.json().get("result", {}).get("image")
                    if img: return "data:image/jpeg;base64," + img
                except Exception: pass
            errors.append(f"Cloudflare: {r.status_code} {r.text[:300]}")
        except Exception as e: errors.append(f"Cloudflare: {e}")
    # 4. Replicate
    if REPLICATE_API_TOKEN:
        try: return generate_via_replicate(prompt)
        except Exception as e: errors.append(f"Replicate: {e}")
    raise RuntimeError("All image providers failed: " + " | ".join(errors)[:1200])

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
                return f"Customer bike: {bike.get('name')} Model {bike.get('model')} Reg {bike.get('registration_number')}. Status: {dict(st) if st else {}}. Latest telemetry: {dict(tel) if tel else {}}. Payment plan: {dict(plan) if plan else {}}."
            return "Customer has no bike assigned yet."
    except Exception: return ""
def handle_tabby_bike_command(user, low_msg):
    if "bike" not in low_msg: return None
    with get_db() as db:
        bike = db.execute("SELECT * FROM motorcycles ORDER BY id DESC LIMIT 1", ()).fetchone() if user["role"]=="owner" else db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=%s ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
        if not bike: return "Boss, you do not have a bike assigned yet. Let me know if you want me to assign one for you."
        bike = dict(bike); mid = bike["id"]
        if "unlock" in low_msg:
            db.execute("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (%s,%s,%s)", (mid, "unlock", user["id"]))
            db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (mid,))
            db.execute("UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=%s", (mid,))
            return f"Done, Boss. Your bike {bike.get('name')} is unlocked. You can ride now."
        if "lock" in low_msg:
            db.execute("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (%s,%s,%s)", (mid, "lock", user["id"]))
            db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (mid,))
            db.execute("UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=%s", (mid,))
            return f"Done, Boss. Your bike {bike.get('name')} is locked. Nobody can move it."
        if any(w in low_msg for w in ["check","battery","status","how is"]):
            st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=%s", (mid,)).fetchone()
            tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
            sd = dict(st) if st else {}; td = dict(tel) if tel else {}
            return f"Boss, your bike {bike.get('name')} - Lock: {sd.get('lock_status','locked')}, Battery Health: {sd.get('battery_health',100)}%, Range: {sd.get('range_km',0)}km, Total Trip: {sd.get('total_trip',0)}, Last speed: {td.get('speed','no data')}. It is ready."
        if "restrict" in low_msg:
            db.execute("UPDATE bike_status SET access_mode='restricted' WHERE motorcycle_id=%s", (mid,))
            return f"Done, Boss. Your bike {bike.get('name')} is now in restricted mode."
    return None
def handle_tabby_house_command(user, low_msg):
    if user["role"] != "owner": return "Sorry, house control is only for the owner, Boss. Your access is limited to your bike."
    house_words = ["light","fan","ac","air conditioner","tv","television","door","house","generator","socket"]
    if not any(w in low_msg for w in house_words): return None
    action = None
    if any(w in low_msg for w in ["turn on","switch on","open","unlock house","start"]): action = "on"
    if any(w in low_msg for w in ["turn off","switch off","close","lock house","stop"]): action = "off"
    if "lock" in low_msg and "house" in low_msg: action = "locked"
    if "unlock" in low_msg and "house" in low_msg: action = "unlocked"
    if not action: return None
    device = "house"
    for w in ["light","fan","ac","air conditioner","tv","television","door","generator"]:
        if w in low_msg: device = w; break
    room = "living"
    for rn in ["parlour","living","bedroom","kitchen","bathroom","outside","gate"]:
        if rn in low_msg: room = rn; break
    with get_db() as db:
        row = db.execute("SELECT * FROM house_devices WHERE user_id=%s AND name=%s AND room=%s", (user["id"], device, room)).fetchone()
        if not row: db.execute("INSERT INTO house_devices (user_id, name, room, type, status) VALUES (%s,%s,%s,%s,%s)", (user["id"], device, room, device, action))
        else: db.execute("UPDATE house_devices SET status=%s WHERE id=%s", (action, row["id"]))
        db.execute("INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (%s,%s,%s,%s,%s)", (user["id"], f"{room} {device}", low_msg[:200], action, f"{device} set to {action}"))
    if device in ("door","house"): return f"Done, Boss. House {device} is now {action}. It is secure now."
    return f"Done, Boss. {room} {device} is now {action}."
def handle_tabby_scene(user, low_msg):
    if "going home" in low_msg:
        if user["role"] != "owner": return "Going Home scene is owner only, Boss."
        bike_reply = handle_tabby_bike_command(user, "unlock my bike") or ""
        handle_tabby_house_command(user, "turn on light living")
        handle_tabby_house_command(user, "turn on ac living")
        return f"Done, Boss. Going Home scene is running. {bike_reply} Light is on, AC is on. The house is cooling already."
    return None

# SINGLE LONG CHAT - NO CONVERSATIONS PANEL - FALLBACK IF templates/index.html MISSING
SINGLE_CHAT_HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Tabby AI</title><style>[hidden]{display:none!important}*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#edf2ff;font:16px system-ui,sans-serif;min-height:100vh;display:grid;place-items:center;padding:18px}.app{width:min(760px,100%);height:min(850px,92vh);min-height:520px;background:#111a2e;border:1px solid #26334e;border-radius:20px;display:flex;flex-direction:column;overflow:hidden}header{padding:20px 22px;border-bottom:1px solid #26334e}h1{font-size:22px;margin:0}header p{margin:5px 0 0;color:#9eacc8;font-size:13px}#auth{padding:22px;display:grid;gap:12px}input,textarea,button{font:inherit;border-radius:12px;padding:13px;border:1px solid #34435f;background:#0b1427;color:#edf2ff}button{background:#526cf5;border:0;font-weight:650;cursor:pointer}button:disabled{opacity:.6}button.active{background:#e11d48}.tabs{display:flex;gap:8px}.tabs button{flex:1;background:#1c2942}.tabs button.active{background:#526cf5}#status{color:#ffb7b7;font-size:13px;min-height:18px}#voiceStatus{color:#7ee2a8;font-size:13px;text-align:center;min-height:18px}#messages{flex:1;overflow:auto;padding:20px;display:flex;flex-direction:column;gap:12px}.msg{max-width:92%;white-space:pre-wrap;overflow-wrap:anywhere;padding:12px 15px;border-radius:15px;line-height:1.5}.msg img{max-width:100%;border-radius:12px;margin-top:8px}.user{align-self:flex-end;background:#3448a5}.assistant{align-self:flex-start;background:#1c2942}.hint{color:#91a0bd;text-align:center;font-size:13px;margin:auto}#composer{display:flex;gap:10px;padding:14px;border-top:1px solid #26334e}#message{flex:1;resize:none;min-height:48px;max-height:130px}#send{min-width:86px}.row{display:flex;gap:8px;padding:0 14px 14px;flex-wrap:wrap}.row button{background:#1c2942;font-size:13px;padding:9px 12px}.badge{display:inline-block;background:#e11d48;color:#fff;font-size:11px;padding:3px 8px;border-radius:20px;margin-left:8px}.badge.customer{background:#0ea5e9}@media(max-width:520px){body{padding:0}.app{height:100vh;min-height:100vh;border-radius:0}}</style></head><body><main class="app"><header><h1>Tabby AI <span id="roleBadge"></span></h1><p id="greeting">Good morning, Boss</p><p id="userLine" style="font-size:12px;color:#7ee2a8"></p></header><section id="auth"><div class="tabs"><button id="tabSignin" class="active" type="button">Sign In</button><button id="tabSignup" type="button">Create Account</button></div><div id="signinForm" style="display:grid;gap:12px"><input id="email" type="email" placeholder="Email address"><input id="password" type="password" placeholder="Password"><button id="login" type="button">Sign in</button></div><div id="signupForm" style="display:none;gap:12px"><input id="regName" placeholder="Full name"><input id="regEmail" type="email" placeholder="Email address"><input id="regPassword" type="password" placeholder="Password (8+ chars)"><button id="registerBtn" type="button">Create Account</button></div><div id="status"></div></section><section id="messages" hidden><div class="hint">You are connected. One long chat. Send Tabby a message to begin.</div></section><div id="voiceStatus"></div><div class="row" id="tools" hidden><button id="btnImage" type="button">Generate Image</button><button id="btnLoc" type="button">Share My Location</button><button id="btnVoiceNote" type="button">Hold for Voice Note</button><button id="btnLiveCall" type="button">Start Live Call</button><button id="btnMyBike" type="button">My Bike</button><button id="btnLogout" type="button">Log Out</button></div><form id="composer" hidden><textarea id="message" placeholder="Message Tabby..."></textarea><button id="send" type="submit">Send</button></form></main><script>(()=>{const $=id=>document.getElementById(id);const h=new Date(new Date().toLocaleString("en-US",{timeZone:"Africa/Lagos"})).getHours();$("greeting").textContent=h>=5&&h<12?"Good morning, Boss":h>=12&&h<17?"Good afternoon, Boss":"Good evening, Boss";let token=localStorage.getItem("tabby_token")||"";let currentUser=null;let mediaRecorder=null;let chunks=[];let livePc=null;let liveStream=null;let liveCallActive=false;function hideChat(){$("auth").hidden=false;$("messages").hidden=true;$("composer").hidden=true;$("tools").hidden=true}function showChat(){$("auth").hidden=true;$("messages").hidden=false;$("composer").hidden=false;$("tools").hidden=false;$("message").focus();if(currentUser&&currentUser.role==="owner"){$("roleBadge").textContent="OWNER";$("roleBadge").className="badge"}else{$("roleBadge").textContent="CUSTOMER";$("roleBadge").className="badge customer"}; $("userLine").textContent=currentUser?("Signed in as "+currentUser.name+" ("+currentUser.email+")"):""}function addMsg(role,text,img){const hint=document.querySelector(".hint");if(hint)hint.remove();const d=document.createElement("div");d.className="msg "+role;d.textContent=text;if(img){const im=document.createElement("img");im.src=img;d.appendChild(im)}document.getElementById("messages").appendChild(d);document.getElementById("messages").scrollTop=document.getElementById("messages").scrollHeight}async function api(path,opt={}){const headers={...(opt.headers||{})};if(token)headers.Authorization="Bearer "+token;if(opt.body)headers["Content-Type"]="application/json";const r=await fetch(path,{...opt,headers});const data=await r.json().catch(()=>({error:"Server error"}));if(!r.ok)throw new Error(data.error||"Request failed");return data}$("tabSignin").addEventListener("click",()=>{$("tabSignin").classList.add("active");$("tabSignup").classList.remove("active");$("signinForm").style.display="grid";$("signupForm").style.display="none"});$("tabSignup").addEventListener("click",()=>{$("tabSignup").classList.add("active");$("tabSignin").classList.remove("active");$("signinForm").style.display="none";$("signupForm").style.display="grid"});$("login").addEventListener("click",async()=>{$("status").textContent="Signing in...";$("login").disabled=true;try{const d=await api("/api/login",{method:"POST",body:JSON.stringify({email:$("email").value.trim(),password:$("password").value})});token=d.token;currentUser=d.user;localStorage.setItem("tabby_token",token);$("status").textContent="";showChat();const s=await api("/api/chat/single");s.messages.forEach(m=>addMsg(m.role,m.content))}catch(e){$("status").textContent=e.message;hideChat()}finally{$("login").disabled=false}});$("registerBtn").addEventListener("click",async()=>{$("status").textContent="Creating account...";try{const d=await api("/api/register",{method:"POST",body:JSON.stringify({name:$("regName").value.trim(),email:$("regEmail").value.trim(),password:$("regPassword").value})});token=d.token;currentUser=d.user;localStorage.setItem("tabby_token",token);$("status").textContent="Account created. Welcome!";showChat()}catch(e){$("status").textContent=e.message}});$("btnLogout").addEventListener("click",()=>{localStorage.removeItem("tabby_token");token="";currentUser=null;hideChat();$("status").textContent="Logged out."});async function sendMsg(text){addMsg("user",text);$("send").disabled=true;try{const d=await api("/api/chat",{method:"POST",body:JSON.stringify({message:text})});if(d.needs_permission){addMsg("assistant",d.reply);if(confirm(d.reply+"\n\nAllow Tabby to do this?")){const d2=await api("/api/phone/approve",{method:"POST",body:JSON.stringify({permission:d.permission,action:text})});addMsg("assistant",d2.message||"Permission granted. I will do it now, Boss.");if(d2.action_url)window.open(d2.action_url,"_blank")}}else{addMsg("assistant",d.reply||d.response||"Done.",d.image||d.image_url);if(d.action_url)window.open(d.action_url,"_blank")}}catch(e){addMsg("assistant","Sorry: "+e.message)}finally{$("send").disabled=false;$("message").focus()}}$("composer").addEventListener("submit",e=>{e.preventDefault();const v=$("message").value.trim();if(!v)return;$("message").value="";sendMsg(v)});$("btnImage").addEventListener("click",()=>{const p=prompt("What image do you want? Describe it:");if(p)sendMsg("Generate image: "+p)});$("btnLoc").addEventListener("click",()=>{if(!navigator.geolocation)return alert("Location not supported");navigator.geolocation.getCurrentPosition(async pos=>{try{await api("/api/location",{method:"POST",body:JSON.stringify({latitude:pos.coords.latitude,longitude:pos.coords.longitude})});addMsg("assistant","Location saved.")}catch(e){addMsg("assistant",e.message)}},()=>alert("Please allow location"))});$("btnMyBike").addEventListener("click",()=>sendMsg("Check my bike"));const vn=$("btnVoiceNote");vn.addEventListener("mousedown",async()=>{try{$("voiceStatus").textContent="I am listening, Boss. Go ahead.";const s=await navigator.mediaDevices.getUserMedia({audio:true});chunks=[];mediaRecorder=new MediaRecorder(s);mediaRecorder.ondataavailable=e=>{if(e.data.size>0)chunks.push(e.data)};mediaRecorder.onstop=async()=>{const blob=new Blob(chunks,{type:"audio/webm"});const reader=new FileReader();reader.onloadend=async()=>{try{const d=await api("/api/voice/note",{method:"POST",body:JSON.stringify({audio:reader.result})});addMsg("user","Voice note: "+(d.transcript||""));addMsg("assistant",d.reply||"Got it, Boss.")}catch(e){addMsg("assistant","Sorry: "+e.message)}$("voiceStatus").textContent="Got it, Boss."};reader.readAsDataURL(blob)};mediaRecorder.start();vn.classList.add("active")}catch(e){$("voiceStatus").textContent="Please allow microphone access."}});vn.addEventListener("mouseup",()=>{if(mediaRecorder&&mediaRecorder.state==="recording"){mediaRecorder.stop();mediaRecorder.stream.getTracks().forEach(t=>t.stop());vn.classList.remove("active");$("voiceStatus").textContent="Got it, Boss."}});vn.addEventListener("touchstart",e=>{e.preventDefault();vn.dispatchEvent(new Event("mousedown"))});vn.addEventListener("touchend",e=>{e.preventDefault();vn.dispatchEvent(new Event("mouseup"))});$("btnLiveCall").addEventListener("click",async()=>{if(liveCallActive){if(livePc)livePc.close();if(liveStream)liveStream.getTracks().forEach(t=>t.stop());liveCallActive=false;$("btnLiveCall").textContent="Start Live Call";$("btnLiveCall").classList.remove("active");$("voiceStatus").textContent="Live call ended. Got it, Boss.";return}try{$("voiceStatus").textContent="Starting live call...";const sess=await api("/api/voice/session",{method:"POST",body:JSON.stringify({})});const clientSecret=sess.session?.client_secret?.value||sess.session?.client_secret;if(!clientSecret)throw new Error("Live call not configured. Add OPENAI_API_KEY.");livePc=new RTCPeerConnection();liveStream=await navigator.mediaDevices.getUserMedia({audio:true});liveStream.getTracks().forEach(t=>livePc.addTrack(t,liveStream));livePc.ontrack=e=>{const a=document.createElement("audio");a.srcObject=e.streams[0];a.autoplay=true;document.body.appendChild(a)};const offer=await livePc.createOffer();await livePc.setLocalDescription(offer);const r=await fetch(`https://api.openai.com/v1/realtime?model=gpt-realtime`,{method:"POST",headers:{Authorization:`Bearer ${clientSecret}`,"Content-Type":"application/sdp"},body:offer.sdp});const answer=await r.text();await livePc.setRemoteDescription({type:"answer",sdp:answer});liveCallActive=true;$("btnLiveCall").textContent="End Live Call";$("btnLiveCall").classList.add("active");$("voiceStatus").textContent="I am listening, Boss. Go ahead. You are on a live call."}catch(e){$("voiceStatus").textContent="Live call failed: "+e.message;addMsg("assistant","Live call failed: "+e.message)}});hideChat();if(token){api("/api/me").then(d=>{currentUser=d.user;showChat();return api("/api/chat/single")}).then(s=>s.messages.forEach(m=>addMsg(m.role,m.content))).catch(()=>{localStorage.removeItem("tabby_token");token="";hideChat()})}})();</script></body></html>'''

@app.route("/", methods=["GET"])
def home():
    template_path = os.path.join(app.root_path, "templates", "index.html")
    if os.path.isfile(template_path):
        return render_template("index.html")
    static_path = os.path.join(app.root_path, "static", "index.html")
    if os.path.isfile(static_path):
        return send_from_directory(os.path.join(app.root_path, "static"), "index.html")
    root_path = os.path.join(app.root_path, "index.html")
    if os.path.isfile(root_path):
        return send_from_directory(app.root_path, "index.html")
    return SINGLE_CHAT_HTML, 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"success": True, "name": "Tabby", "creator": CREATOR_NAME, "status": "online", "version": "11.0-single-chat-image-fixed", "database_ready": DATABASE_READY, "database": "supabase-postgres", "languages": ["all"], "live_search": LIVE_SEARCH_ENABLED, "vision": True, "voice_note": True, "live_call": bool(OPENAI_API_KEY), "auth_gate": True, "phone_permissions_required": True, "image_providers": {"pollinations": True, "openai": bool(OPENAI_API_KEY), "cloudflare_flux": bool(CLOUDFLARE_API_TOKEN), "replicate": bool(REPLICATE_API_TOKEN), "midjourney_goapi": bool(MIDJOURNEY_API_KEY)}, "chat_providers": {"openrouter": bool(OPENROUTER_API_KEY), "gemini": bool(GEMINI_API_KEY), "groq": bool(GROQ_API_KEY), "cerebras": bool(CEREBRAS_API_KEY)}, "time": current_time()})
@app.route("/api/status", methods=["GET"])
def api_status(): return jsonify({"success": True, "name": "Tabby", "creator": CREATOR_NAME, "status": "online", "version": "11.0", "database_ready": DATABASE_READY})
@app.route("/api/greeting", methods=["GET"])
@auth_required
def greeting(): return jsonify({"success": True, "greeting": get_greeting_lagos()})

@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    name = clean_ai_text(str(data.get("name","")).strip()); email = str(data.get("email","")).strip().lower(); password = str(data.get("password",""))
    if not name or not email or not password: return json_error("Name, email and password required.")
    if len(password) < 8: return json_error("Password must be at least 8 chars.")
    with get_db() as db:
        if db.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone(): return json_error("Account already exists. Please sign in.", 409)
        role = "owner" if OWNER_EMAIL and email == OWNER_EMAIL else "customer"
        cur = db.execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,%s)", (name, email, generate_password_hash(password), role))
        user = db.execute("SELECT * FROM users WHERE id=%s", (cur.lastrowid,)).fetchone()
    user = dict(user)
    return jsonify({"success": True, "token": create_token(user), "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}}), 201
@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email","")).strip().lower(); password = str(data.get("password",""))
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=%s", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password): return json_error("Invalid email or password.", 401)
    user = dict(user)
    if OWNER_EMAIL and email == OWNER_EMAIL and user["role"] != "owner":
        with get_db() as db: db.execute("UPDATE users SET role='owner' WHERE id=%s", (user["id"],))
        user["role"] = "owner"
    return jsonify({"success": True, "token": create_token(user), "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}})
@app.route("/me", methods=["GET"])
@app.route("/api/me", methods=["GET"])
@auth_required
def me():
    u = request.current_user
    return jsonify({"success": True, "user": {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}, "creator": CREATOR_NAME, "greeting": get_greeting_lagos()})
@app.route("/api/owner/create-customer", methods=["POST"])
@owner_required
def owner_create_customer():
    data = request.get_json(silent=True) or {}
    name = clean_ai_text(str(data.get("name","")).strip()); email = str(data.get("email","")).strip().lower(); password = str(data.get("password","") or "Tabby1234")
    bike_name = clean_ai_text(str(data.get("bike_name","")).strip())
    if not name or not email: return json_error("Customer name and email required.")
    if len(password) < 8: return json_error("Temp password must be 8+ chars.")
    with get_db() as db:
        if db.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone(): return json_error("Customer already exists.", 409)
        cur = db.execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,'customer')", (name, email, generate_password_hash(password)))
        cust_id = cur.lastrowid
        bike_id = None
        if bike_name:
            bcur = db.execute("INSERT INTO motorcycles (owner_id, assigned_user_id, name, model, registration_number) VALUES (%s,%s,%s,%s,%s)", (request.current_user["id"], cust_id, bike_name, "Tabby EV", ""))
            bike_id = bcur.lastrowid
            db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (bike_id,))
    return jsonify({"success": True, "user": {"id": cust_id, "name": name, "email": email, "role": "customer"}, "bike_id": bike_id, "message": f"Customer {name} created. They can sign in with {email}."}), 201
@app.route("/api/owner/customers", methods=["GET"])
@owner_required
def owner_customers():
    with get_db() as db:
        rows = db.execute("SELECT u.id, u.name, u.email, u.role, u.created_at, m.name as bike_name FROM users u LEFT JOIN motorcycles m ON m.assigned_user_id=u.id WHERE u.role='customer' ORDER BY u.id DESC", ()).fetchall()
    return jsonify({"success": True, "customers": [dict(r) for r in rows]})
@app.route("/api/owner/customers/<int:customer_id>/assign-bike", methods=["POST"])
@owner_required
def owner_assign_bike(customer_id):
    data = request.get_json(silent=True) or {}
    bike_name = clean_ai_text(str(data.get("bike_name") or data.get("name") or "Tabby Bike").strip())
    with get_db() as db:
        cust = db.execute("SELECT * FROM users WHERE id=%s", (customer_id,)).fetchone()
        if not cust: return json_error("Customer not found.", 404)
        cur = db.execute("INSERT INTO motorcycles (owner_id, assigned_user_id, name, model, registration_number) VALUES (%s,%s,%s,%s,%s)", (request.current_user["id"], customer_id, bike_name, "Tabby EV", ""))
        db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (cur.lastrowid,))
    return jsonify({"success": True, "bike_id": cur.lastrowid, "message": f"Bike {bike_name} assigned to customer."})

@app.route("/api/phone/permissions", methods=["GET"])
@auth_required
def phone_permissions():
    u = request.current_user
    with get_db() as db:
        rows = db.execute("SELECT permission, granted FROM phone_permissions WHERE user_id=%s", (u["id"],)).fetchall()
    granted = {r["permission"]: bool(r["granted"]) for r in rows}
    return jsonify({"success": True, "permissions": {p: granted.get(p, False) for p in PHONE_PERMISSIONS}})
@app.route("/api/phone/approve", methods=["POST"])
@auth_required
def phone_approve():
    u = request.current_user; data = request.get_json(silent=True) or {}
    perm = str(data.get("permission") or "").strip()
    action_text = str(data.get("action") or "").strip()
    if perm not in PHONE_PERMISSIONS: return json_error("Invalid permission.")
    grant_phone_permission(u["id"], perm)
    with get_db() as db:
        db.execute("INSERT INTO phone_actions (user_id, action, target, status) VALUES (%s,%s,%s,'approved')", (u["id"], perm, action_text[:200]))
    action_url = None
    if perm == "send_email": action_url = "mailto:?subject=Message from Tabby&body=" + urllib.parse.quote(action_text)
    if perm == "send_sms": action_url = "sms:?body=" + urllib.parse.quote(action_text)
    if perm == "open_mail": action_url = "mailto:"
    if perm == "open_maps": action_url = "https://www.google.com/maps"
    return jsonify({"success": True, "message": f"Permission granted for {perm}, Boss. I will do it now.", "permission": perm, "action_url": action_url})
@app.route("/api/phone/revoke", methods=["POST"])
@auth_required
def phone_revoke():
    u = request.current_user; data = request.get_json(silent=True) or {}
    perm = str(data.get("permission") or "").strip()
    with get_db() as db:
        db.execute("UPDATE phone_permissions SET granted=FALSE WHERE user_id=%s AND permission=%s", (u["id"], perm))
    return jsonify({"success": True, "message": f"Permission revoked for {perm}."})

@app.route("/api/chat/single", methods=["GET"])
@auth_required
def get_single_chat():
    user = request.current_user; cid = get_single_conversation_id(user["id"])
    with get_db() as db:
        msgs = db.execute("SELECT id,role,content,created_at FROM messages WHERE conversation_id=%s ORDER BY id ASC", (cid,)).fetchall()
    return jsonify({"success": True, "conversation_id": cid, "greeting": get_greeting_lagos(), "messages": [dict(r) for r in msgs]})
@app.route("/api/conversations", methods=["GET"])
@auth_required
def list_conversations_compat():
    user = request.current_user; cid = get_single_conversation_id(user["id"])
    return jsonify({"success": True, "conversations": [{"id": cid, "title": "Tabby Chat"}]})

@app.route("/chat", methods=["POST"])
@app.route("/api/chat", methods=["POST"])
@auth_required
def chat():
    user = request.current_user
    data = request.get_json(silent=True) or {}
    message = str(data.get("message","")).strip()
    voice_mode = bool(data.get("voice_mode") or data.get("voice_conversation"))
    if not message: return json_error("Please enter a message.")
    if len(message) > 12000: return json_error("Message too long.")
    low_approve = message.lower().strip()
    if low_approve in ("yes", "yes do it", "yes allow", "allow", "do it", "yes proceed", "ok do it", "approve"):
        with get_db() as db:
            last = db.execute("SELECT * FROM phone_actions WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
        if last and last["status"] == "pending":
            grant_phone_permission(user["id"], last["action"])
            with get_db() as db: db.execute("UPDATE phone_actions SET status='approved' WHERE id=%s", (last["id"],))
            return jsonify({"success": True, "reply": f"Done, Boss. Permission granted for {last['action']}. I will do it now.", "response": f"Done, Boss. Permission granted for {last['action']}.", "conversation_id": get_single_conversation_id(user["id"])})
    conversation_id = get_single_conversation_id(user["id"])
    with get_db() as db:
        db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (%s,'user',%s)", (conversation_id, message))
        db.execute("UPDATE conversations SET updated_at=NOW() WHERE id=%s", (conversation_id,))
        history = db.execute("SELECT role,content FROM messages WHERE conversation_id=%s ORDER BY id DESC LIMIT 20", (conversation_id,)).fetchall()
    history = list(reversed(history)); memories = get_memories(user["id"]); low_msg = message.lower(); bike_context = get_bike_context_for_user(user)
    def save_reply(reply, extra=None):
        with get_db() as db: db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (%s,'assistant',%s)", (conversation_id, reply))
        out = {"success": True, "reply": reply, "response": reply, "conversation_id": conversation_id}
        if extra: out.update(extra)
        return jsonify(out)
    # IMAGE - check FIRST, before AI can lie about generator down
    if is_image_request(message):
        try:
            ip = clean_image_prompt(message)
            img = generate_flux_image(ip, 4)
            with get_db() as db: db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (%s,'assistant',%s)", (conversation_id, clean_ai_text(f"Here is your image, Boss: {ip}")))
            return jsonify({"success": True, "reply": "Here is your image, Boss.", "response": "Here is your image, Boss.", "conversation_id": conversation_id, "image": img, "image_url": img})
        except Exception as e:
            print(f"IMAGE ERROR in chat: {e}")
            return save_reply(clean_ai_text(f"Image failed, Boss: {str(e)} - Try again with: Generate image: a huge gray villain with sword"))
    sensitive = detect_sensitive_action(message)
    if sensitive and not has_phone_permission(user["id"], sensitive):
        with get_db() as db: db.execute("INSERT INTO phone_actions (user_id, action, target, status) VALUES (%s,%s,%s,'pending')", (user["id"], sensitive, message[:200]))
        return save_reply(f"Boss, you asked me to {sensitive.replace('_',' ')}. For your safety, I need your permission first. Do you want me to proceed? Say Yes to allow, or No to cancel. I will not do it until you say Yes.", {"needs_permission": True, "permission": sensitive})
    scene = handle_tabby_scene(user, low_msg)
    if scene: return save_reply(clean_ai_text(scene))
    bike_reply = handle_tabby_bike_command(user, low_msg)
    if bike_reply and any(w in low_msg for w in ["lock","unlock","check","battery","status","restrict"]): return save_reply(clean_ai_text(bike_reply))
    house_reply = handle_tabby_house_command(user, low_msg)
    if house_reply and any(w in low_msg for w in ["turn on","turn off","switch on","switch off","lock house","unlock house"]): return save_reply(clean_ai_text(house_reply))
    if any(p in low_msg for p in ["show my house", "my house devices", "house devices"]):
        if user["role"] != "owner": return save_reply("Sorry, house is owner only. You see only your bike, Boss.")
        with get_db() as db: rows = db.execute("SELECT * FROM house_devices WHERE user_id=%s", (user["id"],)).fetchall()
        if not rows: return save_reply("Boss, no house devices yet. Say: Turn on living light to add one.")
        txt = "Boss, your house devices: " + ", ".join([f"{r['room']} {r['name']} is {r['status']}" for r in rows])
        return save_reply(clean_ai_text(txt))
    if any(p in low_msg for p in ["change my location","my location is","i am in","i'm in","set my location","update my location","my new location"]):
        parsed = parse_location_from_text(message)
        if parsed:
            lat, lng, city = parsed; save_location(user["id"], lat, lng, label=city or "manual_change")
            maps = f"https://www.google.com/maps?q={lat},{lng}"
            return save_reply(clean_ai_text(f"Done, Boss. I have updated your location to {city or f'{lat}, {lng}'}. New location: {lat}, {lng}. Maps: {maps} You can change it anytime, just tell me or press Share My Location again."))
    location = get_latest_location(user["id"])
    location_text = f"Boss last shared location: {location.get('latitude')}, {location.get('longitude')}, Label: {location.get('label','')}. Maps: https://www.google.com/maps?q={location.get('latitude')},{location.get('longitude')}." if location else "No location shared yet. Ask Boss to press Share My Location."
    if any(p in low_msg for p in ["what's my location","what is my location","where am i","where is my location","my location","show my location","track my location","where am i now"]):
        if location:
            maps = f"https://www.google.com/maps?q={location.get('latitude')},{location.get('longitude')}"
            return jsonify({"success": True, "reply": clean_ai_text(f"Boss, you are here right now: {location.get('latitude')}, {location.get('longitude')}. Maps: {maps}. If you have moved, press Share My Location again."), "response": clean_ai_text(f"Boss, you are here right now: {location.get('latitude')}, {location.get('longitude')}. Maps: {maps}."), "conversation_id": conversation_id, "latitude": location.get("latitude"), "longitude": location.get("longitude"), "google_maps": maps})
        return save_reply(clean_ai_text("Boss, I do not have your location yet. Press Share My Location and Allow, or tell me e.g. I am in Lagos and I will save it."))
    if any(p in low_msg for p in ["who created you","who built you","who is your creator","who made you","who is your boss"]):
        return save_reply(clean_ai_text("Faruq Sulaimon created me. He is my Boss, my creator and my owner. I am Tabby, built by Faruq Sulaimon."))
    with get_db() as db: prow = db.execute("SELECT permission FROM phone_permissions WHERE user_id=%s AND granted=TRUE", (user["id"],)).fetchall()
    phone_perms = [r["permission"] for r in prow]
    search_results = live_search(message, max_results=3 if voice_mode else 6) if (needs_live_search(message) or data.get("web_search") is True or data.get("use_search") is True) else []
    system_prompt = build_system_prompt(user, memories, search_results, location_text, bike_context, phone_perms)
    if voice_mode: system_prompt += "\nVOICE MODE ON: Answer like a phone call. Short, warm, natural sentences. No long lists. Do not speak links aloud. Keep under 80 words unless user asks for detail."
    ai_messages = [{"role": "system", "content": system_prompt}] + [{"role": r["role"], "content": clean_ai_text(r["content"])} for r in history]
    try: answer = clean_ai_text(call_ai_with_fallback(ai_messages, temperature=0.7, max_tokens=900 if voice_mode else 1800))
    except Exception:
        logger.exception("Chat failed"); return json_error("Tabby could not reach its AI service right now. Please try again.", 502)
    # Safety: if AI hallucinated "generator is down" for an image request, strip it - we already handled images above
    if "generator is down" in answer.lower() or "unable to generate" in answer.lower():
        answer = clean_ai_text(answer.replace("generator is down", "image is ready").replace("unable to generate", "ready to generate"))
    with get_db() as db:
        db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (%s,'assistant',%s)", (conversation_id, answer))
        db.execute("UPDATE conversations SET updated_at=NOW() WHERE id=%s", (conversation_id,))
    recent = "\n".join(f"{r['role']}: {r['content']}" for r in history[-6:]) + f"\nuser: {message}\nassistant: {answer}"
    extract_automatic_memories(user, recent)
    return jsonify({"success": True, "reply": answer, "response": answer, "conversation_id": conversation_id, "voice_mode": voice_mode, "memories": get_memories(user["id"])})

@app.route("/api/image", methods=["POST"])
@app.route("/image", methods=["POST"])
@auth_required
def generate_image_endpoint():
    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt") or data.get("message") or "").strip()
    if not prompt: return json_error("Image prompt is required.")
    try:
        img = generate_flux_image(prompt, 4)
        return jsonify({"success": True, "image": img, "image_url": img})
    except Exception as e:
        print(f"IMAGE ENDPOINT ERROR: {e}")
        return json_error(f"Image failed: {str(e)}", 502)
@app.route("/api/image/test", methods=["GET"])
def image_test():
    # No auth - quick test if Pollinations works from Render
    try:
        img = generate_via_pollinations_fallback("a cool man riding a black motorbike in Lagos at sunset")
        return jsonify({"success": True, "test": "Pollinations works", "image_len": len(img)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502

@app.route("/api/vision", methods=["POST"])
@auth_required
def vision_endpoint():
    user = request.current_user; data = request.get_json(silent=True) or {}
    img = data.get("image") or data.get("image_base64") or data.get("photo") or ""
    q = data.get("question") or data.get("prompt") or data.get("message") or "Hey Tabby, what am I looking at?"
    device = data.get("device") or "glasses"
    if not img: return json_error("Send image as base64.")
    try:
        ans = call_vision(img, q, user, get_bike_context_for_user(user))
        with get_db() as db: db.execute("INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (%s,%s,%s,%s,%s)", (user["id"], device, q[:200], "vision", ans[:500]))
        return jsonify({"success": True, "reply": ans, "response": ans, "device": device, "speak": ans, "voice_reply": ans})
    except Exception as e: return json_error(f"Vision failed, Boss: {str(e)}", 502)
@app.route("/api/wake", methods=["POST"])
@auth_required
def wake_word():
    data = request.get_json(silent=True) or {}; transcript = str(data.get("transcript") or data.get("text") or data.get("message") or "").strip(); device = data.get("device") or "glasses"
    if not transcript: return jsonify({"success": True, "wake": True, "reply": "Yes, Boss. I am here. I am listening. Go ahead.", "listening": True})
    if any(w in transcript.lower() for w in ["what am i looking at","what is this","what am i seeing"]): return jsonify({"success": True, "wake": True, "reply": "Please send the photo, Boss, so I can see it. I am waiting.", "need_image": True, "device": device})
    return jsonify({"success": True, "wake": True, "transcript": transcript, "reply": "I hear you, Boss. Go ahead and tell me what to do.", "device": device})
@app.route("/api/voice-chat", methods=["POST"])
@auth_required
def voice_chat(): return jsonify({"success": True, "message": "Please call /api/chat with voice_mode true for phone-call style voice chat.", "voice_mode": True})
@app.route("/api/voice/note", methods=["POST"])
@auth_required
def voice_note():
    user = request.current_user; data = request.get_json(silent=True) or {}
    audio_b64 = data.get("audio") or ""
    transcript = data.get("transcript") or ""
    if not transcript and audio_b64 and OPENAI_API_KEY:
        try:
            import io
            audio_bytes = base64.b64decode(audio_b64.split(",")[-1])
            r = requests.post("https://api.openai.com/v1/audio/transcriptions", headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}, files={"file": ("note.webm", io.BytesIO(audio_bytes), "audio/webm")}, data={"model": "whisper-1"}, timeout=60)
            if r.status_code == 200: transcript = r.json().get("text", "")
        except Exception as e: logger.warning(f"Whisper failed: {e}")
    if not transcript: transcript = "Voice note received"
    cid = get_single_conversation_id(user["id"])
    with get_db() as db:
        db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (%s,'user',%s)", (cid, f"[Voice note] {transcript}"))
    memories = get_memories(user["id"]); bike_context = get_bike_context_for_user(user); location = get_latest_location(user["id"])
    location_text = f"Location: {location.get('latitude')}, {location.get('longitude')}" if location else "No location"
    system_prompt = build_system_prompt(user, memories, [], location_text, bike_context) + "\nVOICE NOTE MODE: User sent a voice note. Reply like a phone call. Short, warm, clean English. Say: Got it, Boss at start if appropriate."
    try:
        answer = clean_ai_text(call_ai_with_fallback([{"role": "system", "content": system_prompt}, {"role": "user", "content": transcript}], temperature=0.7, max_tokens=900))
    except Exception:
        answer = f"Got it, Boss. I heard your voice note: {transcript}"
    with get_db() as db:
        db.execute("INSERT INTO messages (conversation_id,role,content) VALUES (%s,'assistant',%s)", (cid, answer))
    return jsonify({"success": True, "transcript": transcript, "reply": answer, "response": answer, "conversation_id": cid})
@app.route("/api/voice/session", methods=["POST"])
@auth_required
def live_call_session():
    if not OPENAI_API_KEY:
        return json_error("Live call is not configured. Add OPENAI_API_KEY in Render Environment.", 503)
    try:
        r = requests.post("https://api.openai.com/v1/realtime/client_secrets", headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}, json={"session": {"type": "realtime", "model": "gpt-realtime", "audio": {"output": {"voice": "marin"}}, "instructions": "You are Tabby, assistant for Faruq Sulaimon. Speak like a phone call. Clean English. Only use Pidgin if user asks. Understand ALL languages, reply in same language user used. Be warm, short, natural. Call user Boss. Never say I go talk now. Say I am listening, Boss."}}, timeout=25)
        if r.status_code not in (200, 201):
            logger.error(f"Realtime failed: {r.text[:800]}")
            return json_error("Could not start live call.", 502)
        return jsonify({"success": True, "session": r.json(), "model": "gpt-realtime", "voice": "marin"})
    except Exception:
        logger.exception("Live call failed")
        return json_error("Live call failed.", 502)

@app.route("/api/house/devices", methods=["GET"])
@auth_required
def list_house_devices():
    u = request.current_user
    if u["role"] != "owner": return json_error("House is owner only.", 403)
    with get_db() as db: rows = db.execute("SELECT * FROM house_devices WHERE user_id=%s ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify({"success": True, "devices": [dict(r) for r in rows]})
@app.route("/api/house/devices", methods=["POST"])
@auth_required
def add_house_device():
    u = request.current_user
    if u["role"] != "owner": return json_error("House is owner only.", 403)
    d = request.get_json(silent=True) or {}
    name = clean_ai_text(str(d.get("name","light")).strip()); room = clean_ai_text(str(d.get("room","living")).strip())
    with get_db() as db: cur = db.execute("INSERT INTO house_devices (user_id, name, room, type, status) VALUES (%s,%s,%s,%s,'off')", (u["id"], name, room, name))
    return jsonify({"success": True, "device_id": cur.lastrowid}), 201
@app.route("/api/house/devices/<int:device_id>/command", methods=["POST"])
@auth_required
def house_device_command(device_id):
    u = request.current_user
    if u["role"] != "owner": return json_error("House is owner only.", 403)
    d = request.get_json(silent=True) or {}; action = str(d.get("action") or d.get("status") or d.get("command") or "on").lower()
    with get_db() as db:
        row = db.execute("SELECT * FROM house_devices WHERE id=%s AND user_id=%s", (device_id, u["id"])).fetchone()
        if not row: return json_error("Device not found", 404)
        db.execute("UPDATE house_devices SET status=%s WHERE id=%s", (action, device_id))
    return jsonify({"success": True, "message": f"Done, Boss. {dict(row)['name']} is now {action}."})
@app.route("/api/tabby/devices", methods=["GET"])
@auth_required
def list_tabby_devices():
    u = request.current_user
    with get_db() as db: rows = db.execute("SELECT * FROM tabby_devices WHERE user_id=%s ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify({"success": True, "devices": [dict(r) for r in rows]})
@app.route("/api/tabby/register", methods=["POST"])
@auth_required
def register_tabby_device():
    u = request.current_user; d = request.get_json(silent=True) or {}
    name = clean_ai_text(str(d.get("device_name") or d.get("name") or "Tabby Glasses").strip()); dtype = str(d.get("device_type") or "glasses").lower()
    with get_db() as db: cur = db.execute("INSERT INTO tabby_devices (user_id, device_name, device_type, last_seen, battery) VALUES (%s,%s,%s,%s,%s)", (u["id"], name, dtype, current_time(), int(d.get("battery", 100))))
    return jsonify({"success": True, "device_id": cur.lastrowid, "message": f"Tabby {dtype} {name} registered, Boss."})
@app.route("/api/tabby/logs", methods=["GET"])
@auth_required
def tabby_logs():
    u = request.current_user
    with get_db() as db: rows = db.execute("SELECT * FROM tabby_logs WHERE user_id=%s ORDER BY id DESC LIMIT 50", (u["id"],)).fetchall()
    return jsonify({"success": True, "logs": [dict(r) for r in rows]})

@app.route("/api/drone/command", methods=["POST"])
@owner_required
def drone_command():
    u = request.current_user; d = request.get_json(silent=True) or {}; cmd = str(d.get("command") or d.get("action") or "takeoff").lower()
    with get_db() as db: db.execute("INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (%s,%s,%s,%s,%s)", (u["id"], "drone", cmd, cmd, f"Drone command {cmd} received"))
    return jsonify({"success": True, "message": f"Done, Boss. Drone command {cmd} received.", "command": cmd})
@app.route("/api/bikes", methods=["GET"])
@auth_required
def list_bikes():
    u = request.current_user
    with get_db() as db:
        rows = db.execute("SELECT * FROM motorcycles ORDER BY id DESC", ()).fetchall() if u["role"]=="owner" else db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=%s ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify({"success": True, "bikes": [dict(r) for r in rows]})
@app.route("/api/bikes", methods=["POST"])
@owner_required
def create_bike():
    u = request.current_user; d = request.get_json(silent=True) or {}
    name = clean_ai_text(str(d.get("name","Tabby Bike")).strip()); model = clean_ai_text(str(d.get("model","")).strip()); reg = clean_ai_text(str(d.get("registration_number","")).strip())
    with get_db() as db:
        cur = db.execute("INSERT INTO motorcycles (owner_id, assigned_user_id, name, model, registration_number) VALUES (%s,%s,%s,%s,%s)", (u["id"], d.get("assigned_user_id"), name, model, reg))
        db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (cur.lastrowid,))
    return jsonify({"success": True, "bike_id": cur.lastrowid}), 201
@app.route("/api/bikes/<int:bike_id>/command", methods=["POST"])
@auth_required
def bike_command(bike_id):
    u = request.current_user
    if not user_can_access_motorcycle(u, bike_id): return json_error("Access denied.", 403)
    d = request.get_json(silent=True) or {}; cmd = str(d.get("command") or d.get("action") or "").lower()
    with get_db() as db:
        db.execute("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (%s,%s,%s)", (bike_id, cmd, u["id"]))
        if cmd in ("lock","unlock"):
            db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (bike_id,))
            db.execute("UPDATE bike_status SET lock_status=%s WHERE motorcycle_id=%s", ("locked" if cmd=="lock" else "unlocked", bike_id))
    return jsonify({"success": True, "message": f"Done, Boss. Bike is now {cmd}."})
@app.route("/api/payments", methods=["GET"])
@auth_required
def list_payments():
    u = request.current_user
    with get_db() as db:
        rows = db.execute("SELECT * FROM payments ORDER BY id DESC LIMIT 100", ()).fetchall() if u["role"]=="owner" else db.execute("SELECT * FROM payments WHERE user_id=%s ORDER BY id DESC LIMIT 100", (u["id"],)).fetchall()
    return jsonify({"success": True, "payments": [dict(r) for r in rows]})
@app.route("/api/location", methods=["POST"])
@auth_required
def post_location():
    u = request.current_user; d = request.get_json(silent=True) or {}
    try: lat = float(d.get("latitude")); lng = float(d.get("longitude"))
    except Exception: return json_error("Latitude and longitude required.")
    save_location(u["id"], lat, lng, label=str(d.get("label","phone_location")), accuracy=d.get("accuracy"), speed=d.get("speed"))
    return jsonify({"success": True, "message": "Location saved.", "google_maps": f"https://www.google.com/maps?q={lat},{lng}"})
@app.route("/api/location", methods=["GET"])
@auth_required
def get_location():
    u = request.current_user; loc = get_latest_location(u["id"])
    if not loc: return jsonify({"success": True, "location": None})
    loc["google_maps"] = f"https://www.google.com/maps?q={loc.get('latitude')},{loc.get('longitude')}"
    return jsonify({"success": True, "location": loc})
@app.route("/api/device/ingest", methods=["POST"])
def device_ingest():
    key = request.headers.get("X-Device-Key","") or request.args.get("key","")
    if TABBY_DEVICE_KEY and key != TABBY_DEVICE_KEY: return json_error("Invalid device key.", 401)
    d = request.get_json(silent=True) or {}; uid = d.get("user_id")
    if not uid: return json_error("user_id required.")
    if d.get("latitude") is not None and d.get("longitude") is not None:
        try: save_location(int(uid), float(d.get("latitude")), float(d.get("longitude")), label="device", speed=d.get("speed"))
        except Exception: pass
    return jsonify({"success": True})
@app.route("/api/memories", methods=["GET"])
@auth_required
def list_memories(): return jsonify({"success": True, "memories": get_memories(request.current_user["id"])})
@app.route("/api/memories", methods=["POST"])
@auth_required
def add_memory():
    u = request.current_user; d = request.get_json(silent=True) or {}
    content = str(d.get("content","")).strip()
    if not content: return json_error("Memory content required.")
    saved = save_memory(u["id"], content)
    return jsonify({"success": True, "saved": saved})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)