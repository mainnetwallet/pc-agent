"""Persistent memory layer built on SQLite.

Memory categories:
  * user_preferences
  * conversation_history
  * task_history
  * project_knowledge
  * application_knowledge
  * skills (reusable workflows)
  * failed_patterns
  * checkpoints

Secrets (passwords, tokens, keys) are never stored here.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import DATA_DIR, LOGS_DIR

DB_PATH = os.path.join(DATA_DIR, "agent.db")

_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class MemoryStore:
    """Thread-safe SQLite-backed memory store."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        return _connect()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_history (
                    id TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan TEXT,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS task_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    tool TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES task_history(id)
                );
                CREATE TABLE IF NOT EXISTS project_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    path TEXT,
                    description TEXT,
                    entry_point TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS application_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_name TEXT NOT NULL,
                    executable TEXT,
                    interaction_method TEXT,
                    notes TEXT,
                    discovered_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    steps TEXT NOT NULL,
                    applications TEXT,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    last_used TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS failed_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT NOT NULL,
                    context TEXT,
                    occurrence_count INTEGER DEFAULT 1,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ----------------------------------------------------------------- preferences
    def set_preference(self, key: str, value: Any) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO user_preferences(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), self._now()),
            )

    def get_preference(self, key: str, default: Any = None) -> Any:
        with self._conn() as c:
            row = c.execute("SELECT value FROM user_preferences WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    # ---------------------------------------------------------------- conversation
    def add_conversation(self, role: str, content: str, metadata: Optional[Dict] = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO conversation_history(role, content, metadata, created_at) "
                "VALUES(?,?,?,?)",
                (role, content, json.dumps(metadata or {}), self._now()),
            )
            return int(cur.lastrowid)

    def get_recent_conversation(self, limit: int = 50) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT role, content, metadata, created_at FROM conversation_history "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def summarize_conversation(self, limit: int = 200) -> str:
        """Return a compact summary of recent conversation for context."""
        rows = self.get_recent_conversation(limit)
        if not rows:
            return ""
        parts = [f"[{r['role']}] {r['content'][:200]}" for r in rows[-20:]]
        return "\n".join(parts)

    # ---------------------------------------------------------------- task history
    def save_task(self, task_id: str, instruction: str, status: str,
                  plan: Optional[Dict] = None, result: Optional[str] = None,
                  error: Optional[str] = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO task_history(id, instruction, status, plan, result, error, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, plan=excluded.plan, "
                "result=excluded.result, error=excluded.error, updated_at=excluded.updated_at",
                (task_id, instruction, status,
                 json.dumps(plan) if plan else None,
                 result, error, self._now(), self._now()),
            )

    def update_task(self, task_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = self._now()
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [task_id]
        with self._conn() as c:
            c.execute(f"UPDATE task_history SET {cols} WHERE id=?", vals)

    def get_task(self, task_id: str) -> Optional[Dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM task_history WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        with self._conn() as c:
            if status:
                rows = c.execute(
                    "SELECT * FROM task_history WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM task_history ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def add_task_step(self, task_id: str, step_index: int, tool: str, action: str,
                      status: str, result: Optional[str] = None,
                      error: Optional[str] = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO task_steps(task_id, step_index, tool, action, status, "
                "result, error, started_at) VALUES(?,?,?,?,?,?,?,?)",
                (task_id, step_index, tool, action, status, result, error, self._now()),
            )
            return int(cur.lastrowid)

    def finish_task_step(self, step_id: int, status: str,
                         result: Optional[str] = None, error: Optional[str] = None) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE task_steps SET status=?, result=?, error=?, finished_at=? WHERE id=?",
                (status, result, error, self._now(), step_id),
            )

    def get_task_steps(self, task_id: str) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM task_steps WHERE task_id=? ORDER BY step_index",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ project knowledge
    def save_project_knowledge(self, name: str, path: Optional[str],
                               description: str, entry_point: Optional[str] = None,
                               metadata: Optional[Dict] = None) -> None:
        with self._conn() as c:
            row = c.execute("SELECT id FROM project_knowledge WHERE name=?", (name,)).fetchone()
            now = self._now()
            if row:
                c.execute(
                    "UPDATE project_knowledge SET path=?, description=?, entry_point=?, "
                    "metadata=?, updated_at=? WHERE name=?",
                    (path, description, entry_point, json.dumps(metadata or {}), now, name),
                )
            else:
                c.execute(
                    "INSERT INTO project_knowledge(name, path, description, entry_point, "
                    "metadata, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                    (name, path, description, entry_point, json.dumps(metadata or {}), now, now),
                )

    def get_project_knowledge(self, name: str) -> Optional[Dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM project_knowledge WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def search_projects(self, query: str) -> List[Dict]:
        like = f"%{query}%"
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM project_knowledge WHERE name LIKE ? OR path LIKE ? OR description LIKE ? "
                "ORDER BY updated_at DESC LIMIT 20",
                (like, like, like),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ application knowledge
    def save_app_knowledge(self, app_name: str, executable: Optional[str] = None,
                           interaction_method: Optional[str] = None, notes: Optional[str] = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO application_knowledge(app_name, executable, interaction_method, notes, discovered_at) "
                "VALUES(?,?,?,?,?)",
                (app_name, executable, interaction_method, notes, self._now()),
            )

    def get_app_knowledge(self, app_name: str) -> Optional[Dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM application_knowledge WHERE app_name=? ORDER BY discovered_at DESC LIMIT 1",
                (app_name,),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ skills
    def save_skill(self, name: str, description: str, steps: List[Dict],
                   applications: Optional[List[str]] = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO skills(name, description, steps, applications, created_at) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET description=excluded.description, "
                "steps=excluded.steps, applications=excluded.applications",
                (name, description, json.dumps(steps),
                 json.dumps(applications or []), self._now()),
            )

    def get_skill(self, name: str) -> Optional[Dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
        if row:
            d = dict(row)
            try:
                d["steps"] = json.loads(d["steps"])
                d["applications"] = json.loads(d["applications"])
            except (json.JSONDecodeError, TypeError):
                pass
            return d
        return None

    def list_skills(self) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM skills ORDER BY success_count DESC, last_used DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["steps"] = json.loads(d["steps"])
                d["applications"] = json.loads(d["applications"])
            except (json.JSONDecodeError, TypeError):
                pass
            out.append(d)
        return out

    def record_skill_use(self, name: str, success: bool) -> None:
        with self._conn() as c:
            if success:
                c.execute("UPDATE skills SET success_count=success_count+1, last_used=? WHERE name=?",
                          (self._now(), name))
            else:
                c.execute("UPDATE skills SET failure_count=failure_count+1 WHERE name=?", (name,))

    # ------------------------------------------------------------ failed patterns
    def record_failed_pattern(self, pattern: str, context: Optional[Dict] = None) -> None:
        with self._conn() as c:
            row = c.execute("SELECT id, occurrence_count, first_seen FROM failed_patterns WHERE pattern=?",
                            (pattern,)).fetchone()
            now = self._now()
            if row:
                c.execute(
                    "UPDATE failed_patterns SET occurrence_count=occurrence_count+1, last_seen=? WHERE id=?",
                    (now, row["id"]),
                )
            else:
                c.execute(
                    "INSERT INTO failed_patterns(pattern, context, first_seen, last_seen) "
                    "VALUES(?,?,?,?)",
                    (pattern, json.dumps(context or {}), now, now),
                )

    # ---------------------------------------------------------------- checkpoints
    def save_checkpoint(self, task_id: str, name: str, state: Dict) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO checkpoints(task_id, name, state, created_at) VALUES(?,?,?,?)",
                (task_id, name, json.dumps(state), self._now()),
            )
            return int(cur.lastrowid)

    def get_checkpoints(self, task_id: str) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM checkpoints WHERE task_id=? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["state"] = json.loads(d["state"])
            except (json.JSONDecodeError, TypeError):
                pass
            out.append(d)
        return out

    # ---------------------------------------------------------------- audit log
    def audit(self, task_id: Optional[str], action: str, details: Optional[Dict] = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit_log(task_id, action, details, created_at) VALUES(?,?,?,?)",
                (task_id, action, json.dumps(details or {}), self._now()),
            )

    def close(self) -> None:
        pass  # connections are per-call


# Singleton instance
memory = MemoryStore()
