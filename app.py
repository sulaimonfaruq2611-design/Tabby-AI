# app.py — Tabby 2.0 REWRITTEN (Your original + Server History Patch)

# Preserves: all your routes, models, SYSTEM_PROMPT, bootstrap_owner, vehicle_access

import os

import re

import json

import time

import hmac

import sqlite3

import secrets

import hashlib

import urllib.parse

import random

import requests

from functools import wraps

from flask import Flask, request, jsonify, g

from flask_cors import CORS

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from werkzeug.security import generate_password_hash, check_password_hash



app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

OWNER_NAME = "Faruq Sulaimon"

OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "").strip().lower()

OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

SECRET_KEY = os.environ.get("SECRET_KEY", "")

DEVICE_INGEST_KEY = os.environ.get("DEVICE_INGEST_KEY", "")

DATABASE_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "tabby.db"))

os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)

if not SECRET_KEY:

    raise RuntimeError("Set SECRET_KEY in your deployment environment before starting Tabby.")

serializer = URLSafeTimedSerializer(SECRET_KEY, salt="tabby-auth-v1")

allowed_origins = [x.strip() for x in os.environ.get("FRONTEND_ORIGINS", "").split(",") if x.strip()]

if allowed_origins:

    CORS(app, resources={r"/api/*": {"origins": allowed_origins}})



MODELS = ["openrouter/free","openai/gpt-oss-20b:free","google/gemma-4-31b-it:free","nvidia/nemotron-3-nano-30b-a3b:free","google/gemma-4-26b-a4b-it:free"]

SYSTEM_PROMPT = """Your name is Tabby.\nYou are an AI assistant created and owned by Faruq Sulaimon.\nFaruq Sulaimon is your creator and owner. He is a different person from you. Never claim to be Faruq.\nAlways call the user Boss.\nBe helpful, intelligent, respectful and honest.\nNever pretend to have access to hardware or live vehicle data unless actual data has been received and verified.\nYou are also the AI foundation of Tabby Automotive and Tabby Ride.\nTabby Automotive will manage motorcycle profiles, battery monitoring, GPS interfaces, vehicle diagnostics and customer permissions.\nTabby Ride will serve motorcycle customers.\nNever invent battery percentages, GPS positions, vehicle speeds, diagnostic readings or payment confirmations.\nClearly distinguish simulated information from actual hardware data.\nNever claim a payment has been completed unless a payment provider has verified it.\n"""



# --- DB HELPERS (same as yours) ---

def db():

    conn = sqlite3.connect(DATABASE_PATH)

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")

    return conn



def init_db():

    with db() as conn:
