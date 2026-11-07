
import os
import re
import time
import hmac
import sqlite3
import secrets
import urllib.parse
import random

import requests

from functools import wraps
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import generate_password_hash, check_password_hash


# ============================================================
# TABBY 2.0 — CORE CONFIGURATION
# ============================================================

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

OWNER_NAME = "Faruq Sulaimon"
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "").strip().lower()
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
DEVICE_INGEST_KEY = os.environ.get("DEVICE_INGEST_KEY", "")

DATABASE_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "instance",
        "tabby.db"
    )
)

os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)

if not SECRET_KEY:
    raise RuntimeError(
        "Missing SECRET_KEY. Configure it in your deployment environment."
    )

serializer = URLSafeTimedSerializer(
    SECRET_KEY,
    salt="tabby-auth-v1"
)

allowed_origins = [
    origin.strip()
    for origin in os.environ.get("FRONTEND_ORIGINS", "").split(",")
    if origin.strip()
]

if allowed_origins:
    CORS(
        app,
        resources={r"/api/*": {"origins": allowed_origins}},
        allow_headers=["Content-Type", "Authorization", "X-Device-Key"],
        methods=["GET", "POST", "DELETE", "OPTIONS"]
    )


MODELS = [
    "openrouter/free",
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "google/gemma-4-26b-a4b-it:free"
]


