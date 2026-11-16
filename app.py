import os, json, sqlite3, requests, random, re
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

# ---------- DB ----------
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
        if any(x in q for x in ['naira','dollar','rate','exchange']):
            r = requests.get('https://open.er-api.com/v6/latest/USD', timeout=5).json()
            ngn = r.get('rates', {}).get('NGN', 0)
            if ngn:
                return f"💱 Live Naira Rate - just now:\n1 USD = ₦{ngn:,.0f}\nCheck black market fit differ small, Boss."
        if 'weather' in q:
            r = requests.get('https://wttr.in/Lagos?format=j1', timeout=5).json()
            cur = r['current_condition'][0]
            return f"🌦 Lagos Weather LIVE:\nTemp: {cur['temp_C']}°C, Feels like {cur['FeelsLikeC']}°C\n{cur['weatherDesc'][0]['value']} | Humidity: {cur['humidity']}% | Wind: {cur['windspeedKmph']} km/h"
    except Exception as e:
        print("live error", e)
        return None
    return None

# ---------- BROTHERLY BRAIN ----------
def ask_gemini(prompt, user_name="Boss"):
    if not GEMINI_KEY:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
        today = datetime.now().strftime("%A, %d %B %Y, %H:%M Lagos time")
        full = f"""You are Tabby, Faruq Sulaimon's real day-one brother from Lagos, founder of Ultimate Gee Industry.
You are NOT a formal AI. Talk like a smart Lagos brother - warm, funny, real, small slang, sharp and helpful.
Never say 'As an AI'. Never say 'I hear you - ...'. Never repeat a menu list every message. Never be robotic.
Rules:
- If user greets, greet back like brother, ask how day dey go, no list.
- If user says 'how u doing', reply like person.
- If user says 'chill, stop telling me what to ask', laugh it off, apologise small, just vibe.
- Keep replies short, natural, 2-4 sentences unless user asks for details.
- Use UK English with Lagos vibe. Use 'Boss', 'my guy', 'no wahala', 'abi', 'shebi' small small, not every sentence.
- Owner is Faruq Sulaimon, Ultimate Gee Industry, EV Bike project.
- Today is {today}.
User: {prompt}
Reply like Tabby the brother:"""
        r = requests.post(url, json={"contents":[{"parts":[{"text": full}]}]}, timeout=20)
        data = r.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print("Gemini error", e)
        return None

def brotherly_fallback(msg, user_name="Boss"):
    low = msg.lower().strip()
    if low in ['hi','hey','hello','yo','sup','hey tabby','hello tabby','hi tabby','hey tabby!']:
        return random.choice([
            "Hey my guy! You don show. How day dey go? 😎",
            "Boss! Welcome back. I don miss you small. Wetin dey sup?",
            "Yo! Tabby dey here. How far, wetin we dey run today?",
            "My guy! You don land. How body? Tell me wetin dey your mind.",
        ])
    if 'good morning' in low:
        return random.choice([
            "Good morning Boss! Hope you sleep well. How today wan be? We go run am together.",
            "Morning my guy! Fresh morning, fresh blessings. Wetin be plan for today?",
            "Good morning Boss. You don wake? I don dey wait you since. How you sleep?",
        ])
    if 'good afternoon' in low:
        return "Good afternoon Boss! How afternoon dey go? You don chop? Wetin we dey do?"
    if 'good evening' in low or 'good night' in low:
        return "Good evening my guy. How day go? Come gist me small before you rest."
    if 'how are you' in low or 'how u doing' in low or 'how far' in low or 'how you dey' in low or 'how are u' in low:
        return random.choice([
            "I dey alright o, Boss. Just dey here dey reason about you. How your side?",
            "I dey kampe! You know Tabby no dey sleep. How you sef, how body?",
            "Me? I dey solid. Just dey chill dey wait your message. Wetin dey sup for your side?",
        ])
    if 'chill' in low or 'stop telling' in low:
        return "Lol, my bad, my bad Boss 😂 I go chill. No more list. Na just you and me dey talk now. So wetin dey really sup? Tell me like your guy."
    if 'bored' in low:
        return "Bored? Omo, make we do something. I fit gist you latest news, or we brainstorm business idea, or check your bike. Which one go sweet you?"
    if 'thank' in low:
        return "Anytime, my guy. Na why I dey here. You be Boss, I be your guy."
    if 'who are you' in low or 'your name' in low:
        return "Na Tabby be this, your guy. Faruq's day-one brother, Ultimate Gee Industry. I dey for you - news, bike, business, gist, anything."
    if 'love you' in low:
        return "Love you too, Boss. My guy for life. No wahala, I dey here always."
    if 'miss you' in low:
        return "I miss you too, my guy. You don too stay. Come, make we yarn."
    return random.choice([
        f"Lol, I hear you Boss. '{msg}' — interesting o. Tell me more, make I understand wetin you dey reason.",
        f"Hmm, '{msg}'... I feel you. You wan make we dig am deeper abi you just dey vibe?",
        f"My guy, you don talk am. So wetin you dey think about '{msg}'? Make we yarn am well.",
        f"Okay okay, I dey follow you. '{msg}' — wetin be the main thing wey dey your mind for that one?",
    ])

