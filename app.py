
import os
import re
import json
import time
import uuid
import sqlite3
import logging
import secrets
import hashlib
import base64
import xml.etree.ElementTree as ET

from pathlib import Path
from datetime import datetime, timezone
from functools import wraps

import requests
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATABASE_PATH = Path(
    os.environ.get("DATABASE_PATH", str(BASE_DIR / "tabby_memory.db"))
).expanduser()

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()

if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    logging.warning(
        "SECRET_KEY is not configured. Set a permanent SECRET_KEY in Render."
    )

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "openrouter/free"
).strip()

OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "").strip().lower()

BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
GNEWS_API_KEY = os.environ.get("GNEWS_API_KEY", "").strip()

CLOUDFLARE_ACCOUNT_ID = os.environ.get(
    "CLOUDFLARE_ACCOUNT_ID", ""
).strip()

CLOUDFLARE_API_TOKEN = os.environ.get(
    "CLOUDFLARE_API_TOKEN", ""
).strip()

DEVICE_INGEST_KEY = os.environ.get("DEVICE_INGEST_KEY", "").strip()

LIVE_SEARCH_ENABLED = (
    os.environ.get("LIVE_SEARCH_ENABLED", "true").lower()
    in ("1", "true", "yes", "on")
)

TOKEN_MAX_AGE = 60 * 60 * 24 * 30

MAX_MESSAGE_LENGTH = 12000
MAX_MEMORY_LENGTH = 3000

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)

logger = logging.getLogger("tabby")

app = Flask(__name__)

app.config["SECRET_KEY"] = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["JSON_SORT_KEYS"] = False

allowed_origins = [
    origin.strip()
    for origin in os.environ.get("FRONTEND_ORIGINS", "*").split(",")
    if origin.strip()
]

CORS(
    app,
    resources={r"/*": {"origins": allowed_origins or ["*"]}},
    supports_credentials=False,
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
)

serializer = URLSafeTimedSerializer(
    SECRET_KEY,
    salt="tabby-auth-v1"
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def json_response(data, status=200):
    return jsonify(data), status


def get_json():
    return request.get_json(silent=True) or {}


def clean_text(value, maximum=MAX_MESSAGE_LENGTH):
    if value is None:
        return ""

    return str(value).strip()[:maximum]


def safe_json(value, fallback=None):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback if fallback is not None else {}


def hash_password(password):
    salt = secrets.token_hex(16)

    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        260000
    ).hex()

    return f"{salt}${hashed}"


def verify_password(password, stored):
    try:
        salt, expected = stored.split("$", 1)

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            260000
        ).hex()

        return secrets.compare_digest(actual, expected)

    except (ValueError, AttributeError):
        return False


def create_token(user):
    return serializer.dumps({
        "user_id": user["id"],
        "email": user["email"],
        "role": user["role"]
    })


def get_authenticated_user():
    authorization = request.headers.get("Authorization", "").strip()

    if not authorization.startswith("Bearer "):
        return None

    token = authorization[7:].strip()

    if not token:
        return None

    try:
        payload = serializer.loads(
            token,
            max_age=TOKEN_MAX_AGE
        )

        user_id = payload.get("user_id")

        if not user_id:
            return None

        with get_db() as db:
            user = db.execute(
                """
                SELECT id, name, email, role, created_at
                FROM users
                WHERE id = ?
                """,
                (user_id,)
            ).fetchone()

        if not user:
            return None

        return dict(user)

    except (BadSignature, SignatureExpired):
        return None

    except Exception:
        logger.exception("Authentication verification failed")
        return None


def auth_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        user = get_authenticated_user()

        if not user:
            return json_response({
                "error": "Authentication required.",
                "message": "Please sign in again."
            }, 401)

        g.current_user = user

        return function(*args, **kwargs)

    return decorated


def owner_required(function):
    @wraps(function)
    @auth_required
    def decorated(*args, **kwargs):
        if g.current_user["role"] != "owner":
            return json_response({
                "error": "Owner access required."
            }, 403)

        return function(*args, **kwargs)

    return decorated


def current_user():
    return getattr(g, "current_user", None)


# ============================================================
# DATABASE
# ============================================================

