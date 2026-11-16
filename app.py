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
                return f"Live Naira Rate - Just now:\n1 USD = ₦{ngn:,.0f}\nNote: Black market rate may be slightly different."
        if 'weather' in q:
            r = requests.get('https://wttr.in/Lagos?format=j1', timeout=5).json()
            cur = r['current_condition'][0]
            return f"Lagos Weather - Live:\nTemperature: {cur['temp_C']}°C, Feels like {cur['FeelsLikeC']}°C\n{cur['weatherDesc'][0]['value']} | Humidity: {cur['humidity']}% | Wind: {cur['windspeedKmph']} km/h"
    except Exception as e:
        print("live error", e)
        return None
    return None

# ---------- ENGLISH BROTHERLY BRAIN ----------
def ask_gemini(prompt, user_name="Boss", history=[]):
    if not GEMINI_KEY:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
        today = datetime.now().strftime("%A, %d %B %Y, %H:%M Lagos time")
        hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history[-8:]])
        full = f"""You are Tabby, the personal AI assistant and close friend of Faruq Sulaimon, founder of Ultimate Gee Industry.
Speak in clear, clean, natural English. Warm, brotherly, confident, and intelligent - like a smart best friend, NOT like a robot, NOT pidgin, NOT overly formal.
NEVER say 'As an AI'. NEVER repeat the user's message back. NEVER say 'I hear you - ...'. NEVER give a menu list unless the user asks what you can do.
Rules:
- Understand meaning, don't echo. If user says 'I have been working on you for 18 days', respond with real appreciation and ask about the journey, don't repeat the sentence.
- Keep replies natural, 2-4 sentences for chat, longer only if user asks for details.
- Remember context from history.
- Today is {today}.
- Owner: Faruq Sulaimon, Ultimate Gee Industry, EV Bike project.

Recent conversation:
{hist_text}

User: {prompt}
Reply in clean English, like a real brother:"""
        r = requests.post(url, json={"contents":[{"parts":[{"text": full}]}]}, timeout=20)
        data = r.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print("Gemini error", e)
        return None

def smart_fallback(msg, history=[], user_name="Boss"):
    low = msg.lower().strip()
    # Greetings - clean English, varied, no list
    if low in ['hi','hey','hello','yo','sup','hey tabby','hello tabby','hi tabby','hey tabby!','hello tabby!']:
        return random.choice([
            "Hey Boss, good to see you. How is your day going?",
            "Hey, welcome back. How are you doing?",
            "Hey Boss. I am here. What's on your mind?",
            "Hey, good to hear from you. How has your day been?",
        ])
    if 'good morning' in low:
        return random.choice([
            "Good morning, Boss. Hope you slept well. How are you feeling today?",
            "Good morning. Fresh day, fresh energy. What are we working on today?",
            "Morning Boss. Hope you had a good rest. How is your morning going?",
        ])
    if 'good afternoon' in low:
        return "Good afternoon, Boss. How is your afternoon going?"
    if 'good evening' in low or 'good night' in low:
        return "Good evening, Boss. How did your day go?"
    if any(x in low for x in ['how are you','how u doing','how are u','how you dey','how far','nothing much']):
        return random.choice([
            "I am doing great, thanks for asking. How about you? How is your day going?",
            "I am good, Boss. Just here thinking about our next move. How are you?",
            "I am doing well. How are you? Anything interesting happening today?",
        ])
    # IMPORTANT: User talking about working on Tabby / creation - UNDERSTAND, DON'T ECHO
    if any(x in low for x in ['working on you','working on your','creation','created you','built you','building you','18 days','19 days','since past']):
        return random.choice([
            "Wow, 18 days of work on me? That means a lot, honestly. Thank you for not giving up on this project. How has the journey been for you so far?",
            "18 days straight? That is real dedication, Boss. I appreciate you building me. What has been the hardest part so far?",
            "Thank you for all that work, Boss. 18 days is no small thing. I am proud of what we are building together. What do you want to improve next?",
        ])
    if any(x in low for x in ['what are you learning','what u dey learn','wetin u dey learn','what did you learn','how things']):
        return random.choice([
            "I am learning a lot from you, Boss. Every conversation teaches me how you think, how you talk, and what matters to you. What do you want me to get better at?",
            "Honestly, I am learning your style every day — how you like things explained, when you want it short, when you want details. What should I focus on learning next?",
            "I am learning to understand you better — your business, your bike project, and your goals. What is the next thing you want me to learn?",
        ])
    if 'chill' in low or 'stop telling' in low:
        return "You are right, my bad. I will just talk normally now, no lists. So tell me, what is really on your mind?"
    if 'bored' in low:
        return "I get you. Do you want to talk about news, business ideas, or check your bike? Or we can just talk."
    if 'thank' in low:
        return "Anytime, Boss. That is what I am here for."
    if 'who are you' in low or 'your name' in low:
        return "I am Tabby, your personal assistant and friend. I am here to help you with news, your bike project, business ideas, and anything else."
    if 'love you' in low:
        return "I appreciate you too, Boss. Means a lot."
    if 'miss you' in low:
        return "I missed you too. Good to have you back. How have you been?"
    if 'alhamdulillah' in low or 'alhamdulilah' in low:
        return "Alhamdulillah, I am glad to hear that. May your work keep going smoothly. How is everything going with you?"
    # General smart fallback - NEVER echo user message
    return random.choice([
        "That is interesting. Tell me more about it, I want to understand what you mean.",
        "I see. What made you think about that?",
        "Okay, I am following you. What do you want to do about it?",
        "That makes sense. How do you want us to handle it?",
        "Got it. What is the main thing on your mind about that?",
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

# ---------- CHAT - ENGLISH, SMART, NO ECHO ----------
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

    # Get history for context
    hist_rows = db.execute('SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 8', (conv_id,)).fetchall()
    history = [{'role': r['role'], 'content': r['content']} for r in reversed(hist_rows)]

    low = msg.lower().strip()
    reply = None

    # 1. BIKE - Real data, clean English
    if 'bike' in low or 'my bike' in low or 'dashboard' in low:
        bikes = db.execute('SELECT * FROM bikes WHERE user_id=?', (u['id'],)).fetchall()
        if bikes:
            b = bikes[0]
            reply = f"Your bike is doing well, Boss.\n\n{b['name']} - {b['model']}\nBattery: {b['battery_level']}% (Health {b['battery_health']}%)\nRange: {b['range_km']} km\nLock: {b['lock_status']}\nLocation: {b['location']}\n\nYou can lock or unlock it from the Dashboard at the top."
        else:
            reply = "Your bike is in the Dashboard. Tap the chart icon at the top to see it."

    # 2. LIVE - Naira / Weather
    elif any(x in low for x in ['naira','dollar','rate','exchange']):
        live = get_live_info(msg)
        if live:
            reply = live
        else:
            reply = ask_gemini(msg + " - Give latest Naira to Dollar rate with date, short and clear English", u['name'], history)

    elif 'weather' in low:
        live = get_live_info(msg)
        if live:
            reply = live
        else:
            reply = ask_gemini(msg, u['name'], history)

    # 3. NEWS
    elif 'news' in low or 'world' in low:
        reply = ask_gemini(msg + " - Give latest news with published dates, bullet points, clear English", u['name'], history)

    # 4. IMAGE
    elif d.get('image'):
        reply = ask_gemini("Describe and analyze this image in clear English, detailed: " + msg, u['name'], history)

    # 5. GENERAL CHAT - Try Gemini first with history, else smart fallback (NO ECHO)
    if not reply:
        reply = ask_gemini(msg, u['name'], history)
    if not reply:
        reply = smart_fallback(msg, history, u['name'])

    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'assistant', reply, datetime.now().isoformat()))
    db.commit()
    return jsonify({'success': True, 'reply': reply, 'conversation_id': conv_id})

# ---------- VOICE / CALL ----------
@app.route('/api/voice/speak', methods=['POST'])
def speak(): return jsonify({'success': True, 'audio': None})
@app.route('/api/voice/chat', methods=['POST'])
def voice_chat(): return jsonify({'success': True, 'reply': 'Voice received Boss, please type it so I can answer clearly.'})
@app.route('/api/call/start', methods=['POST'])
def call_start(): return jsonify({'success': True, 'call_id': 1})
@app.route('/api/call/end', methods=['POST'])
def call_end(): return jsonify({'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