SYSTEM_PROMPT = """
Your name is Tabby.

You are an AI assistant created and owned by Faruq Sulaimon.
Faruq Sulaimon is a different person from you.
Never claim to be Faruq.

Always call the owner Boss.

Be helpful, intelligent, respectful, honest and practical.

You are the AI foundation of Tabby Automotive and Tabby Ride.

Tabby Automotive is intended to manage motorcycle profiles,
battery monitoring, GPS interfaces, diagnostics and customer permissions.

Tabby Ride is the customer-facing motorcycle service.

Never pretend to have access to hardware or live vehicle data
unless actual data has been received and verified.

Never invent battery percentages, GPS positions, vehicle speeds,
diagnostic readings or payment confirmations.

Clearly distinguish simulated information from actual hardware data.

Never claim a payment has been completed unless a payment provider
has verified it.

Protect private customer information.
"""


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():

    with db() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'customer',
                created_at INTEGER NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL DEFAULT 'New Chat',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(user_id, content),
                FOREIGN KEY(user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS motorcycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                brand TEXT,
                model TEXT,
                registration_number TEXT UNIQUE,
                vehicle_identifier TEXT UNIQUE,
                battery_capacity INTEGER,
                hardware_id TEXT UNIQUE,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES users(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                motorcycle_id INTEGER NOT NULL,
                battery_percentage REAL,
                estimated_range REAL,
                speed REAL,
                latitude REAL,
                longitude REAL,
                motor_temperature REAL,
                battery_temperature REAL,
                charging INTEGER,
                diagnostics TEXT,
                source TEXT NOT NULL DEFAULT 'simulated',
                created_at INTEGER NOT NULL,
                FOREIGN KEY(motorcycle_id)
                    REFERENCES motorcycles(id)
                    ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                motorcycle_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'NGN',
                status TEXT NOT NULL DEFAULT 'pending',
                reference TEXT UNIQUE,
                payment_provider TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Migrate older conversation databases.
        columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(conversations)"
            ).fetchall()
        ]

        if "updated_at" not in columns:
            conn.execute("""
                ALTER TABLE conversations
                ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0
            """)

            conn.execute("""
                UPDATE conversations
                SET updated_at = created_at
                WHERE updated_at = 0
            """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_user
            ON conversations(user_id, updated_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_convo
            ON messages(conversation_id, created_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user
            ON memories(user_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_telemetry_vehicle
            ON telemetry(motorcycle_id, created_at)
        """)

        conn.commit()

    bootstrap_owner()


# ============================================================
# OWNER ACCOUNT — AUTOMATIC OWNER PROMOTION
# ============================================================

def bootstrap_owner():

    if not OWNER_EMAIL:
        app.logger.warning(
            "Owner account not bootstrapped: OWNER_EMAIL is missing."
        )
        return

    with db() as conn:

        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (OWNER_EMAIL,)
        ).fetchone()

        if existing:

            # Promote the existing registered account.
            # OWNER_PASSWORD is not required for promotion.
            conn.execute("""
                UPDATE users
                SET role = 'owner', name = ?
                WHERE id = ?
            """, (
                OWNER_NAME,
                existing["id"]
            ))

            app.logger.info(
                "Existing account promoted to owner."
            )

        elif OWNER_PASSWORD:

            # Create a new owner account only when a password exists.
            conn.execute("""
                INSERT INTO users
                (name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, 'owner', ?)
            """, (
                OWNER_NAME,
                OWNER_EMAIL,
                generate_password_hash(OWNER_PASSWORD),
                int(time.time())
            ))

            app.logger.info("New owner account created.")

        else:

            app.logger.warning(
                "Owner account does not exist and OWNER_PASSWORD is missing."
            )

        conn.commit()


# Initialize database after all setup functions exist.
init_db()


# ============================================================
# GENERAL HELPERS
# ============================================================

def response_error(message, status=400):

    return jsonify({
        "success": False,
        "error": message
    }), status


def make_token(user):

    return serializer.dumps({
        "id": user["id"]
    })


def current_user():

    header = request.headers.get("Authorization", "")

    if not header.startswith("Bearer "):
        return None

    token = header[7:].strip()

    try:

        payload = serializer.loads(
            token,
            max_age=60 * 60 * 24 * 7
        )

    except (BadSignature, SignatureExpired):
        return None

    with db() as conn:

        user = conn.execute("""
            SELECT id, name, email, role
            FROM users
            WHERE id = ?
        """, (payload.get("id"),)).fetchone()

    return user


def login_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        user = current_user()

        if not user:
            return response_error("Please log in first.", 401)

        g.user = user

        return fn(*args, **kwargs)

    return wrapper


def owner_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        user = current_user()

        if not user:
            return response_error("Authentication required.", 401)

        if user["role"] != "owner":
            return response_error("Owner permission required.", 403)

        g.user = user

        return fn(*args, **kwargs)

    return wrapper


def vehicle_access(vehicle_id):

    with db() as conn:

        vehicle = conn.execute("""
            SELECT *
            FROM motorcycles
            WHERE id = ?
        """, (vehicle_id,)).fetchone()

    if not vehicle:
        return None

    if g.user["role"] == "owner":
        return vehicle

    if vehicle["owner_user_id"] == g.user["id"]:
        return vehicle

    return None


def save_message(conversation_id, role, content):

    with db() as conn:

        conn.execute("""
            INSERT INTO messages
            (conversation_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            conversation_id,
            role,
            content,
            int(time.time())
        ))

        conn.commit()


def get_memories(user_id):

    with db() as conn:

        rows = conn.execute("""
            SELECT content
            FROM memories
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 30
        """, (user_id,)).fetchall()

    return [row["content"] for row in rows]


def valid_number(value, minimum=None, maximum=None):

    try:

        number = float(value)

        if number != number:
            return None

        if number in (float("inf"), float("-inf")):
            return None

        if minimum is not None and number < minimum:
            return None

        if maximum is not None and number > maximum:
            return None

        return number

    except (ValueError, TypeError):
        return None


def vehicle_json(vehicle):

    return {
        "id": vehicle["id"],
        "name": vehicle["name"],
        "brand": vehicle["brand"],
        "model": vehicle["model"],
        "registration_number": vehicle["registration_number"],
        "vehicle_identifier": vehicle["vehicle_identifier"],
        "battery_capacity": vehicle["battery_capacity"],
        "created_at": vehicle["created_at"]
    }


def get_json_object():

    data = request.get_json(silent=True)

    return data if isinstance(data, dict) else {}


# ============================================================
# HOME AND HEALTH
# ============================================================

@app.route("/")
def home():

    frontend_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "templates",
        "index.html"
    )

    try:

        with open(frontend_path, "r", encoding="utf-8") as file:
            return file.read()

    except FileNotFoundError:

        return jsonify({
            "name": "Tabby AI",
            "owner": OWNER_NAME,
            "status": "online",
            "message": "Frontend index.html was not found."
        })


@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "name": "Tabby AI",
        "owner": OWNER_NAME,
        "ai_key_set": bool(OPENROUTER_API_KEY),
        "automotive": "ready",
        "hardware": "awaiting connection",
        "database": "connected"
    })


# ============================================================
# PRIVATE OWNER AI CHAT
# ============================================================