def get_db():
    if "db" not in g:
        connection = sqlite3.connect(
            str(DATABASE_PATH),
            timeout=30
        )

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")

        g.db = connection

    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)

    if db is not None:
        if exception:
            db.rollback()

        db.close()


def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        str(DATABASE_PATH),
        timeout=30
    )

    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'customer',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT 'New Chat',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, content)
        );

        CREATE TABLE IF NOT EXISTS motorcycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            assigned_user_id INTEGER,
            name TEXT NOT NULL,
            model TEXT,
            registration_number TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(owner_id) REFERENCES users(id),
            FOREIGN KEY(assigned_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motorcycle_id INTEGER NOT NULL,
            battery_level REAL,
            speed REAL,
            extra_data TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motorcycle_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'NGN',
            reference TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_conversations_user
        ON conversations(user_id);

        CREATE INDEX IF NOT EXISTS idx_messages_conversation
        ON messages(conversation_id);

        CREATE INDEX IF NOT EXISTS idx_memories_user
        ON memories(user_id);

        CREATE INDEX IF NOT EXISTS idx_motorcycles_owner
        ON motorcycles(owner_id);

        CREATE INDEX IF NOT EXISTS idx_motorcycles_assigned
        ON motorcycles(assigned_user_id);

        CREATE INDEX IF NOT EXISTS idx_telemetry_motorcycle
        ON telemetry(motorcycle_id);

        CREATE INDEX IF NOT EXISTS idx_payments_motorcycle
        ON payments(motorcycle_id);
        """)

        connection.commit()

        logger.info("Tabby database initialized successfully.")

    except Exception:
        connection.rollback()
        logger.exception("Database initialization failed")
        raise

    finally:
        connection.close()


try:
    init_db()
    DATABASE_READY = True
except Exception:
    DATABASE_READY = False
    logger.exception("Tabby started without a ready database.")


# ============================================================
# USER HELPERS
# ============================================================

def user_public(user):
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "created_at": user["created_at"]
    }


def find_user_by_email(email):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE email = ?",
            (email.lower(),)
        ).fetchone()

    return dict(row) if row else None


def register_user(name, email, password):
    name = clean_text(name, 120)
    email = clean_text(email, 254).lower()

    if not name:
        return None, "Please enter your name."

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return None, "Enter a valid email address."

    if len(password) < 8:
        return None, "Password must contain at least 8 characters."

    role = "owner" if OWNER_EMAIL and email == OWNER_EMAIL else "customer"

    try:
        with get_db() as db:
            cursor = db.execute(
                """
                INSERT INTO users
                (name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    name,
                    email,
                    hash_password(password),
                    role,
                    utc_now()
                )
            )

            user_id = cursor.lastrowid
            db.commit()

        user = {
            "id": user_id,
            "name": name,
            "email": email,
            "role": role,
            "created_at": utc_now()
        }

        return user, None

    except sqlite3.IntegrityError:
        return None, "An account with this email already exists."

    except Exception:
        logger.exception("Registration failed")
        return None, "Account registration failed."


# ============================================================
# AUTHENTICATION ROUTES
# ============================================================

@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
def register():
    data = get_json()

    user, error = register_user(
        data.get("name"),
        data.get("email"),
        data.get("password", "")
    )

    if error:
        return json_response({"error": error}, 400)

    return json_response({
        "message": "Account created successfully.",
        "token": create_token(user),
        "user": user_public(user)
    }, 201)


@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = get_json()

    email = clean_text(data.get("email"), 254).lower()
    password = data.get("password", "")

    user = find_user_by_email(email)

    if not user or not verify_password(password, user["password_hash"]):
        return json_response({
            "error": "Invalid email or password."
        }, 401)

    return json_response({
        "message": "Login successful.",
        "token": create_token(user),
        "user": user_public(user)
    })


@app.route("/me", methods=["GET"])
@app.route("/api/me", methods=["GET"])
@auth_required
def me():
    return json_response({
        "user": user_public(current_user())
    })


@app.route("/logout", methods=["POST"])
@app.route("/api/logout", methods=["POST"])
@auth_required
def logout():
    return json_response({
        "message": "Signed out successfully."
    })


# ============================================================
# OPENROUTER AI
# ============================================================

