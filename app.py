import os, re, json, time, base64, threading, urllib.parse, xml.etree.ElementTree as ET, uuid, sqlite3
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

# --- SQLITE - NO SUPABASE ---
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tabby.db")
_lock = threading.Lock()

class DB:
    def __enter__(self):
        _lock.acquire()
        self.c = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.cur = self.c.cursor()
        return self
    def __exit__(self, *a):
        try: self.c.commit()
        except:
            try: self.c.rollback()
            except: pass
        try: self.cur.close()
        except: pass
        try: self.c.close()
        except: pass
        try: _lock.release()
        except: pass
        return False
    def q(self, sql, p=()):
        if "%s" in sql:
            sql = sql.replace("%s", "?")
        sql = sql.replace("NOW()", "datetime('now')")
        params = tuple(p) if isinstance(p, (list, tuple)) else (p,)
        if params == ((),): params = ()
        self.cur.execute(sql, params)
        return self.cur
    def one(self, sql, p=()):
        self.q(sql, p); r = self.cur.fetchone(); return dict(r) if r else None
    def all(self, sql, p=()):
        self.q(sql, p); return [dict(r) for r in self.cur.fetchall()]
    def ins(self, sql, p=()):
        sql2 = sql
        if "RETURNING" not in sql2.upper(): sql2 += " RETURNING id"
        try:
            self.q(sql2, p); r = self.cur.fetchone(); return r["id"] if r else self.cur.lastrowid
        except Exception:
            sql3 = sql.replace("%s", "?") if "%s" in sql else sql
            self.cur.execute(sql3, tuple(p) if isinstance(p, (list, tuple)) else (p,))
            return self.cur.lastrowid

def init_db():
    try:
        with DB() as db:
            db.q("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE, password_hash TEXT, role TEXT DEFAULT 'customer', created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "userId" INTEGER, role TEXT, content TEXT, conversationid INTEGER DEFAULT 1, "conversationId" INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "userId" INTEGER, content TEXT, created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS motorcycles(id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, "ownerId" INTEGER, assigned_user_id INTEGER, "assignedUserId" INTEGER, name TEXT, model TEXT DEFAULT 'Tabby EV', reg TEXT, status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS bike_status(motorcycle_id INTEGER PRIMARY KEY, battery_health REAL DEFAULT 100, battery_level REAL DEFAULT 100, range_km REAL DEFAULT 125, total_trip REAL DEFAULT 0, total_distance REAL DEFAULT 0, co2_saved REAL DEFAULT 0, lock_status TEXT DEFAULT 'locked', access_mode TEXT DEFAULT 'full', cell_temp REAL DEFAULT 32, motor_temp REAL DEFAULT 35, last_seen TEXT)""")
            db.q("""CREATE TABLE IF NOT EXISTS telemetry(id INTEGER PRIMARY KEY AUTOINCREMENT, motorcycle_id INTEGER, latitude REAL, longitude REAL, speed REAL, battery_level REAL, engine_temp REAL, created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS payment_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, motorcycle_id INTEGER, total_amount REAL, deposit REAL DEFAULT 0, installment_amount REAL, installments_paid INTEGER DEFAULT 0, installments_total INTEGER, next_due_date TEXT, status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, motorcycle_id INTEGER, amount REAL, status TEXT DEFAULT 'pending', reference TEXT UNIQUE, created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS house_devices(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, room TEXT DEFAULT 'living', type TEXT, status TEXT DEFAULT 'off', created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS auto_learned(id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, content TEXT, created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS bike_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, motorcycle_id INTEGER, command TEXT, issued_by INTEGER, status TEXT DEFAULT 'pending', created_at TEXT DEFAULT (datetime('now')))""")
            db.q("""CREATE TABLE IF NOT EXISTS call_sessions(id TEXT PRIMARY KEY, user_id INTEGER, status TEXT DEFAULT 'live', started_at TEXT DEFAULT (datetime('now')), ended_at TEXT)""")
            if OWNER_EMAIL:
                db.q("UPDATE users SET role='owner' WHERE lower(email)=?", (OWNER_EMAIL,))
    except Exception as e: print("DB init", e)