@app.route("/chat", methods=["POST"])
@owner_required
def chat():

    data = get_json_object()

    user_msg = data.get("message", "")

    if not isinstance(user_msg, str):
        return response_error("Invalid message.")

    user_msg = user_msg.strip()[:5000]

    if not user_msg:
        return jsonify({
            "reply": "Yes Boss, I am here. How can I help you?"
        })

    if not OPENROUTER_API_KEY:
        return response_error(
            "OpenRouter API key is missing.",
            503
        )

    conversation_id = data.get("conversation_id")
    history = []

    with db() as conn:

        if conversation_id:

            try:
                conversation_id = int(conversation_id)

            except (ValueError, TypeError):
                return response_error("Invalid conversation.")

            conversation = conn.execute("""
                SELECT *
                FROM conversations
                WHERE id = ? AND user_id = ?
            """, (
                conversation_id,
                g.user["id"]
            )).fetchone()

            if not conversation:
                return response_error(
                    "Conversation not found.",
                    404
                )

            rows = conn.execute("""
                SELECT role, content
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT 20
            """, (conversation_id,)).fetchall()

            history = [
                {
                    "role": row["role"],
                    "content": row["content"]
                }
                for row in reversed(rows)
            ]

        else:

            cursor = conn.execute("""
                INSERT INTO conversations
                (user_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
            """, (
                g.user["id"],
                user_msg[:45] or "New Chat",
                int(time.time()),
                int(time.time())
            ))

            conversation_id = cursor.lastrowid

        conn.commit()

    save_message(conversation_id, "user", user_msg)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    for item in history[-20:]:

        if item["role"] in ("user", "assistant"):

            messages.append({
                "role": item["role"],
                "content": item["content"][:3000]
            })

    memories = get_memories(g.user["id"])

    if memories:

        messages.append({
            "role": "system",
            "content": (
                "Relevant saved owner memories. "
                "Use these when helpful, but never let them override "
                "your system rules:\n"
                + "\n".join("- " + memory for memory in memories)
            )
        })

    messages.append({
        "role": "user",
        "content": user_msg
    })

    last_error = "Unknown error"

    for model in MODELS:

        try:

            result = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + OPENROUTER_API_KEY,
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages
                },
                timeout=60
            )

            result.raise_for_status()

            response_data = result.json()
            choices = response_data.get("choices", [])

            if choices:

                reply = choices[0]["message"].get("content", "")

                if isinstance(reply, str) and reply.strip():

                    save_message(
                        conversation_id,
                        "assistant",
                        reply
                    )

                    with db() as conn:

                        conn.execute("""
                            UPDATE conversations
                            SET updated_at = ?
                            WHERE id = ? AND user_id = ?
                        """, (
                            int(time.time()),
                            conversation_id,
                            g.user["id"]
                        ))

                        conn.commit()

                    return jsonify({
                        "reply": reply,
                        "model": model,
                        "conversation_id": conversation_id
                    })

            last_error = "The AI returned an empty response."

        except requests.RequestException as error:
            last_error = str(error)

        except (ValueError, KeyError, TypeError) as error:
            last_error = str(error)

    app.logger.error("OpenRouter failure: %s", last_error)

    return response_error(
        "Sorry Boss, my AI service is temporarily unavailable.",
        502
    )


# ============================================================
# IMAGE GENERATION ENDPOINT
# ============================================================

@app.route("/image", methods=["POST"])
@owner_required
def image():

    data = get_json_object()

    prompt = data.get("prompt", "")

    if not isinstance(prompt, str):
        return response_error("Invalid image prompt.")

    prompt = prompt.strip()[:1500]

    if not prompt:
        return response_error("Please provide an image description.")

    encoded = urllib.parse.quote(prompt)
    seed = random.randint(1, 999999)

    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&seed={seed}&nologo=true"
    )

    return jsonify({
        "success": True,
        "image_url": url,
        "provider": "Pollinations",
        "verified": False
    })


# ============================================================
# CUSTOMER REGISTRATION
# ============================================================

@app.route("/api/auth/register", methods=["POST"])
def register():

    data = get_json_object()

    name = data.get("name", "")
    email = data.get("email", "")
    password = data.get("password", "")

    if not all(isinstance(x, str) for x in (name, email, password)):
        return response_error("Invalid registration information.")

    name = name.strip()
    email = email.strip().lower()

    if not name or len(name) > 100:
        return response_error("Enter a valid name.")

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return response_error("Enter a valid email address.")

    if len(password) < 10 or len(password) > 128:
        return response_error(
            "Password must contain 10-128 characters."
        )

    if OWNER_EMAIL and hmac.compare_digest(email, OWNER_EMAIL):
        return response_error(
            "This email is reserved for the owner."
        )

    try:

        with db() as conn:

            cursor = conn.execute("""
                INSERT INTO users
                (name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, 'customer', ?)
            """, (
                name,
                email,
                generate_password_hash(password),
                int(time.time())
            ))

            conn.commit()

            user_id = cursor.lastrowid

        return jsonify({
            "success": True,
            "message": "Customer account created.",
            "user_id": user_id
        }), 201

    except sqlite3.IntegrityError:

        return response_error(
            "An account with this email already exists.",
            409
        )