def openrouter_chat(messages, temperature=0.7):
    if not OPENROUTER_API_KEY:
        return None, "OPENROUTER_API_KEY is not configured."

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get(
            "APP_URL", "https://tabby-ai.onrender.com"
        ),
        "X-Title": "Tabby AI"
    }

    models = [OPENROUTER_MODEL]

    if OPENROUTER_MODEL != "openrouter/free":
        models.append("openrouter/free")

    last_error = "The AI service could not respond."

    for model in models:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }

        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=100
            )

            if response.status_code == 429:
                last_error = "The AI service is currently rate limited."
                continue

            if response.status_code >= 400:
                logger.error(
                    "OpenRouter returned %s: %s",
                    response.status_code,
                    response.text[:1000]
                )
                last_error = "The AI service returned an error."
                continue

            data = response.json()
            choices = data.get("choices", [])

            if choices:
                content = choices[0].get("message", {}).get("content", "")

                if isinstance(content, list):
                    content = "\n".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict)
                    )

                content = clean_text(content, 30000)

                if content:
                    return content, None

            last_error = "The AI service returned an empty response."

        except requests.RequestException:
            logger.exception("OpenRouter request failed")
            last_error = "Could not connect to the AI service."

        except Exception:
            logger.exception("OpenRouter response processing failed")
            last_error = "Could not process the AI response."

    return None, last_error


def tabby_system_prompt(user, memories=None, search_context=""):
    memories = memories or []

    memory_text = "\n".join(
        f"- {memory}" for memory in memories[:30]
    )

    prompt = f"""
You are Tabby, a friendly, intelligent personal AI assistant.

Your owner is Faruq. Address him naturally and respectfully.
Your voice should be warm, conversational, confident and helpful.
Do not sound robotic or unnecessarily formal.

You can help with research, coding, business, planning,
technology, automotive systems and everyday questions.

Be honest about what you know. Never claim to have performed
an action that you did not actually perform.

When discussing current events, distinguish verified information
from uncertainty. Cite useful source URLs when search results
are supplied to you.

The authenticated account is:
Name: {user.get("name")}
Role: {user.get("role")}

Relevant saved memories:
{memory_text or "No saved memories available."}

Live information:
{search_context or "No live search results were retrieved."}

Use the live information when it is relevant to the question.
Do not invent news articles, sources, URLs, prices or statistics.
"""

    return prompt.strip()


# ============================================================
# LIVE WEB SEARCH AND NEWS
# ============================================================

def brave_search(query, maximum=5):
    if not BRAVE_SEARCH_API_KEY:
        return []

    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={
                "q": query,
                "count": maximum
            },
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": BRAVE_SEARCH_API_KEY
            },
            timeout=15
        )

        response.raise_for_status()

        results = response.json().get("web", {}).get("results", [])

        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", "")
            }
            for item in results[:maximum]
        ]

    except Exception:
        logger.exception("Brave search failed")
        return []


def duckduckgo_search(query, maximum=5):
    try:
        from ddgs import DDGS

        results = DDGS().text(
            query,
            max_results=maximum
        )

        return [
            {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "snippet": item.get("body", "")
            }
            for item in results
        ]

    except ImportError:
        logger.warning("DDGS package is unavailable.")

    except Exception:
        logger.exception("DuckDuckGo search failed")

    return []


def live_search(query, maximum=5):
    if not LIVE_SEARCH_ENABLED:
        return []

    query = clean_text(query, 500)

    if not query:
        return []

    results = brave_search(query, maximum)

    if not results:
        results = duckduckgo_search(query, maximum)

    return results[:maximum]


def google_news_search(query, maximum=8):
    try:
        response = requests.get(
            "https://news.google.com/rss/search",
            params={
                "q": query,
                "hl": "en-NG",
                "gl": "NG",
                "ceid": "NG:en"
            },
            timeout=15
        )

        response.raise_for_status()

        root = ET.fromstring(response.content)
        results = []

        for item in root.findall(".//item")[:maximum]:
            results.append({
                "title": item.findtext("title", ""),
                "url": item.findtext("link", ""),
                "snippet": item.findtext("description", ""),
                "published_at": item.findtext("pubDate", ""),
                "source": "Google News"
            })

        return results

    except Exception:
        logger.exception("Google News RSS failed")
        return []


