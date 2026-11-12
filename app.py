import os
import re
import json
import time
import base64
import secrets
import logging
from datetime import datetime, timezone
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor

import requests
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


# ============================================================
# CONFIG - TABBY BY FARUQ SULAIMON
# ============================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY",
    "change-this-secret-before-deployment"
)

FRONTEND_ORIGINS = os.getenv("FRONTEND_ORIGINS", "*")

CORS(
    app,
    resources={
        r"/*": {
            "origins": FRONTEND_ORIGINS.split(",")
            if FRONTEND_ORIGINS != "*" else "*"
        }
    },
    supports_credentials=False
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tabby")

CREATOR_NAME = "Faruq Sulaimon"
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "").strip().lower()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = (
    os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()
    or "openrouter/free"
)

VISION_MODEL = os.getenv(
    "VISION_MODEL",
    "openai/gpt-4o-mini"
).strip()

DEVICE_INGEST_KEY = os.getenv("DEVICE_INGEST_KEY", "")

# Supabase PostgreSQL connection.
# Keep the old DATABASE_URL environment variable untouched.
SUPABASE_DATABASE_URL = os.getenv(
    "SUPABASE_DATABASE_URL", ""
).strip()

CLOUDFLARE_ACCOUNT_ID = os.getenv(
    "CLOUDFLARE_ACCOUNT_ID", ""
).strip()

CLOUDFLARE_API_TOKEN = os.getenv(
    "CLOUDFLARE_API_TOKEN", ""
).strip()

FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"

REPLICATE_API_TOKEN = os.getenv(
    "REPLICATE_API_TOKEN", ""
).strip()

GOAPI_API_KEY = os.getenv("GOAPI_API_KEY", "").strip()

MIDJOURNEY_API_KEY = (
    os.getenv("MIDJOURNEY_API_KEY", "").strip()
    or GOAPI_API_KEY
)

AUTO_MEMORY_ENABLED = (
    os.getenv("AUTO_MEMORY_ENABLED", "true").lower() == "true"
)

LIVE_SEARCH_ENABLED = (
    os.getenv("LIVE_SEARCH_ENABLED", "true").lower() == "true"
)

TABBY_DEVICE_KEY = os.getenv(
    "TABBY_DEVICE_KEY",
    DEVICE_INGEST_KEY
).strip()


# ===== FALLBACK PROVIDERS =====

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-1.5-flash"
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.1-8b-instant"
).strip()

CEREBRAS_MODEL = os.getenv(
    "CEREBRAS_MODEL",
    "llama3.1-8b"
).strip()


serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

OPENROUTER_URL = (
    "https://openrouter.ai/api/v1/chat/completions"
)

RETIRED_AI_MODELS = {
    "google/gemini-2.0-flash-001"
}

AI_MODELS = list(dict.fromkeys(
    ([OPENROUTER_MODEL]
     if OPENROUTER_MODEL not in RETIRED_AI_MODELS else [])
    + ["openrouter/free"]
))


# ============================================================
# DATABASE - SUPABASE POSTGRESQL
# ============================================================

def convert_sql(sql):
    """
    Convert SQLite-style SQL into PostgreSQL-compatible SQL.
    Existing application queries can continue using ? placeholders.
    """

    sql = sql.replace("?", "%s")

    # PostgreSQL uses ON CONFLICT instead of INSERT OR IGNORE.
    sql = re.sub(
        r"INSERT\s+OR\s+IGNORE\s+INTO",
        "INSERT INTO",
        sql,
        flags=re.IGNORECASE
    )

    if re.search(
        r"INSERT\s+INTO",
        sql,
        flags=re.IGNORECASE
    ):
        if not re.search(
            r"ON\s+CONFLICT",
            sql,
            flags=re.IGNORECASE
        ):
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    return sql


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.lastrowid = None
        self.rowcount = cursor.rowcount

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.cursor)

    def close(self):
        self.cursor.close()


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=None):
        original_sql = sql.strip()

        is_insert = bool(re.match(
            r"INSERT\s+INTO",
            original_sql,
            flags=re.IGNORECASE
        ))

        # bike_status has motorcycle_id as its primary key,
        # not an id column.
        is_bike_status = bool(re.search(
            r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+bike_status",
            original_sql,
            flags=re.IGNORECASE
        ))

        converted_sql = convert_sql(original_sql)

        cursor = self.connection.cursor(
            cursor_factory=RealDictCursor
        )

        if is_insert and not is_bike_status:
            if not re.search(
                r"\bRETURNING\b",
                converted_sql,
                flags=re.IGNORECASE
            ):
                converted_sql = (
                    converted_sql.rstrip().rstrip(";")
                    + " RETURNING id"
                )

        cursor.execute(converted_sql, params or ())

        wrapped = PostgresCursor(cursor)

        if is_insert and not is_bike_status:
            returned = cursor.fetchone()

            if returned and "id" in returned:
                wrapped.lastrowid = returned["id"]

        wrapped.rowcount = cursor.rowcount
        return wrapped

    def executemany(self, sql, params):
        cursor = self.connection.cursor(
            cursor_factory=RealDictCursor
        )
        cursor.executemany(convert_sql(sql), params)
        return PostgresCursor(cursor)

    def executescript(self, script):
        """
        Execute the existing table creation script using PostgreSQL syntax.
        """

        statements = [
            statement.strip()
            for statement in script.split(";")
            if statement.strip()
        ]

        cursor = self.connection.cursor()

        for statement in statements:
            statement = re.sub(
                r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
                "BIGSERIAL PRIMARY KEY",
                statement,
                flags=re.IGNORECASE
            )

            statement = re.sub(
                r"\bINTEGER\b",
                "BIGINT",
                statement,
                flags=re.IGNORECASE
            )

            cursor.execute(statement)

        cursor.close()

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()

        self.connection.close()
        return False


