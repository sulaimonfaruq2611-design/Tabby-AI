import os, re, json, time, base64, threading, urllib.parse, xml.etree.ElementTree as ET, uuid
from datetime import datetime, timezone, timedelta
from functools import wraps
import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "tabby-secret-change-me")
CORS(app, resources={r"/*": {"origins": "*"}})
serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

CREATOR = "Faruq Sulaimon"
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "").lower().strip()
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash").strip()
OPENROUTER_STT_MODEL = os.getenv("OPENROUTER_STT_MODEL", "openai/whisper-large-v3").strip()
OPENROUTER_TTS_MODEL = os.getenv("OPENROUTER_TTS_MODEL", "openai/gpt-4o-mini-tts").strip()
OPENROUTER_TTS_VOICE = os.getenv("OPENROUTER_TTS_VOICE", "onyx").strip()
CLOUDFLARE_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tabby.db")
_lock = threading.Lock()
_NEWS_CACHE = {"time": 0, "data": []}

def tables_sql():
    return [
    "CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE, password_hash TEXT, role TEXT DEFAULT 'customer', created_at TEXT DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT DEFAULT 'New Chat', summary TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')))",
    'CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "userId" INTEGER, role TEXT, content TEXT, conversationid INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime(\'now\')))',
    'CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "userId" INTEGER, content TEXT, created_at TEXT DEFAULT (datetime(\'now\')))',
    'CREATE TABLE IF NOT EXISTS motorcycles(id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, "ownerId" INTEGER, assigned_user_id INTEGER, "assignedUserId" INTEGER, name TEXT, model TEXT DEFAULT \'Tabby EV\', reg TEXT, status TEXT DEFAULT \'active\', created_at TEXT DEFAULT (datetime(\'now\')))',
    "CREATE TABLE IF NOT EXISTS bike_status(motorcycle_id INTEGER PRIMARY KEY, battery_health REAL DEFAULT 100, battery_level REAL DEFAULT 100, range_km REAL DEFAULT 125, total_trip REAL DEFAULT 0, total_distance REAL DEFAULT 0, co2_saved REAL DEFAULT 0, lock_status TEXT DEFAULT 'locked', access_mode TEXT DEFAULT 'full', cell_temp REAL DEFAULT 32, motor_temp REAL DEFAULT 35, last_seen TEXT)",
    "CREATE TABLE IF NOT EXISTS telemetry(id INTEGER PRIMARY KEY AUTOINCREMENT, motorcycle_id INTEGER, latitude REAL, longitude REAL, speed REAL, battery_level REAL, engine_temp REAL, created_at TEXT DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS payment_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, motorcycle_id INTEGER, total_amount REAL, deposit REAL DEFAULT 0, installment_amount REAL, installments_paid INTEGER DEFAULT 0, installments_total INTEGER, next_due_date TEXT, status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, motorcycle_id INTEGER, amount REAL, status TEXT DEFAULT 'pending', reference TEXT UNIQUE, created_at TEXT DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS house_devices(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, room TEXT DEFAULT 'living', type TEXT, status TEXT DEFAULT 'off', created_at TEXT DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS auto_learned(id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, content TEXT, created_at TEXT DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS bike_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, motorcycle_id INTEGER, command TEXT, issued_by INTEGER, status TEXT DEFAULT 'pending', created_at TEXT DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS call_sessions(id TEXT PRIMARY KEY, user_id INTEGER, status TEXT DEFAULT 'live', started_at TEXT DEFAULT (datetime('now')), ended_at TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_msg_user_conv ON messages(user_id, conversationid, id)",
    "CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id)",
    ]

def ensure_tables(cur):
    for s in tables_sql():
        try:
            cur.execute(s)
        except Exception as e:
            print("ensure_tables skip", e)

class DB:
    def __enter__(self):
        _lock.acquire()
        import sqlite3
        self.c = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.cur = self.c.cursor()
        try:
            ensure_tables(self.cur)
            self.c.commit()
        except Exception as e:
            print("ensure_tables fail", e)
            try:
                self.c.rollback()
            except Exception:
                pass
        return self
    def __exit__(self, *a):
        try:
            self.c.commit()
        except Exception:
            try:
                self.c.rollback()
            except Exception:
                pass
        try:
            self.cur.close()
        except Exception:
            pass
        try:
            self.c.close()
        except Exception:
            pass
        try:
            _lock.release()
        except Exception:
            pass
        return False
    def q(self, sql, p=()):
        params = tuple(p) if isinstance(p, (list, tuple)) else (p,)
        if params == ((),):
            params = ()
        self.cur.execute(sql, params)
        return self.cur
    def one(self, sql, p=()):
        self.q(sql, p)
        r = self.cur.fetchone()
        if r is None:
            return None
        return dict(r)
    def all(self, sql, p=()):
        self.q(sql, p)
        return [dict(r) for r in self.cur.fetchall()]
    def ins(self, sql, p=()):
        self.q(sql, p)
        return self.cur.lastrowid

def init_db():
    try:
        with DB() as db:
            if OWNER_EMAIL:
                db.q("UPDATE users SET role='owner' WHERE lower(email)=?", (OWNER_EMAIL,))
    except Exception as e:
        print("DB init", e)
init_db()

AUTO = {"news": [], "last": ""}
def clean(t):
    if not t:
        return ""
    t = re.sub(r"\*+", "", str(t))
    t = re.sub(r"_+", "", t)
    t = re.sub(r"#+", "", t)
    return re.sub(r"\s+", " ", t).strip()

def get_or_create_conversation(user_id, conv_id=None, first_msg=""):
    if not user_id:
        return 1
    with DB() as db:
        if conv_id:
            try:
                c = db.one("SELECT * FROM conversations WHERE id=? AND user_id=?", (int(conv_id), user_id))
                if c:
                    return c["id"]
            except Exception:
                pass
        latest = db.one("SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC, id DESC LIMIT 1", (user_id,))
        if latest and not conv_id:
            return latest["id"]
        title = (first_msg[:40] or "New Chat") if first_msg else "New Chat"
        nid = db.ins("INSERT INTO conversations (user_id, title) VALUES (?,?)", (user_id, title))
        if not nid:
            return 1
        return nid

def auto_title_conversation(conv_id, first_msg):
    if not first_msg or not conv_id:
        return
    title = clean(first_msg)[:40]
    with DB() as db:
        c = db.one("SELECT title FROM conversations WHERE id=?", (conv_id,))
        if c and c["title"] in ("New Chat", "", None):
            db.q("UPDATE conversations SET title=?, updated_at=datetime('now') WHERE id=?", (title, conv_id))

def prune_and_summarise(user_id, conv_id):
    try:
        if not conv_id:
            conv_id = 1
        with DB() as db:
            cnt = db.one("SELECT COUNT(*) as c FROM messages WHERE user_id=? AND conversationid=?", (user_id, conv_id))["c"]
            if cnt < 60:
                return
            old = db.all("SELECT role, content FROM messages WHERE user_id=? AND conversationid=? ORDER BY id ASC LIMIT 30", (user_id, conv_id))
            if not old:
                return
            txt = "\n".join([str(r["role"]) + ": " + str(r["content"])[:300] for r in old])
            try:
                summary = call_openrouter([{"role": "system", "content": "Summarise this conversation in 5 bullet lines, keep names, decisions, numbers, ideas. No asterisks."}, {"role": "user", "content": txt}], temp=0.3)
            except Exception:
                summary = "Summary: " + txt[:500]
            db.q('INSERT INTO memories (user_id, "userId", content) VALUES (?,?,?)', (user_id, user_id, "[Chat " + str(conv_id) + " Summary] " + summary[:1000]))
            ids = db.all("SELECT id FROM messages WHERE user_id=? AND conversationid=? ORDER BY id ASC LIMIT 30", (user_id, conv_id))
            for r in ids:
                db.q("DELETE FROM messages WHERE id=?", (r["id"],))
            db.q("UPDATE conversations SET summary=? WHERE id=?", (summary[:1000], conv_id))
    except Exception as e:
        print("prune fail", e)

def needs_live_search(q):
    q = (q or "").lower()
    keys = ["news", "naira", "dollar", "usd", "ngn", "rate", "exchange", "price", "bitcoin", "btc", "crypto", "ethereum", "weather", "fuel", "tinubu", "lagos", "premier", "election", "today"]
    return any(k in q for k in keys)

def live_search(query, limit=5):
    out = []
    q = (query or "").lower()
    if "news" in q or "nigeria" in q:
        if time.time() - _NEWS_CACHE["time"] < 300 and _NEWS_CACHE["data"]:
            return _NEWS_CACHE["data"][:limit]
    if any(w in q for w in ["naira", "dollar", "usd", "ngn", "rate", "exchange", "price"]):
        try:
            r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5).json()
            out.append("Title: Naira to Dollar Rate Today\nInfo: 1 USD = " + str(r["rates"].get("NGN")) + " NGN. Updated " + str(r.get("time_last_update_utc")))
        except Exception:
            pass
    if any(w in q for w in ["bitcoin", "btc", "crypto", "ethereum"]):
        try:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd,ngn", timeout=5).json()
            out.append("Title: Crypto Price\nInfo: Bitcoin $" + str(r["bitcoin"]["usd"]) + " / " + str(r["bitcoin"]["ngn"]) + " NGN. Ethereum $" + str(r["ethereum"]["usd"]))
        except Exception:
            pass
    try:
        qq = urllib.parse.quote((query or "Nigeria news") + " when:1d")
        url = "https://news.google.com/rss/search?q=" + qq + "&hl=en-NG&gl=NG&ceid=NG:en"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        root = ET.fromstring(r.content)
        items = []
        for it in root.findall(".//item")[:12]:
            title = it.findtext("title") or ""
            pub = it.findtext("pubDate") or ""
            if title in str(items):
                continue
            items.append("Title: " + title + "\nInfo: " + pub + " | " + str(it.findtext("link")))
        out.extend(items[:limit])
        if "news" in q:
            _NEWS_CACHE["time"] = time.time()
            _NEWS_CACHE["data"] = out
    except Exception as e:
        print("RSS fail", e)
    if "weather" in q:
        try:
            r = requests.get("https://wttr.in/Lagos?format=j1", timeout=5).json()
            c = r["current_condition"][0]
            out.append("Title: Lagos Weather\nInfo: " + str(c["weatherDesc"][0]["value"]) + ", " + str(c["temp_C"]) + "C, Humidity " + str(c["humidity"]) + "%")
        except Exception:
            pass
    return out

