import json
import os
import sqlite3
from pathlib import Path

from constants import AUTOSAVE_FILE
from state import AppState


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("TURNI_DATA_DIR", PROJECT_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "turni.sqlite3"


def connect_db():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 30000")
    return db


def initialize_database():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL
            )
        """)


def serialize_state(state):
    data = state.to_json_dict()
    data["solution"] = state.solution
    data["violations"] = state.violations
    return json.dumps(data, ensure_ascii=False, default=str)


def deserialize_state(payload):
    data = json.loads(payload)
    state = AppState()
    state.load_json_dict(data)
    state.solution = data.get("solution")
    state.violations = data.get("violations", {"hard": [], "soft": []})
    return state


def load_project_file(path: str) -> AppState:
    return deserialize_state(Path(path).read_text(encoding="utf-8-sig"))


def save_project_file(path: str, state: AppState):
    Path(path).write_text(serialize_state(state), encoding="utf-8")


def load_autosave() -> AppState:
    initialize_database()

    with connect_db() as db:
        row = db.execute(
            "SELECT payload FROM projects WHERE id = 1"
        ).fetchone()

    if row:
        return deserialize_state(row["payload"])

    legacy_path = PROJECT_DIR / AUTOSAVE_FILE

    if legacy_path.exists():
        state = load_project_file(str(legacy_path))
    else:
        state = AppState()

    save_autosave(state)
    return state


def save_autosave(state: AppState):
    initialize_database()
    payload = serialize_state(state)

    with connect_db() as db:
        db.execute("""
            INSERT INTO projects (id, payload)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
        """, (payload,))