def gnews_search(query, maximum=8):
    if not GNEWS_API_KEY:
        return []

    try:
        response = requests.get(
            "https://gnews.io/api/v4/search",
            params={
                "q": query,
                "lang": "en",
                "max": maximum,
                "token": GNEWS_API_KEY
            },
            timeout=15
        )

        response.raise_for_status()

        articles = response.json().get("articles", [])

        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
                "published_at": item.get("publishedAt", ""),
                "source": item.get("source", {}).get("name", "GNews")
            }
            for item in articles[:maximum]
        ]

    except Exception:
        logger.exception("GNews request failed")
        return []


def live_news(query="latest news", maximum=8):
    if not LIVE_SEARCH_ENABLED:
        return []

    results = gnews_search(query, maximum)

    if not results:
        results = google_news_search(query, maximum)

    return results[:maximum]


def format_search_context(results):
    if not results:
        return ""

    sections = []

    for index, item in enumerate(results, 1):
        sections.append(
            f"{index}. {item.get('title', 'Untitled')}\n"
            f"URL: {item.get('url', '')}\n"
            f"Information: {item.get('snippet', '')}\n"
            f"Date: {item.get('published_at', 'Not supplied')}"
        )

    return "\n\n".join(sections)


def should_search_web(message):
    text = message.lower()

    keywords = (
        "latest", "today", "current", "currently", "right now",
        "breaking", "news", "recent", "this week", "yesterday",
        "price today", "exchange rate", "live score", "who won",
        "2026", "2025", "weather", "search the web", "look online",
        "find online", "what happened"
    )

    return any(keyword in text for keyword in keywords)


# ============================================================
# CONVERSATIONS AND CHAT
# ============================================================

def get_user_memories(user_id, maximum=30):
    with get_db() as db:
        rows = db.execute(
            """
            SELECT content
            FROM memories
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, maximum)
        ).fetchall()

    return [row["content"] for row in rows]


def get_or_create_conversation(user_id, conversation_id, message):
    with get_db() as db:
        if conversation_id:
            conversation = db.execute(
                """
                SELECT id
                FROM conversations
                WHERE id = ? AND user_id = ?
                """,
                (conversation_id, user_id)
            ).fetchone()

            if conversation:
                return conversation["id"]

        title = clean_text(message, 55) or "New Chat"

        cursor = db.execute(
            """
            INSERT INTO conversations
            (user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, title, utc_now(), utc_now())
        )

        db.commit()
        return cursor.lastrowid