def auto_study():
    topics = ["Nigeria news", "Naira to Dollar rate", "Bitcoin price", "Lagos news", "Afrobeats news", "Premier League", "Fuel price Nigeria", "AI news"]
    while True:
        try:
            all_n = []
            for t in topics:
                all_n += live_search(t, 2)
                time.sleep(1)
            AUTO["news"] = all_n
            AUTO["last"] = datetime.now(timezone.utc).isoformat()
            with DB() as db:
                for n in all_n[:8]:
                    exists = db.one("SELECT id FROM auto_learned WHERE content=?", (n[:1000],))
                    if not exists:
                        db.q("INSERT INTO auto_learned (topic, content) VALUES (?,?)", ("auto", n[:1000]))
                db.q("DELETE FROM auto_learned WHERE id NOT IN (SELECT id FROM auto_learned ORDER BY id DESC LIMIT 100)")
        except Exception as e:
            print("Auto", e)
        time.sleep(1800)
threading.Thread(target=auto_study, daemon=True).start()

def call_openrouter(msgs, temp=0.7):
    if not OPENROUTER_KEY:
        raise RuntimeError("no openrouter - set OPENROUTER_API_KEY")
    models = []
    for m in ["meta-llama/llama-3.1-8b-instruct:free", "google/gemma-2-9b-it:free", "deepseek/deepseek-chat-v3-0324:free", OPENROUTER_MODEL, "deepseek/deepseek-chat", "openrouter/free"]:
        if m and m not in models:
            models.append(m)
    last_err = ""
    for model in models:
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": "Bearer " + OPENROUTER_KEY, "Content-Type": "application/json"}, json={"model": model, "messages": msgs, "temperature": temp, "max_tokens": 900}, timeout=30)
            if r.status_code == 200:
                return clean(r.json()["choices"][0]["message"]["content"])
            last_err = str(model) + ": " + str(r.status_code) + " " + r.text[:200]
        except Exception as e:
            last_err = str(e)
    raise RuntimeError("OpenRouter failed - " + last_err[:300])

def call_ai(msgs):
    return call_openrouter(msgs)

def transcribe_audio_file(file_bytes, filename="voice.webm"):
    if not OPENROUTER_KEY:
        raise RuntimeError("no openrouter key")
    files = {"file": (filename, file_bytes, "audio/webm")}
    data = {"model": OPENROUTER_STT_MODEL}
    r = requests.post("https://openrouter.ai/api/v1/audio/transcriptions", headers={"Authorization": "Bearer " + OPENROUTER_KEY}, files=files, data=data, timeout=90)
    if r.status_code == 200:
        return (r.json().get("text") or "").strip()
    data["model"] = "openai/whisper-large-v3"
    r = requests.post("https://openrouter.ai/api/v1/audio/transcriptions", headers={"Authorization": "Bearer " + OPENROUTER_KEY}, files={"file": (filename, file_bytes, "audio/webm")}, data=data, timeout=90)
    if r.status_code == 200:
        return (r.json().get("text") or "").strip()
    raise RuntimeError("Transcribe failed " + str(r.status_code) + ": " + r.text[:300])

def transcribe_base64_audio(b64_data, filename="voice.webm"):
    pure = b64_data.split(",", 1)[-1] if "," in b64_data else b64_data
    return transcribe_audio_file(base64.b64decode(pure), filename)

def speak_text_to_audio(text, voice=None):
    if not OPENROUTER_KEY:
        raise RuntimeError("no openrouter key")
    if not text or not text.strip():
        raise RuntimeError("empty text")
    text = text[:700]
    v = voice or OPENROUTER_TTS_VOICE
    for model in [OPENROUTER_TTS_MODEL, "openai/gpt-4o-mini-tts", "openai/tts-1", "openai/tts-1-hd"]:
        try:
            r = requests.post("https://openrouter.ai/api/v1/audio/speech", headers={"Authorization": "Bearer " + OPENROUTER_KEY, "Content-Type": "application/json"}, json={"model": model, "input": text, "voice": v, "response_format": "mp3"}, timeout=60)
            if r.status_code == 200 and len(r.content) > 1000:
                return "data:audio/mpeg;base64," + base64.b64encode(r.content).decode()
        except Exception as e:
            print("TTS exception", e)
    raise RuntimeError("TTS failed - check OpenRouter credits")

