-- Tuition Management System — database schema
-- All money is stored as integer sen (RM * 100). Display is whole ringgit.

PRAGMA foreign_keys = ON;

-- ── Settings / single-teacher account ────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    id                     INTEGER PRIMARY KEY CHECK (id = 1),
    teacher_name           TEXT    NOT NULL DEFAULT 'Teacher',
    password_hash          TEXT    NOT NULL DEFAULT '',
    language               TEXT    NOT NULL DEFAULT 'en',
    currency               TEXT    NOT NULL DEFAULT 'RM',
    charge_absence         INTEGER NOT NULL DEFAULT 0,   -- per-lesson model: bill an 'absent' lesson?
    result_subjects        TEXT    NOT NULL DEFAULT '["Bahasa Melayu","English","Mathematics","Science","Chinese"]',
    payment_info           TEXT    NOT NULL DEFAULT '',
    created_at             TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at             TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Families / households (siblings billed together) ─────────────────
CREATE TABLE IF NOT EXISTS families (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    remarks    TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Students ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name_zh       TEXT,
    name_en       TEXT,
    full_name     TEXT    NOT NULL,               -- derived display name
    phone         TEXT,
    gender        TEXT,
    dob           TEXT,
    age           INTEGER,
    school        TEXT,
    grade         TEXT,                           -- 年级
    address       TEXT,
    family_id     INTEGER REFERENCES families(id) ON DELETE SET NULL,
    status        TEXT    NOT NULL DEFAULT 'active',  -- active/trial/inactive/left
    joined_date   TEXT,
    trial_date    TEXT,
    trial_remarks TEXT,
    left_date     TEXT,
    left_reason   TEXT,
    avatar_color  TEXT    NOT NULL DEFAULT '#2f4b7c',
    remarks       TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_students_status ON students(status);

-- ── Parents / guardians (max 2 per student) ──────────────────────────
CREATE TABLE IF NOT EXISTS parents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    slot          INTEGER NOT NULL DEFAULT 1,     -- 1 or 2
    name_zh       TEXT,
    name_en       TEXT,
    phone         TEXT,
    relationship  TEXT,
    email         TEXT,
    UNIQUE(student_id, slot)
);

-- ── Classes ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS classes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    subject           TEXT,
    level             TEXT,                       -- grade / form, used in the auto name
    kind              TEXT,                       -- '1v1' | 'small' | NULL
    weekday           INTEGER,                    -- legacy single day; real schedule = class_schedule
    start_time        TEXT,                       -- legacy
    end_time          TEXT,                       -- legacy
    teacher           TEXT,
    location          TEXT,
    fee_model         TEXT    NOT NULL DEFAULT 'monthly',  -- 'monthly' | 'per_lesson'
    pricing           TEXT    NOT NULL DEFAULT 'fixed',    -- 'fixed' (class fee for all) | 'per_student'
    default_fee_cents INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'active',    -- active/inactive
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Class weekly schedule (a class can run several days; time may differ) ──
CREATE TABLE IF NOT EXISTS class_schedule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    weekday     INTEGER NOT NULL,                 -- 0=Mon .. 6=Sun
    start_time  TEXT,                             -- optional ('' / NULL = time varies)
    end_time    TEXT,
    UNIQUE(class_id, weekday)
);

-- ── Enrollments (student ↔ class, append-only history) ───────────────
CREATE TABLE IF NOT EXISTS enrollments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    start_date  TEXT    NOT NULL,
    end_date    TEXT,
    status      TEXT    NOT NULL DEFAULT 'active',   -- active/ended
    remarks     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_enroll_student ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enroll_class   ON enrollments(class_id);

-- ── Fees (per student + class, dated, append-only) ───────────────────
CREATE TABLE IF NOT EXISTS fees (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    class_id       INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    fee_model      TEXT    NOT NULL DEFAULT 'monthly',   -- 'monthly' | 'per_lesson'
    amount_cents   INTEGER NOT NULL,                     -- monthly fee OR per-lesson fee
    discount_cents INTEGER NOT NULL DEFAULT 0,
    effective_from TEXT    NOT NULL,                     -- 'YYYY-MM-DD'
    effective_to   TEXT,
    remarks        TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fees_lookup ON fees(student_id, class_id, effective_from);

-- ── Attendance (one row per student + class + date) ──────────────────
CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    date        TEXT    NOT NULL,                        -- 'YYYY-MM-DD'
    status       TEXT    NOT NULL,  -- present/absent/student_leave/teacher_leave/holiday/replacement/trial
    makeup       TEXT,              -- 'replacement' rows: which past date this covers
    makeup_plan  TEXT,              -- no-show rows, 预计: '' | 'discussing' | '<date>'
    makeup_final TEXT,              -- no-show rows, 最后决定: '' | 'no' | '<date>'
    remarks      TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(student_id, class_id, date)
);
CREATE INDEX IF NOT EXISTS idx_att_class_date ON attendance(class_id, date);
CREATE INDEX IF NOT EXISTS idx_att_student    ON attendance(student_id, date);

-- ── Results (academic history, append-only) ──────────────────────────
CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    exam_name   TEXT,
    exam_date   TEXT,
    subject     TEXT,
    score       REAL,
    total       REAL    NOT NULL DEFAULT 100,
    grade       TEXT,
    remarks     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_results_student ON results(student_id, exam_date);

-- ── Payments (one row per student + class + month) ───────────────────
CREATE TABLE IF NOT EXISTS payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    class_id       INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    month          TEXT    NOT NULL,                     -- 'YYYY-MM'
    expected_cents INTEGER NOT NULL DEFAULT 0,
    paid_cents     INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'pending',   -- pending/partial/paid/overdue
    payment_date   TEXT,
    method         TEXT,                                 -- cash/bank/duitnow/other
    reference      TEXT,
    remarks        TEXT,
    receipt_path   TEXT,                                 -- file in <DATA_DIR>/receipts/
    auto_expected  INTEGER NOT NULL DEFAULT 1,           -- 1 = expected kept in sync by engine
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(student_id, class_id, month)
);
CREATE INDEX IF NOT EXISTS idx_pay_month ON payments(month);
CREATE INDEX IF NOT EXISTS idx_pay_class ON payments(class_id, month);

-- ── Monthly closing snapshots ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS monthly_finance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    month             TEXT    NOT NULL UNIQUE,           -- 'YYYY-MM'
    expected_cents    INTEGER NOT NULL DEFAULT 0,
    collected_cents   INTEGER NOT NULL DEFAULT 0,
    outstanding_cents INTEGER NOT NULL DEFAULT 0,
    total_students    INTEGER NOT NULL DEFAULT 0,
    total_classes     INTEGER NOT NULL DEFAULT 0,
    attendance_rate   REAL,
    snapshot_json     TEXT,
    closed_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Audit log ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT    NOT NULL,
    detail      TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