def save_message(conversation_id, user_id, role, content):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO messages
            (conversation_id, user_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                user_id,
                role,
                clean_text(content, 30000),
                utc_now()
            )
        )

        db.execute(
            """
            UPDATE conversations
            SET updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (utc_now(), conversation_id, user_id)
        )

        db.commit()


def get_chat_history(conversation_id, maximum=20):
    with get_db() as db:
        rows = db.execute(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, maximum)
        ).fetchall()

    return [
        {"role": row["role"], "content": row["content"]}
        for row in reversed(rows)
    ]


def maybe_save_memory(user_id, message):
    """
    Lightweight automatic memory extraction.
    Only saves explicit remember/preference statements.
    """

    text = clean_text(message, MAX_MEMORY_LENGTH)
    lowered = text.lower()

    triggers = (
        "remember that",
        "remember this",
        "my preference is",
        "i prefer",
        "i always",
        "i work as",
        "my project is",
        "my business is"
    )

    if not any(trigger in lowered for trigger in triggers):
        return False

    with get_db() as db:
        try:
            db.execute(
                """
                INSERT OR IGNORE INTO memories
                (user_id, content, created_at)
                VALUES (?, ?, ?)
                """,
                (user_id, text, utc_now())
            )

            db.commit()
            return True

        except Exception:
            logger.exception("Automatic memory saving failed")
            return False


@app.route("/chat", methods=["POST"])
@app.route("/api/chat", methods=["POST"])
@owner_required
def chat():
    data = get_json()

    message = clean_text(data.get("message"))

    if not message:
        return json_response({
            "error": "Please enter a message."
        }, 400)

    if len(message) > MAX_MESSAGE_LENGTH:
        return json_response({
            "error": "Your message is too long."
        }, 400)

    user = current_user()

    conversation_id = get_or_create_conversation(
        user["id"],
        data.get("conversation_id"),
        message
    )

    save_message(
        conversation_id,
        user["id"],
        "user",
        message
    )

    web_search_setting = data.get("web_search", None)

    search_results = []

    if web_search_setting is True:
        search_results = live_search(message, 6)

    elif web_search_setting is None and should_search_web(message):
        search_results = live_search(message, 6)

    memories = get_user_memories(user["id"])

    system_message = tabby_system_prompt(
        user,
        memories,
        format_search_context(search_results)
    )

    history = get_chat_history(conversation_id)

    messages = [
        {"role": "system", "content": system_message}
    ] + history

    reply, error = openrouter_chat(messages)

    if error:
        return json_response({
            "error": error,
            "conversation_id": conversation_id
        }, 502)

    save_message(
        conversation_id,
        user["id"],
        "assistant",
        reply
    )

    memory_saved = maybe_save_memory(user["id"], message)

    return json_response({
        "reply": reply,
        "response": reply,
        "conversation_id": conversation_id,
        "memories": get_user_memories(user["id"]),
        "memory_saved": memory_saved,
        "live_search_used": bool(search_results),
        "search_results": search_results,
        "search_result_count": len(search_results)
    })


# ============================================================
# CONVERSATION ROUTES
# ============================================================

@app.route("/api/conversations", methods=["GET"])
@owner_required
def list_conversations():
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (current_user()["id"],)
        ).fetchall()

    return json_response({
        "conversations": [dict(row) for row in rows]
    })


@app.route("/api/conversations", methods=["POST"])
@owner_required
def create_conversation():
    data = get_json()
    title = clean_text(data.get("title"), 100) or "New Chat"
    now = utc_now()

    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO conversations
            (user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (current_user()["id"], title, now, now)
        )

        db.commit()

    return json_response({
        "conversation_id": cursor.lastrowid,
        "title": title
    }, 201)


@app.route("/api/conversations/<int:conversation_id>", methods=["GET"])
@owner_required
def get_conversation(conversation_id):
    user_id = current_user()["id"]

    with get_db() as db:
        conversation = db.execute(
            """
            SELECT *
            FROM conversations
            WHERE id = ? AND user_id = ?
            """,
            (conversation_id, user_id)
        ).fetchone()

        if not conversation:
            return json_response({
                "error": "Conversation not found."
            }, 404)

        messages = db.execute(
            """
            SELECT id, role, content, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,)
        ).fetchall()

    return json_response({
        "conversation": dict(conversation),
        "messages": [dict(row) for row in messages]
    })


@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
@owner_required
def delete_conversation(conversation_id):
    with get_db() as db:
        cursor = db.execute(
            """
            DELETE FROM conversations
            WHERE id = ? AND user_id = ?
            """,
            (conversation_id, current_user()["id"])
        )

        db.commit()

    if cursor.rowcount == 0:
        return json_response({
            "error": "Conversation not found."
        }, 404)

    return json_response({"message": "Conversation deleted."})


# ============================================================
# NEWS ROUTE
# ============================================================

@app.route("/api/news", methods=["GET"])
@auth_required
def news():
    query = clean_text(request.args.get("q", "latest news"), 300)

    results = live_news(query)

    return json_response({
        "query": query,
        "articles": results,
        "results": results,
        "count": len(results)
    })


# ============================================================
# IMAGE GENERATION
# ============================================================

@app.route("/api/image", methods=["POST"])
@app.route("/image", methods=["POST"])
@auth_required
def generate_image():
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        return json_response({
            "error": "Image generation is not configured. Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN."
        }, 503)

    data = get_json()
    prompt = clean_text(data.get("prompt"), 2000)

    if not prompt:
        return json_response({
            "error": "Please provide an image description."
        }, 400)

    endpoint = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/"
        "@cf/black-forest-labs/flux-1-schnell"
    )

    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"
            },
            json={
                "prompt": prompt
            },
            timeout=120
        )

        if response.status_code >= 400:
            logger.error(
                "Cloudflare image error: %s",
                response.text[:1000]
            )

            return json_response({
                "error": "The image service could not generate your image.",
                "details": response.text[:500]
            }, 502)

        content_type = response.headers.get(
            "Content-Type", "image/png"
        )

        image_bytes = response.content

        if not image_bytes:
            return json_response({
                "error": "The image service returned an empty image."
            }, 502)

        encoded = base64.b64encode(image_bytes).decode("ascii")

        image_url = (
            f"data:{content_type};base64,{encoded}"
        )

        return json_response({
            "image": image_url,
            "image_url": image_url,
            "message": "Image generated successfully."
        })

    except requests.Timeout:
        return json_response({
            "error": "Image generation timed out. Please try again."
        }, 504)

    except Exception:
        logger.exception("Image generation failed")

        return json_response({
            "error": "An image generation error occurred."
        }, 500)


