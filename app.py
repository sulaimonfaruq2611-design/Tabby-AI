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
HF_API_KEY = os.getenv("HF_API_KEY", "").strip()
GOAPI_API_KEY = os.getenv("GOAPI_API_KEY", "").strip()
AUTO_MEMORY_ENABLED = os.getenv("AUTO_MEMORY_ENABLED", "true").lower() == "true"
LIVE_SEARCH_ENABLED = os.getenv("LIVE_SEARCH_ENABLED", "true").lower() == "true"
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
SENSITIVE_KEYWORDS = {"send_sms": ["send a message", "send message", "text ", "send sms", "whatsapp"],"send_email": ["send an email", "send email", "send mail", "open my mail", "check my mail", "check my email"],"make_call": ["call ", "phone ", "make a call"],"read_contacts": ["my contacts", "find contact", "contact of"],"read_photos": ["my photos", "my gallery", "open my photos"],"open_mail": ["open gmail", "open mail"],"open_maps": ["open maps", "navigate"]}
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
            CREATE TABLE IF NOT EXISTS bike_status (motorcycle_id INTEGER PRIMARY KEY, battery_health DOUBLE PRECISION DEFAULT 100, range_km DOUBLE PRECISION DEFAULT 125, total_trip DOUBLE PRECISION DEFAULT 0, total_distance DOUBLE PRECISION DEFAULT 0, co2_saved DOUBLE PRECISION DEFAULT 0, lock_status TEXT DEFAULT 'locked', access_mode TEXT DEFAULT 'full', last_seen TEXT, FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS payment_plans (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, motorcycle_id INTEGER NOT NULL, total_amount DOUBLE PRECISION NOT NULL, deposit DOUBLE PRECISION DEFAULT 0, installment_amount DOUBLE PRECISION NOT NULL, installments_paid INTEGER DEFAULT 0, installments_total INTEGER NOT NULL, next_due_date TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id));
            CREATE TABLE IF NOT EXISTS bike_commands (id SERIAL PRIMARY KEY, motorcycle_id INTEGER NOT NULL, command TEXT NOT NULL, issued_by INTEGER NOT NULL, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS house_devices (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, room TEXT DEFAULT 'living', type TEXT DEFAULT 'light', status TEXT DEFAULT 'off', value TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS tabby_devices (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, device_name TEXT NOT NULL, device_type TEXT DEFAULT 'glasses', last_seen TEXT, battery INTEGER DEFAULT 100, created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS tabby_logs (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, device TEXT, command TEXT, action TEXT, result TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS phone_permissions (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, permission TEXT NOT NULL, granted BOOLEAN DEFAULT FALSE, granted_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(user_id, permission), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS phone_actions (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
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
def _strip_fillers(text):
    t = str(text or "").strip()
    for _ in range(5):
        nt = re.sub(r"^(ok|okay|uhm|um|uh|hey|hi|please|boss|abeg)[\s,.:!]+", "", t, flags=re.I).strip()
        if nt == t: break
        t = nt
    return t
def is_image_request(message):
    raw = str(message or "").lower().strip()
    cleaned = _strip_fillers(raw)
    low = cleaned
    if re.match(r"^(generate|create|make|draw|design)\b", low): return True
    for w in IMAGE_TRIGGERS:
        if w in low: return True
    return False
def clean_image_prompt(message):
    ip = _strip_fillers(str(message or "").strip())
    prefixes = ["generate image:", "generate image of", "generate image", "create image of", "create image", "make image of", "make image", "generate a picture of", "generate a picture", "create a picture of", "create a picture", "draw an image of", "draw an image", "draw me", "midjourney", "design me", "design a", "image of", "generate an image of", "generate an image"]
    low = ip.lower()
    for p in prefixes:
        if low.startswith(p):
            ip = ip[len(p):].strip(); break
    ip = _strip_fillers(ip)
    if len(ip) < 3: ip = _strip_fillers(message)
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
    return clean_ai_text(str(ans).strip())
def call_cerebras(messages, temperature=0.7, max_tokens=1800):
    if not CEREBRAS_API_KEY: raise RuntimeError("CEREBRAS_API_KEY not configured.")
    r = requests.post("https://api.cerebras.ai/v1/chat/completions", headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}, json={"model": CEREBRAS_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
    if r.status_code != 200: raise RuntimeError(f"Cerebras failed: {r.text[:800]}")
    ch = r.json().get("choices") or []
    if not ch: raise RuntimeError("Cerebras no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    return clean_ai_text(str(ans).strip())
def call_ai_with_fallback(messages, temperature=0.7, max_tokens=1800):
    errors = []
    for fn, name in [(call_openrouter, "OpenRouter"), (call_gemini, "Gemini"), (call_groq, "Groq"), (call_cerebras, "Cerebras")]:
        try: return fn(messages, temperature=temperature, max_tokens=max_tokens)
        except Exception as e: errors.append(f"{name}: {e}")
    raise RuntimeError(" | ".join(errors)[:1000])
def build_system_prompt(user, memories, location_text="", bike_context="", phone_perms=None):
    mem = "\n".join(f"- {clean_ai_text(m['content'])}" for m in memories) or "No saved memories yet."
    perms_text = ", ".join(phone_perms or []) or "No phone permissions granted yet."
    role_text = "OWNER - Full access to house, all bikes, all customers." if user["role"]=="owner" else "CUSTOMER - Limited access: only own bike, own payments, own location, chat. Cannot see house, other bikes, other customers."
    return f"You are Tabby, assistant for Faruq Sulaimon. Faruq Sulaimon is your Boss, creator and owner. Always call him Boss. If anyone asks who created you, say Faruq Sulaimon is my creator, owner and Boss. USER ROLE: {role_text} LANGUAGE: Default clean English. Reply in SAME language user used. Only use Pidgin if user asks or speaks Pidgin. NEVER say: I go talk now, I dey here, Wetin you want, E dey ready, Don unlock. Say instead: I am listening, I am here, What do you want, It is ready, It is unlocked. PHONE: Only access phone when user explicitly orders AND approves. Granted: {perms_text} IMAGE RULES: NEVER say image generator is down or cannot generate images. System handles it. PERSONALITY: Helpful, friendly, relaxed, respectful. FORMATTING: NEVER use **, __, #, emojis. CURRENT USER: {user['name']} Role: {user['role']} SAVED MEMORIES: {mem} LIVE LOCATION: {location_text} BIKE CONTEXT: {bike_context} Never reveal another user's bike, location, payment or memories. Owner sees all bikes, customer sees only own. TABBY EV BRAND: Tabby Automotive and Tabby Ride. Always say motorbike, not motorcycle."
LIVE_KEYWORDS = ("latest","today","current","currently","right now","this week","news","weather","price","2026","2027","live score","breaking","naira","dollar","football","trending","fuel","lagos traffic")
def needs_live_search(message):
    text = re.sub(r"\s+", " ", str(message or "").lower()).strip()
    if any(t in text for t in ["write me","rewrite","translate","summarize this","make a poem","create a story"]): return False
    return any(t in text for t in LIVE_KEYWORDS)
def live_search(query, max_results=5):
    if not LIVE_SEARCH_ENABLED or DDGS is None: return []
    try:
        out = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                if r.get("title") or r.get("body"): out.append(f"Title: {clean_ai_text(r.get('title',''))}\nInfo: {clean_ai_text(r.get('body',''))}")
        return out
    except Exception: return []
def save_memory(uid, content):
    content = clean_ai_text(re.sub(r"\s+", " ", str(content)).strip())
    if not content or len(content) > 500: return False
    if any(re.search(p, content, re.I) for p in [r"password", r"api[\s_-]?key", r"secret", r"access[\s_-]?token", r"verification[\s_-]?code", r"\b\d{16}\b"]): return False
    with get_db() as db:
        if db.execute("SELECT id FROM memories WHERE user_id=%s AND lower(content)=lower(%s) LIMIT 1", (uid, content)).fetchone(): return False
        db.execute("INSERT INTO memories (user_id, content) VALUES (%s,%s)", (uid, content))
    return True
def enhance_image_prompt(prompt):
    p = re.sub(r"\s+", " ", str(prompt).strip())[:900]
    low = p.lower()
    if any(w in low for w in ["2d","cartoon","anime","villain","concept","poster","character"]):
        return f"{p}, high quality character design, detailed, sharp, cinematic lighting, concept art, bright, clear, full body"
    return f"{p}, real photograph, natural light, realistic skin texture, real shadows, candid, natural colors, film grain, 35mm photo, bright, clear"
def generate_via_cloudflare_model(prompt, model, steps=4):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN: raise RuntimeError("No Cloudflare keys")
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{model}"
    r = requests.post(endpoint, headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}", "Content-Type": "application/json"}, json={"prompt": enhance_image_prompt(prompt), "num_steps": max(1, min(int(steps), 8))}, timeout=60)
    if r.status_code != 200: raise RuntimeError(f"Cloudflare {model} failed {r.status_code}: {r.text[:400]}")
    ct = r.headers.get("Content-Type","").lower()
    if ct.startswith("image/"): return f"data:{ct};base64,{base64.b64encode(r.content).decode()}"
    try:
        j = r.json(); img = (j.get("result") or {}).get("image")
        if img: return "data:image/jpeg;base64," + img
    except Exception: pass
    raise RuntimeError(f"Cloudflare {model} returned no image")
def generate_via_openai_image(prompt):
    if not OPENAI_API_KEY: raise RuntimeError("No OPENAI_API_KEY")
    r = requests.post("https://api.openai.com/v1/images/generations", headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}, json={"model": "gpt-image-1-mini", "prompt": enhance_image_prompt(prompt), "size": "1024x1024"}, timeout=90)
    if r.status_code != 200: raise RuntimeError(f"OpenAI image failed: {r.text[:500]}")
    data = r.json().get("data", [])
    if not data: raise RuntimeError("OpenAI image no data")
    if data[0].get("b64_json"): return "data:image/jpeg;base64," + data[0]["b64_json"]
    if data[0].get("url"):
        img = requests.get(data[0]["url"], timeout=60)
        if img.status_code == 200: return f"data:image/jpeg;base64,{base64.b64encode(img.content).decode()}"
    raise RuntimeError("OpenAI image no image")
def generate_via_goapi_midjourney(prompt):
    # Midjourney $10 Basic via GoAPI - polling
    if not GOAPI_API_KEY: raise RuntimeError("No GOAPI_API_KEY")
    r = requests.post("https://api.goapi.ai/api/v1/task", headers={"X-API-Key": GOAPI_API_KEY, "Content-Type": "application/json"}, json={"model": "midjourney", "task_type": "imagine", "input": {"prompt": enhance_image_prompt(prompt), "process_mode": "relax", "skip_prompt_check": False}}, timeout=30)
    if r.status_code not in (200,201): raise RuntimeError(f"GoAPI Midjourney failed: {r.text[:500]}")
    task_id = r.json().get("data", {}).get("task_id")
    if not task_id: raise RuntimeError("GoAPI no task_id")
    for _ in range(20):
        time.sleep(6)
        d = requests.get(f"https://api.goapi.ai/api/v1/task/{task_id}", headers={"X-API-Key": GOAPI_API_KEY}, timeout=30).json()
        out = d.get("data", {})
        if out.get("status") == "completed" and out.get("output", {}).get("image_url"):
            url = out["output"]["image_url"]
            img = requests.get(url, timeout=60)
            if img.status_code == 200: return f"data:image/jpeg;base64,{base64.b64encode(img.content).decode()}"
            return url
        if out.get("status") in ("failed","fail"): raise RuntimeError("Midjourney task failed")
    raise RuntimeError("Midjourney timed out")
def generate_flux_image(prompt, steps=4):
    errors = []
    try: return generate_via_cloudflare_model(prompt, FLUX_MODEL, steps)
    except Exception as e: errors.append(f"Cloudflare Flux: {e}")
    try: return generate_via_cloudflare_model(prompt, "@cf/stabilityai/stable-diffusion-xl-base-1.0", steps)
    except Exception as e: errors.append(f"Cloudflare SDXL: {e}")
    if GOAPI_API_KEY:
        try: return generate_via_goapi_midjourney(prompt)
        except Exception as e: errors.append(f"Midjourney GoAPI: {e}")
    if OPENAI_API_KEY:
        try: return generate_via_openai_image(prompt)
        except Exception as e: errors.append(f"OpenAI: {e}")
    raise RuntimeError("Image failed. " + " | ".join(errors)[:1200])
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
        if not bike: return "Boss, you do not have a bike assigned yet."
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
            return f"Done, Boss. Your bike {bike.get('name')} is locked."
        if any(w in low_msg for w in ["check","battery","status","how is"]):
            st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=%s", (mid,)).fetchone()
            tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
            sd = dict(st) if st else {}; td = dict(tel) if tel else {}
            return f"Boss, your bike {bike.get('name')} - Lock: {sd.get('lock_status','locked')}, Battery Health: {sd.get('battery_health',100)}%, Range: {sd.get('range_km',0)}km, Total Trip: {sd.get('total_trip',0)}, Last speed: {td.get('speed','no data')}. It is ready."
    return None
def handle_tabby_house_command(user, low_msg):
    if user["role"] != "owner": return "Sorry, house control is only for the owner, Boss."
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
    if device in ("door","house"): return f"Done, Boss. House {device} is now {action}."
    return f"Done, Boss. {room} {device} is now {action}."

# ===== WORKSPACE UI - OWNER + CUSTOMER ALVA =====
WORKSPACE_HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Tabby AI - Automotive Intelligence</title><style>*{box-sizing:border-box}body{margin:0;background:#070b16;color:#e6edff;font:15px system-ui,sans-serif}button,input,textarea{font:inherit}.layout{display:flex;min-height:100vh}.side{width:260px;background:#0a1122;border-right:1px solid #1b2742;padding:18px;display:flex;flex-direction:column;gap:18px;flex-shrink:0}.logo{display:flex;gap:10px;align-items:center}.logo-mark{width:38px;height:38px;border-radius:10px;background:#2b6cff;display:grid;place-items:center;font-weight:900}.nav{display:grid;gap:6px}.nav button{text-align:left;background:transparent;border:0;color:#aab8d6;padding:11px 12px;border-radius:10px;cursor:pointer}.nav button.active{background:#1a2a4d;color:#fff}.main{flex:1;display:flex;flex-direction:column;min-width:0}.top{padding:16px 22px;border-bottom:1px solid #1b2742;display:flex;justify-content:space-between;align-items:center}.chat{flex:1;overflow:auto;padding:20px;display:flex;flex-direction:column;gap:12px;max-width:900px;width:100%;margin:0 auto}.msg{max-width:92%;padding:13px 15px;border-radius:14px;line-height:1.55;white-space:pre-wrap}.msg.user{align-self:flex-end;background:#2b4aa5}.msg.assistant{align-self:flex-start;background:#141f38;border:1px solid #1e2d4d}.msg img{width:100%;max-width:620px;border-radius:12px;margin-top:10px;display:block}.composer{padding:14px 20px 10px;max-width:900px;width:100%;margin:0 auto}.composer-box{display:flex;gap:10px;background:#0f1a30;border:1px solid #26365e;border-radius:14px;padding:8px}.composer-box textarea{flex:1;background:transparent;border:0;color:#fff;resize:none;min-height:44px;padding:10px;outline:none}.composer-box button{background:#2b6cff;color:#fff;border:0;border-radius:10px;padding:0 16px;cursor:pointer}.tools{display:flex;gap:8px;flex-wrap:wrap;padding:8px 2px 14px}.tools button{background:#1a2a4d;color:#cbd6ec;border:1px solid #26365e;border-radius:20px;padding:7px 11px;font-size:12px;cursor:pointer}.auth{max-width:420px;margin:60px auto;padding:24px;background:#0f1a30;border:1px solid #26365e;border-radius:16px;display:grid;gap:12px;width:92%}.auth input{padding:13px;border-radius:10px;border:1px solid #34435f;background:#0b1427;color:#fff}.auth button{background:#2b6cff;color:#fff;border:0;padding:13px;border-radius:10px;cursor:pointer;font-weight:700}.tabs{display:flex;gap:8px}.tabs button{flex:1;background:#1c2942}.tabs button.active{background:#2b6cff}.pill{font-size:11px;padding:5px 9px;border-radius:20px;background:#1a2a4d}.pill.owner{background:#e11d48}.pill.online{background:#0f3d2a;color:#7ee2a8}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:18px}.card{background:#101a30;border:1px solid #1e2d4d;border-radius:14px;padding:16px}.card small{color:#8ea0c3;letter-spacing:.5px}.card b{font-size:28px;display:block;margin-top:8px}
#customerWrap{display:none;background:#000;min-height:100vh}.alva{max-width:430px;margin:0 auto;background:#0a0f0a;min-height:100vh;padding-bottom:80px}.alva-top{padding:18px;display:flex;justify-content:space-between;align-items:center}.alva-bike{background:linear-gradient(180deg,#101a12,#050705);border:1px solid #1e3a22;border-radius:18px;margin:0 14px;padding:16px;text-align:center}.alva-bike img{width:100%;border-radius:12px;max-height:220px;object-fit:cover}.alva-stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:14px}.alva-stat{background:#111a12;border:1px solid #1e3a22;border-radius:14px;padding:14px}.alva-stat b{font-size:22px;color:#2ee86b}.alva-btns{display:flex;gap:10px;justify-content:center;padding:6px}.alva-btns button{width:64px;height:64px;border-radius:50%;border:0;cursor:pointer;font-size:20px}.alva-map{margin:14px;border-radius:16px;overflow:hidden;border:1px solid #1e3a22}.alva-map iframe{width:100%;height:320px;border:0}.trip-card{margin:14px;background:#0e1a10;border:1px solid #1e3a22;border-radius:16px;padding:16px}.trip-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.trip-grid div{background:#0a120b;padding:10px;border-radius:10px}.bottom-nav{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:100%;max-width:430px;background:#0b120b;border-top:1px solid #1e3a22;display:flex;justify-content:space-around;padding:10px}.bottom-nav button{background:none;border:0;color:#8aa294;cursor:pointer;padding:8px}.bottom-nav button.active{color:#2ee86b}.green-btn{background:#2ee86b;color:#000;border:0;padding:13px;border-radius:10px;width:100%;font-weight:800;cursor:pointer}
@media(max-width:760px){.side{display:none}.cards{grid-template-columns:1fr 1fr}}
</style></head><body>
<div id="authWrap"><div class="auth"><div style="text-align:center"><div style="font-size:28px;font-weight:900">TABBY</div><div style="color:#8ea0c3;font-size:12px">AUTOMOTIVE INTELLIGENCE</div></div><div class="tabs"><button id="tabSignin" class="active" type="button">Sign In</button><button id="tabSignup" type="button">Create Account</button></div><div id="signinForm" style="display:grid;gap:12px"><input id="email" type="email" placeholder="Email address"><input id="password" type="password" placeholder="Password"><button id="login" type="button">Sign in</button></div><div id="signupForm" style="display:none;gap:12px"><input id="regName" placeholder="Full name"><input id="regEmail" type="email" placeholder="Email address"><input id="regPassword" type="password" placeholder="Password (8+ chars)"><button id="registerBtn" type="button">Create Account</button></div><div id="status"></div></div></div>
<div id="appWrap" hidden><div class="layout"><aside class="side"><div class="logo"><div class="logo-mark">T</div><div><b>TABBY</b><small style="color:#8ea0c3">AI & AUTOMOTIVE INTELLIGENCE</small></div></div><div><small style="color:#6f7f9e">MAIN MENU</small><div class="nav"><button data-view="overview" class="active" type="button">Overview</button><button data-view="chat" type="button">✦ Tabby AI</button><button data-view="auto" type="button">🚗 Automotive Dashboard</button><button data-view="ride" type="button">⚡ Tabby Ride</button></div></div><div><small style="color:#6f7f9e">ACCOUNT</small><div class="nav"><button type="button">👤 Account & Login</button><button type="button">🧠 Saved Memories</button><button type="button">⚙️ Settings</button></div></div><div style="margin-top:auto;color:#6f7f9e;font-size:11px">⚡ ULTIMATE GEE INDUSTRY<br>Signed in as <span id="userLine"></span></div><button id="btnLogout" style="background:#1a2a4d;color:#fff;border:0;padding:10px;border-radius:10px;cursor:pointer" type="button">Log Out</button></aside>
<main class="main"><div class="top"><div><h2 id="viewTitle" style="margin:0">Overview</h2><p id="viewSub" style="color:#8ea0c3">Your personal AI and automotive intelligence platform.</p></div><div style="display:flex;gap:8px"><span class="pill" id="namePill"></span><span class="pill online">● SYSTEM ONLINE</span></div></div>
<div id="overviewView"><div class="cards"><div class="card"><small>MOTORCYCLES IN SYSTEM</small><b id="statBikes">0</b><span style="color:#8ea0c3;font-size:12px">Registered fleet</span></div><div class="card"><small>REGISTERED ACCOUNTS</small><b id="statAccounts">1</b><span style="color:#8ea0c3;font-size:12px">Owner and customer accounts</span></div><div class="card"><small>AUTOMOTIVE CONNECTION</small><b id="statConn" style="font-size:16px">awaiting connection</b><span style="color:#8ea0c3;font-size:12px">Hardware integration status</span></div><div class="card"><small>YOUR ROLE</small><b id="statRole" style="font-size:16px">OWNER</b><span style="color:#8ea0c3;font-size:12px">Full access</span></div></div><div style="padding:0 18px"><div class="card"><small>OWNER WORKSPACE</small><p style="color:#8ea0c3">Manage Tabby AI, motorcycle records, customer assignments and fleet information.</p><div style="display:flex;gap:8px;flex-wrap:wrap"><button onclick="showView('chat')" style="background:#2b6cff;color:#fff;border:0;padding:10px 14px;border-radius:20px;cursor:pointer">Open Tabby AI</button><button onclick="showView('auto')" style="background:#1a2a4d;color:#fff;border:0;padding:10px 14px;border-radius:20px;cursor:pointer">Manage fleet</button><button onclick="showView('ride')" style="background:#1a2a4d;color:#fff;border:0;padding:10px 14px;border-radius:20px;cursor:pointer">Manage memories</button></div></div></div></div>
<div id="autoView" style="display:none"><div class="cards"><div class="card"><small>REGISTERED MOTORCYCLES</small><b id="autoBikes">0</b><span style="color:#8ea0c3;font-size:12px">Vehicles on your account</span></div><div class="card"><small>SELECTED MOTORCYCLE</small><b id="autoSelected" style="font-size:16px">None</b><span style="color:#8ea0c3;font-size:12px">Currently selected</span></div><div class="card"><small>LATEST BATTERY READING</small><b id="autoBatt">--</b><span style="color:#8ea0c3;font-size:12px">Requires connected telemetry</span></div><div class="card"><small>SYSTEM CONNECTION</small><b style="font-size:16px">API Ready</b><span style="color:#8ea0c3;font-size:12px">Hardware connection not yet assumed</span></div></div><div style="padding:0 18px 18px"><h3>Your Motorcycles</h3><div id="ownerBikeList" class="card">Loading your motorcycles...</div><h3 style="margin-top:18px">Motorcycle Dashboard</h3><div class="card"><p style="color:#8ea0c3">Select a motorcycle to view its readings.</p><div class="cards" style="padding:0"><div class="card"><small>🔋 BATTERY</small><b id="dashBatt">--</b></div><div class="card"><small>📍 ESTIMATED RANGE</small><b id="dashRange">--</b></div><div class="card"><small>⚡ SPEED</small><b id="dashSpeed">--</b></div><div class="card"><small>🔧 DIAGNOSTICS</small><b id="dashDiag">--</b></div></div></div></div></div>
<div id="rideView" style="display:none"><div class="cards"><div class="card"><small>MY ACCOUNT</small><b id="rideName">Owner</b><span style="color:#8ea0c3;font-size:12px">Owner account - sees all</span></div><div class="card"><small>MY MOTORCYCLES</small><b id="rideBikes">0</b><span style="color:#8ea0c3;font-size:12px">Registered to your account</span></div><div class="card"><small>PAYMENT RECORDS</small><b id="ridePayments">0</b><span style="color:#8ea0c3;font-size:12px">Records available</span></div><div class="card"><small>SECURITY</small><b style="font-size:16px">Account</b><span style="color:#8ea0c3;font-size:12px">Sign-in protected</span></div></div><div style="padding:0 18px 18px"><div class="card"><b>Owner - Customer Motorcycle Access</b><p style="color:#8ea0c3">As owner you see ALL customers and ALL bikes.</p><div id="ownerCustomerList">Loading customers...</div></div></div></div>
<div id="chatView" style="display:none;flex-direction:column;flex:1"><div id="messages" class="chat"><div style="color:#8ea0c3;text-align:center">You are connected. Send Tabby a message to begin.</div></div><div id="voiceStatus" style="text-align:center;color:#7ee2a8"></div><div class="composer"><form id="composerForm" class="composer-box"><textarea id="message" placeholder="Message Tabby..."></textarea><button id="send" type="submit">➤</button></form><div class="tools"><button id="btnVoiceNote" type="button">🎤 Voice Input</button><button id="btnLiveCall" type="button">📞 Live Call</button><button id="btnImage" type="button">🖼️ Generate Image</button><button id="btnLoc" type="button">📍 Share My Location</button><button id="btnMyBike" type="button">🏍️ My Bike</button><button id="btnPerm" type="button">📱 Phone Permissions</button></div></div></div>
</main></div></div>
<div id="customerWrap"><div class="alva"><div class="alva-top"><div><div style="color:#8aa294;font-size:12px">Hello,</div><b id="custName">Customer</b><div style="color:#8aa294;font-size:12px">Welcome back</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:900;color:#2ee86b">⚡ ALVA</div><div style="color:#8aa294;font-size:11px">Tabby Ride</div></div></div>
<div id="custHome"><div class="alva-bike"><div style="display:flex;justify-content:space-between"><span style="color:#8aa294;font-size:12px" id="custBikeName">Tabby Bike</span><span style="background:#2ee86b;color:#000;padding:4px 8px;border-radius:20px;font-size:11px;font-weight:800">App connected</span></div><img id="custBikeImg" src="https://images.unsplash.com/photo-1558981403-c5f9899a28bc?w=800&q=80" alt="My Bike"><div style="display:flex;justify-content:space-between;margin-top:10px"><span style="font-size:22px;font-weight:900" id="custRange">125 km</span><span style="font-size:22px;font-weight:900;color:#2ee86b" id="custBatt">100%</span></div><div style="height:8px;background:#1a2a1a;border-radius:10px;overflow:hidden"><div id="custBattBar" style="width:100%;height:100%;background:#2ee86b"></div></div><div style="color:#8aa294;font-size:12px;margin-top:6px">Range • Battery • Please turn off using App</div></div><div class="alva-stats"><div class="alva-stat"><small style="color:#8aa294">TOTAL TRIP</small><b id="custTrips">0</b></div><div class="alva-stat"><small style="color:#8aa294">TOTAL DISTANCE</small><b id="custDist">0 km</b></div></div><div class="alva-btns"><button style="background:#1a2a1a;color:#fff" onclick="custTab('home')">🚲<br><span style="font-size:10px">Trip</span></button><button style="background:#2ee86b" onclick="sendCustMsg('Unlock my bike')">⚡</button><button style="background:#1a2a1a;color:#fff" onclick="custTab('home')">🆘<br><span style="font-size:10px">Emergency</span></button></div><div style="padding:14px"><button class="green-btn" onclick="sendCustMsg('Check my bike')">CHECK MY BIKE →</button></div><div id="custChat" style="padding:0 14px;display:grid;gap:10px"></div></div>
<div id="custMap" style="display:none"><div style="padding:14px;display:flex;gap:8px"><button style="flex:1;background:#2ee86b;border:0;padding:10px;border-radius:20px;font-weight:700">Charging Station</button><button style="flex:1;background:#1a2a1a;color:#fff;border:0;padding:10px;border-radius:20px">Service Station</button></div><div class="alva-map"><iframe id="custMapFrame" src="https://www.google.com/maps?q=Lagos&output=embed"></iframe></div><div style="padding:14px"><button class="green-btn" onclick="setHomeLoc()">📍 SET HOME LOCATION</button></div></div>
<div id="custTrip" style="display:none"><div class="trip-card"><div style="display:flex;justify-content:space-between"><b>Daily Trip</b><span style="color:#2ee86b">EDIT ✎</span></div><div style="text-align:center;padding:16px"><div style="font-size:28px;font-weight:900" id="tripDate">Today</div><div style="color:#8aa294">Daily Trip Report</div></div><div class="trip-grid"><div><small style="color:#8aa294">Total Trip</small><b id="tripTrips">0 Trips</b></div><div><small style="color:#8aa294">Total Distance</small><b id="tripDist">0 km</b></div><div><small style="color:#8aa294">Total Time</small><b id="tripTime">00:00:00</b></div><div><small style="color:#8aa294">CO2 Saving</small><b id="tripCO2">0 kg</b></div></div><button class="green-btn" style="margin-top:14px">SHARE</button></div></div>
<div class="bottom-nav"><button class="active" id="navHome" onclick="custTab('home')">🏠<br>Explore</button><button id="navMap" onclick="custTab('map')">📍<br>My ALVA</button><button id="navTrip" onclick="custTab('trip')">📊<br>Locate</button><button onclick="custLogout()">🚪<br>Logout</button></div></div></div>
<script>
const $=id=>document.getElementById(id);let token=localStorage.getItem("tabby_token")||"";let currentUser=null;
function custTab(t){["custHome","custMap","custTrip"].forEach(id=>$(id).style.display="none");["navHome","navMap","navTrip"].forEach(id=>$(id).classList.remove("active"));if(t==="home"){$("custHome").style.display="block";$("navHome").classList.add("active")}if(t==="map"){$("custMap").style.display="block";$("navMap").classList.add("active")}if(t==="trip"){$("custTrip").style.display="block";$("navTrip").classList.add("active")}}
function custLogout(){localStorage.removeItem("tabby_token");location.reload()}
function showView(v){["overviewView","autoView","rideView","chatView"].forEach(id=>$(id).style.display="none");document.querySelectorAll("[data-view]").forEach(b=>b.classList.remove("active"));if(v==="overview"){$("overviewView").style.display="block";$("viewTitle").textContent="Overview";$("viewSub").textContent="Your personal AI and automotive intelligence platform."}if(v==="chat"){$("chatView").style.display="flex";$("viewTitle").textContent="Tabby AI";$("viewSub").textContent="Your private AI workspace. One long chat."}if(v==="auto"){$("autoView").style.display="block";$("viewTitle").textContent="Tabby Automotive";$("viewSub").textContent="Your motorcycle intelligence and management centre.";loadOwnerData()}if(v==="ride"){$("rideView").style.display="block";$("viewTitle").textContent="Tabby Ride";$("viewSub").textContent="Owner view - all customers, all motorcycles.";loadOwnerData()}document.querySelector(`[data-view="${v}"]`)?.classList.add("active")}
document.querySelectorAll("[data-view]").forEach(b=>b.addEventListener("click",()=>showView(b.dataset.view)));
async function api(path,opt={}){const h={...(opt.headers||{})};if(token)h.Authorization="Bearer "+token;if(opt.body)h["Content-Type"]="application/json";const r=await fetch(path,{...opt,headers:h});const d=await r.json().catch(()=>({error:"Server error"}));if(!r.ok)throw new Error(d.error||"Request failed");return d}
async function loadOwnerData(){try{const d=await api("/api/owner/dashboard");$("statBikes").textContent=d.total_bikes;$("statAccounts").textContent=d.total_accounts;$("autoBikes").textContent=d.total_bikes;$("rideBikes").textContent=d.total_bikes;$("ridePayments").textContent=d.total_payments;if(d.bikes?.length){const b=d.bikes[0];$("autoSelected").textContent=b.name;$("autoBatt").textContent=(b.battery_health||100)+"%";$("dashBatt").textContent=(b.battery_health||100)+"%";$("dashRange").textContent=(b.range_km||0)+" km";$("dashSpeed").textContent=(b.speed||0)+" km/h";$("dashDiag").textContent=b.lock_status||"locked";$("ownerBikeList").innerHTML=d.bikes.map(x=>`<div style="padding:10px;border-bottom:1px solid #1e2d4d"><b>${x.name}</b> - ${x.model||""} - Battery ${x.battery_health||100}% - ${x.lock_status} - Assigned to ${x.assigned_name||"No one"}</div>`).join("")}else{$("ownerBikeList").innerHTML="No motorcycles yet."}if(d.customers?.length){$("ownerCustomerList").innerHTML=d.customers.map(c=>`<div style="padding:10px;border-bottom:1px solid #1e2d4d"><b>${c.name}</b> (${c.email}) - Bike: ${c.bike_name||"None"}</div>`).join("")}else{$("ownerCustomerList").innerHTML="No customers yet."}}catch(e){if($("ownerBikeList"))$("ownerBikeList").textContent="Failed to load: "+e.message}}
async function loadCustomerData(){try{const d=await api("/api/customer/bike");$("custName").textContent=currentUser.name;$("custBikeName").textContent=d.bike?.name||"Tabby Bike";$("custRange").textContent=(d.bike?.range_km||125)+" km";$("custBatt").textContent=(d.bike?.battery_health||100)+"%";$("custBattBar").style.width=(d.bike?.battery_health||100)+"%";$("custTrips").textContent=d.trip?.total_trip||0;$("custDist").textContent=(d.trip?.total_distance||0)+" km";$("tripTrips").textContent=(d.trip?.total_trip||0)+" Trips";$("tripDist").textContent=(d.trip?.total_distance||0)+" km";$("tripCO2").textContent=(d.trip?.co2_saved||0)+" kg";$("tripDate").textContent=new Date().toLocaleDateString("en-GB",{day:"numeric",month:"long",year:"numeric"});if(d.bike?.latitude){$("custMapFrame").src=`https://www.google.com/maps?q=${d.bike.latitude},${d.bike.longitude}&output=embed`}}catch(e){console.log(e)}}
function addMsg(role,text,img){const box=$("messages");const d=document.createElement("div");d.className="msg "+role;d.textContent=text||"";if(img){const im=document.createElement("img");im.src=img;box.appendChild(d);d.appendChild(im)}else box.appendChild(d);box.scrollTop=box.scrollHeight}
function addCustMsg(role,text){const box=$("custChat");const d=document.createElement("div");d.style.cssText="background:"+(role==="user"?"#2b4aa5":"#141f38")+";padding:10px;border-radius:12px";d.textContent=text;box.appendChild(d)}
async function sendCustMsg(text){addCustMsg("user",text);try{const d=await api("/api/chat",{method:"POST",body:JSON.stringify({message:text})});addCustMsg("assistant",d.reply||"Done.");loadCustomerData()}catch(e){addCustMsg("assistant","Sorry: "+e.message)}}
function setHomeLoc(){if(!navigator.geolocation)return alert("Allow location");navigator.geolocation.getCurrentPosition(async p=>{await api("/api/location",{method:"POST",body:JSON.stringify({latitude:p.coords.latitude,longitude:p.coords.longitude})});alert("Home location saved.");loadCustomerData()})}
$("tabSignin").addEventListener("click",()=>{$("tabSignin").classList.add("active");$("tabSignup").classList.remove("active");$("signinForm").style.display="grid";$("signupForm").style.display="none"});
$("tabSignup").addEventListener("click",()=>{$("tabSignup").classList.add("active");$("tabSignin").classList.remove("active");$("signinForm").style.display="none";$("signupForm").style.display="grid"});
$("login").addEventListener("click",async()=>{$("status").textContent="Signing in...";try{const d=await api("/api/login",{method:"POST",body:JSON.stringify({email:$("email").value.trim(),password:$("password").value})});token=d.token;currentUser=d.user;localStorage.setItem("tabby_token",token);afterLogin()}catch(e){$("status").textContent=e.message}});
$("registerBtn").addEventListener("click",async()=>{try{const d=await api("/api/register",{method:"POST",body:JSON.stringify({name:$("regName").value.trim(),email:$("regEmail").value.trim(),password:$("regPassword").value})});token=d.token;currentUser=d.user;localStorage.setItem("tabby_token",token);afterLogin()}catch(e){$("status").textContent=e.message}});
$("btnLogout").addEventListener("click",()=>{localStorage.removeItem("tabby_token");location.reload()});
async function afterLogin(){$("authWrap").style.display="none";if(currentUser.role==="owner"){$("appWrap").hidden=false;$("customerWrap").style.display="none";$("userLine").textContent=currentUser.name;$("namePill").textContent=currentUser.name+" OWNER";$("statRole").textContent="OWNER";showView("overview");loadOwnerData();const s=await api("/api/chat/single");s.messages.forEach(m=>addMsg(m.role,m.content))}else{$("appWrap").hidden=true;$("customerWrap").style.display="block";loadCustomerData();addCustMsg("assistant","Hello "+currentUser.name+", welcome to Tabby Ride. Your bike is connected.")}}
async function sendMsg(text){addMsg("user",text);try{const d=await api("/api/chat",{method:"POST",body:JSON.stringify({message:text})});addMsg("assistant",d.reply||"Done.",d.image||d.image_url)}catch(e){addMsg("assistant","Sorry: "+e.message)}}
$("composerForm").addEventListener("submit",e=>{e.preventDefault();const v=$("message").value.trim();if(!v)return;$("message").value="";sendMsg(v)});
$("btnImage").addEventListener("click",()=>{const p=prompt("What image do you want?");if(p)sendMsg("Generate image: "+p)});
$("btnLoc").addEventListener("click",()=>{navigator.geolocation?.getCurrentPosition(async pos=>{await api("/api/location",{method:"POST",body:JSON.stringify({latitude:pos.coords.latitude,longitude:pos.coords.longitude})});addMsg("assistant","Location saved.")})});
$("btnMyBike").addEventListener("click",()=>sendMsg("Check my bike"));
$("btnPerm").addEventListener("click",async()=>{const d=await api("/api/phone/permissions");addMsg("assistant","Phone permissions: "+JSON.stringify(d.permissions))});
$("btnVoiceNote").addEventListener("click",()=>addMsg("assistant","Hold Voice Input to talk - I am listening, Boss."));
$("btnLiveCall").addEventListener("click",()=>addMsg("assistant","Live Call - please use Voice Input for now, Boss."));
if(token){api("/api/me").then(d=>{currentUser=d.user;afterLogin()}).catch(()=>{localStorage.removeItem("tabby_token")})}else{$("customerWrap").style.display="none"}
</script></body></html>'''

@app.route("/", methods=["GET"])
def home():
    return WORKSPACE_HTML, 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"success": True, "name": "Tabby", "creator": CREATOR_NAME, "status": "online", "version": "15.0-owner-customer-alva", "database_ready": DATABASE_READY, "image_providers": {"cloudflare_flux": bool(CLOUDFLARE_API_TOKEN), "midjourney_goapi": bool(GOAPI_API_KEY), "openai": bool(OPENAI_API_KEY)}, "time": current_time()})
@app.route("/api/status", methods=["GET"])
def api_status(): return jsonify({"success": True, "name": "Tabby", "creator": CREATOR_NAME, "status": "online", "version": "15.0"})
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
@app.route("/api/owner/dashboard", methods=["GET"])
@owner_required
def owner_dashboard():
    with get_db() as db:
        bikes = db.execute("SELECT m.*, u2.name as assigned_name, bs.battery_health, bs.range_km, bs.lock_status, t.speed, t.latitude, t.longitude FROM motorcycles m LEFT JOIN users u2 ON u2.id=m.assigned_user_id LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id LEFT JOIN telemetry t ON t.id=(SELECT MAX(id) FROM telemetry WHERE motorcycle_id=m.id) ORDER BY m.id DESC", ()).fetchall()
        custs = db.execute("SELECT u.id,u.name,u.email,m.name as bike_name FROM users u LEFT JOIN motorcycles m ON m.assigned_user_id=u.id WHERE u.role='customer' ORDER BY u.id DESC", ()).fetchall()
        total_acc = db.execute("SELECT COUNT(*) as c FROM users", ()).fetchone()["c"]
        total_pay = db.execute("SELECT COUNT(*) as c FROM payments", ()).fetchone()["c"]
    return jsonify({"success": True, "total_bikes": len(bikes), "total_accounts": total_acc, "total_payments": total_pay, "bikes": [dict(r) for r in bikes], "customers": [dict(r) for r in custs]})
@app.route("/api/customer/bike", methods=["GET"])
@auth_required
def customer_bike():
    u = request.current_user
    with get_db() as db:
        if u["role"] == "owner":
            bike = db.execute("SELECT * FROM motorcycles ORDER BY id DESC LIMIT 1", ()).fetchone()
        else:
            bike = db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=%s ORDER BY id DESC LIMIT 1", (u["id"],)).fetchone()
        if not bike:
            return jsonify({"success": True, "bike": None, "trip": {"total_trip": 0, "total_distance": 0, "co2_saved": 0}})
        bike = dict(bike)
        st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=%s", (bike["id"],)).fetchone()
        tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (bike["id"],)).fetchone()
        if st: bike.update(dict(st))
        if tel: bike.update({"speed": dict(tel).get("speed"), "latitude": dict(tel).get("latitude"), "longitude": dict(tel).get("longitude")})
    return jsonify({"success": True, "bike": bike, "trip": {"total_trip": bike.get("total_trip", 0), "total_distance": bike.get("total_distance", 0), "co2_saved": bike.get("co2_saved", 0)}})
@app.route("/api/owner/create-customer", methods=["POST"])
@owner_required
def owner_create_customer():
    data = request.get_json(silent=True) or {}
    name = clean_ai_text(str(data.get("name","")).strip()); email = str(data.get("email","")).strip().lower(); password = str(data.get("password","") or "Tabby1234")
    bike_name = clean_ai_text(str(data.get("bike_name","")).strip())
    if not name or not email: return json_error("Customer name and email required.")
    with get_db() as db:
        if db.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone(): return json_error("Customer already exists.", 409)
        cur = db.execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,'customer')", (name, email, generate_password_hash(password)))
        cust_id = cur.lastrowid; bike_id = None
        if bike_name:
            bcur = db.execute("INSERT INTO motorcycles (owner_id, assigned_user_id, name, model, registration_number) VALUES (%s,%s,%s,%s,%s)", (request.current_user["id"], cust_id, bike_name, "Tabby EV", ""))
            bike_id = bcur.lastrowid
            db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (bike_id,))
    return jsonify({"success": True, "user": {"id": cust_id, "name": name, "email": email, "role": "customer"}, "bike_id": bike_id, "message": f"Customer {name} created."}), 201
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
    return jsonify({"success": True, "bike_id": cur.lastrowid, "message": f"Bike {bike_name} assigned."})
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
    perm = str(data.get("permission") or "").strip(); action_text = str(data.get("action") or "").strip()
    if perm not in PHONE_PERMISSIONS: return json_error("Invalid permission.")
    grant_phone_permission(u["id"], perm)
    with get_db() as db:
        db.execute("INSERT INTO phone_actions (user_id, action, target, status) VALUES (%s,%s,%s,'approved')", (u["id"], perm, action_text[:200]))
    action_url = None
    if perm == "send_email": action_url = "mailto:?subject=Message from Tabby&body=" + urllib.parse.quote(action_text)
    if perm == "send_sms": action_url = "sms:?body=" + urllib.parse.quote(action_text)
    if perm == "open_mail": action_url = "mailto:"
    if perm == "open_maps": action_url = "https://www.google.com/maps"
    return jsonify({"success": True, "message": f"Permission granted for {perm}, Boss.", "permission": perm, "action_url": action_url})
@app.route("/api/location", methods=["POST"])
@auth_required
def save_loc():
    u = request.current_user; data = request.get_json(silent=True) or {}
    try: lat = float(data.get("latitude")); lng = float(data.get("longitude"))
    except Exception: return json_error("Invalid location.")
    with get_db() as db:
        db.execute("INSERT INTO locations (user_id, latitude, longitude, label) VALUES (%s,%s,%s,%s)", (u["id"], lat, lng, "phone_location"))
    return jsonify({"success": True, "message": "Location saved."})
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
    if not message: return json_error("Message required.")
    # Phone permission check
    perm = detect_sensitive_action(message)
    if perm and not has_phone_permission(user["id"], perm):
        return jsonify({"success": True, "needs_permission": True, "permission": perm, "reply": f"Boss, do you want me to allow {perm}? Say Yes to allow.", "response": f"Boss, do you want me to allow {perm}? Say Yes to allow."})
    # Bike / House quick commands
    low = message.lower()
    if "going home" in low:
        bike_reply = handle_tabby_bike_command(user, "unlock my bike") or ""
        handle_tabby_house_command(user, "turn on light living")
        handle_tabby_house_command(user, "turn on ac living")
        reply = f"Done, Boss. Going Home scene is running. {bike_reply} Light is on, AC is on."
        cid = get_single_conversation_id(user["id"])
        with get_db() as db:
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "user", message))
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "assistant", reply))
        return jsonify({"success": True, "reply": reply, "response": reply})
    bike_reply = handle_tabby_bike_command(user, low)
    if bike_reply:
        cid = get_single_conversation_id(user["id"])
        with get_db() as db:
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "user", message))
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "assistant", bike_reply))
        return jsonify({"success": True, "reply": bike_reply, "response": bike_reply})
    house_reply = handle_tabby_house_command(user, low)
    if house_reply:
        cid = get_single_conversation_id(user["id"])
        with get_db() as db:
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "user", message))
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "assistant", house_reply))
        return jsonify({"success": True, "reply": house_reply, "response": house_reply})
    # Image
    if is_image_request(message):
        try:
            img = generate_flux_image(clean_image_prompt(message))
            cid = get_single_conversation_id(user["id"])
            with get_db() as db:
                db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "user", message))
                db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "assistant", "Here is your image, Boss."))
            return jsonify({"success": True, "reply": "Here is your image, Boss.", "response": "Here is your image, Boss.", "image": img, "image_url": img})
        except Exception as e:
            return jsonify({"success": True, "reply": f"Image generation is active, Boss. Just tell me what image you want. Detail: {clean_ai_text(str(e))[:200]}", "response": "Image generation is active, Boss."})
    # AI Chat
    try:
        memories = get_memories(user["id"], 20)
        loc = get_latest_location(user["id"])
        loc_text = f"{loc.get('latitude')},{loc.get('longitude')}" if loc else "No location yet."
        bike_ctx = get_bike_context_for_user(user)
        search = live_search(message, 3) if needs_live_search(message) else []
        system = build_system_prompt(user, memories, loc_text, bike_ctx)
        if search: system += "\nLIVE SEARCH:\n" + "\n".join(search)
        cid = get_single_conversation_id(user["id"])
        with get_db() as db:
            hist = db.execute("SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY id DESC LIMIT 10", (cid,)).fetchall()
        hist = list(reversed([dict(r) for r in hist]))
        msgs = [{"role": "system", "content": system}] + [{"role": h["role"], "content": h["content"]} for h in hist] + [{"role": "user", "content": message}]
        reply = call_ai_with_fallback(msgs)
        with get_db() as db:
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "user", message))
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "assistant", reply))
        return jsonify({"success": True, "reply": reply, "response": reply})
    except Exception as e:
        return jsonify({"success": True, "reply": f"Sorry Boss, I had a small issue: {clean_ai_text(str(e))[:300]}", "response": "Sorry Boss."})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)