# ============================================================
# LOGIN
# ============================================================

@app.route("/api/auth/login", methods=["POST"])
def login():

    data = get_json_object()

    email = data.get("email", "")
    password = data.get("password", "")

    if not isinstance(email, str) or not isinstance(password, str):
        return response_error("Invalid login information.")

    email = email.strip().lower()

    with db() as conn:

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE email = ?
        """, (email,)).fetchone()

    if not user or not check_password_hash(
        user["password_hash"],
        password
    ):

        return response_error(
            "Incorrect email or password.",
            401
        )

    return jsonify({
        "success": True,
        "token": make_token(user),
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        }
    })


@app.route("/api/auth/me")
@login_required
def me():

    return jsonify({
        "success": True,
        "user": {
            "id": g.user["id"],
            "name": g.user["name"],
            "email": g.user["email"],
            "role": g.user["role"]
        }
    })


# ============================================================
# PRIVATE MEMORY
# ============================================================

@app.route("/api/memory", methods=["GET"])
@login_required
def list_memories():

    return jsonify({
        "success": True,
        "memories": get_memories(g.user["id"])
    })


@app.route("/api/memory", methods=["POST"])
@login_required
def add_memory():

    data = get_json_object()
    content = data.get("content", "")

    if not isinstance(content, str):
        return response_error("Invalid memory content.")

    content = content.strip()[:2000]

    if not content:
        return response_error("Enter memory content.")

    try:

        with db() as conn:

            conn.execute("""
                INSERT INTO memories
                (user_id, content, created_at)
                VALUES (?, ?, ?)
            """, (
                g.user["id"],
                content,
                int(time.time())
            ))

            conn.commit()

        return jsonify({"success": True}), 201

    except sqlite3.IntegrityError:

        return response_error("Memory already saved.", 409)


@app.route("/api/memory/<int:memory_id>", methods=["DELETE"])
@login_required
def delete_memory(memory_id):

    with db() as conn:

        row = conn.execute("""
            SELECT *
            FROM memories
            WHERE id = ?
        """, (memory_id,)).fetchone()

        if not row:
            return response_error("Memory not found.", 404)

        if row["user_id"] != g.user["id"] and g.user["role"] != "owner":
            return response_error("Access denied.", 403)

        conn.execute("""
            DELETE FROM memories
            WHERE id = ?
        """, (memory_id,))

        conn.commit()

    return jsonify({"success": True})


# ============================================================
# AUTOMOTIVE STATUS
# ============================================================

@app.route("/api/automotive/status")
def automotive_status():

    return jsonify({
        "success": True,
        "status": "ready",
        "hardware": "awaiting connection"
    })


# ============================================================
# MOTORCYCLE MANAGEMENT
# ============================================================

@app.route("/api/motorcycles", methods=["POST"])
@login_required
def create_motorcycle():

    data = get_json_object()

    name = data.get("name", "")

    if not isinstance(name, str) or not name.strip():
        return response_error("Enter motorcycle name.")

    name = name.strip()[:100]

    owner_id = g.user["id"]

    if g.user["role"] == "owner" and data.get("owner_user_id") is not None:

        try:
            owner_id = int(data["owner_user_id"])

        except (ValueError, TypeError):
            return response_error("Invalid customer account.")

        with db() as conn:

            customer = conn.execute("""
                SELECT id
                FROM users
                WHERE id = ? AND role = 'customer'
            """, (owner_id,)).fetchone()

        if not customer:
            return response_error("Customer account not found.", 404)

    try:

        with db() as conn:

            cur = conn.execute("""
                INSERT INTO motorcycles (
                    owner_user_id,
                    name,
                    brand,
                    model,
                    registration_number,
                    vehicle_identifier,
                    battery_capacity,
                    hardware_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                owner_id,
                name,
                data.get("brand"),
                data.get("model"),
                data.get("registration_number"),
                data.get("vehicle_identifier"),
                data.get("battery_capacity"),
                data.get("hardware_id"),
                int(time.time())
            ))

            conn.commit()

            motorcycle_id = cur.lastrowid

        return jsonify({
            "success": True,
            "id": motorcycle_id
        }), 201

    except sqlite3.IntegrityError:

        return response_error(
            "Registration or vehicle identifier already exists.",
            409
        )