init_db()

AUTO = {"news": [], "last": ""}
def clean(t):
    if not t: return ""
    t = re.sub(r"\*+", "", str(t)); t = re.sub(r"_+", "", t); t = re.sub(r"#+", "", t)
    return re.sub(r"\s+", " ", t).strip()

def live_search(query, limit=5):
    out = []
    q = (query or "").lower()
    if any(w in q for w in ["naira","dollar","usd","ngn","rate","exchange","price"]):
        try:
            r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8).json()
            out.append(f"Title: Naira to Dollar Rate Today\nInfo: 1 USD = {r['rates'].get('NGN')} NGN. Updated {r.get('time_last_update_utc')}")
        except: pass
    if any(w in q for w in ["bitcoin","btc","crypto","ethereum"]):
        try:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd,ngn", timeout=8).json()
            out.append(f"Title: Crypto Price\nInfo: Bitcoin ${r['bitcoin']['usd']} / {r['bitcoin']['ngn']} NGN. Ethereum ${r['ethereum']['usd']}")
        except: pass
    try:
        qq = urllib.parse.quote(query or "Nigeria news")
        url = f"https://news.google.com/rss/search?q={qq}&hl=en-NG&gl=NG&ceid=NG:en"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        root = ET.fromstring(r.content)
        for it in root.findall(".//item")[:limit]:
            out.append(f"Title: {it.findtext('title')}\nInfo: {it.findtext('pubDate')} | {it.findtext('link')}")
    except Exception as e:
        print("RSS fail", e)
        try:
            r = requests.get("https://api.allorigins.win/raw?url=" + urllib.parse.quote(f"https://news.google.com/rss/search?q={urllib.parse.quote(query or 'Nigeria')}&hl=en-NG&gl=NG&ceid=NG:en"), timeout=10)
            root = ET.fromstring(r.content)
            for it in root.findall(".//item")[:limit]:
                out.append(f"Title: {it.findtext('title')}\nInfo: {it.findtext('pubDate')}")
        except: pass
    if "weather" in q:
        try:
            r = requests.get("https://wttr.in/Lagos?format=j1", timeout=8).json()
            c = r["current_condition"][0]
            out.append(f"Title: Lagos Weather\nInfo: {c['weatherDesc'][0]['value']}, {c['temp_C']}C, Humidity {c['humidity']}%")
        except: pass
    return out

def auto_study():
    topics = ["Nigeria news", "Naira to Dollar rate", "Bitcoin price", "Lagos news", "Afrobeats news", "Premier League", "Fuel price Nigeria", "AI news"]
    while True:
        try:
            all_n = []
            for t in topics:
                all_n += live_search(t, 2); time.sleep(1)
            AUTO["news"] = all_n; AUTO["last"] = datetime.now(timezone.utc).isoformat()
            with DB() as db:
                for n in all_n[:8]:
                    db.q("INSERT INTO auto_learned (topic, content) VALUES (?,?)", ("auto", n[:1000]))
            print("Auto-learn done", len(all_n))
        except Exception as e: print("Auto", e)
        time.sleep(1800)
threading.Thread(target=auto_study, daemon=True).start()

def call_openrouter(msgs, temp=0.7):
    if not OPENROUTER_KEY: raise RuntimeError("no openrouter - set OPENROUTER_API_KEY")
    models = []
    for m in ["meta-llama/llama-3.1-8b-instruct:free", "google/gemma-2-9b-it:free", "deepseek/deepseek-chat-v3-0324:free", OPENROUTER_MODEL, "deepseek/deepseek-chat", "openrouter/free"]:
        if m and m not in models: models.append(m)
    last_err = ""
    for model in models:
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}, json={"model": model, "messages": msgs, "temperature": temp, "max_tokens": 1800}, timeout=60)
            if r.status_code == 200: return clean(r.json()["choices"][0]["message"]["content"])
            last_err = f"{model}: {r.status_code} {r.text[:200]}"
            print(f"OpenRouter {model} {r.status_code}")
        except Exception as e:
            last_err = str(e); print(f"OpenRouter {model} error", e)
    raise RuntimeError(f"OpenRouter failed - {last_err[:300]}")