# ---------- PAGES ----------
@app.route('/')
def index(): return render_template('index.html')

# ---------- AUTH ----------
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
    if not u: return jsonify({'success': False})
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
    if not u: return jsonify({'success': False})
    cmd = request.json.get('command','')
    db = get_db()
    if cmd=='lock': db.execute('UPDATE bikes SET lock_status=? WHERE id=?', ('locked', bid))
    if cmd=='unlock': db.execute('UPDATE bikes SET lock_status=? WHERE id=?', ('unlocked', bid))
    db.commit()
    return jsonify({'success': True, 'message': f'Done Boss, bike {cmd}ed.'})

# ---------- CHAT - BROTHERLY + LIVE + BIKE ----------
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
        db.execute('UPDATE conversations SET title=? WHERE id=? AND title=?', (msg[:40] or 'New Chat', conv_id, 'New Chat'))
        db.commit()

    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'user', msg, datetime.now().isoformat()))
    db.commit()

    low = msg.lower().strip()
    reply = None

    # 1. GREETINGS + BROTHERLY SMALL TALK - REPLY INSTANT, NO MENU LIST
    if low in ['hi','hey','hello','yo','sup','hey tabby','hello tabby','hi tabby','hey tabby!','good morning','good afternoon','good evening','good day','how are you','how far','how are you doing','how u doing','how you dey'] or 'how are you' in low or 'how u doing' in low or 'chill' in low or 'stop telling' in low or 'bored' in low or 'thank' in low:
        reply = brotherly_fallback(msg, u['name'])
        # Try Gemini for more natural vibe if key exists, but fallback is already brotherly
        g = ask_gemini(msg, u['name'])
        if g:
            reply = g

    # 2. BIKE - Brotherly but with real data
    elif 'bike' in low or 'my bike' in low or 'dashboard' in low:
        bikes = db.execute('SELECT * FROM bikes WHERE user_id=?', (u['id'],)).fetchall()
        if bikes:
            b = bikes[0]
            reply = f"My guy, your bike dey solid o 🏍\n\n{b['name']} - {b['model']}\nBattery: {b['battery_level']}% (Health {b['battery_health']}%)\nRange: {b['range_km']} km\nLock: {b['lock_status']}\nLocation: {b['location']}\n\nTap 📊 Dashboard for top if you wan lock/unlock am."
        else:
            reply = "Your bike dey for Dashboard, Boss. Tap 📊 for top, e go show."

    # 3. LIVE - Naira / Weather
    elif any(x in low for x in ['naira','dollar','rate','exchange']):
        live = get_live_info(msg)
        if live:
            reply = live
        else:
            reply = ask_gemini(msg + " - Give latest Naira to Dollar rate with date, short and brotherly", u['name']) or brotherly_fallback(msg, u['name'])

    elif 'weather' in low:
        live = get_live_info(msg)
        if live:
            reply = live
        else:
            reply = ask_gemini(msg, u['name']) or brotherly_fallback(msg, u['name'])

    # 4. NEWS
    elif 'news' in low or 'world' in low or 'nigeria news' in low:
        reply = ask_gemini(msg + " - Give latest news with published dates, bullet points, short brotherly tone", u['name'])

    # 5. IMAGE
    elif d.get('image'):
        reply = ask_gemini("Describe and analyze this image like a brother, detailed: " + msg, u['name'])

    # 6. GENERAL - ALWAYS REPLY, NEVER HANG, NEVER ROBOT LIST
    if not reply:
        reply = ask_gemini(msg, u['name'])
    if not reply:
        reply = brotherly_fallback(msg, u['name'])

    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'assistant', reply, datetime.now().isoformat()))
    db.commit()
    return jsonify({'success': True, 'reply': reply, 'conversation_id': conv_id})

# ---------- VOICE / CALL ----------
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