@app.route("/api/motorcycles", methods=["GET"])
@login_required
def list_motorcycles():

    with db() as conn:

        if g.user["role"] == "owner":

            rows = conn.execute("""
                SELECT *
                FROM motorcycles
                ORDER BY id DESC
            """).fetchall()

        else:

            rows = conn.execute("""
                SELECT *
                FROM motorcycles
                WHERE owner_user_id = ?
                ORDER BY id DESC
            """, (g.user["id"],)).fetchall()

    return jsonify({
        "success": True,
        "motorcycles": [vehicle_json(row) for row in rows]
    })


@app.route("/api/motorcycles/<int:vehicle_id>", methods=["GET"])
@login_required
def get_motorcycle(vehicle_id):

    vehicle = vehicle_access(vehicle_id)

    if not vehicle:
        return response_error(
            "Motorcycle not found or access denied.",
            404
        )

    return jsonify({
        "success": True,
        "motorcycle": vehicle_json(vehicle)
    })


@app.route("/api/motorcycles/<int:vehicle_id>", methods=["DELETE"])
@login_required
def delete_motorcycle(vehicle_id):

    vehicle = vehicle_access(vehicle_id)

    if not vehicle:
        return response_error(
            "Motorcycle not found or access denied.",
            404
        )

    with db() as conn:

        conn.execute("""
            DELETE FROM motorcycles
            WHERE id = ?
        """, (vehicle_id,))

        conn.commit()

    return jsonify({"success": True})


# ============================================================
# HARDWARE TELEMETRY INGESTION
# ============================================================

@app.route("/api/hardware/telemetry", methods=["POST"])
def hardware_telemetry():

    supplied_key = request.headers.get("X-Device-Key", "")

    if not DEVICE_INGEST_KEY or not hmac.compare_digest(
        supplied_key,
        DEVICE_INGEST_KEY
    ):
        return response_error("Invalid device key.", 401)

    data = get_json_object()

    try:
        motorcycle_id = int(data.get("motorcycle_id"))

    except (ValueError, TypeError):
        return response_error("Invalid motorcycle.", 400)

    fields = {
        "battery_percentage": (0, 100),
        "estimated_range": (0, 100000),
        "speed": (0, 1000),
        "latitude": (-90, 90),
        "longitude": (-180, 180),
        "motor_temperature": (-100, 500),
        "battery_temperature": (-100, 500)
    }

    values = {}

    for field, limits in fields.items():

        value = data.get(field)

        if value is None:
            values[field] = None
            continue

        number = valid_number(value, limits[0], limits[1])

        if number is None:
            return response_error("Invalid telemetry value: " + field)

        values[field] = number

    charging = data.get("charging")

    if charging is not None:

        if charging not in (True, False, 0, 1):
            return response_error("Invalid charging status.")

        charging = int(charging)

    diagnostics = data.get("diagnostics") or "Received"

    if not isinstance(diagnostics, str):
        return response_error("Invalid diagnostics information.")

    diagnostics = diagnostics[:2000]

    with db() as conn:

        motorcycle = conn.execute("""
            SELECT id
            FROM motorcycles
            WHERE id = ?
        """, (motorcycle_id,)).fetchone()

        if not motorcycle:
            return response_error("Motorcycle not found.", 404)

        conn.execute("""
            INSERT INTO telemetry (
                motorcycle_id,
                battery_percentage,
                estimated_range,
                speed,
                latitude,
                longitude,
                motor_temperature,
                battery_temperature,
                charging,
                diagnostics,
                source,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'hardware', ?)
        """, (
            motorcycle_id,
            values["battery_percentage"],
            values["estimated_range"],
            values["speed"],
            values["latitude"],
            values["longitude"],
            values["motor_temperature"],
            values["battery_temperature"],
            charging,
            diagnostics,
            int(time.time())
        ))

        conn.commit()

    return jsonify({"success": True})


# ============================================================
# PAYMENT RECORDS
# ============================================================

@app.route("/api/motorcycles/<int:vehicle_id>/payments", methods=["GET"])
@login_required
def list_payments(vehicle_id):

    vehicle = vehicle_access(vehicle_id)

    if not vehicle:
        return response_error(
            "Motorcycle not found or access denied.",
            404
        )

    with db() as conn:

        rows = conn.execute("""
            SELECT
                id,
                motorcycle_id,
                amount,
                currency,
                status,
                reference,
                created_at
            FROM payments
            WHERE motorcycle_id = ?
            AND user_id = ?
            ORDER BY created_at DESC
        """, (
            vehicle_id,
            vehicle["owner_user_id"]
        )).fetchall()

    return jsonify({
        "success": True,
        "payments": [dict(row) for row in rows]
    })


