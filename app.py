import os, re, json, time, base64, threading, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from functools import wraps
import requests
import psycopg2, psycopg2.extras, psycopg2.pool
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
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()
CLOUDFLARE_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
DB_URL = os.getenv("SUPABASE_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o-mini").strip()

_pool = None
def pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DB_URL, sslmode="require", connect_timeout=10)
    return _pool

class DB:
    def __enter__(self):
        self.c = pool().getconn(); self.c.autocommit = False
        self.cur = self.c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self
    def __exit__(self, *a):
        try: self.c.commit()
        except: self.c.rollback()
        try: self.cur.close(); pool().putconn(self.c)
        except: pass
        return False
    def q(self, sql, p=()):
        self.cur.execute(sql, tuple(p) if isinstance(p, (list, tuple)) else (p,))
        return self.cur
    def one(self, sql, p=()):
        self.q(sql, p); r = self.cur.fetchone(); return dict(r) if r else None
    def all(self, sql, p=()):
        self.q(sql, p); return [dict(r) for r in self.cur.fetchall()]
    def ins(self, sql, p=()):
        sql2 = sql
        if "RETURNING" not in sql2.upper(): sql2 += " RETURNING id"
        self.q(sql2, p); r = self.cur.fetchone(); return r["id"] if r else None

def init_db():
    try:
        with DB() as db:
            db.q("""
            CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY, name TEXT, email TEXT UNIQUE, password_hash TEXT, role TEXT DEFAULT 'customer', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS messages(id SERIAL PRIMARY KEY, user_id INT, role TEXT, content TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS memories(id SERIAL PRIMARY KEY, user_id INT, content TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS motorcycles(id SERIAL PRIMARY KEY, owner_id INT, assigned_user_id INT, name TEXT, model TEXT DEFAULT 'Tabby EV', reg TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS bike_status(motorcycle_id INT PRIMARY KEY, battery_health DOUBLE PRECISION DEFAULT 100, battery_level DOUBLE PRECISION DEFAULT 100, range_km DOUBLE PRECISION DEFAULT 125, total_trip DOUBLE PRECISION DEFAULT 0, total_distance DOUBLE PRECISION DEFAULT 0, co2_saved DOUBLE PRECISION DEFAULT 0, lock_status TEXT DEFAULT 'locked', access_mode TEXT DEFAULT 'full', cell_temp DOUBLE PRECISION DEFAULT 32, motor_temp DOUBLE PRECISION DEFAULT 35, last_seen TEXT);
            CREATE TABLE IF NOT EXISTS telemetry(id SERIAL PRIMARY KEY, motorcycle_id INT, latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, speed DOUBLE PRECISION, battery_level DOUBLE PRECISION, engine_temp DOUBLE PRECISION, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS payment_plans(id SERIAL PRIMARY KEY, user_id INT, motorcycle_id INT, total_amount DOUBLE PRECISION, deposit DOUBLE PRECISION DEFAULT 0, installment_amount DOUBLE PRECISION, installments_paid INT DEFAULT 0, installments_total INT, next_due_date TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS payments(id SERIAL PRIMARY KEY, user_id INT, motorcycle_id INT, amount DOUBLE PRECISION, status TEXT DEFAULT 'pending', reference TEXT UNIQUE, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS house_devices(id SERIAL PRIMARY KEY, user_id INT, name TEXT, room TEXT DEFAULT 'living', type TEXT, status TEXT DEFAULT 'off', created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS auto_learned(id SERIAL PRIMARY KEY, topic TEXT, content TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS bike_commands(id SERIAL PRIMARY KEY, motorcycle_id INT, command TEXT, issued_by INT, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW());
            """)
            # --- MIGRATION: Fix old Supabase tables with camelCase userId ---
            # This is the fix for: column "userId" does not exist
            for table in ["messages", "memories"]:
                try:
                    db.q(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id INT')
                    db.q(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "userId" INT')
                    db.q(f'UPDATE {table} SET user_id = COALESCE(user_id, "userId") WHERE user_id IS NULL')
                    db.q(f'UPDATE {table} SET "userId" = COALESCE("userId", user_id) WHERE "userId" IS NULL')
                except Exception as e:
                    print(f"migrate {table}", e)
            try:
                db.q('ALTER TABLE motorcycles ADD COLUMN IF NOT EXISTS assigned_user_id INT')
                db.q('ALTER TABLE motorcycles ADD COLUMN IF NOT EXISTS "assignedUserId" INT')
                db.q('UPDATE motorcycles SET assigned_user_id = COALESCE(assigned_user_id, "assignedUserId") WHERE assigned_user_id IS NULL')
                db.q('UPDATE motorcycles SET "assignedUserId" = COALESCE("assignedUserId", assigned_user_id) WHERE "assignedUserId" IS NULL')
            except Exception as e:
                print("migrate motorcycles", e)
            try:
                db.q('ALTER TABLE motorcycles ADD COLUMN IF NOT EXISTS owner_id INT')
                db.q('ALTER TABLE motorcycles ADD COLUMN IF NOT EXISTS "ownerId" INT')
                db.q('UPDATE motorcycles SET owner_id = COALESCE(owner_id, "ownerId") WHERE owner_id IS NULL')
            except Exception as e:
                print("migrate owner", e)
            if OWNER_EMAIL:
                db.q("UPDATE users SET role='owner' WHERE lower(email)=%s", (OWNER_EMAIL,))
    except Exception as e:
        print("DB init", e)
init_db()

AUTO = {"news": [], "last": ""}
def clean(t):
    if not t: return ""
    t = re.sub(r"\*+", "", str(t)); t = re.sub(r"_+", "", t); t = re.sub(r"#+", "", t)
    return re.sub(r"\s+", " ", t).strip()

# ---------- LIVE SEARCH - AUTOMATIC ----------
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
                    db.q("INSERT INTO auto_learned (topic, content) VALUES (%s,%s)", ("auto", n[:1000]))
            print("Auto-learn done", len(all_n))
        except Exception as e: print("Auto", e)
        time.sleep(1800)
threading.Thread(target=auto_study, daemon=True).start()

# ---------- AI: OPENAI -> GEMINI -> OPENROUTER ----------
def call_openai(msgs, temp=0.7):
    if not OPENAI_KEY: raise RuntimeError("no openai key")
    r = requests.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}, json={"model": "gpt-4o-mini", "messages": msgs, "temperature": temp, "max_tokens": 1800}, timeout=60)
    if r.status_code in (429, 401, 403, 500, 503): raise RuntimeError(f"OpenAI limit/error {r.status_code}: {r.text[:300]}")
    if r.status_code != 200: raise RuntimeError(r.text[:400])
    return clean(r.json()["choices"][0]["message"]["content"])