def get_db():
    if not SUPABASE_DATABASE_URL:
        raise RuntimeError(
            "SUPABASE_DATABASE_URL is missing. "
            "Configure it in your Render environment variables."
        )

    connection = psycopg2.connect(
        SUPABASE_DATABASE_URL,
        connect_timeout=15,
        sslmode="require"
    )

    return PostgresConnection(connection)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():
    try:
        with get_db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'customer',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT DEFAULT 'New conversation',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS motorcycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                assigned_user_id INTEGER,
                name TEXT NOT NULL,
                model TEXT,
                registration_number TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_id) REFERENCES users(id),
                FOREIGN KEY(assigned_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                motorcycle_id INTEGER NOT NULL,
                latitude REAL,
                longitude REAL,
                speed REAL,
                battery_level REAL,
                fuel_level REAL,
                engine_temperature REAL,
                extra_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id)
                ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                motorcycle_id INTEGER,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'NGN',
                status TEXT DEFAULT 'pending',
                reference TEXT UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id)
            );

            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                latitude REAL,
                longitude REAL,
                label TEXT,
                accuracy REAL,
                speed REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bike_status (
                motorcycle_id INTEGER PRIMARY KEY,
                battery_health REAL DEFAULT 100,
                range_km REAL DEFAULT 0,
                total_trip REAL DEFAULT 0,
                total_distance REAL DEFAULT 0,
                co2_saved REAL DEFAULT 0,
                lock_status TEXT DEFAULT 'locked',
                access_mode TEXT DEFAULT 'full',
                last_seen TEXT,
                FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id)
                ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS payment_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                motorcycle_id INTEGER NOT NULL,
                total_amount REAL NOT NULL,
                deposit REAL DEFAULT 0,
                installment_amount REAL NOT NULL,
                installments_paid INTEGER DEFAULT 0,
                installments_total INTEGER NOT NULL,
                next_due_date TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id)
            );

            CREATE TABLE IF NOT EXISTS bike_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                motorcycle_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                issued_by INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(motorcycle_id) REFERENCES motorcycles(id)
                ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS house_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                room TEXT DEFAULT 'living',
                type TEXT DEFAULT 'light',
                status TEXT DEFAULT 'off',
                value TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS tabby_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                device_name TEXT NOT NULL,
                device_type TEXT DEFAULT 'glasses',
                last_seen TEXT,
                battery INTEGER DEFAULT 100,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS tabby_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                device TEXT,
                command TEXT,
                action TEXT,
                result TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, id);

            CREATE INDEX IF NOT EXISTS idx_memories_user
            ON memories(user_id);

            CREATE INDEX IF NOT EXISTS idx_telemetry_motorcycle
            ON telemetry(motorcycle_id, id);
            """)

            if OWNER_EMAIL:
                db.execute(
                    "UPDATE users SET role='owner' WHERE lower(email)=?",
                    (OWNER_EMAIL,)
                )

        logger.info("Tabby Supabase database ready")
        return True

    except Exception:
        logger.exception("Supabase database initialization failed")
        return False


DATABASE_READY = init_db()


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_ai_text(t):
    if not t:
        return t

    t = str(t)
    t = re.sub(r'\*\*+', '', t)
    t = re.sub(r'__+', '', t)
    t = re.sub(r'\*([^\*]+)\*', r'\1', t)
    t = t.replace('**', '').replace('*', '')

    t = t.replace('😂', ' lol ')
    t = t.replace('🤣', ' lol ')
    t = t.replace('😆', ' haha ')
    t = t.replace('😅', ' haha ')
    t = t.replace('😹', ' lol ')
    t = t.replace('😊', ' ')
    t = t.replace('😁', ' lol ')

    t = re.sub(
        r'[\U0001F600-\U0001F64F'
        r'\U0001F300-\U0001F5FF'
        r'\U0001F680-\U0001F6FF'
        r'\U0001F1E0-\U0001F1FF'
        r'\U00002702-\U000027B0'
        r'\U000024C2-\U0001F251]+',
        ' ',
        t
    )

    t = re.sub(r'\s+', ' ', t).strip()
    return t


def row_dict(r):
    return dict(r) if r else None


def json_error(m, s=400):
    return jsonify({
        "success": False,
        "error": clean_ai_text(m)
    }), s


def current_time():
    return datetime.now(timezone.utc).isoformat()