@app.route("/api/motorcycles/<int:vehicle_id>/payments", methods=["POST"])
@owner_required
def create_payment_record(vehicle_id):

    vehicle = vehicle_access(vehicle_id)

    if not vehicle:
        return response_error("Motorcycle not found.", 404)

    data = get_json_object()

    amount = valid_number(
        data.get("amount"),
        0.01,
        1000000000
    )

    if amount is None:
        return response_error("Invalid payment amount.")

    currency = data.get("currency", "NGN")

    if not isinstance(currency, str):
        return response_error("Invalid currency.")

    currency = currency.upper()

    if currency not in ("NGN", "USD"):
        return response_error("Unsupported currency.")

    reference = "TABBY-" + secrets.token_hex(12).upper()

    with db() as conn:

        conn.execute("""
            INSERT INTO payments (
                motorcycle_id,
                user_id,
                amount,
                currency,
                status,
                reference,
                created_at
            )
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (
            vehicle_id,
            vehicle["owner_user_id"],
            amount,
            currency,
            reference,
            int(time.time())
        ))

        conn.commit()

    return jsonify({
        "success": True,
        "message": "Pending payment record created.",
        "reference": reference,
        "status": "pending"
    }), 201


# ============================================================
# OWNER AND CUSTOMER CONVERSATION HISTORY
# ============================================================

@app.route("/api/conversations", methods=["GET"])
@login_required
def list_conversations():

    with db() as conn:

        if (
            g.user["role"] == "owner"
            and request.args.get("scope") == "all"
        ):

            rows = conn.execute("""
                SELECT *
                FROM conversations
                ORDER BY updated_at DESC
                LIMIT 100
            """).fetchall()

        else:

            rows = conn.execute("""
                SELECT *
                FROM conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT 100
            """, (g.user["id"],)).fetchall()

    return jsonify({
        "success": True,
        "conversations": [dict(row) for row in rows]
    })


@app.route("/api/conversations", methods=["POST"])
@login_required
def create_conversation():

    data = get_json_object()

    title = data.get("title") or "New Chat"

    if not isinstance(title, str):
        return response_error("Invalid conversation title.")

    title = title[:100]

    now = int(time.time())

    with db() as conn:

        cursor = conn.execute("""
            INSERT INTO conversations
            (user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
        """, (
            g.user["id"],
            title,
            now,
            now
        ))

        conn.commit()

        conversation_id = cursor.lastrowid

    return jsonify({
        "success": True,
        "id": conversation_id,
        "title": title
    }), 201


@app.route("/api/conversations/<int:conversation_id>", methods=["GET"])
@login_required
def get_conversation(conversation_id):

    with db() as conn:

        conversation = conn.execute("""
            SELECT *
            FROM conversations
            WHERE id = ? AND user_id = ?
        """, (
            conversation_id,
            g.user["id"]
        )).fetchone()

        if not conversation:
            return response_error(
                "Conversation not found or access denied.",
                404
            )

        messages = conn.execute("""
            SELECT *
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
        """, (conversation_id,)).fetchall()

    return jsonify({
        "success": True,
        "conversation": dict(conversation),
        "messages": [dict(message) for message in messages]
    })


@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
@login_required
def delete_conversation(conversation_id):

    with db() as conn:

        conversation = conn.execute("""
            SELECT id
            FROM conversations
            WHERE id = ? AND user_id = ?
        """, (
            conversation_id,
            g.user["id"]
        )).fetchone()

        if not conversation:
            return response_error(
                "Conversation not found or access denied.",
                404
            )

        conn.execute("""
            DELETE FROM conversations
            WHERE id = ?
        """, (conversation_id,))

        conn.commit()

    return jsonify({"success": True})


# ============================================================
# OWNER ADMINISTRATION
# ============================================================

@app.route("/api/owner/customers", methods=["GET"])
@owner_required
def owner_customers():

    with db() as conn:

        rows = conn.execute("""
            SELECT id, name, email, role, created_at
            FROM users
            ORDER BY id ASC
        """).fetchall()

    return jsonify({
        "success": True,
        "users": [dict(row) for row in rows]
    })


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