def call_gemini(msgs, temp=0.7):
    if not GEMINI_KEY: raise RuntimeError("no gemini key")
    system = "\n".join([m["content"] for m in msgs if m["role"] == "system"])
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs if m["role"] != "system"]
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}", json={"system_instruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": {"temperature": temp, "maxOutputTokens": 1800}}, timeout=60)
    if r.status_code != 200: raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    return clean(r.json()["candidates"][0]["content"]["parts"][0]["text"])

def call_openrouter(msgs, temp=0.7):
    if not OPENROUTER_KEY: raise RuntimeError("no openrouter")
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}, json={"model": OPENROUTER_MODEL, "messages": msgs, "temperature": temp, "max_tokens": 1800}, timeout=60)
    if r.status_code != 200: raise RuntimeError(r.text[:300])
    return clean(r.json()["choices"][0]["message"]["content"])

def call_ai(msgs):
    errs = []
    for fn, name in [(call_openai, "OpenAI"), (call_gemini, "Gemini"), (call_openrouter, "OpenRouter")]:
        try: return fn(msgs)
        except Exception as e: errs.append(f"{name}: {e}"); print(f"AI fallback {name} failed", e)
    raise RuntimeError(" | ".join(errs)[:800])

def analyze_image_b64(b64, prompt):
    if OPENAI_KEY:
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}, json={"model": VISION_MODEL, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": b64}}]}], "max_tokens": 1200}, timeout=60)
            if r.status_code == 200: return clean(r.json()["choices"][0]["message"]["content"])
        except Exception as e: print("Vision OpenAI", e)
    if GEMINI_KEY:
        try:
            pure = b64.split(",", 1)[-1] if "," in b64 else b64
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}", json={"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": pure}}]}]}, timeout=60)
            if r.status_code == 200: return clean(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as e: print("Vision Gemini", e)
    raise RuntimeError("Image analysis unavailable - check API keys")

# ---------- IMAGE: CLOUDFLARE ----------
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
        with DB() as db: return db.one("SELECT * FROM users WHERE id=%s", (d["user_id"],))
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
        b = db.one('SELECT * FROM motorcycles WHERE COALESCE(assigned_user_id, "assignedUserId")=%s ORDER BY id DESC LIMIT 1', (user["id"],))
        if not b: return "No bike assigned."
        st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=%s", (b["id"],))
        pl = db.one("SELECT * FROM payment_plans WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (b["id"],))
        return f"Bike: {b} Status: {st} Plan: {pl}"

# Helper to save message in BOTH columns so old + new code work
def save_msg(user_id, role, content):
    with DB() as db:
        db.q('INSERT INTO messages (user_id, "userId", role, content) VALUES (%s,%s,%s,%s)', (user_id, user_id, role, content))

@app.route("/")
def home(): return render_template("index.html")
@app.route("/health")
def health(): return jsonify({"success": True, "version": "18.0-final-fixed", "auto_last": AUTO.get("last"), "auto_count": len(AUTO.get("news", []))})

@app.route("/api/register", methods=["POST"])
@owner_only
def create_customer():
    d = request.get_json() or {}
    name = clean(d.get("name")); email = str(d.get("email","")).lower().strip(); pw = str(d.get("password","") or "Tabby1234")
    bike_name = clean(d.get("bike_name") or "Tabby EV 1")
    if not name or not email: return jsonify({"success": False, "error": "Name + email required"}), 400
    with DB() as db:
        if db.one("SELECT id FROM users WHERE email=%s", (email,)): return jsonify({"success": False, "error": "Customer already exists"}), 409
        uid = db.ins("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,'customer')", (name, email, generate_password_hash(pw)))
        bid = db.ins('INSERT INTO motorcycles (owner_id, "ownerId", assigned_user_id, "assignedUserId", name) VALUES (%s,%s,%s,%s,%s)', (request.user["id"], request.user["id"], uid, uid, bike_name))
        db.q("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (bid,))
        if d.get("total_amount"):
            db.q("INSERT INTO payment_plans (user_id,motorcycle_id,total_amount,deposit,installment_amount,installments_total,next_due_date) VALUES (%s,%s,%s,%s,%s,%s,%s)", (uid, bid, float(d.get("total_amount")), float(d.get("deposit") or 0), float(d.get("installment_amount") or 0), int(d.get("installments_total") or 1), str(d.get("next_due_date") or "")))
    return jsonify({"success": True, "user_id": uid, "bike_id": bid, "message": f"Customer {name} created. Password: {pw}"}), 201

@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json() or {}
    email = str(d.get("email","")).lower().strip(); pw = str(d.get("password",""))
    with DB() as db: u = db.one("SELECT * FROM users WHERE email=%s", (email,))
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
            bikes = db.all('SELECT m.*, bs.* FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id WHERE COALESCE(m.assigned_user_id, m."assignedUserId")=%s', (u["id"],))
            plans = db.all("SELECT * FROM payment_plans WHERE user_id=%s", (u["id"],))
            return jsonify({"success": True, "role": "customer", "bikes": bikes, "plans": plans})

@app.route("/api/bikes", methods=["GET"])
@auth
def list_bikes():
    u = request.user
    with DB() as db:
        if u["role"] == "owner": rows = db.all("SELECT m.*, bs.* FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id ORDER BY m.id DESC")
        else: rows = db.all('SELECT m.*, bs.* FROM motorcycles m LEFT JOIN bike_status bs ON bs.motorcycle_id=m.id WHERE COALESCE(m.assigned_user_id, m."assignedUserId")=%s', (u["id"],))
    return jsonify({"success": True, "bikes": rows})

@app.route("/api/bike/<int:bid>/command", methods=["POST"])
@auth
def bike_cmd(bid):
    u = request.user; cmd = str((request.get_json() or {}).get("command","")).lower()
    with DB() as db:
        bike = db.one("SELECT * FROM motorcycles WHERE id=%s", (bid,))
        if not bike: return jsonify({"success": False, "error": "Bike not found"}), 404
        assigned = bike.get("assigned_user_id") or bike.get("assignedUserId")
        if u["role"] != "owner" and assigned != u["id"]: return jsonify({"success": False, "error": "Not your bike"}), 403
        st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=%s", (bid,))
        if st and st.get("access_mode") == "restricted" and cmd in ("unlock", "start"):
            return jsonify({"success": False, "error": "Bike in restricted mode - payment overdue. Contact Ultimate Gee Industry."}), 403
        db.q("INSERT INTO bike_commands (motorcycle_id, command, issued_by) VALUES (%s,%s,%s)", (bid, cmd, u["id"]))
        if cmd in ("lock","unlock"):
            db.q("UPDATE bike_status SET lock_status=%s WHERE motorcycle_id=%s", ("locked" if cmd=="lock" else "unlocked", bid))
        if cmd == "locate":
            tel = db.one("SELECT * FROM telemetry WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (bid,))
            return jsonify({"success": True, "message": "Bike location", "telemetry": tel})
    return jsonify({"success": True, "message": f"Bike {cmd} done, Boss." if u["role"]=="owner" else f"Bike {cmd} done."})

@app.route("/api/bike/<int:bid>/telemetry", methods=["POST"])
def telemetry(bid):
    d = request.get_json() or {}
    with DB() as db:
        db.q("INSERT INTO telemetry (motorcycle_id, latitude, longitude, speed, battery_level, engine_temp) VALUES (%s,%s,%s,%s,%s,%s)", (bid, d.get("latitude"), d.get("longitude"), d.get("speed"), d.get("battery_level"), d.get("engine_temp")))
        db.q("INSERT INTO bike_status (motorcycle_id) VALUES (%s) ON CONFLICT (motorcycle_id) DO NOTHING", (bid,))
        db.q("UPDATE bike_status SET battery_level=COALESCE(%s,battery_level), last_seen=%s WHERE motorcycle_id=%s", (d.get("battery_level"), datetime.now(timezone.utc).isoformat(), bid))
    return jsonify({"success": True})

@app.route("/api/payments/remind", methods=["POST"])
@owner_only
def remind():
    with DB() as db:
        due = db.all("SELECT pp.*, u.name, u.email FROM payment_plans pp JOIN users u ON u.id=pp.user_id WHERE pp.status='active' AND pp.next_due_date <= %s", ((datetime.now(timezone.utc)+timedelta(days=3)).date().isoformat(),))
    return jsonify({"success": True, "due_soon": due, "message": f"{len(due)} customers due soon. Reminders ready to send."})

@app.route("/api/payments/overdue-check", methods=["POST"])
@owner_only
def overdue():
    with DB() as db:
        overdue_plans = db.all("SELECT * FROM payment_plans WHERE status='active' AND next_due_date < %s", (datetime.now(timezone.utc).date().isoformat(),))
        for p in overdue_plans:
            try:
                due = datetime.fromisoformat(p["next_due_date"]).date()
                if (datetime.now(timezone.utc).date() - due).days > 7:
                    db.q("UPDATE bike_status SET access_mode='restricted' WHERE motorcycle_id=%s", (p["motorcycle_id"],))
                    db.q("UPDATE payment_plans SET status='overdue' WHERE id=%s", (p["id"],))
            except: pass
    return jsonify({"success": True, "overdue": len(overdue_plans)})

@app.route("/api/chat/history")
@owner_only
def hist():
    with DB() as db: rows = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=%s ORDER BY id ASC', (request.user["id"],))
    return jsonify({"success": True, "messages": rows})

@app.route("/api/chat", methods=["POST"])
@owner_only
def chat():
    u = request.user
    d = request.get_json() or {}
    msg = str(d.get("message","")).strip()
    image_b64 = d.get("image")
    if not msg and not image_b64: return jsonify({"success": False, "error": "Message required"}), 400
    low = (msg or "").lower()
    with DB() as db:
        if "bike" in low and any(w in low for w in ["lock","unlock","check","battery","locate","status"]):
            bike = db.one("SELECT * FROM motorcycles ORDER BY id DESC LIMIT 1")
            if bike:
                st = db.one("SELECT * FROM bike_status WHERE motorcycle_id=%s", (bike["id"],))
                if "unlock" in low: db.q("UPDATE bike_status SET lock_status='unlocked' WHERE motorcycle_id=%s", (bike["id"],)); reply = f"Done, Boss. Your bike {bike['name']} is unlocked."
                elif "lock" in low: db.q("UPDATE bike_status SET lock_status='locked' WHERE motorcycle_id=%s", (bike["id"],)); reply = f"Done, Boss. Your bike {bike['name']} is locked."
                elif "locate" in low:
                    tel = db.one("SELECT * FROM telemetry WHERE motorcycle_id=%s ORDER BY id DESC LIMIT 1", (bike["id"],)); reply = f"Boss, your bike is at {tel}."
                else: reply = f"Boss, your bike {bike['name']} - Lock: {st.get('lock_status') if st else 'locked'}, Battery: {st.get('battery_level') if st else 100}%, Health: {st.get('battery_health') if st else 100}%, Range: {st.get('range_km') if st else 125}km. BMS: Cell temp {st.get('cell_temp')}C, Motor {st.get('motor_temp')}C. It is ready."
                save_msg(u["id"], "user", msg); save_msg(u["id"], "assistant", reply)
                return jsonify({"success": True, "reply": reply})
        if any(w in low for w in ["light","fan","ac","tv","generator","house"]):
            act = "on" if any(w in low for w in ["turn on","switch on","open","start"]) else "off" if any(w in low for w in ["turn off","switch off","close","stop"]) else None
            if act:
                dev = next((w for w in ["light","fan","ac","tv","generator"] if w in low), "light")
                db.q("INSERT INTO house_devices (user_id,name,room,type,status) VALUES (%s,%s,%s,%s,%s)", (u["id"], dev, "living", dev, act))
                reply = f"Done, Boss. Living {dev} is now {act}."
                save_msg(u["id"], "user", msg); save_msg(u["id"], "assistant", reply)
                return jsonify({"success": True, "reply": reply})
    if image_b64:
        try:
            analysis = analyze_image_b64(image_b64, msg or "Describe this image in detail, what do you see? Be friendly and helpful.")
            save_msg(u["id"], "user", msg or "Analyze this image"); save_msg(u["id"], "assistant", analysis)
            return jsonify({"success": True, "reply": analysis})
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
            mems = db.all('SELECT content FROM memories WHERE COALESCE(user_id, "userId")=%s ORDER BY id DESC LIMIT 15', (u["id"],))
            hist = db.all('SELECT role, content FROM messages WHERE COALESCE(user_id, "userId")=%s ORDER BY id DESC LIMIT 12', (u["id"],))
        mem_txt = "\n".join([m["content"] for m in mems]) or "No memories yet. Boss is Faruq Sulaimon, owner of Ultimate Gee Industry, creator of Tabby."
        if "remember" in low:
            with DB() as db: db.q('INSERT INTO memories (user_id, "userId", content) VALUES (%s,%s,%s)', (u["id"], u["id"], msg))
        search = live_search(msg, 5)
        if AUTO.get("news"): search = search + ["AUTO-LEARNED TODAY: " + x for x in AUTO["news"][:4]]
        system = f"You are Tabby, friendly AI assistant for Faruq Sulaimon, Boss, creator and owner of Ultimate Gee Industry. Call him Boss. You are warm, friendly, witty, not generic robot. Basic language: English with UK accent style (use British spelling: colour, favourite, organise). You understand ANY language - reply in same language user used, but default English. NEVER say I do not have live access, my knowledge goes up to, I cannot give latest news. You HAVE live internet - LIVE SEARCH is provided automatically. If LIVE SEARCH is given, USE IT. If empty, say Network small Boss, try again. NEVER use asterisks, hashtags, emojis. Be direct first, then small detail. USER: {u['name']} Role owner. MEMORIES: {mem_txt}. BIKE CONTEXT: {bike_ctx(u)}. You manage Tabby EV bikes: Intelligent BMS, GPS tracking, anti-theft, journey assistant, predictive maintenance, remote security, installment payments."
        if search: system += "\nLIVE SEARCH - REAL LIVE DATA, USE THIS, DO NOT INVENT:\n" + "\n".join(search)
        msgs = [{"role": "system", "content": system}] + [{"role": r["role"], "content": r["content"]} for r in reversed(hist)] + [{"role": "user", "content": msg}]
        reply = call_ai(msgs)
        save_msg(u["id"], "user", msg); save_msg(u["id"], "assistant", reply)
        return jsonify({"success": True, "reply": reply})
    except Exception as e:
        return jsonify({"success": True, "reply": f"Sorry Boss, small issue: {clean(str(e))[:300]}"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))