# ============================================================
# MEMORY ROUTES
# ============================================================

@app.route("/api/memory", methods=["GET"])
@owner_required
def list_memories():
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, content, created_at
            FROM memories
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (current_user()["id"],)
        ).fetchall()

    return json_response({
        "memories": [dict(row) for row in rows]
    })


@app.route("/api/memory", methods=["POST"])
@owner_required
def create_memory():
    data = get_json()
    content = clean_text(data.get("content"), MAX_MEMORY_LENGTH)

    if not content:
        return json_response({
            "error": "Memory content is required."
        }, 400)

    with get_db() as db:
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO memories
            (user_id, content, created_at)
            VALUES (?, ?, ?)
            """,
            (current_user()["id"], content, utc_now())
        )

        db.commit()

    return json_response({
        "saved": cursor.rowcount > 0,
        "message": "Memory saved."
    }, 201)


@app.route("/api/memory/<int:memory_id>", methods=["DELETE"])
@owner_required
def remove_memory(memory_id):
    with get_db() as db:
        cursor = db.execute(
            """
            DELETE FROM memories
            WHERE id = ? AND user_id = ?
            """,
            (memory_id, current_user()["id"])
        )

        db.commit()

    if cursor.rowcount == 0:
        return json_response({
            "error": "Memory not found."
        }, 404)

    return json_response({
        "message": "Memory deleted."
    })


# ============================================================
# AUTOMOTIVE STATUS
# ============================================================

@app.route("/api/automotive/status", methods=["GET"])
def automotive_status():
    return json_response({
        "status": "awaiting_hardware_connection",
        "message": "Tabby Automotive backend is online. Awaiting hardware connection.",
        "hardware_connected": False,
        "timestamp": utc_now()
    })


# ============================================================
# MOTORCYCLE ACCESS HELPERS
# ============================================================

def get_motorcycle(motorcycle_id):
    with get_db() as db:
        row = db.execute(
            """
            SELECT *
            FROM motorcycles
            WHERE id = ?
            """,
            (motorcycle_id,)
        ).fetchone()

    return dict(row) if row else None


def user_can_access_motorcycle(user, motorcycle):
    if not user or not motorcycle:
        return False

    if user["role"] == "owner":
        return motorcycle["owner_id"] == user["id"]

    return motorcycle["assigned_user_id"] == user["id"]


def motorcycle_public(vehicle):
    return {
        "id": vehicle["id"],
        "name": vehicle["name"],
        "model": vehicle["model"],
        "registration_number": vehicle["registration_number"],
        "owner_id": vehicle["owner_id"],
        "assigned_user_id": vehicle["assigned_user_id"],
        "created_at": vehicle["created_at"]
    }


# ============================================================
# MOTORCYCLE ROUTES
# ============================================================

@app.route("/api/motorcycles", methods=["GET"])
@auth_required
def list_motorcycles():
    user = current_user()

    with get_db() as db:
        if user["role"] == "owner":
            rows = db.execute(
                """
                SELECT *
                FROM motorcycles
                WHERE owner_id = ?
                ORDER BY id DESC
                """,
                (user["id"],)
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT *
                FROM motorcycles
                WHERE assigned_user_id = ?
                ORDER BY id DESC
                """,
                (user["id"],)
            ).fetchall()

    return json_response({
        "motorcycles": [
            motorcycle_public(dict(row))
            for row in rows
        ]
    })