def call_ai(msgs):
    return call_openrouter(msgs)

def transcribe_audio_file(file_bytes, filename="voice.webm"):
    if not OPENROUTER_KEY: raise RuntimeError("no openrouter key")
    files = {"file": (filename, file_bytes, "audio/webm")}
    data = {"model": OPENROUTER_STT_MODEL}
    r = requests.post("https://openrouter.ai/api/v1/audio/transcriptions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}, files=files, data=data, timeout=90)
    if r.status_code == 200: return (r.json().get("text") or "").strip()
    data["model"] = "openai/whisper-large-v3"
    r = requests.post("https://openrouter.ai/api/v1/audio/transcriptions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}, files={"file": (filename, file_bytes, "audio/webm")}, data=data, timeout=90)
    if r.status_code == 200: return (r.json().get("text") or "").strip()
    raise RuntimeError(f"Transcribe failed {r.status_code}: {r.text[:300]}")

def transcribe_base64_audio(b64_data, filename="voice.webm"):
    pure = b64_data.split(",", 1)[-1] if "," in b64_data else b64_data
    return transcribe_audio_file(base64.b64decode(pure), filename)

def speak_text_to_audio(text, voice=None):
    if not OPENROUTER_KEY: raise RuntimeError("no openrouter key")
    if not text or not text.strip(): raise RuntimeError("empty text")
    text = text[:1200]
    v = voice or OPENROUTER_TTS_VOICE
    for model in [OPENROUTER_TTS_MODEL, "openai/gpt-4o-mini-tts", "openai/tts-1", "openai/tts-1-hd"]:
        try:
            r = requests.post("https://openrouter.ai/api/v1/audio/speech", headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}, json={"model": model, "input": text, "voice": v, "response_format": "mp3"}, timeout=90)
            if r.status_code == 200 and len(r.content) > 1000:
                return "data:audio/mpeg;base64," + base64.b64encode(r.content).decode()
            print("TTS fail", model, r.status_code, r.text[:200])
        except Exception as e: print("TTS exception", e)
    raise RuntimeError("TTS failed - check OpenRouter credits - need at least $0.50 balance for audio")

def analyze_image_b64(b64, prompt):
    if not OPENROUTER_KEY: raise RuntimeError("Image analysis unavailable - no OpenRouter key")
    for model in ["openai/gpt-4o-mini", "google/gemma-3-27b-it"]:
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": b64}}]}], "max_tokens": 1200}, timeout=60)
            if r.status_code == 200: return clean(r.json()["choices"][0]["message"]["content"])
        except Exception as e: print("Vision OpenRouter", e)
    raise RuntimeError("Image analysis unavailable - check OpenRouter key / credits")

def gen_image_cloudflare(prompt):
    if not CLOUDFLARE_ID or not CLOUDFLARE_TOKEN: raise RuntimeError("Cloudflare keys missing in Render Env")
    p = prompt + ", high quality, detailed, cinematic lighting, sharp, clear"
    for model in ["@cf/black-forest-labs/flux-1-schnell", "@cf/stabilityai/stable-diffusion-xl-base-1.0"]:
        try:
            r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ID}/ai/run/{model}", headers={"Authorization": f"Bearer {CLOUDFLARE_TOKEN}", "Content-Type": "application/json"}, json={"prompt": p, "num_steps": 4}, timeout=60)
            if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/"):
                return "data:" + r.headers.get("Content-Type") + ";base64," + base64.b64encode(r.content).decode()
            try:
                j = r.json(); img = (j.get("result") or {}).get("image")
                if img: return "data:image/jpeg;base64," + img
            except: pass
        except Exception as e: print("CF", model, e)
    raise RuntimeError("Cloudflare image failed - check CLOUDFLARE_ACCOUNT_ID / TOKEN")

