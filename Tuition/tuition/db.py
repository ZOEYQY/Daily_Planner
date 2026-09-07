"""SQLite access — stdlib sqlite3 only, no ORM."""
import os
import sqlite3
import shutil
import datetime as dt
from flask import g, current_app

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DB_PATH"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    lastrow = cur.lastrowid
    cur.close()
    return lastrow


def log(action, detail=""):
    execute("INSERT INTO audit_log (action, detail) VALUES (?, ?)", (action, detail))


def init_db(app):
    """Create tables and seed the single settings row."""
    con = sqlite3.connect(app.config["DB_PATH"])
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        con.executescript(f.read())
    row = con.execute("SELECT id FROM settings WHERE id = 1").fetchone()
    if row is None:
        con.execute("INSERT INTO settings (id, password_hash) VALUES (1, '')")
    _run_migrations(con)
    con.commit()
    con.close()


def _run_migrations(con):
    """Additive column migrations for databases created by an older version."""
    def cols(table):
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}

    if "address" not in cols("students"):
        con.execute("ALTER TABLE students ADD COLUMN address TEXT")

    con.execute("""CREATE TABLE IF NOT EXISTS families (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, remarks TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    if "family_id" not in cols("students"):
        con.execute("ALTER TABLE students ADD COLUMN family_id INTEGER REFERENCES families(id) ON DELETE SET NULL")

    if "receipt_path" not in cols("payments"):
        con.execute("ALTER TABLE payments ADD COLUMN receipt_path TEXT")

    for col in ("level", "kind"):
        if col not in cols("classes"):
            con.execute(f"ALTER TABLE classes ADD COLUMN {col} TEXT")
    if "pricing" not in cols("classes"):
        con.execute("ALTER TABLE classes ADD COLUMN pricing TEXT NOT NULL DEFAULT 'fixed'")
    if "lesson_fee_cents" not in cols("classes"):
        con.execute("ALTER TABLE classes ADD COLUMN lesson_fee_cents INTEGER NOT NULL DEFAULT 0")
    # per-lesson billing modes (the real fee model)
    ccols = cols("classes")
    for col, decl in (
        ("bill_mode", "TEXT NOT NULL DEFAULT 'student_attend'"),
        ("base_fee_cents", "INTEGER NOT NULL DEFAULT 0"),
        ("base_head_count", "INTEGER NOT NULL DEFAULT 0"),
        ("per_head_cents", "INTEGER NOT NULL DEFAULT 0"),
        ("agent_name", "TEXT"),
        ("agent_phone", "TEXT"),
        ("billed_family_id", "INTEGER REFERENCES families(id) ON DELETE SET NULL"),
    ):
        if col not in ccols:
            con.execute(f"ALTER TABLE classes ADD COLUMN {col} {decl}")
    # 1v1 classes always bill per the student's own attendance
    con.execute("UPDATE classes SET bill_mode = 'student_attend' WHERE kind = '1v1'")
    con.execute("""CREATE TABLE IF NOT EXISTS class_bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        month TEXT NOT NULL,
        expected_cents INTEGER NOT NULL DEFAULT 0,
        paid_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        payment_date TEXT, method TEXT, reference TEXT, remarks TEXT, receipt_path TEXT,
        auto_expected INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(class_id, month))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_class_bills_month ON class_bills(month)")

    if "makeup" not in cols("attendance"):
        con.execute("ALTER TABLE attendance ADD COLUMN makeup TEXT")
        # old 'replacement' rows kept the made-up date in remarks
        con.execute("""UPDATE attendance SET makeup = remarks
                        WHERE status = 'replacement' AND remarks LIKE '____-__-__'""")
    con.execute("UPDATE attendance SET status = 'student_leave' WHERE status = 'excused'")
    con.execute("UPDATE attendance SET status = 'holiday'       WHERE status = 'no_class'")

    acols = cols("attendance")
    if "makeup_plan" not in acols:
        con.execute("ALTER TABLE attendance ADD COLUMN makeup_plan TEXT")
        # earlier single-field makeup on no-show rows -> plan / final
        con.execute("""UPDATE attendance SET makeup_plan = makeup
                        WHERE status IN ('absent','student_leave','teacher_leave','holiday')
                          AND makeup IN ('discussing','tentative')""")
        con.execute("""UPDATE attendance SET makeup_plan = makeup
                        WHERE status IN ('absent','student_leave','teacher_leave','holiday')
                          AND makeup LIKE '____-__-__'""")
    if "makeup_final" not in acols:
        con.execute("ALTER TABLE attendance ADD COLUMN makeup_final TEXT")
        con.execute("""UPDATE attendance SET makeup_final = 'no'
                        WHERE status IN ('absent','student_leave','teacher_leave','holiday')
                          AND makeup = 'no'""")

    con.execute("""CREATE TABLE IF NOT EXISTS class_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        weekday INTEGER NOT NULL, start_time TEXT, end_time TEXT,
        UNIQUE(class_id, weekday))""")
    # backfill from the legacy single-day columns
    have = {r[0] for r in con.execute("SELECT DISTINCT class_id FROM class_schedule")}
    for row in con.execute(
            "SELECT id, weekday, start_time, end_time FROM classes WHERE weekday IS NOT NULL"):
        if row[0] in have:
            continue
        con.execute(
            "INSERT INTO class_schedule (class_id, weekday, start_time, end_time) VALUES (?,?,?,?)",
            (row[0], row[1], row[2], row[3]))


def backup_db(app):
    """Keep a daily copy of the database (last 21)."""
    db_path = app.config["DB_PATH"]
    if not os.path.exists(db_path):
        return
    bdir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = dt.date.today().isoformat()
    dest = os.path.join(bdir, f"tuition-{stamp}.db")
    if not os.path.exists(dest):
        try:
            shutil.copy2(db_path, dest)
        except OSError:
            return
    backups = sorted(
        (f for f in os.listdir(bdir) if f.startswith("tuition-") and f.endswith(".db"))
    )
    for old in backups[:-21]:
        try:
            os.remove(os.path.join(bdir, old))
        except OSError:
            pass