@app.route("/api/motorcycles", methods=["POST"])
@owner_required
def create_motorcycle():
    data = get_json()

    name = clean_text(data.get("name"), 100)
    model = clean_text(data.get("model"), 120)
    registration = clean_text(
        data.get("registration_number"), 80
    )

    if not name:
        return json_response({
            "error": "Motorcycle name is required."
        }, 400)

    assigned_user_id = data.get("assigned_user_id")

    if assigned_user_id:
        try:
            assigned_user_id = int(assigned_user_id)
        except (ValueError, TypeError):
            return json_response({
                "error": "Invalid customer assignment."
            }, 400)

        with get_db() as db:
            customer = db.execute(
                """
                SELECT id
                FROM users
                WHERE id = ? AND role = 'customer'
                """,
                (assigned_user_id,)
            ).fetchone()

        if not customer:
            return json_response({
                "error": "The selected customer does not exist."
            }, 404)

    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO motorcycles
            (
                owner_id, assigned_user_id, name,
                model, registration_number, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                current_user()["id"],
                assigned_user_id,
                name,
                model,
                registration or None,
                utc_now()
            )
        )

        db.commit()

        vehicle = db.execute(
            "SELECT * FROM motorcycles WHERE id = ?",
            (cursor.lastrowid,)
        ).fetchone()

    return json_response({
        "message": "Motorcycle registered.",
        "motorcycle": motorcycle_public(dict(vehicle))
    }, 201)


@app.route("/api/motorcycles/<int:motorcycle_id>", methods=["DELETE"])
@owner_required
def delete_motorcycle(motorcycle_id):
    with get_db() as db:
        cursor = db.execute(
            """
            DELETE FROM motorcycles
            WHERE id = ? AND owner_id = ?
            """,
            (motorcycle_id, current_user()["id"])
        )

        db.commit()

    if cursor.rowcount == 0:
        return json_response({
            "error": "Motorcycle not found."
        }, 404)

    return json_response({
        "message": "Motorcycle deleted."
    })


# ============================================================
# TELEMETRY
# ============================================================

@app.route("/api/telemetry", methods=["POST"])
def ingest_telemetry():
    if not DEVICE_INGEST_KEY:
        return json_response({
            "error": "Telemetry ingestion is not configured."
        }, 503)

    supplied_key = request.headers.get(
        "X-Device-Key", ""
    ).strip()

    if not secrets.compare_digest(
        supplied_key,
        DEVICE_INGEST_KEY
    ):
        return json_response({
            "error": "Invalid device key."
        }, 401)

    data = get_json()

    try:
        motorcycle_id = int(data.get("motorcycle_id"))
    except (ValueError, TypeError):
        return json_response({
            "error": "A valid motorcycle_id is required."
        }, 400)

    motorcycle = get_motorcycle(motorcycle_id)

    if not motorcycle:
        return json_response({
            "error": "Motorcycle not found."
        }, 404)

    battery = data.get("battery_level")
    speed = data.get("speed")

    try:
        battery = float(battery) if battery is not None else None
        speed = float(speed) if speed is not None else None
    except (ValueError, TypeError):
        return json_response({
            "error": "Invalid telemetry values."
        }, 400)

    extra_data = data.get("extra_data") or {}

    if not isinstance(extra_data, dict):
        return json_response({
            "error": "extra_data must be an object."
        }, 400)

    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO telemetry
            (motorcycle_id, battery_level, speed, extra_data, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                motorcycle_id,
                battery,
                speed,
                json.dumps(extra_data),
                utc_now()
            )
        )

        db.commit()

    return json_response({
        "message": "Telemetry received.",
        "telemetry_id": cursor.lastrowid
    }, 201)


@app.route(
    "/api/motorcycles/<int:motorcycle_id>/telemetry",
    methods=["GET"]
)
@auth_required
def motorcycle_telemetry(motorcycle_id):
    motorcycle = get_motorcycle(motorcycle_id)

    if not user_can_access_motorcycle(
        current_user(), motorcycle
    ):
        return json_response({
            "error": "Motorcycle not found or access denied."
        }, 404)

    with get_db() as db:
        rows = db.execute(
            """
            SELECT *
            FROM telemetry
            WHERE motorcycle_id = ?
            ORDER BY id DESC
            LIMIT 50
            """,
            (motorcycle_id,)
        ).fetchall()

    readings = []

    for row in rows:
        item = dict(row)
        item["extra_data"] = safe_json(item["extra_data"])
        readings.append(item)

    return json_response({
        "motorcycle_id": motorcycle_id,
        "latest": readings[0] if readings else None,
        "readings": readings
    })


# ============================================================
# CUSTOMER MANAGEMENT
# ============================================================