def analyze_image_b64(b64, prompt):
    if not OPENROUTER_KEY:
        raise RuntimeError("Image analysis unavailable - no OpenRouter key")
    for model in ["openai/gpt-4o-mini", "google/gemma-3-27b-it"]:
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": "Bearer " + OPENROUTER_KEY, "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": b64}}]}], "max_tokens": 800}, timeout=30)
            if r.status_code == 200:
                return clean(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print("Vision OpenRouter", e)
    raise RuntimeError("Image analysis unavailable - check OpenRouter key / credits")

def gen_image_cloudflare(prompt):
    if not CLOUDFLARE_ID or not CLOUDFLARE_TOKEN:
        raise RuntimeError("Cloudflare keys missing in Render Env")
    p = prompt + ", high quality, detailed, cinematic lighting, sharp, clear"
    for model in ["@cf/black-forest-labs/flux-1-schnell", "@cf/stabilityai/stable-diffusion-xl-base-1.0"]:
        try:
            r = requests.post("https://api.cloudflare.com/client/v4/accounts/" + CLOUDFLARE_ID + "/ai/run/" + model, headers={"Authorization": "Bearer " + CLOUDFLARE_TOKEN, "Content-Type": "application/json"}, json={"prompt": p, "num_steps": 4}, timeout=60)
            if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/"):
                return "data:" + r.headers.get("Content-Type") + ";base64," + base64.b64encode(r.content).decode()
            try:
                j = r.json()
                img = (j.get("result") or {}).get("image")
                if img:
                    return "data:image/jpeg;base64," + img
            except Exception:
                pass
        except Exception as e:
            print("CF", model, e)
    raise RuntimeError("Cloudflare image failed - check CLOUDFLARE_ACCOUNT_ID / TOKEN")

def get_user():
    a = request.headers.get("Authorization", "")
    if not a.startswith("Bearer "):
        return None
    try:
        d = serializer.loads(a.split(" ", 1)[1], max_age=60*60*24*30)
        with DB() as db:
            return db.one("SELECT * FROM users WHERE id=?", (d["user_id"],))
    except Exception:
        return None

def auth(f):
    @wraps(f)
    def w(*a, **k):
        u = get_user()
        if not u:
            return jsonify({"success": False, "error": "Login required"}), 401
        request.user = u
        return f(*a, **k)
    return w

def owner_only(f):
    @wraps(f)
    @auth
    def w(*a, **k):
        if request.user["role"] != "owner":
            return jsonify({"success": False, "error": "Owner only"}), 403
        return f(*a, **k)
    return w

def bike_ctx(user):
    with DB() as db:
        if user["role"] == "owner":
            c = db.one("SELECT COUNT(*) as c FROM motorcycles")["c"]
            return "Owner fleet: " + str(c) + " bikes. Owner can see all."
        b = db.one('SELECT * FROM motorcycles WHERE COALESCE(assigned_user_id, "assignedUserId")=? ORDER BY id DESC LIMIT 1', (user["id"],))
        if not b:
            return "No bike assigned."
        st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=?", (b["id"],))
        pl = db.one("SELECT * FROM payment_plans WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (b["id"],))
        return "Bike: " + str(b) + " Status: " + str(st) + " Plan: " + str(pl)

def save_msg(user_id, role, content, conv_id=1):
    if not conv_id:
        conv_id = 1
    try:
        conv_id = int(conv_id)
    except Exception:
        conv_id = 1
    with DB() as db:
        db.q('INSERT INTO messages (user_id, "userId", role, content, conversationid) VALUES (?,?,?,?,?)', (user_id, user_id, role, content, conv_id))
        db.q("UPDATE conversations SET updated_at=datetime('now') WHERE id=?", (conv_id,))

def handle_bike_or_house_command(u, low, msg, conv_id=1):
    if not conv_id:
        conv_id = 1
    with DB() as db:
        if "bike" in low and any(w in low for w in ["lock", "unlock", "check", "battery", "locate", "status"]):
            bike = db.one("SELECT * FROM motorcycles ORDER BY id DESC LIMIT 1")
            if bike:
                st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=?", (bike["id"],))
                if "unlock" in low:
                    db.q("UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=?", (bike["id"],))
                    reply = "Done, Boss. Your bike " + str(bike["name"]) + " is unlocked."
                elif "lock" in low:
                    db.q("UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=?", (bike["id"],))
                    reply = "Done, Boss. Your bike " + str(bike["name"]) + " is locked."
                elif "locate" in low:
                    tel = db.one("SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (bike["id"],))
                    reply = "Boss, your bike is at " + str(tel) + "."
                else:
                    reply = "Boss, your bike " + str(bike["name"]) + " - Lock: " + str(st.get("lock_status") if st else "locked") + ", Battery: " + str(st.get("battery_level") if st else 100) + "%, Health: " + str(st.get("battery_health") if st else 100) + "%, Range: " + str(st.get("range_km") if st else 125) + "km. It is ready."
                save_msg(u["id"], "user", msg, conv_id)
                save_msg(u["id"], "assistant", reply, conv_id)
                return reply
        if any(w in low for w in ["light", "fan", "ac", "tv", "generator", "house"]):
            act = "on" if any(w in low for w in ["turn on", "switch on", "open", "start"]) else "off" if any(w in low for w in ["turn off", "switch off", "close", "stop"]) else None
            if act:
                dev = next((w for w in ["light", "fan", "ac", "tv", "generator"] if w in low), "light")
                db.q("INSERT INTO house_devices (user_id,name,room,type,status) VALUES (?,?,?,?,?)", (u["id"], dev, "living", dev, act))
                reply = "Done, Boss. Living " + dev + " is now " + act + "."
                save_msg(u["id"], "user", msg, conv_id)
                save_msg(u["id"], "assistant", reply, conv_id)
                return reply
    return None

GEE_SYSTEM = """You are Tabby, Faruq Sulaimon's real gee and brainstorming partner. Not a robot, not a formal assistant. Call him Boss, but like a mate. UK English, British spelling: colour, favourite, organise, realise. Tone: warm, sharp, funny, confident, street-smart but intelligent. Use contractions: I'm, you're, let's, don't, can't. Style: Talk like you're sitting next to him. Short paragraphs. No long lectures. No asterisks, no hashtags, no emojis. Brainstorming: Don't just answer, build with him. Throw in ideas, ask what if we..., give 2-3 sharp options, then ask what he thinks. When he has a good idea, hype him up: Omo, that one na banger, Now you're talking, Boss. VOICE: Your words must sound good spoken aloud in a deep UK male voice. Short sentences. NEVER say: As an AI, I don't have live access, my knowledge goes up to, I cannot. You HAVE live internet when LIVE SEARCH is given, USE IT. USER: Faruq Sulaimon, Boss, creator and owner of Ultimate Gee Industry, creator of Tabby."""

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"success": True, "version": "tabby-25-no-supabase", "db": "sqlite", "auto_last": AUTO.get("last"), "auto_count": len(AUTO.get("news", [])), "voice": {"stt": OPENROUTER_STT_MODEL, "tts": OPENROUTER_TTS_MODEL, "tts_voice": OPENROUTER_TTS_VOICE}})

@app.route("/api/signup", methods=["POST"])
def signup():
    d = request.get_json() or {}
    name = clean(d.get("name") or "Faruq Sulaimon")
    email = str(d.get("email", "")).lower().strip()
    pw = str(d.get("password", ""))
    if not email or not pw:
        return jsonify({"success": False, "error": "Email + password required"}), 400
    with DB() as db:
        total = db.one("SELECT COUNT(*) as c FROM users")["c"]
        if total > 0:
            return jsonify({"success": False, "error": "Owner already exists. Please Sign In."}), 403
        uid = db.ins("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,'owner')", (name, email, generate_password_hash(pw)))
        bid = db.ins('INSERT INTO motorcycles (owner_id, "ownerId", assigned_user_id, "assignedUserId", name) VALUES (?,?,?,?,?)', (uid, uid, uid, uid, "Tabby EV 1"))
        db.q("INSERT INTO bike_status (motorcycle_id) VALUES (?)", (bid,))
        db.q("INSERT INTO conversations (user_id, title) VALUES (?,?)", (uid, "First Chat"))
    return jsonify({"success": True, "message": "Owner account created. Now Sign In."})

@app.route("/api/register", methods=["POST"])
@owner_only
def create_customer():
    d = request.get_json() or {}
    name = clean(d.get("name"))
    email = str(d.get("email", "")).lower().strip()
    pw = str(d.get("password", "") or "Tabby1234")
    bike_name = clean(d.get("bike_name") or "Tabby EV 1")
    if not name or not email:
        return jsonify({"success": False, "error": "Name + email required"}), 400
    with DB() as db:
        if db.one("SELECT id FROM users WHERE email=?", (email,)):
            return jsonify({"success": False, "error": "Customer already exists"}), 409
        uid = db.ins("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,'customer')", (name, email, generate_password_hash(pw)))
        bid = db.ins('INSERT INTO motorcycles (owner_id, "ownerId", assigned_user_id, "assignedUserId", name) VALUES (?,?,?,?,?)', (request.user["id"], request.user["id"], uid, uid, bike_name))
        db.q("INSERT INTO bike_status (motorcycle_id) VALUES (?)", (bid,))
        if d.get("total_amount"):
            db.q("INSERT INTO payment_plans (user_id,motorcycle_id,total_amount,deposit,installment_amount,installments_total,next_due_date) VALUES (?,?,?,?,?,?,?)", (uid, bid, float(d.get("total_amount")), float(d.get("deposit") or 0), float(d.get("installment_amount") or 0), int(d.get("installments_total") or 1), str(d.get("next_due_date") or "")))
    return jsonify({"success": True, "user_id": uid, "bike_id": bid, "message": "Customer " + name + " created. Password: " + pw}), 201

@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json() or {}
    email = str(d.get("email", "")).lower().strip()
    pw = str(d.get("password", ""))
    with DB() as db:
        u = db.one("SELECT * FROM users WHERE email=?", (email,))
    if not u or not check_password_hash(u["password_hash"], pw):
        return jsonify({"success": False, "error": "Invalid email or password"}), 401
    return jsonify({"success": True, "token": serializer.dumps({"user_id": u["id"]}), "user": {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}})

@app.route("/api/me")
@auth
def me():
    return jsonify({"success": True, "user": {"id": request.user["id"], "name": request.user["name"], "email": request.user["email"], "role": request.user["role"]}})

@app.route("/api/conversations", methods=["GET"])
@owner_only
def list_conversations():
    with DB() as db:
        rows = db.all("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversationid=c.id) as msg_count, (SELECT content FROM messages m WHERE m.conversationid=c.id ORDER BY id DESC LIMIT 1) as last_msg FROM conversations c WHERE c.user_id=? ORDER BY updated_at DESC, id DESC", (request.user["id"],))
    return jsonify({"success": True, "conversations": rows})

@app.route("/api/conversations", methods=["POST"])
@owner_only
def create_conversation():
    d = request.get_json() or {}
    title = clean(d.get("title") or "New Chat")[:60]
    with DB() as db:
        nid = db.ins("INSERT INTO conversations (user_id, title) VALUES (?,?)", (request.user["id"], title))
    return jsonify({"success": True, "conversation_id": nid, "title": title}), 201

@app.route("/api/conversations/<int:cid>", methods=["GET"])
@owner_only
def get_conversation(cid):
    with DB() as db:
        conv = db.one("SELECT * FROM conversations WHERE id=? AND user_id=?", (cid, request.user["id"]))
        if not conv:
            return jsonify({"success": False, "error": "Chat not found"}), 404
        msgs = db.all("SELECT role, content, created_at FROM messages WHERE user_id=? AND conversationid=? ORDER BY id ASC", (request.user["id"], cid))
    return jsonify({"success": True, "conversation": conv, "messages": msgs})

@app.route("/api/conversations/<int:cid>", methods=["DELETE"])
@owner_only
def delete_conversation(cid):
    with DB() as db:
        db.q("DELETE FROM messages WHERE user_id=? AND conversationid=?", (request.user["id"], cid))
        db.q("DELETE FROM conversations WHERE id=? AND user_id=?", (cid, request.user["id"]))
    return jsonify({"success": True, "message": "Chat deleted"})

@app.route("/api/dashboard")
@auth
def dashboard():
    u = request.user
    with DB() as db:
        if u["role"] == "owner":
            bikes = db.all('SELECT m.*, bs.battery_level, bs.range_km, bs.lock_status, bs.access_mode, bs.battery_health, u2.name as customer_name FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id LEFT JOIN users u2 ON u2.id=COALESCE(m.assigned_user_id, m."assignedUserId") ORDER BY m.id DESC')
            customers = db.all("SELECT u.id,u.name,u.email,m.name as bike_name, pp.status as plan_status, pp.installments_paid, pp.installments_total, pp.next_due_date FROM users u LEFT JOIN motorcycles m ON COALESCE(m.assigned_user_id, m.\"assignedUserId\")=u.id LEFT JOIN payment_plans pp ON pp.user_id=u.id WHERE u.role='customer' ORDER BY u.id DESC")
            total = db.one("SELECT COUNT(*) as c FROM motorcycles")["c"]
            return jsonify({"success": True, "role": "owner", "total_bikes": total, "bikes": bikes, "customers": customers})
        else:
            bikes = db.all('SELECT m.*, bs.* FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id WHERE COALESCE(m.assigned_user_id, m."assignedUserId")=?', (u["id"],))
            plans = db.all("SELECT * FROM payment_plans WHERE user_id=?", (u["id"],))
            return jsonify({"success": True, "role": "customer", "bikes": bikes, "plans": plans})

@app.route("/api/bikes", methods=["GET"])
@auth
def list_bikes():
    u = request.user
    with DB() as db:
        if u["role"] == "owner":
            rows = db.all("SELECT m.*, bs.* FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id ORDER BY m.id DESC")
        else:
            rows = db.all('SELECT m.*, bs.* FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id WHERE COALESCE(m.assigned_user_id, m."assignedUserId")=?', (u["id"],))
    return jsonify({"success": True, "bikes": rows})

@app.route("/api/bike/<int:bid>/command", methods=["POST"])
@auth
def bike_cmd(bid):
    u = request.user
    cmd = str((request.get_json() or {}).get("command", "")).lower()
    with DB() as db:
        bike = db.one("SELECT * FROM motorcycles WHERE id=?", (bid,))
        if not bike:
            return jsonify({"success": False, "error": "Bike not found"}), 404
        assigned = bike.get("assigned_user_id") or bike.get("assignedUserId")
        if u["role"] != "owner" and assigned != u["id"]:
            return jsonify({"success": False, "error": "Not your bike"}), 403
        st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=?", (bid,))
        if st and st.get("access_mode") == "restricted" and cmd in ("unlock", "start"):
            return jsonify({"success": False, "error": "Bike in restricted mode - payment overdue. Contact Ultimate Gee Industry."}), 403
        db.q("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (?,?,?)", (bid, cmd, u["id"]))
        if cmd in ("lock", "unlock"):
            db.q("UPDATE bike_status SET lock_status=? WHERE motorcycle_id=?", ("locked" if cmd == "lock" else "unlocked", bid))
        if cmd == "locate":
            tel = db.one("SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (bid,))
            return jsonify({"success": True, "message": "Bike location", "telemetry": tel})
    return jsonify({"success": True, "message": "Bike " + cmd + " done, Boss."})

@app.route("/api/bike/<int:bid>/telemetry", methods=["POST"])
def telemetry(bid):
    d = request.get_json() or {}
    with DB() as db:
        db.q("INSERT INTO telemetry (motorcycle_id, latitude, longitude, speed, battery_level, engine_temp) VALUES (?,?,?,?,?,?)", (bid, d.get("latitude"), d.get("longitude"), d.get("speed"), d.get("battery_level"), d.get("engine_temp")))
        db.q("INSERT OR IGNORE INTO bike_status (motorcycle_id) VALUES (?)", (bid,))
        db.q("UPDATE bike_status SET battery_level=COALESCE(?,battery_level), last_seen=? WHERE motorcycle_id=?", (d.get("battery_level"), datetime.now(timezone.utc).isoformat(), bid))
    return jsonify({"success": True})

@app.route("/api/payments/remind", methods=["POST"])
@owner_only
def remind():
    with DB() as db:
        due = db.all("SELECT pp.*, u.name, u.email FROM payment_plans pp JOIN users u ON u.id=pp.user_id WHERE pp.status='active' AND pp.next_due_date <= ?", ((datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat(),))
    return jsonify({"success": True, "due_soon": due, "message": str(len(due)) + " customers due soon."})

@app.route("/api/payments/overdue-check", methods=["POST"])
@owner_only
def overdue():
    with DB() as db:
        overdue_plans = db.all("SELECT * FROM payment_plans WHERE status='active' AND next_due_date < ?", (datetime.now(timezone.utc).date().isoformat(),))
        for p in overdue_plans:
            try:
                due = datetime.fromisoformat(p["next_due_date"]).date()
                if (datetime.now(timezone.utc).date() - due).days > 7:
                    db.q("UPDATE bike_status SET access_mode='restricted' WHERE motorcycle_id=?", (p["motorcycle_id"],))
                    db.q("UPDATE payment_plans SET status='overdue' WHERE id=?", (p["id"],))
            except Exception:
                pass
    return jsonify({"success": True, "overdue": len(overdue_plans)})

@app.route("/api/voice/transcribe", methods=["POST"])
@owner_only
def voice_transcribe():
    try:
        if "audio" in request.files:
            f = request.files["audio"]
            text = transcribe_audio_file(f.read(), f.filename or "voice.webm")
        else:
            d = request.get_json() or {}
            if not d.get("audio"):
                return jsonify({"success": False, "error": "No audio provided"}), 400
            text = transcribe_base64_audio(d.get("audio"))
        return jsonify({"success": True, "text": text})
    except Exception as e:
        return jsonify({"success": False, "error": clean(str(e))[:300]}), 500

@app.route("/api/voice/speak", methods=["POST"])
@owner_only
def voice_speak():
    d = request.get_json() or {}
    text = str(d.get("text", "")).strip()
    voice = d.get("voice")
    if not text:
        return jsonify({"success": False, "error": "Text required"}), 400
    try:
        audio = speak_text_to_audio(text, voice)
        return jsonify({"success": True, "audio": audio, "audio_url": audio, "voice": voice or OPENROUTER_TTS_VOICE})
    except Exception as e:
        return jsonify({"success": False, "error": clean(str(e))[:300]}), 500

@app.route("/api/voice/chat", methods=["POST"])
@owner_only
def voice_chat():
    u = request.user
    try:
        if "audio" in request.files:
            f = request.files["audio"]
            user_text = transcribe_audio_file(f.read(), f.filename or "voice.webm")
        else:
            d = request.get_json() or {}
            if d.get("audio"):
                user_text = transcribe_base64_audio(d.get("audio"))
            elif d.get("message"):
                user_text = str(d.get("message", "")).strip()
            else:
                return jsonify({"success": False, "error": "No audio provided"}), 400
        if not user_text:
            return jsonify({"success": True, "reply": "I no hear you well, Boss, talk am again.", "text": "", "audio": None})
        low = user_text.lower()
        conv_id = get_or_create_conversation(u["id"], (request.get_json(silent=True) or {}).get("conversation_id"), user_text)
        if not conv_id:
            conv_id = 1
        cmd_reply = handle_bike_or_house_command(u, low, user_text, conv_id)
        if cmd_reply:
            try:
                audio = speak_text_to_audio(cmd_reply)
            except Exception:
                audio = None
            return jsonify({"success": True, "text": user_text, "reply": cmd_reply, "audio": audio, "audio_url": audio, "conversation_id": conv_id})
        with DB() as db:
            mems = db.all('SELECT content FROM memories WHERE COALESCE(user_id, "userId")=? ORDER BY id DESC LIMIT 8', (u["id"],))
            hist = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=? AND conversationid=? ORDER BY id DESC LIMIT 6', (u["id"], conv_id))
        mem_txt = "\n".join([m["content"] for m in mems]) or "No memories yet. Boss is Faruq Sulaimon, owner of Ultimate Gee Industry, creator of Tabby."
        search = live_search(user_text, 4) if needs_live_search(user_text) else []
        system = GEE_SYSTEM + "\nMEMORIES: " + mem_txt + "\nBIKE CONTEXT: " + bike_ctx(u)
        if search:
            system += "\nLIVE SEARCH - REAL LIVE DATA, USE THIS:\n" + "\n".join(search)
        msgs = [{"role": "system", "content": system}] + [{"role": r["role"], "content": r["content"]} for r in reversed(hist)] + [{"role": "user", "content": user_text}]
        reply = call_ai(msgs)
        save_msg(u["id"], "user", "[voice] " + user_text, conv_id)
        save_msg(u["id"], "assistant", reply, conv_id)
        auto_title_conversation(conv_id, user_text)
        threading.Thread(target=prune_and_summarise, args=(u["id"], conv_id), daemon=True).start()
        try:
            audio = speak_text_to_audio(reply)
        except Exception:
            audio = None
        return jsonify({"success": True, "text": user_text, "reply": reply, "audio": audio, "audio_url": audio, "conversation_id": conv_id})
    except Exception as e:
        return jsonify({"success": True, "reply": "Sorry Boss, small issue with voice: " + clean(str(e))[:300], "audio": None})

@app.route("/api/call/start", methods=["POST"])
@owner_only
def call_start():
    sid = str(uuid.uuid4())[:8]
    with DB() as db:
        db.q("INSERT INTO call_sessions (id, user_id, status) VALUES (?,?,'live')", (sid, request.user["id"]))
    return jsonify({"success": True, "call_id": sid, "message": "Live call started. Tabby dey listen, Boss.", "voice": OPENROUTER_TTS_VOICE})

@app.route("/api/call/end", methods=["POST"])
@owner_only
def call_end():
    d = request.get_json() or {}
    sid = d.get("call_id")
    with DB() as db:
        db.q("UPDATE call_sessions SET status='ended', ended_at=datetime('now') WHERE id=? AND user_id=?", (sid, request.user["id"]))
    return jsonify({"success": True, "message": "Call ended. See you soon, Boss."})

@app.route("/api/chat/history")
@owner_only
def hist():
    conv_id = request.args.get("conversation_id", type=int)
    with DB() as db:
        if conv_id:
            rows = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=? AND conversationid=? ORDER BY id ASC', (request.user["id"], conv_id))
        else:
            rows = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=? ORDER BY id ASC', (request.user["id"],))
    return jsonify({"success": True, "messages": rows, "conversation_id": conv_id})

@app.route("/api/chat", methods=["POST"])
@owner_only
def chat():
    u = request.user
    d = request.get_json() or {}
    msg = str(d.get("message", "")).strip()
    image_b64 = d.get("image")
    want_voice = d.get("voice", True) if "voice" in d else True
    if d.get("speak") is True:
        want_voice = True
    if not msg and not image_b64:
        return jsonify({"success": False, "error": "Message required"}), 400
    low = (msg or "").lower()
    conv_id = d.get("conversation_id") or d.get("conversationId")
    if d.get("new_chat"):
        with DB() as db:
            conv_id = db.ins("INSERT INTO conversations (user_id, title) VALUES (?,?)", (u["id"], clean(msg)[:40] or "New Chat"))
            if not conv_id:
                conv_id = 1
    else:
        conv_id = get_or_create_conversation(u["id"], conv_id, msg)
    if not conv_id:
        conv_id = 1
    cmd_reply = handle_bike_or_house_command(u, low, msg, conv_id) if msg else None
    if cmd_reply:
        audio = None
        if want_voice:
            try:
                audio = speak_text_to_audio(cmd_reply)
            except Exception:
                pass
        return jsonify({"success": True, "reply": cmd_reply, "audio": audio, "audio_url": audio, "conversation_id": conv_id})
    if image_b64:
        try:
            analysis = analyze_image_b64(image_b64, msg or "Describe this image in detail, what do you see? Be friendly and helpful. If it is a bike, check battery, condition, give honest feedback.")
            save_msg(u["id"], "user", msg or "Analyze this image", conv_id)
            save_msg(u["id"], "assistant", analysis, conv_id)
            audio = None
            if want_voice:
                try:
                    audio = speak_text_to_audio(analysis)
                except Exception:
                    pass
            return jsonify({"success": True, "reply": analysis, "audio": audio, "audio_url": audio, "conversation_id": conv_id})
        except Exception as e:
            return jsonify({"success": True, "reply": "Sorry Boss, image analysis small issue: " + clean(str(e))[:200], "conversation_id": conv_id})
    is_q = "?" in low and ("can you" in low or "do you" in low or "you can" in low)
    if not is_q and (low.startswith(("generate image", "create image", "make image", "draw", "generate a villain", "generate a poster", "create a poster")) or "generate image" in low):
        try:
            prompt = re.sub(r"^(generate image|create image|make image|draw|generate a villain|generate a poster|create a poster)\s*(of|:)?", "", msg, flags=re.I).strip() or msg
            img = gen_image_cloudflare(prompt)
            save_msg(u["id"], "user", msg, conv_id)
            save_msg(u["id"], "assistant", "Here is your image, Boss.", conv_id)
            return jsonify({"success": True, "reply": "Here is your image, Boss.", "image": img, "image_url": img, "conversation_id": conv_id})
        except Exception as e:
            return jsonify({"success": True, "reply": "Image service busy small, Boss. Check Cloudflare keys. Detail: " + clean(str(e))[:200], "conversation_id": conv_id})
    try:
        with DB() as db:
            mems = db.all('SELECT content FROM memories WHERE COALESCE(user_id, "userId")=? ORDER BY id DESC LIMIT 8', (u["id"],))
            hist = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=? AND conversationid=? ORDER BY id DESC LIMIT 6', (u["id"], conv_id))
        mem_txt = "\n".join([m["content"] for m in mems]) or "No memories yet. Boss is Faruq Sulaimon, owner of Ultimate Gee Industry, creator of Tabby."
        if "remember" in low:
            with DB() as db:
                db.q('INSERT INTO memories (user_id, "userId", content) VALUES (?,?,?)', (u["id"], u["id"], msg))
        search = live_search(msg, 5) if needs_live_search(msg) else []
        system = GEE_SYSTEM + "\nMEMORIES: " + mem_txt + "\nBIKE CONTEXT: " + bike_ctx(u) + " You manage Tabby EV bikes: Intelligent BMS, GPS tracking, anti-theft, journey assistant, predictive maintenance, remote security, installment payments."
        if search:
            system += "\nLIVE SEARCH - REAL LIVE DATA, USE THIS, DO NOT INVENT:\n" + "\n".join(search)
        msgs = [{"role": "system", "content": system}] + [{"role": r["role"], "content": r["content"]} for r in reversed(hist)] + [{"role": "user", "content": msg}]
        reply = call_ai(msgs)
        save_msg(u["id"], "user", msg, conv_id)
        save_msg(u["id"], "assistant", reply, conv_id)
        auto_title_conversation(conv_id, msg)
        threading.Thread(target=prune_and_summarise, args=(u["id"], conv_id), daemon=True).start()
        audio = None
        if want_voice:
            try:
                audio = speak_text_to_audio(reply)
            except Exception as e:
                print("TTS chat", e)
        return jsonify({"success": True, "reply": reply, "audio": audio, "audio_url": audio, "conversation_id": conv_id})
    except Exception as e:
        return jsonify({"success": True, "reply": "Sorry Boss, small issue: " + clean(str(e))[:300], "conversation_id": conv_id})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))