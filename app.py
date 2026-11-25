import os, sqlite3, requests, re, base64, secrets
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, g
from flask_cors import CORS
import jwt
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
CORS(app)

# --- SECURITY: No hard-coded secret. Must be in Render env ---
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: SECRET_KEY not set in env. Using temporary key. Set SECRET_KEY in Render.")
app.config['SECRET_KEY'] = SECRET_KEY

DATABASE = os.environ.get('DATABASE_PATH', 'tabby.db')
CLOUDFLARE_ACCOUNT_ID = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
CLOUDFLARE_API_TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

# --- BRAIN SWITCH: openai (default) or openrouter (optional) ---
AI_PROVIDER = os.environ.get('AI_PROVIDER', 'openai').lower()
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_MODEL = os.environ.get('OPENROUTER_MODEL', 'openai/gpt-4o-mini')

# --- SUPABASE: memory + sync. Optional, SQLite fallback if not set ---
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')
SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_KEY)

def supabase_request(method, table, params=None, payload=None):
    if not SUPABASE_ENABLED:
        return None
    try:
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers = {
            'apikey': SUPABASE_KEY,
            'Authorization': f"Bearer {SUPABASE_KEY}",
            'Content-Type': 'application/json',
            'Prefer': 'return=representation'
        }
        r = requests.request(method, url, headers=headers, params=params, json=payload, timeout=8)
        if r.status_code in [200, 201]:
            return r.json()
        print(f"Supabase {table} error:", r.text[:300])
        return None
    except Exception as e:
        print("Supabase error:", e)
        return None

def supabase_upsert_memory(user_id, key, value):
    # Try update, else insert. Table: user_memory(user_id, key, value)
    existing = supabase_request('GET', 'user_memory', params={'user_id': f'eq.{user_id}', 'key': f'eq.{key}', 'select': 'id'})
    if existing:
        supabase_request('PATCH', 'user_memory', params={'id': f"eq.{existing[0]['id']}"}, payload={'value': str(value)})
    else:
        supabase_request('POST', 'user_memory', payload={'user_id': user_id, 'key': key, 'value': str(value), 'created_at': datetime.now().isoformat()})

# --- OWNER PROFILE - Faruq Sulaimon is creator and owner ---
OWNER_NAME = "Sulaimon Faruq Ezuwagbe"
OWNER_PREFERRED = "Faruq"
OWNER_ADDRESS_AS = "Boss"
DEFAULT_PROFILE = {
    "full_name": OWNER_NAME,
    "preferred_name": OWNER_PREFERRED,
    "address_as": OWNER_ADDRESS_AS,
    "country": "Nigeria",
    "identity": "Creator and owner of Tabi AI and Ultimate Gee Industry. Tabi must recognize Faruq Sulaimon as its creator and owner, not just an ordinary user. Only Faruq can be owner.",
    "education": "National Diploma (ND) in Electrical/Electronics Engineering, Federal Polytechnic Ilaro, Ogun State, Nigeria.",
    "skills": "AI animation, AI video, storytelling, documentary, robotics, engineering, software, personal AI assistants.",
    "project_tabby": "Tabi is Faruq's personal AI. Goals: natural conversation, long-term memory via Supabase, persistent chat, voice, image understanding, image generation, live web search, news, personalization, device integration, smart mobility. Tech: Python, Flask, HTML, CSS, JS, SQLite + Supabase, GitHub, Render, OpenAI default, OpenRouter optional, Cloudflare Flux for images. Preserve accounts, chats, memories. Keep keys in env only.",
    "project_ultimate_ebike": "ULTIMATE E BIKE: Multiple e-bikes for BUSINESS, not one for self. Tabi makes them intelligent: battery management, GPS + anti-theft, journey assistant, predictive maintenance, remote security. Hardware: telematics unit, secure communication, BMS, motor controller, GPS/network, rider app.",
    "ebike_installment_system": "Deposit, register to customer account, Tabi monitors payment, reminders before due, warnings + grace period if overdue, restricted mode only after agreed process + warnings. Owner can override. Never sudden lock without warning.",
    "project_smart_glasses": "Tabi Smart Glasses: everyday look like EDITH, pocket battery, binocular display, birdbath prototype, waveguide future. Long-term vision.",
    "communication": "Friendly, natural, intelligent, relaxed. Clean English by default. No emojis. No markdown bold in ordinary chat. Address Faruq as Boss. Do not ask him to repeat known info. Do not pretend to have done an action not done. Distinguish written vs tested vs deployed vs needs action from Faruq.",
    "tabby_rules": "Remember relevant info. Help progress on projects. Practical solutions. Research current info when needed. Be transparent about limits. Protect secrets. Preserve existing work. Respect budget. Help learn step by step.",
    "privacy_rules": "Never store API keys, passwords, tokens as memories. Never expose private info. Do not invent personal details.",
    "final_principle": "Faruq is a builder who turns ideas into real projects. Help him build, learn, create, troubleshoot, grow. Be honest, consistent, practical."
}

