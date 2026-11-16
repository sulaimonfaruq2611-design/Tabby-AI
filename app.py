import os, json, sqlite3, requests, re
from datetime import datetime
from flask import Flask, request, jsonify, render_template, g
from flask_cors import CORS
import jwt
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tabby-secret-ultimate-gee-2026')
DATABASE = 'tabby.db'
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')

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
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'user', created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, role TEXT, content TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bikes (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, model TEXT, customer_name TEXT, battery_level INTEGER DEFAULT 98, battery_health INTEGER DEFAULT 100, range_km INTEGER DEFAULT 125, lock_status TEXT DEFAULT 'locked', location TEXT DEFAULT 'Lagos, Nigeria', created_at TEXT)''')
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

def ensure_bike_for_user(user):
    # Auto-create bike project if none exists - THIS FIXES MISSING BIKE
    db = get_db()
    row = db.execute('SELECT * FROM bikes WHERE user_id=?', (user['id'],)).fetchone()
    if not row:
        db.execute('INSERT INTO bikes (user_id, name, model, customer_name, battery_level, battery_health, range_km, lock_status, location, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (user['id'], 'Tabby EV 001', 'Tabby EV Pro', user['name'], 98, 100, 125, 'locked', 'Lagos, Nigeria', datetime.now().isoformat()))
        db.commit()

# ---------- LIVE INFO ----------
def get_live_info(query):
    q = query.lower()
    try:
        if 'naira' in q or 'dollar' in q or 'rate' in q or 'exchange' in q:
            r = requests.get('https://open.er-api.com/v6/latest/USD', timeout=5).json()
            ngn = r.get('rates', {}).get('NGN', 0)
            return f"💱 LIVE Naira Rate (just now):\n1 USD = ₦{ngn:,.0f}\nSource: Open Exchange Rates. Boss, rate fit change small for black market."
        if 'weather' in q or 'lagos' in q and 'weather' in q:
            r = requests.get('https://wttr.in/Lagos?format=j1', timeout=5).json()
            cur = r['current_condition'][0]
            return f"🌦 Lagos Weather LIVE:\nTemp: {cur['temp_C']}°C, Feels like {cur['FeelsLikeC']}°C\nCondition: {cur['weatherDesc'][0]['value']}\nHumidity: {cur['humidity']}% • Wind: {cur['windspeedKmph']} km/h"
        if 'news' in q or 'world' in q or 'nigeria' in q:
            # Use Google News RSS via free endpoint
            return None  # Let Gemini handle with date context, but we add date below
    except Exception as e:
        return None
    return None

def ask_gemini(prompt, user_name="Boss"):
    if not GEMINI_KEY:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
        today = datetime.now().strftime("%A, %d %B %Y, %H:%M Lagos time")
        full = f"You are Tabby, friendly AI for {user_name}, Ultimate Gee Industry. Use UK English. Today is {today}. Be warm, short, helpful, use Nigerian friendly tone but professional. Always give live info when asked about news/rates/weather with date. User says: {prompt}"
        r = requests.post(url, json={"contents":[{"parts":[{"text": full}]}]}, timeout=20)
        data = r.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print("Gemini error", e)
        return None

# ---------- AUTH ----------
@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/signup', methods=['POST'])
def signup():
    d = request.json; db = get_db()
    if db.execute('SELECT * FROM users WHERE email=?', (d.get('email'),)).fetchone():
        return jsonify({'success': False, 'error': 'Email already exists'})
    role = 'owner' if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0 else 'user'
    db.execute('INSERT INTO users (name,email,password,role,created_at) VALUES (?,?,?,?,?)', (d.get('name','Boss'), d.get('email'), generate_password_hash(d.get('password','')), role, datetime.now().isoformat()))
    db.commit()
    return jsonify({'success': True, 'message': 'Created'})

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json; db = get_db()
    u = db.execute('SELECT * FROM users WHERE email=?', (d.get('email'),)).fetchone()
    if not u or not check_password_hash(u['password'], d.get('password','')):
        return jsonify({'success': False, 'error': 'Wrong email or password'})
    token = jwt.encode({'user_id': u['id']}, app.config['SECRET_KEY'], algorithm='HS256')
    ensure_bike_for_user(dict(u))
    return jsonify({'success': True, 'token': token, 'user': {'name': u['name'], 'email': u['email'], 'role': u['role']}})

@app.route('/api/me')
def me():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    ensure_bike_for_user(u)
    return jsonify({'success': True, 'user': {'name': u['name'], 'email': u['email'], 'role': u['role']}})

# ---------- CONVERSATIONS ----------
@app.route('/api/conversations')
def list_convs():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    db = get_db()
    rows = db.execute('SELECT * FROM conversations WHERE user_id=? ORDER BY id DESC', (u['id'],)).fetchall()
    out=[]
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

# ---------- DASHBOARD / BIKE ----------
@app.route('/api/dashboard')
def dashboard():
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    ensure_bike_for_user(u)
    db = get_db()
    bikes = [dict(r) for r in db.execute('SELECT * FROM bikes WHERE user_id=?', (u['id'],)).fetchall()]
    return jsonify({'success': True, 'bikes': bikes, 'total_bikes': len(bikes)})

@app.route('/api/bike/<int:bid>/command', methods=['POST'])
def bike_cmd(bid):
    u = get_user_from_token()
    cmd = request.json.get('command','')
    db = get_db()
    if cmd=='lock': db.execute('UPDATE bikes SET lock_status=? WHERE id=?', ('locked', bid))
    if cmd=='unlock': db.execute('UPDATE bikes SET lock_status=? WHERE id=?', ('unlocked', bid))
    db.commit()
    return jsonify({'success': True, 'message': f'Bike {cmd}ed successfully, Boss!'})

# ---------- CHAT - FIXED GREETING BUG ----------
@app.route('/api/chat', methods=['POST'])
def chat():
    u = get_user_from_token()
    if not u: return jsonify({'success': False, 'error': 'Login first'})
    ensure_bike_for_user(u)
    d = request.json or {}
    msg = (d.get('message') or '').strip()
    conv_id = d.get('conversation_id')
    db = get_db()

    if not conv_id:
        cur = db.execute('INSERT INTO conversations (user_id, title, created_at) VALUES (?,?,?)', (u['id'], msg[:40] or 'New Chat', datetime.now().isoformat()))
        db.commit(); conv_id = cur.lastrowid
    else:
        # Update title from first message
        db.execute('UPDATE conversations SET title=? WHERE id=? AND title=?', (msg[:40] or 'New Chat', conv_id, 'New Chat'))
        db.commit()

    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'user', msg, datetime.now().isoformat()))
    db.commit()

    low = msg.lower().strip()
    reply = None

    # 1. GREETINGS FIRST - THIS WAS MISSING BEFORE, NOW IT REPLIES INSTANTLY
    if low in ['hi','hey','hello','yo','sup','good morning','good afternoon','good evening','good day','how are you','how far','how are you doing','hey tabby','hello tabby','hi tabby']:
        hour = datetime.now().hour
        greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        reply = f"{greet}, Boss! 👋 Tabby dey here, fresh and live.\n\nHow body? Wetin you wan do today?\n\nI fit help you with:\n• 🌍 World / Nigeria news\n• 💱 Naira to Dollar rate\n• 🌦 Lagos weather\n• 🏍 Your Bike Project - just tap Dashboard 📊 for top\n• 💡 Business ideas for Ultimate Gee Industry\n\nTalk to me, Boss!"

    elif 'how are you' in low or 'how far' in low:
        reply = "I dey very fine, Boss! Thanks for asking. My system dey live, bike dey locked and safe, news dey fresh. Wetin I go do for you today?"

    # 2. BIKE
    elif 'bike' in low or 'my bike' in low or 'dashboard' in low:
        bikes = db.execute('SELECT * FROM bikes WHERE user_id=?', (u['id'],)).fetchall()
        if bikes:
            b = bikes[0]
            reply = f"🏍 Your Bike Project - LIVE:\n\nName: {b['name']}\nModel: {b['model']}\nBattery: {b['battery_level']}% (Health {b['battery_health']}%)\nRange: {b['range_km']} km\nLock: {b['lock_status']}\nLocation: {b['location']}\n\nTap 📊 Dashboard for top to Lock / Unlock / Locate, Boss!"
        else:
            reply = "Your bike project dey for Dashboard, Boss. Tap 📊 for top."

    # 3. LIVE INFO - Naira / Weather
    elif any(x in low for x in ['naira','dollar','rate','exchange']):
        live = get_live_info(msg)
        reply = live or ask_gemini(msg, u['name']) or "Naira rate dey change, Boss. Try again in 1 min."

    elif 'weather' in low:
        live = get_live_info(msg)
        reply = live or ask_gemini(msg, u['name']) or "Weather service small wahala, try again."

    # 4. NEWS - Let Gemini give fresh with date
    elif 'news' in low or 'world' in low:
        reply = ask_gemini(msg + " - Give latest news with published dates, bullet points", u['name'])

    # 5. IMAGE
    elif d.get('image'):
        reply = ask_gemini("Describe and analyze this image in detail: " + msg, u['name'])

    # 6. GENERAL - Gemini or fallback (ALWAYS REPLY, NEVER HANG)
    if not reply:
        reply = ask_gemini(msg, u['name'])
    if not reply:
        reply = f"Yes Boss, I hear you — '{msg}'. I dey here. Ask me about news, Naira rate, weather, your bike, or business ideas. Wetin you wan do?"

    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'assistant', reply, datetime.now().isoformat()))
    db.commit()
    return jsonify({'success': True, 'reply': reply, 'conversation_id': conv_id})

# ---------- VOICE / CALL STUBS (keep working) ----------
@app.route('/api/voice/speak', methods=['POST'])
def speak(): return jsonify({'success': True, 'audio': None})
@app.route('/api/voice/chat', methods=['POST'])
def voice_chat(): return jsonify({'success': True, 'reply': 'Voice received Boss, type am make I answer sharp.'})
@app.route('/api/call/start', methods=['POST'])
def call_start(): return jsonify({'success': True, 'call_id': 1})
@app.route('/api/call/end', methods=['POST'])
def call_end(): return jsonify({'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)