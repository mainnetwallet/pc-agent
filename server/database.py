"""Server database layer.

SQLite for standalone/local, PostgreSQL via DATABASE_URL when distributed.
Schema mirrors the agent memory store for task/event synchronization.
"""

import os
import sqlite3
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL and "postgres" in DATABASE_URL.lower())
SQLITE_PATH = os.path.join(BASE_DIR, "data", "server.db")

_conn_lock = __import__("threading").RLock()


def get_conn():
    if USE_POSTGRES:
        import psycopg2  # type: ignore
        return psycopg2.connect(DATABASE_URL)
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    return sqlite3.connect(SQLITE_PATH, timeout=30)


def init_db():
    conn = get_conn()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan TEXT,
                    result TEXT,
                    error TEXT,
                    created_at REAL,
                    updated_at REAL,
                    completed_at REAL,
                    source TEXT,
                    metadata TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    task_id TEXT,
                    type TEXT NOT NULL,
                    data TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    paired BOOLEAN DEFAULT FALSE,
                    token TEXT,
                    created_at REAL,
                    last_seen REAL
                );
                CREATE TABLE IF NOT EXISTS task_messages (
                    id SERIAL PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_task_messages_task ON task_messages(task_id, id);
            """)
        else:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan TEXT,
                    result TEXT,
                    error TEXT,
                    created_at REAL,
                    updated_at REAL,
                    completed_at REAL,
                    source TEXT,
                    metadata TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    type TEXT NOT NULL,
                    data TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    paired BOOLEAN DEFAULT FALSE,
                    token TEXT,
                    created_at REAL,
                    last_seen REAL
                );
                CREATE TABLE IF NOT EXISTS task_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_task_messages_task ON task_messages(task_id, id);
            """)
        conn.commit()
    finally:
        conn.close()


init_db()