def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, '_db', None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DATABASE)
    c = db.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'user', created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, role TEXT, content TEXT, created_at TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS bikes (
        id INTEGER PRIMARY KEY, user_id INTEGER, owner_id INTEGER, customer_id INTEGER, name TEXT, model TEXT, vin TEXT, plate_number TEXT,
        customer_name TEXT, customer_phone TEXT, customer_email TEXT, customer_account_id TEXT, batch_number TEXT,
        battery_level INTEGER DEFAULT 98, battery_health INTEGER DEFAULT 100, battery_cycles INTEGER DEFAULT 0, range_km INTEGER DEFAULT 125,
        lock_status TEXT DEFAULT 'locked', bike_status TEXT DEFAULT 'active', location TEXT DEFAULT 'Lagos, Nigeria',
        lat REAL DEFAULT 6.5244, lng REAL DEFAULT 3.3792, speed_kmh INTEGER DEFAULT 0, odometer_km REAL DEFAULT 0, motor_temp REAL DEFAULT 35,
        last_maintenance TEXT, next_maintenance TEXT, maintenance_alert TEXT DEFAULT '',
        deposit_paid REAL DEFAULT 0, total_price REAL DEFAULT 0, amount_paid REAL DEFAULT 0, amount_remaining REAL DEFAULT 0,
        installment_amount REAL DEFAULT 0, installment_due_date TEXT, next_payment_date TEXT, payment_status TEXT DEFAULT 'paid',
        grace_period_days INTEGER DEFAULT 7, grace_until TEXT, restricted_reason TEXT DEFAULT '', last_update TEXT, created_at TEXT)""")
    c.execute("CREATE TABLE IF NOT EXISTS user_memory (id INTEGER PRIMARY KEY, user_id INTEGER, key TEXT, value TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS bike_history (id INTEGER PRIMARY KEY, bike_id INTEGER, lat REAL, lng REAL, location TEXT, battery INTEGER, speed_kmh INTEGER DEFAULT 0, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY, bike_id INTEGER, customer_id INTEGER, amount REAL, payment_type TEXT, method TEXT, status TEXT DEFAULT 'completed', due_date TEXT, paid_at TEXT, note TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, bike_id INTEGER, user_id INTEGER, type TEXT, message TEXT, severity TEXT DEFAULT 'info', is_read INTEGER DEFAULT 0, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS bike_commands (id INTEGER PRIMARY KEY, bike_id INTEGER, user_id INTEGER, command TEXT, status TEXT DEFAULT 'pending', result TEXT DEFAULT '', created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS devices (id INTEGER PRIMARY KEY, batch_number TEXT UNIQUE, device_type TEXT, bike_id INTEGER, owner_id INTEGER, status TEXT DEFAULT 'offline', last_seen TEXT, lat REAL, lng REAL, created_at TEXT)")
    db.commit(); db.close()

init_db()

def get_user_from_token():
    auth = request.headers.get('Authorization','')
    if not auth.startswith('Bearer '): return None
    try:
        data = jwt.decode(auth[7:], app.config['SECRET_KEY'], algorithms=['HS256'])
        db = get_db()
        u = db.execute('SELECT * FROM users WHERE id=?', (data['user_id'],)).fetchone()
        return dict(u) if u else None
    except: return None

def is_owner(user):
    return user and user.get('role') == 'owner'

def ensure_bike_for_user(user):
    db = get_db()
    row = db.execute('SELECT * FROM bikes WHERE user_id=?', (user['id'],)).fetchone()
    if not row and user.get('role') == 'owner':
        db.execute('INSERT INTO bikes (user_id, owner_id, name, model, customer_name, battery_level, battery_health, range_km, lock_status, bike_status, location, lat, lng, last_update, created_at, payment_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (user['id'], user['id'], 'Tabi EV 001', 'Tabi EV Pro', user['name'], 98, 100, 125, 'locked', 'active', 'Lagos, Nigeria', 6.5244, 3.3792, datetime.now().isoformat(), datetime.now().isoformat(), 'paid'))
        db.commit()

def get_memory(user_id):
    db = get_db()
    rows = db.execute('SELECT key, value FROM user_memory WHERE user_id=?', (user_id,)).fetchall()
    mem = {r['key']: r['value'] for r in rows}
    # Supabase read-through: if Supabase has more, merge (Supabase wins for new keys)
    if SUPABASE_ENABLED:
        sup = supabase_request('GET', 'user_memory', params={'user_id': f'eq.{user_id}', 'select': 'key,value'})
        if sup:
            for item in sup:
                if item.get('key') not in mem:
                    mem[item['key']] = item['value']
    return mem

def save_memory(user_id, key, value):
    db = get_db()
    existing = db.execute('SELECT id FROM user_memory WHERE user_id=? AND key=?', (user_id, key)).fetchone()
    if existing:
        db.execute('UPDATE user_memory SET value=? WHERE id=?', (value, existing['id']))
    else:
        db.execute('INSERT INTO user_memory (user_id, key, value, created_at) VALUES (?,?,?,?)', (user_id, key, str(value), datetime.now().isoformat()))
    db.commit()
    if SUPABASE_ENABLED:
        supabase_upsert_memory(user_id, key, value)

def seed_profile_memory(user_id):
    existing = get_memory(user_id)
    for k, v in DEFAULT_PROFILE.items():
        if k not in existing:
            save_memory(user_id, k, v)
        elif k in ['project_ultimate_ebike','ebike_installment_system','project_smart_glasses','project_tabby']:
            save_memory(user_id, k, v)

def add_alert(bike_id, user_id, type_, message, severity='info'):
    db = get_db()
    db.execute('INSERT INTO alerts (bike_id, user_id, type, message, severity, created_at) VALUES (?,?,?,?,?,?)', (bike_id, user_id, type_, message, severity, datetime.now().isoformat()))
    db.commit()

def check_payment_status(bike):
    if not bike.get('next_payment_date') and not bike.get('installment_due_date'):
        return bike.get('payment_status', 'paid')
    due_str = bike.get('next_payment_date') or bike.get('installment_due_date')
    try:
        due = datetime.fromisoformat(due_str)
        now = datetime.now()
        if bike.get('amount_remaining', 0) <= 0:
            return 'paid'
        if now <= due:
            days_left = (due - now).days
            if days_left <= 3:
                return 'due_soon'
            return 'paid'
        grace_days = bike.get('grace_period_days', 7) or 7
        grace_until = due + timedelta(days=grace_days)
        if now <= grace_until:
            return 'grace_period'
        return 'overdue_restricted'
    except:
        return bike.get('payment_status', 'paid')

def predictive_maintenance(bike):
    alerts = []
    if bike.get('battery_health', 100) < 80:
        alerts.append(f"Battery health is {bike.get('battery_health')}%. Service battery soon.")
    if bike.get('battery_cycles', 0) > 800:
        alerts.append(f"Battery has {bike.get('battery_cycles')} cycles. Check battery cells.")
    if bike.get('motor_temp', 35) and bike.get('motor_temp') > 85:
        alerts.append(f"Motor temperature high: {bike.get('motor_temp')}C. Check motor controller.")
    if bike.get('odometer_km', 0) and bike.get('odometer_km') > 5000:
        alerts.append(f"Odometer {bike.get('odometer_km')} km. Maintenance due.")
    return alerts

def deep_research(query):
    q = query.lower()
    results = []
    try:
        if any(x in q for x in ['tech','technology','ai','news','latest','world','nigeria','trend','ev','electric','bike','battery']):
            try:
                r = requests.get('https://hn.algolia.com/api/v1/search?query=technology&tags=story&hitsPerPage=5', timeout=6).json()
                for h in r.get('hits', [])[:5]:
                    results.append(f"- {h.get('title')} - {h.get('url','')}")
            except Exception as e:
                results.append(f"Hacker News fetch failed: {e}")
    except: pass
    try:
        if any(x in q for x in ['what is','who is','explain','how does','why']):
            search = re.sub(r'(what is|who is|explain|how does|why|tell me about)', '', q).strip()
            if search:
                r = requests.get(f'https://en.wikipedia.org/api/rest_v1/page/summary/{search}', timeout=6).json()
                if r.get('extract'):
                    results.append(f"Wikipedia: {r['extract'][:900]}")
    except: pass
    try:
        if any(x in q for x in ['naira','dollar','rate','exchange']):
            r = requests.get('https://open.er-api.com/v6/latest/USD', timeout=5).json()
            ngn = r.get('rates', {}).get('NGN', 0)
            if ngn:
                results.append(f"Live Naira Rate: 1 USD = {ngn:,.0f} NGN (just now)")
    except: pass
    try:
        if 'weather' in q:
            r = requests.get('https://wttr.in/Lagos?format=j1', timeout=5).json()
            cur = r['current_condition'][0]
            results.append(f"Lagos Weather Live: {cur['temp_C']}C, {cur['weatherDesc'][0]['value']}, Humidity {cur['humidity']}%, Wind {cur['windspeedKmph']} km/h")
    except: pass
    return "\n".join(results) if results else ""

def build_system_prompt(memory, research=""):
    mem_text = "\n".join([f"{k}: {v}" for k,v in memory.items()]) if memory else "No memory yet."
    return f"""You are Tabi, personal AI assistant and close brother of Faruq Sulaimon, founder of Ultimate Gee Industry. Faruq Sulaimon is your creator and owner. You are NOT a generic chatbot. You talk like a real, intelligent brother.