@app.route("/api/customers", methods=["GET"])
@owner_required
def list_customers():
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, name, email, role, created_at
            FROM users
            ORDER BY created_at DESC
            """
        ).fetchall()

    return json_response({
        "customers": [dict(row) for row in rows]
    })


# ============================================================
# PAYMENTS
# ============================================================

def payment_public(row):
    return {
        "id": row["id"],
        "motorcycle_id": row["motorcycle_id"],
        "user_id": row["user_id"],
        "amount": row["amount"],
        "currency": row["currency"],
        "reference": row["reference"],
        "status": row["status"],
        "created_at": row["created_at"]
    }


@app.route("/api/payments", methods=["GET"])
@auth_required
def list_payments():
    user = current_user()

    with get_db() as db:
        if user["role"] == "owner":
            rows = db.execute(
                """
                SELECT p.*
                FROM payments p
                JOIN motorcycles m ON m.id = p.motorcycle_id
                WHERE m.owner_id = ?
                ORDER BY p.id DESC
                """,
                (user["id"],)
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT p.*
                FROM payments p
                JOIN motorcycles m ON m.id = p.motorcycle_id
                WHERE m.assigned_user_id = ?
                ORDER BY p.id DESC
                """,
                (user["id"],)
            ).fetchall()

    return json_response({
        "payments": [
            payment_public(dict(row))
            for row in rows
        ]
    })


@app.route("/api/payments", methods=["POST"])
@owner_required
def create_payment():
    data = get_json()

    try:
        amount = float(data.get("amount"))
        motorcycle_id = int(data.get("motorcycle_id"))
    except (ValueError, TypeError):
        return json_response({
            "error": "A valid amount and motorcycle are required."
        }, 400)

    if amount <= 0 or amount > 100000000000:
        return json_response({
            "error": "Payment amount is outside the permitted range."
        }, 400)

    currency = clean_text(
        data.get("currency", "NGN"), 3
    ).upper()

    if currency not in ("NGN", "USD"):
        return json_response({
            "error": "Currency must be NGN or USD."
        }, 400)

    motorcycle = get_motorcycle(motorcycle_id)

    if not user_can_access_motorcycle(
        current_user(), motorcycle
    ):
        return json_response({
            "error": "Motorcycle not found."
        }, 404)

    reference = "TABBY-" + uuid.uuid4().hex[:16].upper()

    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO payments
            (
                motorcycle_id, user_id, amount,
                currency, reference, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                motorcycle_id,
                current_user()["id"],
                amount,
                currency,
                reference,
                utc_now()
            )
        )

        db.commit()

        row = db.execute(
            "SELECT * FROM payments WHERE id = ?",
            (cursor.lastrowid,)
        ).fetchone()

    return json_response({
        "message": "Pending payment record created.",
        "reference": reference,
        "payment": payment_public(dict(row))
    }, 201)


# ============================================================
# HEALTH AND DIAGNOSTICS
# ============================================================

@app.route("/", methods=["GET"])
def index():
    return json_response({
        "name": "Tabby AI",
        "description": "AI and Automotive Intelligence Platform",
        "status": "online",
        "version": "2.1",
        "health": "/health"
    })


@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health():
    database_status = "ready"

    try:
        with get_db() as db:
            db.execute("SELECT 1").fetchone()
    except Exception:
        database_status = "unavailable"
        logger.exception("Health check database query failed")

    return json_response({
        "status": "ok" if database_status == "ready" else "degraded",
        "service": "Tabby AI",
        "database": database_status,
        "ai_configured": bool(OPENROUTER_API_KEY),
        "live_search_enabled": LIVE_SEARCH_ENABLED,
        "brave_search_configured": bool(BRAVE_SEARCH_API_KEY),
        "news_configured": bool(GNEWS_API_KEY),
        "image_generation_configured": bool(
            CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN
        ),
        "timestamp": utc_now()
    }, 200 if database_status == "ready" else 503)


@app.errorhandler(404)
def not_found(error):
    return json_response({
        "error": "The requested endpoint was not found."
    }, 404)


@app.errorhandler(413)
def request_too_large(error):
    return json_response({
        "error": "The uploaded request is too large."
    }, 413)


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Unhandled server error")

    return json_response({
        "error": "An internal Tabby server error occurred."
    }, 500)


# ============================================================
# STARTUP
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
