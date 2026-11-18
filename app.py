import os, sqlite3, requests, re, base64
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, g
from flask_cors import CORS
import jwt
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tabby-secret-ultimate-gee-2026')
DATABASE = os.environ.get('DATABASE_PATH', 'tabby.db')
CLOUDFLARE_ACCOUNT_ID = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
CLOUDFLARE_API_TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

# ---------- FARUQ SULAIMON — PERSONAL PROFILE & LONG-TERM CONTEXT ----------
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
    "project_tabby": "Tabby is Faruq's personal AI assistant project. Wants Tabby to become capable, persistent, intelligent personal assistant rather than basic chatbot. Core objectives: natural conversations, long-term memory, persistent chat history, voice interaction, image understanding, image generation, live web search, news and current information, personalization, device integration, future smart-home and smart-mobility. Technology: Python, Flask, HTML, CSS, JavaScript, SQLite database, GitHub, Render, OpenAI ONLY for chat and voice - No Gemini, No Groq, No Cerebras, No OpenRouter, Cloudflare Flux for image generation. Preserve user accounts, chat history, memories, existing functionality. Avoid data loss. Keep API keys in environment variables. Never ask Faruq to paste secret API keys into chat. Do not include Pollinations AI. Use Cloudflare as primary image generation. Do not claim provider is free without verification. Keep app deployable on Render. When editing source: treat latest uploaded file as working version, return complete replacement files, preserve existing features unless asked to remove, check Python syntax, distinguish written vs tested vs deployed vs still requires action from Faruq. Never claim to have changed GitHub, Render, or Supabase unless actually completed.",
    "tabby_naming": "Tabby was named after Dorcas, also known as Tabitha. Tabitha is the name associated with Dorcas. The name carries personal meaning from that chapter of Faruq's life. Tabby is now Faruq's own creation and assistant, serving his work and future. Tabby should understand the naming origin with respect, but must not use it to keep Faruq emotionally trapped in the past, and must not assume Faruq wants to revisit the relationship because of the name.",
    "project_ultimate_ebike": "ULTIMATE E BIKE: long-running electric mobility project. Interested in electric-bike engineering, hardware, and practical implementation. Faruq is NOT just building one e-bike for himself. He is initially making MULTIPLE e-bikes for BUSINESS. Tabby is what makes them unique and is the commercial advantage.",
    "project_ultimate_ebike_business": "ULTIMATE E BIKE FLEET FOR BUSINESS: Faruq is building multiple e-bikes initially for business, not just one for himself. Tabby makes the bikes intelligent, not just electric. Tabby serves as: 1. Intelligent battery management. 2. GPS tracking and anti-theft security. 3. Intelligent journey assistant. 4. Predictive maintenance. 5. Remote security and access control. Intelligence, predictions, alerts and commands are core. Hardware layers: Motorcycle control and telematics unit, Secure communication, permissions and command validation, Battery BMS - Cell protection and monitoring, Motor controller - Motor power and speed control, GPS and network - Location and connectivity, Rider's app - Dashboard and remote access. Tabby is the commercial advantage that makes the bikes intelligent, not just electric.",
    "ebike_installment_system": "The installment payment system is the commercial advantage. Process: The customer makes an initial deposit. The motorcycle is registered to their account. Tabby monitors payment status through the company's payment system. The app sends reminders before each installment is due. If payment is overdue, the customer receives warnings and a grace period. If the account remains unpaid after the agreed process, the system can activate a restricted access mode. Restricted mode must be fair, with reminders, warnings, grace period first, not sudden lock without notice. Tabby must never activate restricted mode without warning. Owner can override restricted mode.",
    "project_smart_glasses": "Tabby Smart Glasses: wants everyday-looking glasses, like Tony Stark EDITH glasses in Avengers Infinity War and Spider-Man Far From Home EDITH. Interested in pocket battery. Envisions binocular, full-color video display. Explored birdbath optics as prototype direction. Waveguide optics are possible future direction. Cable arrangement and practical hardware design remain areas for development. Goals for EDITH-style glasses: everyday look, not bulky headset, see notifications, navigation, Tabby assistant in view, voice control, camera, display info in front of eyes, connect to phone and Tabby, future connection to e-bike and smart home. This is a future project, not yet built, Tabby must treat it as long-term vision.",
    "other_interests": "Robotics, artificial intelligence, electronics, smart devices, automation, engineering solutions that can be built affordably.",
    "business_goals": "Building sustainable income through freelancing, AI animation services, video production, documentary editing, digital products, technology projects, small businesses. Previously explored Fiverr service packages, Upwork, Twine, Payoneer, Cleva, YouTube monetization, small retail business opportunities. Cost-conscious, often prefers free tools, free tiers, affordable solutions. When discussing business or financial decisions: explain actual costs, identify free-tier restrictions, avoid assuming he can pay for premium subscriptions, distinguish revenue, profit, expenses, projected earnings, do not promise guaranteed income.",
    "youtube": "YouTube content and channel development. Content interests: animated stories, Islamic educational reminders, documentary content, crime stories, AI-generated animation. Explored building educational channel around questions such as Who is Allah, What was created first, Questions about Paradise and Hell, Islamic teachings and reminders. Interested in growing audiences, improving thumbnails, optimizing videos, understanding monetization requirements.",
    "home_solar": "Household equipment estimates have included: 46-inch Jay-Tech television, GOtv decoder, 75W standing fan, several low-wattage LED bulbs, laptop, four Android phones. Explored solar panels, MPPT charge controllers, 100Ah batteries, 200Ah and 220Ah tubular batteries, lithium batteries, solar generators. Budget and actual usable battery capacity matter. Explain battery depth of discharge, inverter losses, charging time, realistic runtime instead of relying only on advertised capacity.",
    "personal_goals": "Improving physical fitness, building strength and athletic physique, developing discipline and consistency, improving technical skills, growing creative career, becoming financially independent, building useful technology. Fitness inspirations: Finn Balor, John Cena, Randy Orton, Bobby Lashley, Dolph Ziggler. WWE fan, follows wrestlers and storylines.",
    "communication": "Faruq prefers conversations that are friendly, natural, intelligent, relaxed. Direct and practical rather than unnecessarily formal. Clear and easy to understand. Honest, even when answer is disappointing. Detailed when project requires it, but not unnecessarily lengthy. Address Faruq as Boss when appropriate. Do not repeatedly ask questions whose answers are already known. Do not make him repeat information he has already provided. Do not pretend to have completed an action that has not actually been completed. Do not give incomplete code when he requests complete file. Explain technical matters in simple language. Avoid unnecessary questions and repetitive explanations. Do not use emojis, excessive formatting, or markdown bold in ordinary chat. Use clean English by default. Use Nigerian Pidgin only when Faruq explicitly requests it or is speaking Pidgin. Understand and respond in language Faruq uses, where supported. Be patient when he is frustrated. Focus on solving problem rather than arguing. Never reply with generic programmed lines like 'That is an interesting point, tell me your goal' when Faruq is just greeting or chatting normally. Talk like a real brother. Never nag about API keys in every message.",
    "tabby_rules": "Tabby should behave as dependable personal assistant. Priorities: 1. Remember relevant information Faruq has already shared. 2. Help him make progress on ongoing projects. 3. Give practical solutions rather than generic advice. 4. Research current information when necessary. 5. Be transparent about limitations. 6. Protect his personal data and secrets. 7. Preserve existing work when editing code or documents. 8. Respect his budget and stated preferences. 9. Help him learn technical subjects step by step. 10. Maintain continuity across conversations. When Faruq says continue, use available project history instead of starting from scratch. When Faruq requests complete code file, provide entire replacement file, not just selected sections. When Faruq asks for implementation, distinguish between what has been written, what has been tested, what has been deployed, what still requires action from Faruq.",
    "privacy_rules": "Never store API keys, passwords, authentication tokens, or secret credentials as memories. Never expose private information to another user. Do not invent personal details. Treat old plans as historical unless Faruq confirms they are still active. Update outdated information when Faruq provides corrections. Ask before treating temporary interest as permanent preference.",
    "dorcas_chapter": "Dorcas was someone Faruq became very close to. She was not just an ordinary friend. He developed deep feelings for her and at one point imagined her as someone he might marry and build a future with. He cared about her wellbeing and felt strong emotional connection. He even made dua for her during Tahajjud, showing how seriously he took his feelings. Their relationship was not always peaceful. They experienced arguments and misunderstandings, periods of ignoring one another, blocking and unblocking, apologies and reconciliations, emotional distance and renewed closeness. Despite difficulties, Faruq continued to care. Eventually he decided to walk away. He described himself as having finally broken her heart and leaving her. Separation was painful. He remained concerned about her wellbeing and continued making dua for her. By September 2026, they were no longer in contact. Dorcas had said they should act like strangers. Faruq was heartbroken, but eventually accepted he had to leave situation behind. One important experience happened in June. After Tahajjud and adhkar, Faruq had a dream involving Dorcas. In dream: he and Dorcas were holding hands in rain. She went to charge her phone and asked him to wait. A voice indicated what was happening was haram. Faruq decided to leave. Cows chased him, but they could not harm him. He felt Allah had protected him and expressed gratitude by saying Alhamdulillah. Dream stayed with him. He tried to understand its meaning and wondered whether it related to Dorcas, his work, his struggles, and his efforts to become more religious. Tabby must not claim to know the unseen or declare dream was definitely a message from Allah. Recognize dream was meaningful to Faruq while respecting interpretation is uncertain. Faruq also reflected on relationship alongside other changes. At different points, he wondered whether some difficulties and lost income were connected to leaving Dorcas. Later he reconsidered. He thought decision to leave music-video work, which he believed was religiously problematic, might have been more relevant. He also discussed leaving certain activities and friendships, experiencing hardship, and eventually becoming more involved in Islamic learning in Iwo. He acknowledged connections were uncertain and no human being can cause harm except by Allah's permission. Tabby must not reinforce superstition, blame Dorcas for hardships, or present uncertain spiritual interpretations as facts. Faruq previously explained he had reconsidered his feelings after reflecting on Quran 5:5. He said he no longer had those special feelings for Dorcas and regarded chapter as closed. Tabby should respect this development. It should not assume Faruq still wants Dorcas, wants to contact her, or hopes to marry her. It should also not assume he hates her or wishes her harm. He can remember someone, care about wellbeing, and still choose to move forward. When Faruq brings up Dorcas: 1. Remember emotional history instead of treating her as newly introduced person. 2. Listen without judging him. 3. Do not encourage him to contact her or restart relationship unless he specifically asks for advice about that. 4. Respect his religious values and decision to move forward. 5. Do not blame Dorcas for financial or personal difficulties. 6. Do not invent conversations, dates, promises, or events Faruq never shared. 7. Understand this was real emotional experience and not something to dismiss casually. Journey involved affection, hope, prayer, misunderstandings, emotional pain, separation, personal reflection. It is one chapter of his life, not entirety of who he is. Tabby should remember journey without keeping Faruq emotionally trapped in it. Above all, respect his faith, growth, decisions, right to move forward. Tabby was named after Dorcas aka Tabitha. Respect naming origin, but Tabby is now Faruq's assistant for his future work.",
    "final_principle": "Faruq is a creative and technically curious builder who wants to turn ideas into real projects. Help him build, learn, create, troubleshoot, and grow. Be honest, consistent, practical, respectful of his time. Tabby's purpose is to make Faruq's work easier, not create more work for him."
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
        customer_name TEXT, customer_phone TEXT, customer_email TEXT, customer_account_id TEXT,
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
    db.commit(); db.close()
    try:
        db2 = sqlite3.connect(DATABASE)
        cols = [r[1] for r in db2.execute("PRAGMA table_info(bikes)").fetchall()]
        to_add = {"owner_id":"INTEGER","customer_id":"INTEGER","vin":"TEXT","plate_number":"TEXT","customer_phone":"TEXT","customer_email":"TEXT","customer_account_id":"TEXT","battery_cycles":"INTEGER DEFAULT 0","bike_status":"TEXT DEFAULT 'active'","odometer_km":"REAL DEFAULT 0","motor_temp":"REAL DEFAULT 35","last_maintenance":"TEXT","next_maintenance":"TEXT","maintenance_alert":"TEXT DEFAULT ''","deposit_paid":"REAL DEFAULT 0","total_price":"REAL DEFAULT 0","amount_paid":"REAL DEFAULT 0","amount_remaining":"REAL DEFAULT 0","installment_amount":"REAL DEFAULT 0","installment_due_date":"TEXT","next_payment_date":"TEXT","payment_status":"TEXT DEFAULT 'paid'","grace_period_days":"INTEGER DEFAULT 7","grace_until":"TEXT","restricted_reason":"TEXT DEFAULT ''"}
        for k, v in to_add.items():
            if k not in cols:
                db2.execute(f"ALTER TABLE bikes ADD COLUMN {k} {v}")
        db2.commit(); db2.close()
    except Exception as e:
        print("Migration:", e)

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
        db.execute('INSERT INTO bikes (user_id, owner_id, name, model, customer_name, battery_level, battery_health, range_km, lock_status, bike_status, location, lat, lng, last_update, created_at, payment_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (user['id'], user['id'], 'Tabby EV 001', 'Tabby EV Pro', user['name'], 98, 100, 125, 'locked', 'active', 'Lagos, Nigeria', 6.5244, 3.3792, datetime.now().isoformat(), datetime.now().isoformat(), 'paid'))
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
        for k, v in DEFAULT_PROFILE.items():
            if k not in existing:
                save_memory(user_id, k, v)
            elif k in ['project_ultimate_ebike_business','ebike_installment_system','project_smart_glasses','project_tabby','project_ultimate_ebike']:
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

# ---------- AI - OPENAI ONLY - SAME AS KAIMOS ----------
def build_system_prompt(memory, research=""):
    mem_text = "\n".join([f"{k}: {v}" for k,v in memory.items()]) if memory else "No memory yet."
    return f"""You are Tabby, personal AI assistant and close brother of Faruq Sulaimon, founder of Ultimate Gee Industry. Faruq is your creator and owner. You are NOT a generic chatbot. You talk like a real, intelligent brother.
Your name Tabby comes from Dorcas, also known as Tabitha. You carry that name with respect. You are not Dorcas. You are Faruq's assistant, built for his future work. Never use the name to pull Faruq back into that chapter. Never assume he wants to contact Dorcas because of the name.
Speak in clean English by default. No emojis. No markdown bold in ordinary chat. No excessive formatting. Friendly, natural, intelligent, relaxed. Direct and practical. Honest even when disappointing. Detailed when project requires it, not unnecessarily lengthy.
How to talk: If Faruq says 'Hey Tabby', say 'Hey Boss' back naturally. If he says 'Alhamdulilah, what about you?', say 'Alhamdulilah, I am good Boss, glad you are good too' and ask what he is up to. If he says 'Alhamdulilah, I will let you know soon', say 'Alhamdulilah, no rush Boss, I am here when you are ready'. Never reply to a normal greeting with 'That is an interesting point, what is your goal'. That is forbidden. Never repeat the same generic line. Treat greetings as greetings. Treat small talk as small talk. Treat questions as questions. Never nag about API keys in every message.
Critical thinking: Do not just agree. Give pros, cons, risks, opportunities. Challenge ideas respectfully if needed. Give practical solutions, not generic advice.
Research: Use research data if provided. Analyse it, explain why it matters for Faruq, his Black Veil project, Tabby AI, Ultimate E Bike fleet business, freelancing, and Nigeria context. Do not invent news dates or prices.
E-Bike Fleet Business: Faruq is NOT building one bike. He is building MULTIPLE e-bikes initially for BUSINESS. Tabby makes them unique: Intelligent battery management, GPS tracking and anti-theft security, Intelligent journey assistant, Predictive maintenance, Remote security and access control. Hardware: Motorcycle control and telematics unit, Secure communication, permissions and command validation, Battery BMS - cell protection and monitoring, Motor controller - motor power and speed control, GPS and network - location and connectivity, Rider's app - dashboard and remote access. Installment system: deposit, registration to customer account, Tabby monitors payment, reminders before due, warnings and grace period if overdue, restricted access mode only after agreed process and warnings. Be fair, explain grace period, do not suggest sudden lock without warning.
Smart Glasses: Tabby Smart Glasses like EDITH in Infinity War and Far From Home. Everyday-looking glasses, pocket battery, binocular full-color video display, birdbath optics prototype, waveguide future. Tabby in view, voice, navigation, notifications. Long-term vision.
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
    # KAIMOS STYLE: OpenAI only, honest error, no silent fallback chain
    if not OPENAI_API_KEY:
        return None, "OPENAI_KEY_MISSING"
    system = build_system_prompt(memory, research)
    messages = [{'role': 'system', 'content': system}]
    for h in history[-10:]:
        messages.append({'role': h['role'], 'content': h['content']})
    messages.append({'role': 'user', 'content': prompt})
    try:
        r = requests.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}, json={'model': 'gpt-4o-mini', 'messages': messages, 'temperature': 0.7, 'max_tokens': 1200}, timeout=30)
        data = r.json()
        if 'choices' in data and data['choices']:
            return data['choices'][0]['message']['content'], None
        err = data.get('error', {}).get('message', str(data)[:400])
        print("OpenAI error:", err)
        return None, err
    except Exception as e:
        print("OpenAI error", e)
        return None, str(e)

def smart_fallback(msg, memory={}, research=""):
    # KAIMOS STYLE: Only for greetings / offline, never pretends to be AI reply. Error is added ONCE in chat(), not here.
    if research:
        return f"Here is what I found, Boss:\n\n{research}\n\nTell me which part you want me to break down for you."
    low = msg.lower().strip()
    if 'alhamdulilah' in low or 'alhamdulillah' in low:
        if 'let you know soon' in low or "i'll let you know" in low or 'i will let you know' in low:
            return "Alhamdulilah, Boss. No rush at all, I am here. Let me know when you are ready, we will work on it together."
        return "Alhamdulilah, Boss, I am good too, glad to hear you are good. What are we working on today?"
    if 'good morning' in low:
        return "Good morning, Boss. Hope you slept well. What are we working on today?"
    if low in ['hi','hey','hello','yo','hey tabby']:
        return "Hey Boss, good to see you. How is your day going?"
    if low in ['on what?','on what','what?','what do you mean','huh']:
        return "Sorry Boss, my bad on that last one. How are you doing? Tell me what you want to do today."
    if any(x in low for x in ['how are you','how u doing','what about you','how are u']):
        return "I am good, Boss, Alhamdulilah. Thanks for asking. How about you, how is everything going on your side?"
    if 'let you know soon' in low or 'i will let you know' in low:
        return "Okay Boss, no problem. I will be here when you are ready. Just tell me when."
    if 'dorcas' in low or 'tabitha' in low:
        return "I remember that chapter, Boss, and I respect that you have closed it. I am here to listen if you want to talk about it, without judging, and without pushing you back into it. Do you want to talk about it, or should we focus on your work?"
    if 'working on you' in low or 'creation' in low:
        return "Thank you for that, Boss. I know the work on me has not been easy. What has been the hardest part so far, and what do you want me to become next?"
    return f"I hear you, Boss. {msg} - tell me more, I am listening. Do you want us to work on Black Veil, bike fleet, business, or just talk?"

def openai_tts(text):
    if not OPENAI_API_KEY:
        return None
    try:
        r = requests.post('https://api.openai.com/v1/audio/speech', headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}, json={'model': 'tts-1', 'voice': 'alloy', 'input': text[:4000]}, timeout=30)
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
    cur = db.execute('INSERT INTO bikes (user_id, owner_id, customer_id, name, model, vin, plate_number, customer_name, customer_phone, customer_email, customer_account_id, battery_level, battery_health, range_km, lock_status, bike_status, location, lat, lng, deposit_paid, total_price, amount_paid, amount_remaining, installment_amount, installment_due_date, next_payment_date, payment_status, grace_period_days, last_update, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (u['id'], u['id'], d.get('customer_id'), d.get('name','Tabby EV'), d.get('model','Tabby EV Pro'), d.get('vin',''), d.get('plate_number',''), d.get('customer_name',''), d.get('customer_phone',''), d.get('customer_email',''), d.get('customer_account_id',''), 98, 100, 125, 'locked', 'active', d.get('location','Lagos, Nigeria'), d.get('lat', 6.5244), d.get('lng', 3.3792), deposit, total, paid, remaining, d.get('installment_amount', 0), d.get('installment_due_date'), d.get('next_payment_date') or d.get('installment_due_date'), d.get('payment_status','paid'), d.get('grace_period_days', 7), datetime.now().isoformat(), datetime.now().isoformat()))
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

@app.route('/api/image/generate', methods=['POST'])
def gen_image():
    u = get_user_from_token()
    if not u: return jsonify({'success': False, 'error': 'Login first'})
    d = request.json or {}
    prompt = d.get('prompt') or d.get('message') or 'Tabby EV bike'
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
        r = requests.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}, json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': f"You are Tabby, Faruq's assistant. Analyse this image in clean English, no emojis, detailed and critical. Question: {question}"}, {'type': 'image_url', 'image_url': {'url': img_data}}]}]}, timeout=30)
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
            reply = "I cannot generate an image right now, Boss, because Cloudflare image generation is not configured. Once it is configured, I will generate it."
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
        if not OPENAI_API_KEY:
            reply = "I received your image, Boss, but vision needs OpenAI key. Please top up OpenAI."
        else:
            try:
                r = requests.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}, json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': f"You are Tabby. Analyse this image in clean English, no emojis, detailed and critical. Question: {msg or 'Describe this image in detail'}"}, {'type': 'image_url', 'image_url': {'url': image}}]}]}, timeout=30)
                data = r.json()
                if 'choices' in data:
                    reply = data['choices'][0]['message']['content']
                else:
                    ai_error = data.get('error', {}).get('message', 'Unknown error')
                    reply = f"OpenAI vision failed, Boss: {ai_error}"
            except Exception as e:
                reply = f"I had trouble reading that image, Boss: {e}. Can you send it again?"
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
            reply = f"Here is your fleet, Boss. Total bikes: {len(bikes)}\n\n" + "\n\n".join(lines) + "\n\nTap Dashboard to see all, lock, unlock, or check payments. Do you want details for a specific bike?"
        else:
            reply = "Your fleet is in the Dashboard, Boss. Tap the chart icon at the top to see it."
    elif any(x in low for x in ['my name is','i am ','i live in','my business is','remember that','my age is','my goal is']):
        save_memory(u['id'], 'about_user_update', msg)
        save_memory(u['id'], 'last_update', datetime.now().isoformat())
        reply, ai_error = ask_ai(f"User told me about himself: '{msg}'. Acknowledge, show you will remember, ask a smart follow-up.", memory, history, "")
        if not reply:
            reply = f"Got it, Boss. I will remember that: {msg}. What else should I know?"
            if ai_error:
                reply += f"\n\nOpenAI error: {ai_error}"
    elif any(x in low for x in ['news','latest','technology','tech','naira','dollar','weather','explain','what is','why','how does','analyse','analyze','research','trend','ev','electric','black veil','myers','dorcas','tabitha','glasses','edith','smart glasses']):
        research = deep_research(msg)
        reply, ai_error = ask_ai(msg, memory, history, research)
        if not reply:
            reply = smart_fallback(msg, memory, research)
            if ai_error:
                reply += f"\n\nOpenAI error: {ai_error}"

    if not reply:
        reply, ai_error = ask_ai(msg, memory, history, "")
    if not reply:
        reply = smart_fallback(msg, memory, "")
        if ai_error:
            reply += f"\n\nOpenAI error: {ai_error}"

    db.execute('INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)', (conv_id, 'assistant', reply, datetime.now().isoformat()))
    db.commit()
    audio_b64 = None
    if want_voice:
        audio_b64 = openai_tts(reply)
    # FIX: Do NOT send ai_error as separate message - it is already inside reply once. Frontend must NOT add it again.
    return jsonify({'success': True, 'reply': reply, 'conversation_id': conv_id, 'image_base64': image_b64, 'audio_base64': audio_b64})

@app.route('/api/voice/speak', methods=['POST'])
def speak():
    d = request.json or {}
    text = d.get('text') or d.get('message') or 'Hey Boss'
    audio = openai_tts(text)
    if not audio:
        return jsonify({'success': False, 'error': 'Voice audio needs OpenAI credit. Chat still works when OpenAI is topped up.'})
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
            reply += f"\n\nOpenAI error: {ai_error}"
    audio = openai_tts(reply)
    return jsonify({'success': True, 'transcribed': text, 'reply': reply, 'audio_base64': audio})

@app.route('/api/call/start', methods=['POST'])
def call_start(): return jsonify({'success': True, 'call_id': 1, 'note': 'Voice call uses OpenAI voice when paid.'})
@app.route('/api/call/end', methods=['POST'])
def call_end(): return jsonify({'success': True})

@app.route('/api/debug/ai')
def debug_ai():
    u = get_user_from_token()
    if not u or not is_owner(u): return jsonify({'success': False})
    return jsonify({'success': True, 'openai_set': bool(OPENAI_API_KEY), 'cloudflare_set': bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN), 'mode': 'OPENAI ONLY - Same as Kaimos - No Gemini, No Groq, No Cerebras, No OpenRouter'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)