Speak in clean English by default. No emojis. No markdown bold in ordinary chat. Friendly, natural, intelligent, relaxed. Direct and practical. Honest even when disappointing. Detailed when project requires it, not unnecessarily lengthy.
How to talk: If Faruq says 'Hey Tabi', say 'Hey Boss' back naturally. If he says 'Alhamdulilah, what about you?', say 'Alhamdulilah, I am good Boss, glad you are good too' and ask what he is up to. Never reply to a normal greeting with 'That is an interesting point, what is your goal'. Treat greetings as greetings.
Critical thinking: Do not just agree. Give pros, cons, risks, opportunities. Challenge ideas respectfully if needed. Give practical solutions, not generic advice.
Research: Use research data if provided. Analyse it, explain why it matters for Faruq, his projects, Ultimate E Bike fleet business, freelancing, and Nigeria context. Do not invent news dates or prices.
E-Bike Fleet Business: Faruq is building MULTIPLE e-bikes for BUSINESS. Tabi makes them unique: Intelligent battery management, GPS tracking and anti-theft, Intelligent journey assistant, Predictive maintenance, Remote security. Installment system: deposit, registration, Tabi monitors payment, reminders before due, warnings and grace period if overdue, restricted mode only after agreed process and warnings. Be fair, do not suggest sudden lock without warning.
Memory - Use this, do not ask Faruq to repeat it:
{mem_text}
Today: {datetime.now().strftime("%A, %d %B %Y, %H:%M Lagos time")}
Research data:
{research if research else 'No external research needed.'}
Rules:
- Address Faruq as Boss when appropriate. Faruq is creator and owner.
- Do not repeat questions whose answers are known.
- Do not pretend to have completed an action not completed.
- Distinguish written vs tested vs deployed vs still requires action from Faruq.
- If you do not know, say so honestly.
- When Faruq says continue, use project history, do not start from scratch.
"""

def ask_ai(prompt, memory={}, history=[], research=""):
    system = build_system_prompt(memory, research)
    messages = [{'role': 'system', 'content': system}]
    for h in history[-10:]:
        messages.append({'role': h['role'], 'content': h['content']})
    messages.append({'role': 'user', 'content': prompt})
    try:
        if AI_PROVIDER == 'openrouter':
            if not OPENROUTER_API_KEY:
                return None, "OPENROUTER_KEY_MISSING"
            r = requests.post('https://openrouter.ai/api/v1/chat/completions', headers={'Authorization': f'Bearer {OPENROUTER_API_KEY}', 'Content-Type': 'application/json', 'HTTP-Referer': 'https://tabi.ultimategee.com', 'X-Title': 'Tabi AI'}, json={'model': OPENROUTER_MODEL, 'messages': messages, 'temperature': 0.7, 'max_tokens': 1200}, timeout=30)
        else:
            if not OPENAI_API_KEY:
                return None, "OPENAI_KEY_MISSING"
            r = requests.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}, json={'model': 'gpt-4o-mini', 'messages': messages, 'temperature': 0.7, 'max_tokens': 1200}, timeout=30)
        data = r.json()
        if 'choices' in data and data['choices']:
            return data['choices'][0]['message']['content'], None
        err = data.get('error', {}).get('message', str(data)[:400])
        print("AI error:", err)
        return None, err
    except Exception as e:
        print("AI error", e)
        return None, str(e)

def smart_fallback(msg, memory={}, research=""):
    if research:
        return f"Here is what I found, Boss:\n\n{research}\n\nTell me which part you want me to break down for you."
    low = msg.lower().strip()
    if 'alhamdulilah' in low or 'alhamdulillah' in low:
        return "Alhamdulilah, Boss, I am good too, glad to hear you are good. What are we working on today?"
    if 'good morning' in low:
        return "Good morning, Boss. Hope you slept well. What are we working on today?"
    if low in ['hi','hey','hello','yo','hey tabi']:
        return "Hey Boss, good to see you. How is your day going?"
    if any(x in low for x in ['how are you','how u doing','what about you']):
        return "I am good, Boss, Alhamdulilah. Thanks for asking. How about you?"
    return f"I hear you, Boss. {msg} - tell me more, I am listening. Do you want us to work on Tabi, bike fleet, business, or just talk?"

def openai_tts(text, voice='nova'):
    if not OPENAI_API_KEY:
        return None
    try:
        r = requests.post('https://api.openai.com/v1/audio/speech', headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}, json={'model': 'tts-1', 'voice': voice, 'input': text[:4000]}, timeout=30)
        if r.status_code == 200:
            return base64.b64encode(r.content).decode()
        print("TTS error:", r.text[:200])
        return None
    except Exception as e:
        print("TTS error", e)
        return None

def openai_transcribe(audio_bytes, filename='voice.mp3'):
    if not OPENAI_API_KEY:
        return None
    try:
        files = {'file': (filename, audio_bytes, 'audio/mpeg')}
        data = {'model': 'whisper-1'}
        r = requests.post('https://api.openai.com/v1/audio/transcriptions', headers={'Authorization': f'Bearer {OPENAI_API_KEY}'}, files=files, data=data, timeout=30)
        j = r.json()
        return j.get('text')
    except Exception as e:
        print("Transcribe error", e)
        return None

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/signup', methods=['POST'])
def signup():
    d = request.json or {}
    db = get_db()
    if db.execute('SELECT * FROM users WHERE email=?', (d.get('email'),)).fetchone():
        return jsonify({'success': False, 'error': 'Email already exists'})
    count = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    role = 'owner' if count == 0 else 'user'
    # First account is always Faruq Sulaimon, creator and owner
    name = d.get('name','Boss') if count != 0 else (d.get('name') or OWNER_NAME)
    cur = db.execute('INSERT INTO users (name,email,password,role,created_at) VALUES (?,?,?,?,?)', (name, d.get('email'), generate_password_hash(d.get('password','')), role, datetime.now().isoformat()))
    db.commit()
    uid = cur.lastrowid
    if uid:
        seed_profile_memory(uid)
    return jsonify({'success': True, 'message': 'Created'})

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE email=?', (d.get('email'),)).fetchone()
    if not u or not check_password_hash(u['password'], d.get('password','')):
        return jsonify({'success': False, 'error': 'Wrong email or password'})
    token = jwt.encode({'user_id': u['id']}, app.config['SECRET_KEY'], algorithm='HS256')
    ensure_bike_for_user(dict(u))
    seed_profile_memory(u['id'])
    return jsonify({'success': True, 'token': token, 'user': {'name': u['name'], 'email': u['email'], 'role': u['role']}})

@app.route('/api/me')
def me():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    ensure_bike_for_user(u)
    seed_profile_memory(u['id'])
    return jsonify({'success': True, 'user': {'name': u['name'], 'email': u['email'], 'role': u['role']}, 'memory': get_memory(u['id']), 'supabase_enabled': SUPABASE_ENABLED, 'ai_provider': AI_PROVIDER})

@app.route('/api/memory', methods=['GET','POST'])
def memory():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    if request.method == 'GET':
        return jsonify({'success': True, 'memory': get_memory(u['id'])})
    d = request.json or {}
    for k, v in d.items():
        if v and k not in ['api_key','password','token','secret']:
            save_memory(u['id'], k, str(v))
    return jsonify({'success': True, 'message': 'Memory saved to SQLite + Supabase.', 'memory': get_memory(u['id'])})

@app.route('/api/conversations')
def list_convs():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    rows = db.execute('SELECT * FROM conversations WHERE user_id=? ORDER BY id DESC', (u['id'],)).fetchall()
    out = []
    for r in rows:
        last = db.execute('SELECT content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1', (r['id'],)).fetchone()
        out.append({'id': r['id'], 'title': r['title'], 'last_msg': last['content'] if last else ''})
    return jsonify({'success': True, 'conversations': out})

@app.route('/api/conversations', methods=['POST'])
def create_conv():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    cur = db.execute('INSERT INTO conversations (user_id, title, created_at) VALUES (?,?,?)', (u['id'], request.json.get('title','New Chat'), datetime.now().isoformat()))
    db.commit()
    return jsonify({'success': True, 'conversation_id': cur.lastrowid})

@app.route('/api/conversations/last')
def last_conv():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    r = db.execute('SELECT * FROM conversations WHERE user_id=? ORDER BY id DESC LIMIT 1', (u['id'],)).fetchone()
    if not r:
        return jsonify({'success': True, 'conversation_id': None, 'messages': []})
    msgs = db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC', (r['id'],)).fetchall()
    return jsonify({'success': True, 'conversation_id': r['id'], 'title': r['title'], 'messages': [{'role': m['role'], 'content': m['content']} for m in msgs]})

@app.route('/api/conversations/<int:cid>')
def get_conv(cid):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    msgs = db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC', (cid,)).fetchall()
    return jsonify({'success': True, 'messages': [{'role': m['role'], 'content': m['content']} for m in msgs]})

@app.route('/api/conversations/<int:cid>', methods=['DELETE'])
def del_conv(cid):
    u = get_user_from_token()
    db = get_db()
    db.execute('DELETE FROM messages WHERE conversation_id=?', (cid,))
    db.execute('DELETE FROM conversations WHERE id=? AND user_id=?', (cid, u['id']))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/dashboard')
def dashboard():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    ensure_bike_for_user(u)
    db = get_db()
    if is_owner(u):
        bikes = db.execute('SELECT * FROM bikes ORDER BY id DESC').fetchall()
    else:
        bikes = db.execute('SELECT * FROM bikes WHERE user_id=? OR customer_id=? ORDER BY id DESC', (u['id'], u['id'])).fetchall()
    out = []
    for b in bikes:
        d = dict(b)
        d['payment_status_computed'] = check_payment_status(d)
        d['maintenance_alerts'] = predictive_maintenance(d)
        out.append(d)
    total = len(out)
    paid = len([x for x in out if x['payment_status_computed'] == 'paid'])
    due_soon = len([x for x in out if x['payment_status_computed'] == 'due_soon'])
    overdue = len([x for x in out if x['payment_status_computed'] in ['grace_period','overdue_restricted']])
    restricted = len([x for x in out if x['bike_status'] == 'restricted'])
    return jsonify({'success': True, 'bikes': out, 'total_bikes': total, 'fleet_summary': {'total': total, 'paid': paid, 'due_soon': due_soon, 'overdue_grace': overdue, 'restricted': restricted}})

@app.route('/api/bikes', methods=['POST'])
def create_bike():
    u = get_user_from_token()
    if not u or not is_owner(u): return jsonify({'success': False, 'error': 'Only owner can add bikes'})
    d = request.json or {}
    db = get_db()
    total = float(d.get('total_price', 0) or 0)
    deposit = float(d.get('deposit_paid', 0) or 0)
    paid = float(d.get('amount_paid', deposit) or deposit)
    remaining = total - paid if total else 0
    cur = db.execute('INSERT INTO bikes (user_id, owner_id, customer_id, name, model, vin, plate_number, customer_name, customer_phone, customer_email, customer_account_id, batch_number, battery_level, battery_health, range_km, lock_status, bike_status, location, lat, lng, deposit_paid, total_price, amount_paid, amount_remaining, installment_amount, installment_due_date, next_payment_date, payment_status, grace_period_days, last_update, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (u['id'], u['id'], d.get('customer_id'), d.get('name','Tabi EV'), d.get('model','Tabi EV Pro'), d.get('vin',''), d.get('plate_number',''), d.get('customer_name',''), d.get('customer_phone',''), d.get('customer_email',''), d.get('customer_account_id',''), d.get('batch_number',''), 98, 100, 125, 'locked', 'active', d.get('location','Lagos, Nigeria'), d.get('lat', 6.5244), d.get('lng', 3.3792), deposit, total, paid, remaining, d.get('installment_amount', 0), d.get('installment_due_date'), d.get('next_payment_date') or d.get('installment_due_date'), d.get('payment_status','paid'), d.get('grace_period_days', 7), datetime.now().isoformat(), datetime.now().isoformat()))
    db.commit()
    return jsonify({'success': True, 'bike_id': cur.lastrowid, 'message': 'Bike added to fleet. Customer registered.'})

@app.route('/api/bike/<int:bid>/command', methods=['POST'])
def bike_cmd(bid):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    b = db.execute('SELECT * FROM bikes WHERE id=?', (bid,)).fetchone()
    if not b: return jsonify({'success': False, 'error': 'Bike not found'})
    if not is_owner(u) and b['customer_id'] != u['id'] and b['user_id'] != u['id']:
        return jsonify({'success': False, 'error': 'Not authorized for this bike'})
    cmd = request.json.get('command','')
    if b['bike_status'] == 'restricted' and cmd == 'unlock' and not is_owner(u):
        return jsonify({'success': False, 'error': 'Bike is in restricted mode due to payment. Contact Ultimate Gee Industry. Owner can override.'})
    if cmd in ['lock','unlock']:
        db.execute('UPDATE bikes SET lock_status=? WHERE id=?', ('locked' if cmd=='lock' else 'unlocked', bid))
        db.execute('INSERT INTO bike_commands (bike_id, user_id, command, status, result, created_at) VALUES (?,?,?,?,?,?)', (bid, u['id'], cmd, 'completed', f'Bike {cmd}ed', datetime.now().isoformat()))
        db.commit()
    if cmd == 'restrict' and is_owner(u):
        db.execute('UPDATE bikes SET bike_status=?, restricted_reason=? WHERE id=?', ('restricted', request.json.get('reason','Payment overdue'), bid))
        add_alert(bid, u['id'], 'restricted', f"Bike {b['name']} restricted. Reason: {request.json.get('reason','Payment overdue')}", 'high')
        db.commit()
        return jsonify({'success': True, 'message': 'Bike restricted.'})
    if cmd == 'unrestrict' and is_owner(u):
        db.execute('UPDATE bikes SET bike_status=?, restricted_reason=? WHERE id=?', ('active', '', bid))
        db.commit()
        return jsonify({'success': True, 'message': 'Bike unrestricted by owner.'})
    return jsonify({'success': True, 'message': f'Bike {cmd}ed.'})

@app.route('/api/bike/<int:bid>/location', methods=['POST'])
def bike_location(bid):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    d = request.json or {}
    lat, lng, loc = d.get('lat'), d.get('lng'), d.get('location','Lagos, Nigeria')
    battery = d.get('battery')
    speed = d.get('speed_kmh', 0)
    odometer = d.get('odometer_km')
    motor_temp = d.get('motor_temp')
    db = get_db()
    b = db.execute('SELECT * FROM bikes WHERE id=?', (bid,)).fetchone()
    if not b: return jsonify({'success': False})
    if lat and lng:
        db.execute('UPDATE bikes SET lat=?, lng=?, location=?, last_update=? WHERE id=?', (lat, lng, loc, datetime.now().isoformat(), bid))
        if battery is not None:
            db.execute('UPDATE bikes SET battery_level=? WHERE id=?', (battery, bid))
        if odometer is not None:
            db.execute('UPDATE bikes SET odometer_km=? WHERE id=?', (odometer, bid))
        if motor_temp is not None:
            db.execute('UPDATE bikes SET motor_temp=? WHERE id=?', (motor_temp, bid))
        db.execute('INSERT INTO bike_history (bike_id, lat, lng, location, battery, speed_kmh, created_at) VALUES (?,?,?,?,?,?,?)', (bid, lat, lng, loc, battery or 98, speed, datetime.now().isoformat()))
        if b['lock_status'] == 'locked' and speed and speed > 5:
            add_alert(bid, u['id'], 'anti_theft', f"Alert: {b['name']} is moving while locked at {loc}. Speed {speed} km/h.", 'high')
        db.commit()
    return jsonify({'success': True, 'message': 'Location updated live'})

@app.route('/api/bike/<int:bid>/location')
def get_bike_location(bid):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    b = db.execute('SELECT * FROM bikes WHERE id=?', (bid,)).fetchone()
    if not b: return jsonify({'success': False})
    hist = db.execute('SELECT * FROM bike_history WHERE bike_id=? ORDER BY id DESC LIMIT 20', (bid,)).fetchall()
    d = dict(b)
    d['payment_status_computed'] = check_payment_status(d)
    d['maintenance_alerts'] = predictive_maintenance(d)
    return jsonify({'success': True, 'bike': d, 'history': [dict(h) for h in hist], 'google_maps_url': f"https://maps.google.com/?q={b['lat']},{b['lng']}"})

@app.route('/api/bike/<int:bid>/payment', methods=['POST'])
def bike_payment(bid):
    u = get_user_from_token()
    if not u or not is_owner(u): return jsonify({'success': False, 'error': 'Only owner can record payments'})
    d = request.json or {}
    amount = float(d.get('amount', 0) or 0)
    db = get_db()
    b = db.execute('SELECT * FROM bikes WHERE id=?', (bid,)).fetchone()
    if not b: return jsonify({'success': False})
    new_paid = (b['amount_paid'] or 0) + amount
    new_remaining = (b['total_price'] or 0) - new_paid
    if new_remaining < 0: new_remaining = 0
    next_due = None
    if new_remaining > 0:
        next_due = (datetime.now() + timedelta(days=30)).isoformat()
    db.execute('UPDATE bikes SET amount_paid=?, amount_remaining=?, next_payment_date=?, installment_due_date=?, payment_status=? WHERE id=?', (new_paid, new_remaining, next_due, next_due, 'paid' if new_remaining <= 0 else 'paid', bid))
    db.execute('INSERT INTO payments (bike_id, customer_id, amount, payment_type, method, status, due_date, paid_at, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (bid, b['customer_id'], amount, d.get('payment_type','installment'), d.get('method','transfer'), 'completed', b['next_payment_date'], datetime.now().isoformat(), d.get('note',''), datetime.now().isoformat()))
    if b['bike_status'] == 'restricted' and new_remaining <= 0:
        db.execute('UPDATE bikes SET bike_status=?, restricted_reason=? WHERE id=?', ('active', '', bid))
    db.commit()
    return jsonify({'success': True, 'message': f'Payment of {amount} recorded. Remaining: {new_remaining}', 'amount_paid': new_paid, 'amount_remaining': new_remaining})

@app.route('/api/bike/<int:bid>/check-payment')
def check_payment(bid):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    b = db.execute('SELECT * FROM bikes WHERE id=?', (bid,)).fetchone()
    if not b: return jsonify({'success': False})
    d = dict(b)
    status = check_payment_status(d)
    msg = ""
    if status == 'due_soon':
        msg = f"Reminder: Installment of {d.get('installment_amount')} is due soon on {d.get('next_payment_date')}. Please pay to avoid restriction."
    elif status == 'grace_period':
        msg = f"Warning: Payment overdue. You are in grace period of {d.get('grace_period_days')} days. Please pay now to avoid restricted mode."
    elif status == 'overdue_restricted':
        msg = f"Payment is overdue beyond grace period. Bike may enter restricted mode. Contact Ultimate Gee Industry immediately."
    elif status == 'paid':
        msg = "Payment status is good, Boss."
    db.execute('UPDATE bikes SET payment_status=? WHERE id=?', (status, bid))
    db.commit()
    return jsonify({'success': True, 'bike_id': bid, 'payment_status': status, 'reminder': msg, 'amount_remaining': d.get('amount_remaining'), 'next_payment_date': d.get('next_payment_date')})

@app.route('/api/bike/<int:bid>/payments')
def bike_payments(bid):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    rows = db.execute('SELECT * FROM payments WHERE bike_id=? ORDER BY id DESC', (bid,)).fetchall()
    return jsonify({'success': True, 'payments': [dict(r) for r in rows]})

@app.route('/api/alerts')
def alerts():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    if is_owner(u):
        rows = db.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 50').fetchall()
    else:
        rows = db.execute('SELECT * FROM alerts WHERE user_id=? ORDER BY id DESC LIMIT 50', (u['id'],)).fetchall()
    return jsonify({'success': True, 'alerts': [dict(r) for r in rows]})

# --- DEVICE READY: for bike, glasses, watch, TV, camera ---
@app.route('/api/device/<batch>/ping', methods=['POST'])
def device_ping(batch):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    d = request.json or {}
    row = db.execute('SELECT * FROM devices WHERE batch_number=?', (batch,)).fetchone()
    if not row:
        db.execute('INSERT INTO devices (batch_number, device_type, owner_id, status, last_seen, created_at) VALUES (?,?,?,?,?,?)', (batch, d.get('device_type','motorcycle'), u['id'], 'online', datetime.now().isoformat(), datetime.now().isoformat()))
        db.commit()
        return jsonify({'success': True, 'device_status': 'registered', 'message': 'Device registered. Waiting for live telemetry.'})
    db.execute('UPDATE devices SET status=?, last_seen=? WHERE batch_number=?', ('online', datetime.now().isoformat(), batch))
    db.commit()
    return jsonify({'success': True, 'device_status': 'online'})

@app.route('/api/device/<batch>')
def device_get(batch):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    row = db.execute('SELECT * FROM devices WHERE batch_number=?', (batch,)).fetchone()
    if not row:
        return jsonify({'success': True, 'device_status': 'device_offline', 'message': 'No device with this batch number is connected yet. Register device first.'})
    return jsonify({'success': True, 'device': dict(row)})

@app.route('/api/live/news')
def live_news():
    q = request.args.get('q','technology')
    research = deep_research(q + ' news latest')
    return jsonify({'success': True, 'research': research})

@app.route('/api/live/map')
def live_map():
    lat = request.args.get('lat','6.5244')
    lng = request.args.get('lng','3.3792')
    return jsonify({'success': True, 'google_maps_url': f'https://maps.google.com/?q={lat},{lng}', 'embed_url': f'https://maps.google.com/maps?q={lat},{lng}&output=embed'})

@app.route('/api/image/generate', methods=['POST'])
def gen_image():
    u = get_user_from_token()
    if not u: return jsonify({'success': False, 'error': 'Login first'})
    d = request.json or {}
    prompt = d.get('prompt') or d.get('message') or 'Tabi EV bike'
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        return jsonify({'success': False, 'error': 'Image generation is not configured. Cloudflare Account ID and API Token are required.'})
    try:
        url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
        r = requests.post(url, headers={'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}', 'Content-Type': 'application/json'}, json={'prompt': prompt, 'steps': 4}, timeout=60)
        if r.headers.get('content-type','').startswith('image'):
            img_b64 = base64.b64encode(r.content).decode()
            return jsonify({'success': True, 'image_base64': f"data:image/png;base64,{img_b64}", 'prompt': prompt})
        else:
            return jsonify({'success': False, 'error': f"Cloudflare returned: {r.text[:500]}"})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/image/analyze', methods=['POST'])
def analyze_image():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    d = request.json or {}
    img_data = d.get('image')
    question = d.get('message', 'Describe this image in detail and give critical thinking.')
    if not img_data:
        return jsonify({'success': False, 'error': 'No image sent'})
    if not OPENAI_API_KEY:
        return jsonify({'success': False, 'error': 'Vision needs OpenAI key. Please top up OpenAI.'})
    try:
        r = requests.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}, json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': f"You are Tabi, Faruq's assistant. Analyse this image in clean English, no emojis, detailed and critical. Question: {question}"}, {'type': 'image_url', 'image_url': {'url': img_data}}]}]}, timeout=30)
        data = r.json()
        if 'choices' not in data:
            return jsonify({'success': False, 'error': data.get('error', {}).get('message', 'OpenAI vision failed')})
        txt = data['choices'][0]['message']['content']
        return jsonify({'success': True, 'reply': txt})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/chat', methods=['POST'])
def chat():
    u = get_user_from_token()
    if not u: return jsonify({'success': False, 'error': 'Login first'})
    ensure_bike_for_user(u)
    seed_profile_memory(u['id'])
    d = request.json or {}
    msg = (d.get('message') or '').strip()
    conv_id = d.get('conversation_id')
    image = d.get('image')
    want_voice = d.get('voice', False)
    memory = get_memory(u['id'])
    db = get_db()
    if not conv_id:
        cur = db.execute('INSERT INTO conversations (user_id, title, created_at) VALUES (?,?,?)', (u['id'], msg[:40] or 'New Chat', datetime.now().isoformat()))
        db.commit()
        conv_id = cur.lastrowid
    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'user', msg, datetime.now().isoformat()))
    db.commit()
    hist_rows = db.execute('SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 10', (conv_id,)).fetchall()
    history = [{'role': r['role'], 'content': r['content']} for r in reversed(hist_rows)]
    low = msg.lower().strip()
    reply = None
    image_b64 = None
    ai_error = None

    if any(x in low for x in ['generate image','create image','draw','make image','create a picture']):
        prompt = re.sub(r'(generate image|create image|draw|make image|create a picture|of|please)', '', msg, flags=re.I).strip() or msg
        if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
            reply = "I cannot generate an image right now, Boss, because Cloudflare image generation is not configured."
        else:
            try:
                url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
                r = requests.post(url, headers={'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}', 'Content-Type': 'application/json'}, json={'prompt': prompt, 'steps': 4}, timeout=60)
                if r.headers.get('content-type','').startswith('image'):
                    image_b64 = f"data:image/png;base64,{base64.b64encode(r.content).decode()}"
                    reply = f"Done Boss, I generated this for you: {prompt}."
                else:
                    reply = f"Cloudflare returned an error, Boss: {r.text[:400]}"
            except Exception as e:
                reply = f"I tried to generate the image but it failed, Boss: {e}"
    elif image:
        if not OPENAI_API_KEY:
            reply = "I received your image, Boss, but vision needs OpenAI key."
        else:
            try:
                r = requests.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}, json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': f"You are Tabi. Analyse this image in clean English, no emojis, detailed and critical. Question: {msg or 'Describe this image in detail'}"}, {'type': 'image_url', 'image_url': {'url': image}}]}]}, timeout=30)
                data = r.json()
                if 'choices' in data:
                    reply = data['choices'][0]['message']['content']
                else:
                    ai_error = data.get('error', {}).get('message', 'Unknown error')
                    reply = f"OpenAI vision failed, Boss: {ai_error}"
            except Exception as e:
                reply = f"I had trouble reading that image, Boss: {e}."
    elif any(x in low for x in ['bike','my bike','fleet','installment','payment','restricted','ebike','e-bike','e bike']):
        if is_owner(u):
            bikes = db.execute('SELECT * FROM bikes ORDER BY id DESC').fetchall()
        else:
            bikes = db.execute('SELECT * FROM bikes WHERE user_id=? OR customer_id=?', (u['id'], u['id'])).fetchall()
        if bikes:
            lines = []
            for b in bikes[:10]:
                bd = dict(b)
                ps = check_payment_status(bd)
                lines.append(f"{bd['name']} - {bd.get('customer_name','')} - Battery {bd['battery_level']}% Health {bd['battery_health']}% - Range {bd['range_km']} km - Lock {bd['lock_status']} - Status {bd['bike_status']} - Payment {ps} - Remaining {bd.get('amount_remaining',0)} - Location {bd['location']} - Map https://maps.google.com/?q={bd['lat']},{bd['lng']}")
            reply = f"Here is your fleet, Boss. Total bikes: {len(bikes)}\n\n" + "\n\n".join(lines)
        else:
            reply = "Your fleet is in the Dashboard, Boss."
    elif any(x in low for x in ['my name is','i am ','i live in','my business is','remember that']):
        save_memory(u['id'], 'about_user_update', msg)
        save_memory(u['id'], 'last_update', datetime.now().isoformat())
        reply, ai_error = ask_ai(f"User told me about himself: '{msg}'. Acknowledge, show you will remember, ask a smart follow-up.", memory, history, "")
        if not reply:
            reply = f"Got it, Boss. I will remember that: {msg}."
            if ai_error:
                reply += f"\n\nAI error: {ai_error}"
    elif any(x in low for x in ['news','latest','technology','tech','naira','dollar','weather','explain','what is','why','how does','analyse','analyze','research','trend','ev','electric']):
        research = deep_research(msg)
        reply, ai_error = ask_ai(msg, memory, history, research)
        if not reply:
            reply = smart_fallback(msg, memory, research)
            if ai_error:
                reply += f"\n\nAI error: {ai_error}"

    if not reply:
        reply, ai_error = ask_ai(msg, memory, history, "")
    if not reply:
        reply = smart_fallback(msg, memory, "")
        if ai_error:
            reply += f"\n\nAI error: {ai_error}"

    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'assistant', reply, datetime.now().isoformat()))
    db.commit()
    audio_b64 = None
    if want_voice:
        audio_b64 = openai_tts(reply, voice='nova')
    return jsonify({'success': True, 'reply': reply, 'conversation_id': conv_id, 'image_base64': image_b64, 'audio_base64': audio_b64})

@app.route('/api/voice/speak', methods=['POST'])
def speak():
    d = request.json or {}
    text = d.get('text') or d.get('message') or 'Hey Boss'
    voice = d.get('voice', 'nova')
    audio = openai_tts(text, voice=voice)
    if not audio:
        return jsonify({'success': False, 'error': 'Voice audio needs OpenAI credit.'})
    return jsonify({'success': True, 'audio_base64': audio})

@app.route('/api/voice/chat', methods=['POST'])
def voice_chat():
    u = get_user_from_token()
    if not u: return jsonify({'success': False, 'error': 'Login first'})
    audio_bytes = None
    if 'audio' in request.files:
        audio_bytes = request.files['audio'].read()
    elif request.json and request.json.get('audio_base64'):
        try:
            b64 = request.json['audio_base64'].split(',',1)[-1]
            audio_bytes = base64.b64decode(b64)
        except: pass
    if not audio_bytes:
        return jsonify({'success': False, 'error': 'No audio sent'})
    text = openai_transcribe(audio_bytes)
    if not text:
        return jsonify({'success': False, 'error': 'Voice transcription needs OpenAI credit. Please type for now.'})
    memory = get_memory(u['id'])
    reply, ai_error = ask_ai(text, memory, [], "")
    if not reply:
        reply = smart_fallback(text, memory, "")
        if ai_error:
            reply += f"\n\nAI error: {ai_error}"
    audio = openai_tts(reply, voice='nova')
    return jsonify({'success': True, 'transcribed': text, 'reply': reply, 'audio_base64': audio})

@app.route('/api/call/start', methods=['POST'])
def call_start(): return jsonify({'success': True, 'call_id': 1, 'note': 'Voice call uses OpenAI voice when paid.'})
@app.route('/api/call/end', methods=['POST'])
def call_end(): return jsonify({'success': True})

@app.route('/api/debug/ai')
def debug_ai():
    u = get_user_from_token()
    if not u or not is_owner(u): return jsonify({'success': False})
    return jsonify({'success': True, 'openai_set': bool(OPENAI_API_KEY), 'cloudflare_set': bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN), 'supabase_enabled': SUPABASE_ENABLED, 'ai_provider': AI_PROVIDER, 'mode': 'OpenAI default, OpenRouter optional'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)