def get_user():
    a = request.headers.get("Authorization", "")
    if not a.startswith("Bearer "): return None
    try:
        d = serializer.loads(a.split(" ", 1)[1], max_age=60*60*24*30)
        with DB() as db: return db.one("SELECT * FROM users WHERE id=?", (d["user_id"],))
    except: return None

def auth(f):
    @wraps(f)
    def w(*a, **k):
        u = get_user()
        if not u: return jsonify({"success": False, "error": "Login required"}), 401
        request.user = u; return f(*a, **k)
    return w
def owner_only(f):
    @wraps(f)
    @auth
    def w(*a, **k):
        if request.user["role"] != "owner": return jsonify({"success": False, "error": "Owner only"}), 403
        return f(*a, **k)
    return w

def bike_ctx(user):
    with DB() as db:
        if user["role"] == "owner":
            c = db.one("SELECT COUNT(*) as c FROM motorcycles")["c"]
            return f"Owner fleet: {c} bikes. Owner can see all."
        b = db.one('SELECT * FROM motorcycles WHERE COALESCE(assigned_user_id, "assignedUserId")=? ORDER BY id DESC LIMIT 1', (user["id"],))
        if not b: return "No bike assigned."
        st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=?", (b["id"],))
        pl = db.one("SELECT * FROM payment_plans WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (b["id"],))
        return f"Bike: {b} Status: {st} Plan: {pl}"

def save_msg(user_id, role, content):
    with DB() as db:
        db.q('INSERT INTO messages (user_id, "userId", role, content, conversationid, "conversationId") VALUES (?,?,?,?,1,1)', (user_id, user_id, role, content))

def handle_bike_or_house_command(u, low, msg):
    with DB() as db:
        if "bike" in low and any(w in low for w in ["lock","unlock","check","battery","locate","status"]):
            bike = db.one("SELECT * FROM motorcycles ORDER BY id DESC LIMIT 1")
            if bike:
                st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=?", (bike["id"],))
                if "unlock" in low: db.q("UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=?", (bike["id"],)); reply = f"Done, Boss. Your bike {bike['name']} is unlocked."
                elif "lock" in low: db.q("UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=?", (bike["id"],)); reply = f"Done, Boss. Your bike {bike['name']} is locked."
                elif "locate" in low:
                    tel = db.one("SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (bike["id"],)); reply = f"Boss, your bike is at {tel}."
                else: reply = f"Boss, your bike {bike['name']} - Lock: {st.get('lock_status') if st else 'locked'}, Battery: {st.get('battery_level') if st else 100}%, Health: {st.get('battery_health') if st else 100}%, Range: {st.get('range_km') if st else 125}km. BMS: Cell temp {st.get('cell_temp')}C, Motor {st.get('motor_temp')}C. It is ready."
                save_msg(u["id"], "user", msg); save_msg(u["id"], "assistant", reply)
                return reply
        if any(w in low for w in ["light","fan","ac","tv","generator","house"]):
            act = "on" if any(w in low for w in ["turn on","switch on","open","start"]) else "off" if any(w in low for w in ["turn off","switch off","close","stop"]) else None
            if act:
                dev = next((w for w in ["light","fan","ac","tv","generator"] if w in low), "light")
                db.q("INSERT INTO house_devices (user_id,name,room,type,status) VALUES (?,?,?,?,?)", (u["id"], dev, "living", dev, act))
                reply = f"Done, Boss. Living {dev} is now {act}."
                save_msg(u["id"], "user", msg); save_msg(u["id"], "assistant", reply)
                return reply
    return None

@app.route("/")
def home(): return render_template("index.html")
@app.route("/health")
def health(): return jsonify({"success": True, "version": "tabby-20-voice-sqlite", "auto_last": AUTO.get("last"), "auto_count": len(AUTO.get("news", [])), "voice": {"stt": OPENROUTER_STT_MODEL, "tts": OPENROUTER_TTS_MODEL, "tts_voice": OPENROUTER_TTS_VOICE}})

# ---------- REGISTER / SIGNUP - PUBLIC FOR FIRST OWNER ONLY ----------
@app.route("/api/signup", methods=["POST"])
def signup():
    d = request.get_json() or {}
    name = clean(d.get("name") or "Faruq Sulaimon")
    email = str(d.get("email","")).lower().strip()
    pw = str(d.get("password",""))
    if not email or not pw:
        return jsonify({"success": False, "error": "Email + password required"}), 400
    with DB() as db:
        total = db.one("SELECT COUNT(*) as c FROM users")["c"]
        if total > 0:
            return jsonify({"success": False, "error": "Owner already exists. Please Sign In. Customer accounts are created by Owner only."}), 403
        uid = db.ins("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,'owner')", (name, email, generate_password_hash(pw)))
        bid = db.ins('INSERT INTO motorcycles (owner_id, "ownerId", assigned_user_id, "assignedUserId", name) VALUES (?,?,?,?,?)', (uid, uid, uid, uid, "Tabby EV 1"))
        db.q("INSERT INTO bike_status (motorcycle_id) VALUES (?) ON CONFLICT (motorcycle_id) DO NOTHING", (bid,))
    return jsonify({"success": True, "message": "Owner account created. Now Sign In."})

@app.route("/api/register", methods=["POST"])
@owner_only
def create_customer():
    d = request.get_json() or {}
    name = clean(d.get("name")); email = str(d.get("email","")).lower().strip(); pw = str(d.get("password","") or "Tabby1234")
    bike_name = clean(d.get("bike_name") or "Tabby EV 1")
    if not name or not email: return jsonify({"success": False, "error": "Name + email required"}), 400
    with DB() as db:
        if db.one("SELECT id FROM users WHERE email=?", (email,)): return jsonify({"success": False, "error": "Customer already exists"}), 409
        uid = db.ins("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,'customer')", (name, email, generate_password_hash(pw)))
        bid = db.ins('INSERT INTO motorcycles (owner_id, "ownerId", assigned_user_id, "assignedUserId", name) VALUES (?,?,?,?,?)', (request.user["id"], request.user["id"], uid, uid, bike_name))
        db.q("INSERT INTO bike_status (motorcycle_id) VALUES (?) ON CONFLICT (motorcycle_id) DO NOTHING", (bid,))
        if d.get("total_amount"):
            db.q("INSERT INTO payment_plans (user_id,motorcycle_id,total_amount,deposit,installment_amount,installments_total,next_due_date) VALUES (?,?,?,?,?,?,?)", (uid, bid, float(d.get("total_amount")), float(d.get("deposit") or 0), float(d.get("installment_amount") or 0), int(d.get("installments_total") or 1), str(d.get("next_due_date") or "")))
    return jsonify({"success": True, "user_id": uid, "bike_id": bid, "message": f"Customer {name} created. Password: {pw}"}), 201

@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json() or {}
    email = str(d.get("email","")).lower().strip(); pw = str(d.get("password",""))
    with DB() as db: u = db.one("SELECT * FROM users WHERE email=?", (email,))
    if not u or not check_password_hash(u["password_hash"], pw): return jsonify({"success": False, "error": "Invalid email or password"}), 401
    return jsonify({"success": True, "token": serializer.dumps({"user_id": u["id"]}), "user": {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}})

@app.route("/api/me")
@auth
def me(): return jsonify({"success": True, "user": {"id": request.user["id"], "name": request.user["name"], "email": request.user["email"], "role": request.user["role"]}})

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
        if u["role"] == "owner": rows = db.all("SELECT m.*, bs.* FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id ORDER BY m.id DESC")
        else: rows = db.all('SELECT m.*, bs.* FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id WHERE COALESCE(m.assigned_user_id, m."assignedUserId")=?', (u["id"],))
    return jsonify({"success": True, "bikes": rows})

@app.route("/api/bike/<int:bid>/command", methods=["POST"])
@auth
def bike_cmd(bid):
    u = request.user; cmd = str((request.get_json() or {}).get("command","")).lower()
    with DB() as db:
        bike = db.one("SELECT * FROM motorcycles WHERE id=?", (bid,))
        if not bike: return jsonify({"success": False, "error": "Bike not found"}), 404
        assigned = bike.get("assigned_user_id") or bike.get("assignedUserId")
        if u["role"] != "owner" and assigned != u["id"]: return jsonify({"success": False, "error": "Not your bike"}), 403
        st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=?", (bid,))
        if st and st.get("access_mode") == "restricted" and cmd in ("unlock", "start"):
            return jsonify({"success": False, "error": "Bike in restricted mode - payment overdue. Contact Ultimate Gee Industry."}), 403
        db.q("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (?,?,?)", (bid, cmd, u["id"]))
        if cmd in ("lock","unlock"):
            db.q("UPDATE bike_status SET lock_status=? WHERE motorcycle_id=?", ("locked" if cmd=="lock" else "unlocked", bid))
        if cmd == "locate":
            tel = db.one("SELECT * FROM telemetry WHERE motorcycle_id=? ORDER BY id DESC LIMIT 1", (bid,))
            return jsonify({"success": True, "message": "Bike location", "telemetry": tel})
    return jsonify({"success": True, "message": f"Bike {cmd} done, Boss." if u["role"]=="owner" else f"Bike {cmd} done."})

@app.route("/api/bike/<int:bid>/telemetry", methods=["POST"])
def telemetry(bid):
    d = request.get_json() or {}
    with DB() as db:
        db.q("INSERT INTO telemetry (motorcycle_id, latitude, longitude, speed, battery_level, engine_temp) VALUES (?,?,?,?,?,?)", (bid, d.get("latitude"), d.get("longitude"), d.get("speed"), d.get("battery_level"), d.get("engine_temp")))
        db.q("INSERT INTO bike_status (motorcycle_id) VALUES (?) ON CONFLICT (motorcycle_id) DO NOTHING", (bid,))
        db.q("UPDATE bike_status SET battery_level=COALESCE(?,battery_level), last_seen=? WHERE motorcycle_id=?", (d.get("battery_level"), datetime.now(timezone.utc).isoformat(), bid))
    return jsonify({"success": True})

@app.route("/api/payments/remind", methods=["POST"])
@owner_only
def remind():
    with DB() as db:
        due = db.all("SELECT pp.*, u.name, u.email FROM payment_plans pp JOIN users u ON u.id=pp.user_id WHERE pp.status='active' AND pp.next_due_date <= ?", ((datetime.now(timezone.utc)+timedelta(days=3)).date().isoformat(),))
    return jsonify({"success": True, "due_soon": due, "message": f"{len(due)} customers due soon. Reminders ready to send."})

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
            except: pass
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
            if not d.get("audio"): return jsonify({"success": False, "error": "No audio provided"}), 400
            text = transcribe_base64_audio(d.get("audio"))
        return jsonify({"success": True, "text": text})
    except Exception as e:
        return jsonify({"success": False, "error": clean(str(e))[:300]}), 500

@app.route("/api/voice/speak", methods=["POST"])
@owner_only
def voice_speak():
    d = request.get_json() or {}
    text = str(d.get("text","")).strip()
    voice = d.get("voice")
    if not text: return jsonify({"success": False, "error": "Text required"}), 400
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
            if d.get("audio"): user_text = transcribe_base64_audio(d.get("audio"))
            elif d.get("message"): user_text = str(d.get("message","")).strip()
            else: return jsonify({"success": False, "error": "No audio provided"}), 400
        if not user_text:
            return jsonify({"success": True, "reply": "I no hear you well, Boss, talk am again.", "text": "", "audio": None})
        low = user_text.lower()
        cmd_reply = handle_bike_or_house_command(u, low, user_text)
        if cmd_reply:
            try: audio = speak_text_to_audio(cmd_reply)
            except: audio = None
            return jsonify({"success": True, "text": user_text, "reply": cmd_reply, "audio": audio, "audio_url": audio})
        if "generate image" in low or "draw" in low:
            try:
                prompt = re.sub(r"^(generate image|create image|make image|draw)\s*(of|:)?", "", user_text, flags=re.I).strip() or user_text
                img = gen_image_cloudflare(prompt)
                save_msg(u["id"], "user", user_text); save_msg(u["id"], "assistant", "Here is your image, Boss.")
                return jsonify({"success": True, "text": user_text, "reply": "Here is your image, Boss.", "image": img, "image_url": img, "audio": None})
            except Exception as e: pass
        with DB() as db:
            mems = db.all('SELECT content FROM memories WHERE COALESCE(user_id, "userId")=? ORDER BY id DESC LIMIT 15', (u["id"],))
            hist = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=? ORDER BY id DESC LIMIT 10', (u["id"],))
        mem_txt = "\n".join([m["content"] for m in mems]) or "No memories yet. Boss is Faruq Sulaimon, owner of Ultimate Gee Industry, creator of Tabby."
        search = live_search(user_text, 4)
        if AUTO.get("news"): search = search + ["AUTO-LEARNED TODAY: " + x for x in AUTO["news"][:2]]
        system = f"You are Tabby, friendly AI assistant for Faruq Sulaimon, Boss, creator and owner of Ultimate Gee Industry. Call him Boss. Warm, friendly, witty, not generic robot. Basic language: English with UK accent style. You understand ANY language - reply in same language user used. VOICE MODE: Reply SHORT, 2-4 sentences max, conversational, like phone call. No long lists, no asterisks, no hashtags. USER: {u['name']} MEMORIES: {mem_txt} BIKE CONTEXT: {bike_ctx(u)}"
        if search: system += "\nLIVE SEARCH - REAL LIVE DATA, USE THIS:\n" + "\n".join(search)
        msgs = [{"role": "system", "content": system}] + [{"role": r["role"], "content": r["content"]} for r in reversed(hist)] + [{"role": "user", "content": user_text}]
        reply = call_ai(msgs)
        save_msg(u["id"], "user", f"[voice] {user_text}"); save_msg(u["id"], "assistant", reply)
        try: audio = speak_text_to_audio(reply)
        except Exception as e: print("TTS voice_chat", e); audio = None
        return jsonify({"success": True, "text": user_text, "reply": reply, "audio": audio, "audio_url": audio})
    except Exception as e:
        return jsonify({"success": True, "reply": f"Sorry Boss, small issue with voice: {clean(str(e))[:300]}", "audio": None})

@app.route("/api/call/start", methods=["POST"])
@owner_only
def call_start():
    sid = str(uuid.uuid4())[:8]
    with DB() as db:
        db.q("INSERT INTO call_sessions (id, user_id, status) VALUES (?,?, 'live')", (sid, request.user["id"]))
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
    with DB() as db: rows = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=? ORDER BY id ASC', (request.user["id"],))
    return jsonify({"success": True, "messages": rows})

@app.route("/api/chat", methods=["POST"])
@owner_only
def chat():
    u = request.user
    d = request.get_json() or {}
    msg = str(d.get("message","")).strip()
    image_b64 = d.get("image")
    want_voice = bool(d.get("voice") or d.get("speak"))
    if not msg and not image_b64: return jsonify({"success": False, "error": "Message required"}), 400
    low = (msg or "").lower()
    cmd_reply = handle_bike_or_house_command(u, low, msg) if msg else None
    if cmd_reply:
        audio = None
        if want_voice:
            try: audio = speak_text_to_audio(cmd_reply)
            except: pass
        return jsonify({"success": True, "reply": cmd_reply, "audio": audio, "audio_url": audio})
    if image_b64:
        try:
            analysis = analyze_image_b64(image_b64, msg or "Describe this image in detail, what do you see? Be friendly and helpful. If it is a bike, check battery, condition, give honest feedback.")
            save_msg(u["id"], "user", msg or "Analyze this image"); save_msg(u["id"], "assistant", analysis)
            audio = None
            if want_voice:
                try: audio = speak_text_to_audio(analysis)
                except: pass
            return jsonify({"success": True, "reply": analysis, "audio": audio, "audio_url": audio})
        except Exception as e: return jsonify({"success": True, "reply": f"Sorry Boss, image analysis small issue: {clean(str(e))[:200]}"})
    is_q = "?" in low and ("can you" in low or "do you" in low or "you can" in low)
    if not is_q and (low.startswith(("generate image","create image","make image","draw","generate a villain","generate a poster","create a poster")) or "generate image" in low):
        try:
            prompt = re.sub(r"^(generate image|create image|make image|draw|generate a villain|generate a poster|create a poster)\s*(of|:)?", "", msg, flags=re.I).strip() or msg
            img = gen_image_cloudflare(prompt)
            save_msg(u["id"], "user", msg); save_msg(u["id"], "assistant", "Here is your image, Boss.")
            return jsonify({"success": True, "reply": "Here is your image, Boss.", "image": img, "image_url": img})
        except Exception as e: return jsonify({"success": True, "reply": f"Image service busy small, Boss. Check Cloudflare keys in Render. Detail: {clean(str(e))[:200]}"})
    try:
        with DB() as db:
            mems = db.all('SELECT content FROM memories WHERE COALESCE(user_id, "userId")=? ORDER BY id DESC LIMIT 15', (u["id"],))
            hist = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=? ORDER BY id DESC LIMIT 12', (u["id"],))
        mem_txt = "\n".join([m["content"] for m in mems]) or "No memories yet. Boss is Faruq Sulaimon, owner of Ultimate Gee Industry, creator of Tabby."
        if "remember" in low:
            with DB() as db: db.q('INSERT INTO memories (user_id, "userId", content) VALUES (?,?,?)', (u["id"], u["id"], msg))
        search = live_search(msg, 5)
        if AUTO.get("news"): search = search + ["AUTO-LEARNED TODAY: " + x for x in AUTO["news"][:4]]
        system = f"You are Tabby, friendly AI assistant for Faruq Sulaimon, Boss, creator and owner of Ultimate Gee Industry. Call him Boss. You are warm, friendly, witty, not generic robot. Basic language: English with UK accent style (use British spelling: colour, favourite, organise). You understand ANY language - reply in same language user used, but default English. NEVER say I do not have live access, my knowledge goes up to, I cannot give latest news. You HAVE live internet - LIVE SEARCH is provided automatically. If LIVE SEARCH is given, USE IT. If empty, say Network small Boss, try again. NEVER use asterisks, hashtags, emojis. Be direct first, then small detail. USER: {u['name']} Role owner. MEMORIES: {mem_txt}. BIKE CONTEXT: {bike_ctx(u)}. You manage Tabby EV bikes: Intelligent BMS, GPS tracking, anti-theft, journey assistant, predictive maintenance, remote security, installment payments."
        if search: system += "\nLIVE SEARCH - REAL LIVE DATA, USE THIS, DO NOT INVENT:\n" + "\n".join(search)
        msgs = [{"role": "system", "content": system}] + [{"role": r["role"], "content": r["content"]} for r in reversed(hist)] + [{"role": "user", "content": msg}]
        reply = call_ai(msgs)
        save_msg(u["id"], "user", msg); save_msg(u["id"], "assistant", reply)
        audio = None
        if want_voice:
            try: audio = speak_text_to_audio(reply)
            except Exception as e: print("TTS chat", e)
        return jsonify({"success": True, "reply": reply, "audio": audio, "audio_url": audio})
    except Exception as e:
        return jsonify({"success": True, "reply": f"Sorry Boss, small issue: {clean(str(e))[:300]}"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))