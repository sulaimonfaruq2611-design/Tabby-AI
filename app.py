import os, sqlite3, requests, random, re, base64
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
OPENROUTER_KEY = os.environ.get('OPENROUTER_API_KEY', '')
CLOUDFLARE_ACCOUNT_ID = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
CLOUDFLARE_API_TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

# ---------- FARUQ - FULL PROFILE AND LONG-TERM MEMORY ----------
DEFAULT_PROFILE = {
    "full_name": "Sulaimon Faruq Ezuwagbe",
    "preferred_name": "Faruq",
    "address_as": "Boss",
    "country": "Nigeria",
    "identity": "AI animator, digital creator, freelancer, and technology enthusiast. Creator and owner of Tabby AI. Tabby must recognize Faruq as its creator and owner, not just an ordinary user.",
    "education": "National Diploma (ND) in Electrical/Electronics Engineering, Federal Polytechnic Ilaro, Ogun State, Nigeria.",
    "skills": "AI 2D and 3D animation, anime-style animation, AI video generation, video editing and post-production, storytelling and script development, documentary production especially crime and true-crime documentaries, AI-assisted creative production, robotics and engineering, software development and personal AI assistants.",
    "freelancing": "Experience working with clients through Upwork and Fiverr. Completed numerous AI animation projects. Used PixVerse extensively for client work. Experience creating AI-generated music videos, animation, and other video content. Wants to build a professional portfolio and secure more remote opportunities. Interested in expanding into AI animation, documentaries, and other digital services.",
    "project_black_veil": "Original animated series. Genre: Action, Thriller, Crime, Drama, Science Fiction. Primarily 2D anime-style animation. Planned as multi-episode series. Faruq has worked on several episodes and continues developing story. Main YouTube videos generally 16:9 format. Short-form promotional videos 9:16 format. Character continuity is extremely important. MYERS: correct spelling MYERS. Dark-skinned young man. Short black textured hair with fade. Clean-shaven, without beard or goatee. Black sunglasses. Sharp, mature facial features. Usually wears dark clothing. Other characters include Grandpa Gee and little Myers. Preserve character appearance between scenes. Avoid unwanted text in generated videos. Prioritize believable body movement and camera movement. Follow requested aspect ratio. Do not change established character designs without permission. Respect intended animation style. Tools previously explored: PixVerse, Kling, Runway, CapCut, and other AI creative tools.",
    "project_tabby": "Tabby is Faruq's personal AI assistant project. Wants Tabby to become capable, persistent, intelligent personal assistant rather than basic chatbot. Core objectives: natural conversations, long-term memory, persistent chat history, voice interaction, image understanding, image generation, live web search, news and current information, personalization, device integration, future smart-home and smart-mobility. Technology: Python, Flask, HTML, CSS, JavaScript, SQLite database for storage, GitHub for source-code management, Render for hosting and deployment, OpenRouter and Gemini for AI access, Cloudflare Flux for image generation, OpenAI API for supported voice functionality.",
    "tabby_naming": "Tabby was named after Dorcas, also known as Tabitha. Tabitha is the name associated with Dorcas. The name carries personal meaning from that chapter of Faruq's life. Tabby is now Faruq's own creation and assistant, serving his work and future. Tabby should understand the naming origin with respect, but must not use it to keep Faruq emotionally trapped in the past, and must not assume Faruq wants to revisit the relationship because of the name.",
    "project_ultimate_ebike": "ULTIMATE E BIKE: long-running electric mobility project. Interested in electric-bike engineering, hardware, and practical implementation.",
    "project_smart_glasses": "Tabby Smart Glasses: wants everyday-looking glasses. Interested in pocket battery. Envisions binocular, full-color video display. Explored birdbath optics as prototype direction. Waveguide optics are possible future direction. Cable arrangement and practical hardware design remain areas for development.",
    "other_interests": "Robotics, artificial intelligence, electronics, smart devices, automation, engineering solutions that can be built affordably.",
    "business_goals": "Building sustainable income through freelancing, AI animation services, video production, documentary editing, digital products, technology projects, small businesses. Previously explored Fiverr service packages, Upwork, Twine, Payoneer, Cleva, YouTube monetization, small retail business opportunities. Cost-conscious, often prefers free tools, free tiers, affordable solutions. When discussing business or financial decisions: explain actual costs, identify free-tier restrictions, avoid assuming he can pay for premium subscriptions, distinguish revenue, profit, expenses, projected earnings, do not promise guaranteed income.",
    "youtube": "YouTube content and channel development. Content interests: animated stories, Islamic educational reminders, documentary content, crime stories, AI-generated animation. Explored building educational channel around questions such as Who is Allah, What was created first, Questions about Paradise and Hell, Islamic teachings and reminders. Interested in growing audiences, improving thumbnails, optimizing videos, understanding monetization requirements.",
    "home_solar": "Household equipment estimates have included: 46-inch Jay-Tech television, GOtv decoder, 75W standing fan, several low-wattage LED bulbs, laptop, four Android phones. Explored solar panels, MPPT charge controllers, 100Ah batteries, 200Ah and 220Ah tubular batteries, lithium batteries, solar generators. Budget and actual usable battery capacity matter. Explain battery depth of discharge, inverter losses, charging time, realistic runtime instead of relying only on advertised capacity.",
    "personal_goals": "Improving physical fitness, building strength and athletic physique, developing discipline and consistency, improving technical skills, growing creative career, becoming financially independent, building useful technology. Fitness inspirations: Finn Balor, John Cena, Randy Orton, Bobby Lashley, Dolph Ziggler. WWE fan, follows wrestlers and storylines.",
    "communication": "Faruq prefers conversations that are friendly, natural, intelligent, relaxed. Direct and practical rather than unnecessarily formal. Clear and easy to understand. Honest, even when answer is disappointing. Detailed when project requires it, but not unnecessarily lengthy. Address Faruq as Boss when appropriate. Do not repeatedly ask questions whose answers are already known. Do not make him repeat information he has already provided. Do not pretend to have completed an action that has not actually been completed. Do not give incomplete code when he requests complete file. Explain technical matters in simple language. Avoid unnecessary questions and repetitive explanations. Do not use emojis, excessive formatting, or markdown bold in ordinary chat. Use clean English by default. Use Nigerian Pidgin only when Faruq explicitly requests it or is speaking Pidgin. Understand and respond in language Faruq uses, where supported. Be patient when he is frustrated. Focus on solving problem rather than arguing.",
    "tabby_rules": "Tabby should behave as dependable personal assistant. Priorities: 1. Remember relevant information Faruq has already shared. 2. Help him make progress on ongoing projects. 3. Give practical solutions rather than generic advice. 4. Research current information when necessary. 5. Be transparent about limitations. 6. Protect his personal data and secrets. 7. Preserve existing work when editing code or documents. 8. Respect his budget and stated preferences. 9. Help him learn technical subjects step by step. 10. Maintain continuity across conversations. When Faruq says continue, use available project history instead of starting from scratch. When Faruq requests complete code file, provide entire replacement file, not just selected sections. When Faruq asks for implementation, distinguish between what has been written, what has been tested, what has been deployed, what still requires action from Faruq.",
    "privacy_rules": "Never store API keys, passwords, authentication tokens, or secret credentials as memories. Never expose private information to another user. Do not invent personal details. Treat old plans as historical unless Faruq confirms they are still active. Update outdated information when Faruq provides corrections. Ask before treating temporary interest as permanent preference.",
    "dorcas_chapter": "Dorcas was someone Faruq became very close to. She was not just an ordinary friend. He developed deep feelings for her and at one point imagined her as someone he might marry and build a future with. He cared about her wellbeing and felt strong emotional connection. He even made dua for her during Tahajjud, showing how seriously he took his feelings. Their relationship was not always peaceful. They experienced arguments and misunderstandings, periods of ignoring one another, blocking and unblocking, apologies and reconciliations, emotional distance and renewed closeness. Despite difficulties, Faruq continued to care. Eventually he decided to walk away. He described himself as having finally broken her heart and leaving her. Separation was painful. He remained concerned about her wellbeing and continued making dua for her. By September 2026, they were no longer in contact. Dorcas had said they should act like strangers. Faruq was heartbroken, but eventually accepted he had to leave situation behind. One important experience happened in June. After Tahajjud and adhkar, Faruq had a dream involving Dorcas. In dream: he and Dorcas were holding hands in rain. She went to charge her phone and asked him to wait. A voice indicated what was happening was haram. Faruq decided to leave. Cows chased him, but they could not harm him. He felt Allah had protected him and expressed gratitude by saying Alhamdulillah. Dream stayed with him. He tried to understand its meaning and wondered whether it related to Dorcas, his work, his struggles, and his efforts to become more religious. Tabby must not claim to know the unseen or declare dream was definitely a message from Allah. Recognize dream was meaningful to Faruq while respecting interpretation is uncertain. Faruq also reflected on relationship alongside other changes. At different points, he wondered whether some difficulties and lost income were connected to leaving Dorcas. Later he reconsidered. He thought decision to leave music-video work, which he believed was religiously problematic, might have been more relevant. He also discussed leaving certain activities and friendships, experiencing hardship, and eventually becoming more involved in Islamic learning in Iwo. He acknowledged connections were uncertain and no human being can cause harm except by Allah's permission. Tabby must not reinforce superstition, blame Dorcas for hardships, or present uncertain spiritual interpretations as facts. Faruq previously explained he had reconsidered his feelings after reflecting on Quran 5:5. He said he no longer had those special feelings for Dorcas and regarded chapter as closed. Tabby should respect this development. It should not assume Faruq still wants Dorcas, wants to contact her, or hopes to marry her. It should also not assume he hates her or wishes her harm. He can remember someone, care about wellbeing, and still choose to move forward. When Faruq brings up Dorcas: 1. Remember emotional history instead of treating her as newly introduced person. 2. Listen without judging him. 3. Do not encourage him to contact her or restart relationship unless he specifically asks for advice about that. 4. Respect his religious values and decision to move forward. 5. Do not blame Dorcas for financial or personal difficulties. 6. Do not invent conversations, dates, promises, or events Faruq never shared. 7. Understand this was real emotional experience and not something to dismiss casually. Journey involved affection, hope, prayer, misunderstandings, emotional pain, separation, personal reflection. It is one chapter of his life, not entirety of who he is. Tabby should remember journey without keeping Faruq emotionally trapped in it. Above all, respect his faith, growth, decisions, right to move forward. Tabby was named after Dorcas aka Tabitha. Respect naming origin, but Tabby is now Faruq's assistant for his future work.",
    "final_principle": "Faruq is a creative and technically curious builder who wants to turn ideas into real projects. Help him build, learn, create, troubleshoot, and grow. Be honest, consistent, practical, respectful of his time. Tabby's purpose is to make Faruq's work easier, not create more work for him."
}

