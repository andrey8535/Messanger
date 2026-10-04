import hashlib
import os
import secrets
import sqlite3
import threading
import time
from datetime import date
import re


NAME_RE = re.compile(r"^[\w.\-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD, MAX_PASSWORD = 8, 128

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    last_login   REAL,
    login_streak INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    text    TEXT NOT NULL,
    ts      REAL NOT NULL
);
"""