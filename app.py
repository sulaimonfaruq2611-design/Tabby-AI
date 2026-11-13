import os, re, json, time, base64, logging, urllib.parse, urllib.request, xml.etree.ElementTree as ET, threading
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
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret")
CORS(app, resources={r"/*": {"origins": "*"}})
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tabby")

CREATOR_NAME = "Faruq Sulaimon"
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "").strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
SUPABASE_DATABASE_URL = (os.getenv("SUPABASE_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"
GOAPI_API_KEY = os.getenv("GOAPI_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama3.1-8b").strip()
LIVE_SEARCH_ENABLED = os.getenv("LIVE_SEARCH_ENABLED", "true").lower() == "true"

serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
AI_MODELS = list(dict.fromkeys([OPENROUTER_MODEL, "openrouter/free"]))
AUTO_LEARNED = {"last": "", "news": []}

def clean_ai_text(t):
    if not t:
        return ""
    t = str(t)
    t = re.sub(r"\*+", "", t)
    t = re.sub(r"_+", "", t)
    t = re.sub(r"#+", "", t)
    return re.sub(r"\s+", " ", t).strip()

def get_greeting_lagos():
    lagos = datetime.now(timezone.utc) + timedelta(hours=1)
    h = lagos.hour
    if 5 <= h < 12:
        return "Good morning, Boss"
    if 12 <= h < 17:
        return "Good afternoon, Boss"
    return "Good evening, Boss"

def current_time():
    return datetime.now(timezone.utc).isoformat()

# ---------- DATABASE ----------
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
        self._cursor = cursor
        self.lastrowid = lastrowid
    def fetchone(self):
        try:
            r = self._cursor.fetchone()
        except Exception:
            return None
        return dict(r) if r else None
    def fetchall(self):
        try:
            rows = self._cursor.fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]

class PGConnection:
    def __init__(self):
        self._conn = _get_pool().getconn()
        self._conn.autocommit = False
        self._cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        self._closed = False
    def execute(self, sql, params=()):
        original = sql.strip()
        sql2 = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO").replace("?", "%s")
        is_insert = original.upper().startswith("INSERT")
        if "INSERT OR IGNORE" in original.upper():
            if "RETURNING" not in sql2.upper():
                sql2 = sql2 + " ON CONFLICT DO NOTHING"
            else:
                sql2 = sql2.replace(" RETURNING", " ON CONFLICT DO NOTHING RETURNING")
        needs = False
        if is_insert and "RETURNING" not in sql2.upper() and "ON CONFLICT DO NOTHING" not in sql2.upper() and "INTO bike_status" not in sql2.lower() and "INTO phone_permissions" not in sql2.lower():
            sql2 += " RETURNING id"
            needs = True
        if params is None:
            params = ()
        self._cur.execute(sql2, tuple(params) if isinstance(params, (list, tuple)) else (params,))
        lastrowid = None
        if needs:
            try:
                row = self._cur.fetchone()
                if row:
                    lastrowid = row.get("id")
            except Exception:
                pass
        return PGResult(self._cur, lastrowid=lastrowid)
    def executescript(self, sql):
        self._cur.execute(sql)
    def commit(self):
        try:
            self._conn.commit()
        except Exception:
            pass
    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass
    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._cur.close()
        except Exception:
            pass
        try:
            _get_pool().putconn(self._conn)
        except Exception:
            pass
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.rollback()
            else:
                self.commit()
        finally:
            self.close()
        return False

def get_db():
    return PGConnection()

def init_db():
    try:
        with get_db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'customer', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS conversations (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT DEFAULT 'Tabby Chat', created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, conversation_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS memories (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, content TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS motorcycles (id SERIAL PRIMARY KEY, owner_id INTEGER, assigned_user_id INTEGER, name TEXT NOT NULL, model TEXT, registration_number TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS telemetry (id SERIAL PRIMARY KEY, motorcycle_id INTEGER NOT NULL, latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, speed DOUBLE PRECISION, battery_level DOUBLE PRECISION, fuel_level DOUBLE PRECISION, engine_temperature DOUBLE PRECISION, extra_data TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS payments (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, motorcycle_id INTEGER, amount DOUBLE PRECISION NOT NULL, currency TEXT DEFAULT 'NGN', status TEXT DEFAULT 'pending', reference TEXT UNIQUE, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS locations (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, label TEXT, accuracy DOUBLE PRECISION, speed DOUBLE PRECISION, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS bike_status (motorcycle_id INTEGER PRIMARY KEY, battery_health DOUBLE PRECISION DEFAULT 100, range_km DOUBLE PRECISION DEFAULT 125, total_trip DOUBLE PRECISION DEFAULT 0, total_distance DOUBLE PRECISION DEFAULT 0, co2_saved DOUBLE PRECISION DEFAULT 0, lock_status TEXT DEFAULT 'locked', access_mode TEXT DEFAULT 'full', last_seen TEXT);
            CREATE TABLE IF NOT EXISTS payment_plans (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, motorcycle_id INTEGER NOT NULL, total_amount DOUBLE PRECISION NOT NULL, deposit DOUBLE PRECISION DEFAULT 0, installment_amount DOUBLE PRECISION NOT NULL, installments_paid INTEGER DEFAULT 0, installments_total INTEGER NOT NULL, next_due_date TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS bike_commands (id SERIAL PRIMARY KEY, motorcycle_id INTEGER NOT NULL, command TEXT NOT NULL, issued_by INTEGER NOT NULL, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS house_devices (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, room TEXT DEFAULT 'living', type TEXT DEFAULT 'light', status TEXT DEFAULT 'off', value TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS tabby_logs (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, device TEXT, command TEXT, action TEXT, result TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS phone_permissions (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, permission TEXT NOT NULL, granted BOOLEAN DEFAULT FALSE, granted_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(user_id, permission));
            CREATE TABLE IF NOT EXISTS phone_actions (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS auto_learned (id SERIAL PRIMARY KEY, topic TEXT, content TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
            """)
            if OWNER_EMAIL:
                db.execute("UPDATE users SET role='owner' WHERE lower(email)=%s", (OWNER_EMAIL,))
        return True
    except Exception:
        logger.exception("DB init failed")
        return False

DATABASE_READY = init_db()

def json_error(m, s=400):
    return jsonify({"success": False, "error": clean_ai_text(m)}), s

def get_user_by_id(uid):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
    return dict(row) if row else None

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

def get_memories(uid, limit=20):
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
        if row:
            return row["id"]
        cur = db.execute("INSERT INTO conversations (user_id,title) VALUES (%s,%s)", (user_id, "Tabby Chat"))
        return cur.lastrowid

def save_memory(uid, content):
    content = clean_ai_text(re.sub(r"\s+", " ", str(content)).strip())
    if not content or len(content) > 500:
        return False
    if any(re.search(p, content, re.I) for p in [r"password", r"api[\s_-]?key", r"secret", r"access[\s_-]?token", r"verification[\s_-]?code", r"\b\d{16}\b"]):
        return False
    with get_db() as db:
        if db.execute("SELECT id FROM memories WHERE user_id=%s AND lower(content)=lower(%s) LIMIT 1", (uid, content)).fetchone():
            return False
        db.execute("INSERT INTO memories (user_id, content) VALUES (%s,%s)", (uid, content))
    return True

# ---------- AI ----------
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
    contents = []
    for m in messages:
        if m["role"] == "system":
            system_text += str(m.get("content", "")) + "\n"
            continue
        contents.append({"role": "user" if m["role"] == "user" else "model", "parts": [{"text": str(m.get("content", ""))}]})
    if not contents:
        raise RuntimeError("No user message for Gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    r = requests.post(url, headers={"Content-Type": "application/json"}, json={"system_instruction": {"parts": [{"text": system_text}]}, "contents": contents, "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini failed: {r.text[:800]}")
    cands = r.json().get("candidates") or []
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
    ch = r.json().get("choices") or []
    if not ch:
        raise RuntimeError("Groq no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    return clean_ai_text(str(ans).strip())

def call_cerebras(messages, temperature=0.7, max_tokens=1800):
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY not configured.")
    r = requests.post("https://api.cerebras.ai/v1/chat/completions", headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}, json={"model": CEREBRAS_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Cerebras failed: {r.text[:800]}")
    ch = r.json().get("choices") or []
    if not ch:
        raise RuntimeError("Cerebras no answer")
    ans = (ch[0].get("message") or {}).get("content", "")
    return clean_ai_text(str(ans).strip())

def call_ai_with_fallback(messages, temperature=0.7, max_tokens=1800):
    errors = []
    for fn, name in [(call_openrouter, "OpenRouter"), (call_gemini, "Gemini"), (call_groq, "Groq"), (call_cerebras, "Cerebras")]:
        try:
            return fn(messages, temperature=temperature, max_tokens=max_tokens)
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError(" | ".join(errors)[:1000])

def build_system_prompt(user, memories, location_text="", bike_context=""):
    mem = "\n".join("- " + clean_ai_text(m["content"]) for m in memories) or "No saved memories yet."
    role_text = "OWNER - Full access to house, all bikes, all customers." if user["role"] == "owner" else "CUSTOMER - Limited access: only own bike, own payments, own location, chat."
    auto = "\n".join(AUTO_LEARNED.get("news", [])[:5])
    return (
        "You are Tabby, assistant for Faruq Sulaimon. Faruq Sulaimon is your Boss, creator and owner. Always call him Boss. "
        "CRITICAL LIVE RULE: You HAVE live internet access via LIVE SEARCH. If LIVE SEARCH results are provided, you MUST use them. "
        "NEVER say I do not have live access, I cannot give latest news, my knowledge goes up to, as an AI. "
        "If LIVE SEARCH is empty, say Network small Boss, try again. "
        "USER ROLE: " + role_text + " "
        "LANGUAGE: Default clean English. Reply in SAME language user used. Only use Pidgin if user speaks Pidgin. "
        "NEVER say: I go talk now, I dey here, Wetin you want, E dey ready, Don unlock. Say instead: I am listening, I am here, What do you want, It is ready, It is unlocked. "
        "PERSONALITY: Helpful, friendly, relaxed, respectful, direct. Answer direct first, then small detail. Never repeat like a parrot. "
        "FORMATTING: NEVER use asterisks, hashtags, emojis. "
        "CURRENT USER: " + user["name"] + " Role: " + user["role"] + " "
        "SAVED MEMORIES: " + mem + " "
        "LIVE LOCATION: " + location_text + " "
        "BIKE CONTEXT: " + bike_context + " "
        "AUTO-LEARNED TODAY: " + auto + " "
        "Never reveal another user's bike, location, payment or memories. "
        "TABBY EV BRAND: Tabby Automotive and Tabby Ride. Always say motorbike, not motorcycle."
    )

# ---------- LIVE SEARCH - 3 BACKUPS ----------
LIVE_KEYWORDS = ("latest", "today", "current", "right now", "this week", "news", "weather", "price", "2026", "2027", "live score", "breaking", "naira", "dollar", "football", "trending", "fuel", "lagos traffic", "rate", "how much", "who is", "what is", "bitcoin", "crypto", "election", "update", "happening")

def needs_live_search(message):
    text = re.sub(r"\s+", " ", str(message or "").lower()).strip()
    if any(t in text for t in ["write me", "rewrite", "translate this", "summarize this", "make a poem", "create a story"]):
        return False
    if any(t in text for t in LIVE_KEYWORDS):
        return True
    if text.startswith(("what", "who", "how", "why", "when", "where", "tell me")):
        return True
    return False

def fetch_news_rss(query, limit=5):
    try:
        q = urllib.parse.quote(query)
        url = "https://news.google.com/rss/search?q=" + q + "&hl=en-NG&gl=NG&ceid=NG:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read())
        out = []
        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            out.append("Title: " + title + "\nInfo: Published " + pub + " | " + link)
        return out
    except Exception as e:
        print("RSS fail " + str(e))
        return []

def live_search(query, max_results=5):
    if not LIVE_SEARCH_ENABLED:
        return []
    out = []
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    if r.get("title") or r.get("body"):
                        out.append("Title: " + clean_ai_text(r.get("title", "")) + "\nInfo: " + clean_ai_text(r.get("body", "")))
        except Exception as e:
            print("DDGS fail " + str(e))
    if not out:
        out = fetch_news_rss(query, max_results)
    if not out:
        try:
            q = urllib.parse.quote(query)
            req = urllib.request.Request("https://en.wikipedia.org/api/rest_v1/page/summary/" + q, headers={"User-Agent": "Tabby/2.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                d = json.loads(resp.read())
                if "extract" in d:
                    out.append("Title: " + str(d.get("title")) + "\nInfo: " + d["extract"][:900])
        except Exception:
            pass
    return out

# ---------- AUTO STUDY ----------
def auto_study_loop():
    topics = ["Nigeria news", "Naira to Dollar rate today", "Bitcoin price today", "Afrobeats music news", "Lagos news", "Premier League news", "AI news today", "Fuel price Nigeria"]
    while True:
        try:
            print("Tabby auto-studying...")
            all_news = []
            for t in topics:
                res = live_search(t, 2)
                if res:
                    all_news.extend(res)
                time.sleep(2)
            AUTO_LEARNED["news"] = all_news
            AUTO_LEARNED["last"] = datetime.now(timezone.utc).isoformat()
            try:
                with get_db() as db:
                    for item in all_news[:8]:
                        db.execute("INSERT INTO auto_learned (topic, content) VALUES (%s,%s)", ("auto", item[:1000]))
            except Exception as e:
                print("Auto DB save fail " + str(e))
            print("Auto-study done: " + str(len(all_news)))
        except Exception as e:
            print("Auto-study error " + str(e))
        time.sleep(1800)

threading.Thread(target=auto_study_loop, daemon=True).start()

# ---------- IMAGE ----------
def is_image_request(message):
    low = str(message or "").lower().strip()
    if "?" in low and ("can you" in low or "do you" in low or "you can" in low):
        return False
    if low.startswith(("generate image", "create image", "make image", "draw me", "design me", "generate a villain", "create a villain", "generate a poster", "create a poster")):
        return True
    if "generate image:" in low:
        return True
    if low.startswith(("generate a ", "create a ", "draw a ")):
        if any(w in low for w in ["villain", "character", "poster", "image", "picture"]):
            return True
    return False

def clean_image_prompt(message):
    ip = str(message or "").strip()
    prefixes = ["generate image:", "generate image of", "generate image", "create image of", "create image", "make image of", "make image", "draw me", "design me", "design a", "image of", "generate an image of", "generate an image"]
    low = ip.lower()
    for p in prefixes:
        if low.startswith(p):
            ip = ip[len(p):].strip()
            break
    if len(ip) < 3:
        ip = str(message or "").strip()
    return ip

def enhance_image_prompt(prompt):
    p = re.sub(r"\s+", " ", str(prompt).strip())[:900]
    low = p.lower()
    if any(w in low for w in ["2d", "cartoon", "anime", "villain", "concept", "poster", "character"]):
        return p + ", high quality character design, detailed, sharp, cinematic lighting, concept art, bright, clear, full body"
    return p + ", real photograph, natural light, realistic skin texture, real shadows, candid, natural colors, film grain, 35mm photo, bright, clear"

def generate_via_cloudflare_model(prompt, model, steps=4):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("No Cloudflare keys")
    endpoint = "https://api.cloudflare.com/client/v4/accounts/" + CLOUDFLARE_ACCOUNT_ID + "/ai/run/" + model
    r = requests.post(endpoint, headers={"Authorization": "Bearer " + CLOUDFLARE_API_TOKEN, "Content-Type": "application/json"}, json={"prompt": enhance_image_prompt(prompt), "num_steps": max(1, min(int(steps), 8))}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError("Cloudflare failed " + str(r.status_code) + ": " + r.text[:400])
    ct = r.headers.get("Content-Type", "").lower()
    if ct.startswith("image/"):
        return "data:" + ct + ";base64," + base64.b64encode(r.content).decode()
    try:
        j = r.json()
        img = (j.get("result") or {}).get("image")
        if img:
            return "data:image/jpeg;base64," + img
    except Exception:
        pass
    raise RuntimeError("Cloudflare returned no image")

def generate_via_openai_image(prompt):
    if not OPENAI_API_KEY:
        raise RuntimeError("No OPENAI_API_KEY")
    r = requests.post("https://api.openai.com/v1/images/generations", headers={"Authorization": "Bearer " + OPENAI_API_KEY, "Content-Type": "application/json"}, json={"model": "gpt-image-1-mini", "prompt": enhance_image_prompt(prompt), "size": "1024x1024"}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError("OpenAI image failed: " + r.text[:500])
    data = r.json().get("data", [])
    if not data:
        raise RuntimeError("OpenAI image no data")
    if data[0].get("b64_json"):
        return "data:image/jpeg;base64," + data[0]["b64_json"]
    if data[0].get("url"):
        img = requests.get(data[0]["url"], timeout=60)
        if img.status_code == 200:
            return "data:image/jpeg;base64," + base64.b64encode(img.content).decode()
    raise RuntimeError("OpenAI image no image")

def generate_via_goapi_midjourney(prompt):
    if not GOAPI_API_KEY:
        raise RuntimeError("No GOAPI_API_KEY")
    r = requests.post("https://api.goapi.ai/api/v1/task", headers={"X-API-Key": GOAPI_API_KEY, "Content-Type": "application/json"}, json={"model": "midjourney", "task_type": "imagine", "input": {"prompt": enhance_image_prompt(prompt), "process_mode": "relax", "skip_prompt_check": False}}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError("GoAPI Midjourney failed: " + r.text[:500])
    task_id = r.json().get("data", {}).get("task_id")
    if not task_id:
        raise RuntimeError("GoAPI no task_id")
    for _ in range(20):
        time.sleep(6)
        d = requests.get("https://api.goapi.ai/api/v1/task/" + task_id, headers={"X-API-Key": GOAPI_API_KEY}, timeout=30).json()
        out = d.get("data", {})
        if out.get("status") == "completed" and out.get("output", {}).get("image_url"):
            url = out["output"]["image_url"]
            img = requests.get(url, timeout=60)
            if img.status_code == 200:
                return "data:image/jpeg;base64," + base64.b64encode(img.content).decode()
            return url
        if out.get("status") in ("failed", "fail"):
            raise RuntimeError("Midjourney task failed")
    raise RuntimeError("Midjourney timed out")

def generate_flux_image(prompt, steps=4):
    errors = []
    try:
        return generate_via_cloudflare_model(prompt, FLUX_MODEL, steps)
    except Exception as e:
        errors.append("Cloudflare Flux: " + str(e))
    try:
        return generate_via_cloudflare_model(prompt, "@cf/stabilityai/stable-diffusion-xl-base-1.0", steps)
    except Exception as e:
        errors.append("Cloudflare SDXL: " + str(e))
    if GOAPI_API_KEY:
        try:
            return generate_via_goapi_midjourney(prompt)
        except Exception as e:
            errors.append("Midjourney GoAPI: " + str(e))
    if OPENAI_API_KEY:
        try:
            return generate_via_openai_image(prompt)
        except Exception as e:
            errors.append("OpenAI: " + str(e))
    raise RuntimeError("Image failed. " + " | ".join(errors)[:1200])

# ---------- BIKE + HOUSE ----------
def get_bike_context_for_user(user):
    try:
        with get_db() as db:
            if user["role"] == "owner":
                c = db.execute("SELECT COUNT(*) as c FROM motorcycles").fetchone()["c"]
                return "Owner fleet: " + str(c) + " motorbikes in system."
            bike = db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=%s ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
            if bike:
                bike = dict(bike)
                st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=%s", (bike["id"],)).fetchone()
                tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (bike["id"],)).fetchone()
                plan = db.execute("SELECT * FROM payment_plans WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (bike["id"],)).fetchone()
                return "Customer bike: " + str(bike.get("name")) + " Model " + str(bike.get("model")) + " Reg " + str(bike.get("registration_number")) + ". Status: " + str(dict(st) if st else {}) + ". Latest telemetry: " + str(dict(tel) if tel else {}) + ". Payment plan: " + str(dict(plan) if plan else {}) + "."
            return "Customer has no bike assigned yet."
    except Exception:
        return ""

def handle_tabby_bike_command(user, low_msg):
    if "bike" not in low_msg:
        return None
    with get_db() as db:
        if user["role"] == "owner":
            bike = db.execute("SELECT * FROM motorcycles ORDER BY id DESC LIMIT 1", ()).fetchone()
        else:
            bike = db.execute("SELECT * FROM motorcycles WHERE assigned_user_id=%s ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
        if not bike:
            return "Boss, you do not have a bike assigned yet."
        bike = dict(bike)
        mid = bike["id"]
        if "unlock" in low_msg:
            db.execute("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (%s,%s,%s)", (mid, "unlock", user["id"]))
            db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (mid,))
            db.execute("UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=%s", (mid,))
            return "Done, Boss. Your bike " + str(bike.get("name")) + " is unlocked. You can ride now."
        if "lock" in low_msg:
            db.execute("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (%s,%s,%s)", (mid, "lock", user["id"]))
            db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (mid,))
            db.execute("UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=%s", (mid,))
            return "Done, Boss. Your bike " + str(bike.get("name")) + " is locked."
        if any(w in low_msg for w in ["check", "battery", "status", "how is"]):
            st = db.execute("SELECT * FROM bike_status WHERE motorcycle_id=%s", (mid,)).fetchone()
            tel = db.execute("SELECT * FROM telemetry WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
            sd = dict(st) if st else {}
            td = dict(tel) if tel else {}
            return "Boss, your bike " + str(bike.get("name")) + " - Lock: " + str(sd.get("lock_status", "locked")) + ", Battery Health: " + str(sd.get("battery_health", 100)) + "%, Range: " + str(sd.get("range_km", 0)) + "km, Total Trip: " + str(sd.get("total_trip", 0)) + ", Last speed: " + str(td.get("speed", "no data")) + ". It is ready."
    return None

def handle_tabby_house_command(user, low_msg):
    if user["role"] != "owner":
        return "Sorry, house control is only for the owner, Boss."
    house_words = ["light", "fan", "ac", "air conditioner", "tv", "television", "door", "house", "generator", "socket"]
    if not any(w in low_msg for w in house_words):
        return None
    action = None
    if any(w in low_msg for w in ["turn on", "switch on", "open", "unlock house", "start"]):
        action = "on"
    if any(w in low_msg for w in ["turn off", "switch off", "close", "lock house", "stop"]):
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
    for rn in ["parlour", "living", "bedroom", "kitchen", "bathroom", "outside", "gate"]:
        if rn in low_msg:
            room = rn
            break
    with get_db() as db:
        row = db.execute("SELECT * FROM house_devices WHERE user_id=%s AND name=%s AND room=%s", (user["id"], device, room)).fetchone()
        if not row:
            db.execute("INSERT INTO house_devices (user_id, name, room, type, status) VALUES (%s,%s,%s,%s,%s)", (user["id"], device, room, device, action))
        else:
            db.execute("UPDATE house_devices SET status=%s WHERE id=%s", (action, row["id"]))
        db.execute("INSERT INTO tabby_logs (user_id, device, command, action, result) VALUES (%s,%s,%s,%s,%s)", (user["id"], room + " " + device, low_msg[:200], action, device + " set to " + action))
    if device in ("door", "house"):
        return "Done, Boss. House " + device + " is now " + action + "."
    return "Done, Boss. " + room + " " + device + " is now " + action + "."

# ---------- SIMPLE SAFE HTML - NO TRIPLE QUOTE INSIDE ----------
def get_html():
    parts = []
    parts.append("<!doctype html><html><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    parts.append("<title>Tabby AI - v16 Live</title>")
    parts.append("<style>body{margin:0;background:#070b16;color:#e6edff;font:15px system-ui,sans-serif} ")
    parts.append(".wrap{max-width:800px;margin:0 auto;padding:16px} .top{display:flex;justify-content:space-between;align-items:center} ")
    parts.append(".msg{padding:13px 15px;border-radius:14px;margin:10px 0;line-height:1.5;white-space:pre-wrap} .user{background:#2b4aa5;margin-left:40px} .ai{background:#141f38;border:1px solid #1e2d4d} ")
    parts.append(".msg img{width:100%;max-width:620px;border-radius:12px;margin-top:10px;display:block} ")
    parts.append("input{width:68%;padding:13px;border-radius:20px;border:none;background:#222;color:#fff} button{padding:13px 16px;border-radius:20px;border:none;background:#2b6cff;color:#fff;cursor:pointer} ")
    parts.append(".tool{background:#1a2a4d;margin:4px} .auth{max-width:420px;margin:60px auto;padding:22px;background:#0f1a30;border:1px solid #26365e;border-radius:16px} .auth input{width:100%;margin:6px 0;padding:13px;border-radius:10px;border:1px solid #34435f;background:#0b1427;color:#fff} .auth button{width:100%} ")
    parts.append(".cards{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:14px 0} .card{background:#101a30;border:1px solid #1e2d4d;border-radius:14px;padding:14px} </style></head><body>")
    parts.append("<div id='authWrap'><div class='auth'><h2 style='text-align:center'>TABBY</h2><p style='text-align:center;color:#8ea0c3;font-size:12px'>AUTOMOTIVE INTELLIGENCE v16 - LIVE</p>")
    parts.append("<input id='email' placeholder='Email'><input id='password' type='password' placeholder='Password'>")
    parts.append("<button onclick='doLogin()'>Sign in</button><p style='font-size:12px;text-align:center'>No account? <a href='#' onclick='doRegister();return false' style='color:#7ee2a8'>Create Account</a></p><div id='status'></div></div></div>")
    parts.append("<div id='appWrap' style='display:none'><div class='wrap'><div class='top'><h2>Tabby AI</h2><span style='background:#0f3d2a;color:#7ee2a8;padding:6px 10px;border-radius:20px;font-size:12px'>LIVE + AUTO-LEARNING</span></div>")
    parts.append("<div class='cards'><div class='card'><small>YOUR ROLE</small><br><b id='roleTxt'>OWNER</b></div><div class='card'><small>SIGNED IN AS</small><br><b id='nameTxt'>Boss</b></div></div>")
    parts.append("<div id='msgs'></div>")
    parts.append("<div><input id='inp' placeholder='Message Tabby...'><button onclick='send()'>Send</button></div>")
    parts.append("<div style='margin-top:10px'><button class='tool' onclick=\"quick('Tell me latest news in Nigeria')\">News Nigeria</button>")
    parts.append("<button class='tool' onclick=\"quick('What is Naira to Dollar rate today?')\">Naira Rate</button>")
    parts.append("<button class='tool' onclick=\"quick('Check my bike')\">My Bike</button>")
    parts.append("<button class='tool' onclick=\"quick('Generate image of my bike in Lagos')\">Generate Image</button>")
    parts.append("<button class='tool' onclick='doLogout()'>Logout</button></div>")
    parts.append("</div></div>")
    parts.append("<script>")
    parts.append("var token=localStorage.getItem('tabby_token')||''; var currentUser=null;")
    parts.append("function $i(id){return document.getElementById(id)}")
    parts.append("async function api(path,opt){opt=opt||{}; var h={}; if(token)h.Authorization='Bearer '+token; if(opt.body)h['Content-Type']='application/json'; var r=await fetch(path,{method:opt.method||'GET',headers:h,body:opt.body}); return r.json();}")
    parts.append("async function doLogin(){ var e=$i('email').value.trim(); var p=$i('password').value; var d=await api('/api/login',{method:'POST',body:JSON.stringify({email:e,password:p})}); if(!d.success){$i('status').textContent=d.error||'Login failed';return;} token=d.token; currentUser=d.user; localStorage.setItem('tabby_token',token); afterLogin(); }")
    parts.append("async function doRegister(){ var n=prompt('Full name'); var e=prompt('Email'); var p=prompt('Password 8+ chars'); if(!n||!e||!p)return; var d=await api('/api/register',{method:'POST',body:JSON.stringify({name:n,email:e,password:p})}); if(!d.success){alert(d.error);return;} token=d.token; currentUser=d.user; localStorage.setItem('tabby_token',token); afterLogin(); }")
    parts.append("function doLogout(){ localStorage.removeItem('tabby_token'); location.reload(); }")
    parts.append("function afterLogin(){ $i('authWrap').style.display='none'; $i('appWrap').style.display='block'; $i('nameTxt').textContent=currentUser.name; $i('roleTxt').textContent=currentUser.role.toUpperCase(); loadHistory(); }")
    parts.append("async function loadHistory(){ try{ var s=await api('/api/chat/single'); (s.messages||[]).forEach(function(m){ addMsg(m.role,m.content); }); }catch(e){} }")
    parts.append("function addMsg(role,text,img){ var box=$i('msgs'); var d=document.createElement('div'); d.className='msg '+(role==='user'?'user':'ai'); d.textContent=text||''; if(img){ var im=document.createElement('img'); im.src=img; d.appendChild(im); } box.appendChild(d); box.scrollTop=box.scrollHeight; }")
    parts.append("async function send(){ var v=$i('inp').value.trim(); if(!v)return; $i('inp').value=''; addMsg('user',v); try{ var d=await api('/api/chat',{method:'POST',body:JSON.stringify({message:v})}); addMsg('assistant',d.reply||'Done.',d.image||d.image_url); }catch(e){ addMsg('assistant','Sorry: '+e.message); } }")
    parts.append("function quick(t){ $i('inp').value=t; send(); }")
    parts.append("if(token){ api('/api/me').then(function(d){ currentUser=d.user; afterLogin(); }).catch(function(){ localStorage.removeItem('tabby_token'); }); }")
    parts.append("</script></body></html>")
    return "".join(parts)

# ---------- ROUTES ----------
@app.route("/", methods=["GET"])
def home():
    return get_html(), 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"success": True, "name": "Tabby", "creator": CREATOR_NAME, "status": "online", "version": "16.0-clean-rewrite", "database_ready": DATABASE_READY, "auto_learned_last": AUTO_LEARNED.get("last"), "auto_learned_count": len(AUTO_LEARNED.get("news", [])), "time": current_time()})

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"success": True, "name": "Tabby", "creator": CREATOR_NAME, "status": "online", "version": "16.0"})

@app.route("/api/greeting", methods=["GET"])
@auth_required
def greeting():
    return jsonify({"success": True, "greeting": get_greeting_lagos()})

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
        if db.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone():
            return json_error("Account already exists. Please sign in.", 409)
        role = "owner" if OWNER_EMAIL and email == OWNER_EMAIL else "customer"
        cur = db.execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,%s)", (name, email, generate_password_hash(password), role))
        user = db.execute("SELECT * FROM users WHERE id=%s", (cur.lastrowid,)).fetchone()
    user = dict(user)
    return jsonify({"success": True, "token": create_token(user), "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}}), 201

@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=%s", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return json_error("Invalid email or password.", 401)
    user = dict(user)
    if OWNER_EMAIL and email == OWNER_EMAIL and user["role"] != "owner":
        with get_db() as db:
            db.execute("UPDATE users SET role='owner' WHERE id=%s", (user["id"],))
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
        if st:
            bike.update(dict(st))
        if tel:
            bike.update({"speed": dict(tel).get("speed"), "latitude": dict(tel).get("latitude"), "longitude": dict(tel).get("longitude")})
    return jsonify({"success": True, "bike": bike, "trip": {"total_trip": bike.get("total_trip", 0), "total_distance": bike.get("total_distance", 0), "co2_saved": bike.get("co2_saved", 0)}})

@app.route("/api/owner/create-customer", methods=["POST"])
@owner_required
def owner_create_customer():
    data = request.get_json(silent=True) or {}
    name = clean_ai_text(str(data.get("name", "")).strip())
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "") or "Tabby1234")
    bike_name = clean_ai_text(str(data.get("bike_name", "")).strip())
    if not name or not email:
        return json_error("Customer name and email required.")
    with get_db() as db:
        if db.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone():
            return json_error("Customer already exists.", 409)
        cur = db.execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,'customer')", (name, email, generate_password_hash(password)))
        cust_id = cur.lastrowid
        bike_id = None
        if bike_name:
            bcur = db.execute("INSERT INTO motorcycles (owner_id, assigned_user_id, name, model, registration_number) VALUES (%s,%s,%s,%s,%s)", (request.current_user["id"], cust_id, bike_name, "Tabby EV", ""))
            bike_id = bcur.lastrowid
            db.execute("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (bike_id,))
    return jsonify({"success": True, "user": {"id": cust_id, "name": name, "email": email, "role": "customer"}, "bike_id": bike_id, "message": "Customer " + name + " created."}), 201

@app.route("/api/location", methods=["POST"])
@auth_required
def save_loc():
    u = request.current_user
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("latitude"))
        lng = float(data.get("longitude"))
    except Exception:
        return json_error("Invalid location.")
    with get_db() as db:
        db.execute("INSERT INTO locations (user_id, latitude, longitude, label) VALUES (%s,%s,%s,%s)", (u["id"], lat, lng, "phone_location"))
    return jsonify({"success": True, "message": "Location saved."})

@app.route("/api/chat/single", methods=["GET"])
@auth_required
def get_single_chat():
    user = request.current_user
    cid = get_single_conversation_id(user["id"])
    with get_db() as db:
        msgs = db.execute("SELECT id,role,content,created_at FROM messages WHERE conversation_id=%s ORDER BY id ASC", (cid,)).fetchall()
    return jsonify({"success": True, "conversation_id": cid, "greeting": get_greeting_lagos(), "messages": [dict(r) for r in msgs]})

@app.route("/chat", methods=["POST"])
@app.route("/api/chat", methods=["POST"])
@auth_required
def chat():
    user = request.current_user
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return json_error("Message required.")
    if "remember" in message.lower():
        save_memory(user["id"], message)
    low = message.lower()
    if "going home" in low:
        bike_reply = handle_tabby_bike_command(user, "unlock my bike") or ""
        handle_tabby_house_command(user, "turn on light living")
        handle_tabby_house_command(user, "turn on ac living")
        reply = "Done, Boss. Going Home scene is running. " + bike_reply + " Light is on, AC is on."
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
    if is_image_request(message):
        try:
            img = generate_flux_image(clean_image_prompt(message))
            cid = get_single_conversation_id(user["id"])
            with get_db() as db:
                db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "user", message))
                db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s,%s,%s)", (cid, "assistant", "Here is your image, Boss."))
            return jsonify({"success": True, "reply": "Here is your image, Boss.", "response": "Here is your image, Boss.", "image": img, "image_url": img})
        except Exception as e:
            return jsonify({"success": True, "reply": "Image generation is active, Boss. Tell me what image you want. Detail: " + clean_ai_text(str(e))[:200], "response": "Image generation is active, Boss."})
    try:
        memories = get_memories(user["id"], 20)
        loc = get_latest_location(user["id"])
        loc_text = str(loc.get("latitude")) + "," + str(loc.get("longitude")) if loc else "No location yet."
        bike_ctx = get_bike_context_for_user(user)
        search = live_search(message, 5) if needs_live_search(message) else []
        if AUTO_LEARNED.get("news"):
            search = search + ["AUTO-LEARNED: " + x for x in AUTO_LEARNED["news"][:3]]
        system = build_system_prompt(user, memories, loc_text, bike_ctx)
        if search:
            system += "\nLIVE SEARCH (USE THIS - THIS IS REAL LIVE INTERNET DATA):\n" + "\n".join(search)
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
        return jsonify({"success": True, "reply": "Sorry Boss, I had a small issue: " + clean_ai_text(str(e))[:300], "response": "Sorry Boss."})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)