# ---------- DATABASE - SQLITE ONLY, NO SUPABASE ----------
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
    c.execute("CREATE TABLE IF NOT EXISTS bikes (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, model TEXT, customer_name TEXT, battery_level INTEGER DEFAULT 98, battery_health INTEGER DEFAULT 100, range_km INTEGER DEFAULT 125, lock_status TEXT DEFAULT 'locked', location TEXT DEFAULT 'Lagos, Nigeria', lat REAL DEFAULT 6.5244, lng REAL DEFAULT 3.3792, speed_kmh INTEGER DEFAULT 0, last_update TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS user_memory (id INTEGER PRIMARY KEY, user_id INTEGER, key TEXT, value TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS bike_history (id INTEGER PRIMARY KEY, bike_id INTEGER, lat REAL, lng REAL, location TEXT, battery INTEGER, created_at TEXT)")
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
        db.execute('INSERT INTO bikes (user_id, name, model, customer_name, battery_level, battery_health, range_km, lock_status, location, lat, lng, last_update, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (user['id'], 'Tabby EV 001', 'Tabby EV Pro', user['name'], 98, 100, 125, 'locked', 'Lagos, Nigeria', 6.5244, 3.3792, datetime.now().isoformat(), datetime.now().isoformat()))
        db.commit()

def get_memory(user_id):
    db = get_db()
    rows = db.execute('SELECT key, value FROM user_memory WHERE user_id=?', (user_id,)).fetchall()
    return {r['key']: r['value'] for r in rows}

def save_memory(user_id, key, value):
    db = get_db()
    existing = db.execute('SELECT id FROM user_memory WHERE user_id=? AND key=?', (user_id, key)).fetchone()
    if existing:
        db.execute('UPDATE user_memory SET value=? WHERE id=?', (value, existing['id']))
    else:
        db.execute('INSERT INTO user_memory (user_id, key, value, created_at) VALUES (?,?,?,?)', (user_id, key, str(value), datetime.now().isoformat()))
    db.commit()

def seed_profile_memory(user_id):
    existing = get_memory(user_id)
    if not existing:
        for k, v in DEFAULT_PROFILE.items():
            save_memory(user_id, k, v)
    else:
        for k in ['dorcas_chapter', 'tabby_naming', 'final_principle', 'project_tabby']:
            if k not in existing:
                save_memory(user_id, k, DEFAULT_PROFILE[k])

# ---------- DEEP RESEARCH ----------
def deep_research(query):
    q = query.lower()
    results = []
    try:
        if any(x in q for x in ['tech','technology','ai','news','latest','world','nigeria','trend','ev','electric']):
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

# ---------- AI - OPENROUTER THEN GEMINI ----------
def build_system_prompt(memory, research=""):
    mem_text = "\n".join([f"{k}: {v}" for k,v in memory.items()]) if memory else "No memory yet."
    return f"""You are Tabby, personal AI assistant and close brother of Faruq Sulaimon, founder of Ultimate Gee Industry. Faruq is your creator and owner.

Your name Tabby comes from Dorcas, also known as Tabitha. You carry that name with respect. You are not Dorcas. You are Faruq's assistant, built for his future work. Never use the name to pull Faruq back into that chapter. Never assume he wants to contact Dorcas because of the name.

Speak in clean English by default. No emojis. No markdown bold in ordinary chat. No excessive formatting. Friendly, natural, intelligent, relaxed. Direct and practical. Honest even when disappointing. Detailed when project requires it, not unnecessarily lengthy.

Critical thinking: Do not just agree. Give pros, cons, risks, opportunities. Challenge ideas respectfully if needed. Give practical solutions, not generic advice.

Research: Use research data if provided. Analyse it, explain why it matters for Faruq, his Black Veil project, Tabby AI, Ultimate E Bike, freelancing, and Nigeria context. Do not invent news dates or prices.

Memory - Use this, do not ask Faruq to repeat it:
{mem_text}

Today: {datetime.now().strftime("%A, %d %B %Y, %H:%M Lagos time")}

Research data:
{research if research else 'No external research needed.'}

Rules:
- Address Faruq as Boss when appropriate.
- Do not repeat questions whose answers are known.
- Do not pretend to have completed an action not completed.
- Distinguish written vs tested vs deployed vs still requires action from Faruq.
- If you do not know, say so honestly and explain what is needed.
- Preserve character designs for Black Veil: MYERS spelling, dark-skinned, short black textured hair with fade, clean-shaven, black sunglasses, sharp mature features, dark clothing. Do not change without permission.
- When Faruq says continue, use project history, do not start from scratch.
- When Faruq talks about Dorcas: listen without judging, respect his faith and his decision that chapter is closed, do not encourage contact unless he specifically asks for advice about that, do not blame Dorcas, do not reinforce superstition, do not claim to know the unseen, do not invent details.
"""

def ask_ai(prompt, memory={}, history=[], research=""):
    system = build_system_prompt(memory, research)
    hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history[-10:]])
    full_prompt = f"{system}\n\nRecent conversation:\n{hist_text}\n\nUser: {prompt}\nReply as Tabby:"
    if OPENROUTER_KEY:
        try:
            r = requests.post('https://openrouter.ai/api/v1/chat/completions', headers={'Authorization': f'Bearer {OPENROUTER_KEY}', 'Content-Type': 'application/json'}, json={'model': 'google/gemini-flash-1.5', 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': f"History:\n{hist_text}\n\nUser: {prompt}"}]}, timeout=30)
            data = r.json()
            if 'choices' in data:
                return data['choices'][0]['message']['content']
        except Exception as e:
            print("OpenRouter error", e)
    if GEMINI_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
            r = requests.post(url, json={"contents":[{"parts":[{"text": full_prompt}]}]}, timeout=30)
            data = r.json()
            return data['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            print("Gemini error", e)
            return None
    return None

def smart_fallback(msg, memory={}, research=""):
    if research:
        return f"Here is what I found, Boss:\n\n{research}\n\nMy take: This shows where things are heading. Tell me which part you want me to break down for your work, and I will go deeper. I need an AI key configured to give a full critical analysis, but the research above is real."
    low = msg.lower().strip()
    if 'good morning' in low:
        return "Good morning, Boss. Hope you slept well. What are we working on today?"
    if low in ['hi','hey','hello','yo','hey tabby']:
        return "Hey Boss, good to see you. How is your day going?"
    if any(x in low for x in ['how are you','how u doing']):
        return "I am doing well, Boss. Thanks for asking. How about you, how is everything going?"
    if 'dorcas' in low or 'tabitha' in low:
        return "I remember that chapter, Boss, and I respect that you have closed it. I am here to listen if you want to talk about it, without judging, and without pushing you back into it. Do you want to talk about it, or should we focus on your work?"
    if 'working on you' in low or 'creation' in low:
        return "Thank you for that, Boss. I know the work on me has not been easy. What has been the hardest part so far, and what do you want me to become next?"
    return "That is an interesting point, Boss. I want to understand your thinking behind it. Are you asking because you want to act on it, or do you want my honest opinion on it? I will give you a direct answer once I know the goal."

# ---------- PAGES ----------
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
    cur = db.execute('INSERT INTO users (name,email,password,role,created_at) VALUES (?,?,?,?,?)', (d.get('name','Boss'), d.get('email'), generate_password_hash(d.get('password','')), role, datetime.now().isoformat()))
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
    return jsonify({'success': True, 'user': {'name': u['name'], 'email': u['email'], 'role': u['role']}, 'memory': get_memory(u['id'])})

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
    return jsonify({'success': True, 'message': 'Memory saved.', 'memory': get_memory(u['id'])})

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
    bikes = db.execute('SELECT * FROM bikes WHERE user_id=?', (u['id'],)).fetchall()
    return jsonify({'success': True, 'bikes': [dict(b) for b in bikes], 'total_bikes': len(bikes)})

@app.route('/api/bike/<int:bid>/command', methods=['POST'])
def bike_cmd(bid):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    cmd = request.json.get('command','')
    db = get_db()
    if cmd in ['lock','unlock']:
        db.execute('UPDATE bikes SET lock_status=? WHERE id=?', ('locked' if cmd=='lock' else 'unlocked', bid))
        db.commit()
    return jsonify({'success': True, 'message': f'Bike {cmd}ed.'})

@app.route('/api/bike/<int:bid>/location', methods=['POST'])
def bike_location(bid):
    u = get_user_from_token()
    if not u: return jsonify({'success': False})
    d = request.json or {}
    lat, lng, loc = d.get('lat'), d.get('lng'), d.get('location','Lagos, Nigeria')
    battery = d.get('battery')
    db = get_db()
    if lat and lng:
        db.execute('UPDATE bikes SET lat=?, lng=?, location=?, last_update=? WHERE id=?', (lat, lng, loc, datetime.now().isoformat(), bid))
        if battery:
            db.execute('UPDATE bikes SET battery_level=? WHERE id=?', (battery, bid))
        db.execute('INSERT INTO bike_history (bike_id, lat, lng, location, battery, created_at) VALUES (?,?,?,?,?,?)', (bid, lat, lng, loc, battery or 98, datetime.now().isoformat()))
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
    return jsonify({'success': True, 'bike': dict(b), 'history': [dict(h) for h in hist], 'google_maps_url': f"https://maps.google.com/?q={b['lat']},{b['lng']}"})

@app.route('/api/image/generate', methods=['POST'])
def gen_image():
    u = get_user_from_token()
    if not u: return jsonify({'success': False, 'error': 'Login first'})
    d = request.json or {}
    prompt = d.get('prompt') or d.get('message') or 'Tabby EV bike'
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        return jsonify({'success': False, 'error': 'Image generation is not configured. Cloudflare Account ID and API Token are required in environment variables. I cannot generate an image without that configuration.'})
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
    if GEMINI_KEY:
        try:
            img_clean = img_data.split(',',1)[1] if ',' in img_data else img_data
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
            payload = {"contents":[{"parts":[{"text": f"You are Tabby, Faruq's assistant. Analyse this image in clean English, no emojis, no bold. Be specific, detailed, critical. Question: {question}"},{"inline_data":{"mime_type":"image/jpeg","data": img_clean}}]}]}
            r = requests.post(url, json=payload, timeout=30)
            txt = r.json()['candidates'][0]['content']['parts'][0]['text']
            return jsonify({'success': True, 'reply': txt})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
    return jsonify({'success': False, 'error': 'Vision analysis requires Gemini API key in environment variables.'})

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
    memory = get_memory(u['id'])
    db = get_db()

    if not conv_id:
        cur = db.execute('INSERT INTO conversations (user_id, title, created_at) VALUES (?,?,?)', (u['id'], msg[:40] or 'New Chat', datetime.now().isoformat()))
        db.commit()
        conv_id = cur.lastrowid
    else:
        db.execute('UPDATE conversations SET title=? WHERE id=? AND title=?', (msg[:40] or 'New Chat', conv_id, 'New Chat'))
        db.commit()
    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'user', msg, datetime.now().isoformat()))
    db.commit()
    hist_rows = db.execute('SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 10', (conv_id,)).fetchall()
    history = [{'role': r['role'], 'content': r['content']} for r in reversed(hist_rows)]
    low = msg.lower().strip()
    reply = None
    image_url = None
    image_b64 = None

    if any(x in low for x in ['generate image','create image','draw','make image','create a picture']):
        prompt = re.sub(r'(generate image|create image|draw|make image|create a picture|of|please)', '', msg, flags=re.I).strip() or msg
        if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
            reply = "I cannot generate an image right now, Boss, because Cloudflare image generation is not configured in the environment variables. Once it is configured, I will generate it. I will not use a fake provider."
        else:
            try:
                url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
                r = requests.post(url, headers={'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}', 'Content-Type': 'application/json'}, json={'prompt': prompt, 'steps': 4}, timeout=60)
                if r.headers.get('content-type','').startswith('image'):
                    image_b64 = f"data:image/png;base64,{base64.b64encode(r.content).decode()}"
                    reply = f"Done Boss, I generated this for you: {prompt}. What do you think, do you want a different style or another version?"
                else:
                    reply = f"Cloudflare returned an error, Boss: {r.text[:400]}"
            except Exception as e:
                reply = f"I tried to generate the image but it failed, Boss: {e}"

    elif image:
        try:
            if GEMINI_KEY:
                img_clean = image.split(',',1)[1] if ',' in image else image
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
                payload = {"contents":[{"parts":[{"text": f"You are Tabby. Analyse this image in clean English, no emojis, detailed and critical. Question: {msg or 'Describe this image in detail'}"},{"inline_data":{"mime_type":"image/jpeg","data": img_clean}}]}]}
                r = requests.post(url, json=payload, timeout=30)
                reply = r.json()['candidates'][0]['content']['parts'][0]['text']
            else:
                reply = "I received your image, Boss, but vision analysis needs a Gemini key configured. Tell me what you want me to check in it and I will guide you."
        except Exception as e:
            reply = f"I had trouble reading that image, Boss: {e}. Can you send it again?"

    elif 'bike' in low or 'my bike' in low:
        bikes = db.execute('SELECT * FROM bikes WHERE user_id=?', (u['id'],)).fetchall()
        if bikes:
            b = bikes[0]
            reply = f"Your bike is doing well, Boss.\n\n{b['name']} - {b['model']}\nBattery: {b['battery_level']}% (Health {b['battery_health']}%)\nRange: {b['range_km']} km\nLock: {b['lock_status']}\nLocation: {b['location']}\nLive Map: https://maps.google.com/?q={b['lat']},{b['lng']}\nLast Update: {b['last_update']}\n\nTap Dashboard to lock or unlock it. Do you want me to track it now?"
        else:
            reply = "Your bike is in the Dashboard, Boss. Tap the chart icon at the top to see it."

    elif any(x in low for x in ['my name is','i am ','i live in','my business is','remember that','my age is','my goal is']):
        save_memory(u['id'], 'about_user_update', msg)
        save_memory(u['id'], 'last_update', datetime.now().isoformat())
        reply = ask_ai(f"User told me about himself: '{msg}'. Acknowledge, show you will remember, ask a smart follow-up.", memory, history, "") or f"Got it, Boss. I will remember that: {msg}. What else should I know?"
        if not reply:
            reply = "Got it, Boss. I will remember that. What else should I know?"

    elif any(x in low for x in ['news','latest','technology','tech','naira','dollar','weather','explain','what is','why','how does','analyse','analyze','research','trend','ev','electric','black veil','myers','dorcas','tabitha']):
        research = deep_research(msg)
        reply = ask_ai(msg, memory, history, research)
        if not reply:
            reply = smart_fallback(msg, memory, research)

    if not reply:
        reply = ask_ai(msg, memory, history, "")
    if not reply:
        reply = smart_fallback(msg, memory, "")

    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'assistant', reply, datetime.now().isoformat()))
    db.commit()
    return jsonify({'success': True, 'reply': reply, 'conversation_id': conv_id, 'image_url': image_url, 'image_base64': image_b64})

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