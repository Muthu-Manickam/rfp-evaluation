import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CRITERIA = [
    (1, "Technical Capability", "Architecture, integrations, scalability, technical fit", 30),
    (2, "Implementation Plan", "Timeline, milestones, staffing, risk plan", 20),
    (3, "Commercial Value", "Pricing clarity, total cost, assumptions", 20),
    (4, "Security & Compliance", "Controls, certifications, privacy, auditability", 20),
    (5, "Support & Experience", "Support model, similar projects, references", 10),
]

INSERT_CRITERION = ("INSERT INTO evaluation_criteria (criterion_id, name, description, weight, max_score, is_active, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)")


def db_path():
    return Path(os.getenv("RFP_DB_PATH") or ROOT / "data" / "rfp_evaluation.db")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def setup(reset_criteria=False):
    with connect() as conn:
        conn.executescript((ROOT / "db" / "tables.sql").read_text())
        if reset_criteria:
            conn.execute("DELETE FROM evaluation_criteria")
        if conn.execute("SELECT COUNT(*) FROM evaluation_criteria").fetchone()[0] == 0:
            rows = [(number, name, text, weight, 10, 1, now()) for number, name, text, weight in DEFAULT_CRITERIA]
            conn.executemany(INSERT_CRITERION, rows)


def get_criteria(active_only=False):
    sql = "SELECT * FROM evaluation_criteria"
    if active_only:
        sql += " WHERE is_active = 1"
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql + " ORDER BY criterion_id")]


def save_criteria(rows):
    with connect() as conn:
        conn.execute("DELETE FROM evaluation_criteria")
        for row in rows:
            conn.execute(
                INSERT_CRITERION,
                (int(row["criterion_id"]), str(row["name"]).strip(), str(row.get("description") or ""),
                 float(row["weight"]), float(row["max_score"]), 1 if row.get("is_active") else 0, now()))


def create_run(run_id, title, model, criteria):
    with connect() as conn:
        conn.execute("INSERT INTO rfp_runs (rfp_run_id, created_at, status, title, model, criteria_snapshot) "
                     "VALUES (?, ?, 'RUNNING', ?, ?, ?)", (run_id, now(), title, model, json.dumps(criteria)))


def finish_run(run_id, status, warnings, error=None):
    with connect() as conn:
        conn.execute("UPDATE rfp_runs SET status = ?, warnings_json = ?, completed_at = ?, error = ? "
                     "WHERE rfp_run_id = ?", (status, json.dumps(warnings), now(), error, run_id))


def save_results(run_id, suppliers):
    with connect() as conn:
        conn.execute("DELETE FROM supplier_results WHERE rfp_run_id = ?", (run_id,))
        for s in suppliers:
            conn.execute(
                "INSERT INTO supplier_results (rfp_run_id, supplier_name, submission_date, experience_rating, "
                "absolute_score, ppi, final_rank, file_name, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, s["supplier_name"], s["submission_date"], s["experience_rating"], s["absolute_score"],
                 s["ppi"], s["final_rank"], s.get("file_name"), json.dumps(s, default=str)))


def list_runs():
    sql = """
        SELECT r.rfp_run_id, r.created_at, r.status, r.title, r.model, r.locked,
               COUNT(s.id) AS suppliers,
               MAX(CASE WHEN s.final_rank = 1 THEN s.supplier_name END) AS winner
        FROM rfp_runs r
        LEFT JOIN supplier_results s ON s.rfp_run_id = r.rfp_run_id
        GROUP BY r.rfp_run_id
        ORDER BY r.created_at DESC
    """
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql)]


def get_run(run_id):
    with connect() as conn:
        run = conn.execute("SELECT * FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)).fetchone()
        if run is None:
            return None
        results = conn.execute("SELECT result_json FROM supplier_results WHERE rfp_run_id = ? ORDER BY final_rank",
                               (run_id,)).fetchall()
        events = conn.execute("SELECT * FROM evaluation_events WHERE rfp_run_id = ? ORDER BY id", (run_id,)).fetchall()
    run = dict(run)
    run["criteria"] = json.loads(run.pop("criteria_snapshot") or "[]")
    run["warnings"] = json.loads(run.pop("warnings_json") or "[]")
    run["suppliers"] = [json.loads(row["result_json"]) for row in results]
    run["events"] = [dict(row) for row in events]
    run["locked"] = bool(run["locked"])
    return run


def delete_run(run_id):
    with connect() as conn:
        conn.execute("DELETE FROM rfp_runs WHERE rfp_run_id = ?", (run_id,))


def add_event(run_id, event_type, reason, supplier=None, criterion_id=None, old_score=None, new_score=None,
              actor="reviewer"):
    with connect() as conn:
        conn.execute(
            "INSERT INTO evaluation_events (rfp_run_id, event_type, supplier_name, criterion_id, old_score, new_score, "
            "reason, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, event_type, supplier, criterion_id, old_score, new_score, reason, actor, now()))


def lock_run(run_id, note, actor="reviewer"):
    with connect() as conn:
        conn.execute("UPDATE rfp_runs SET locked = 1, locked_at = ? WHERE rfp_run_id = ?", (now(), run_id))
    add_event(run_id, "LOCK", note, actor=actor)
