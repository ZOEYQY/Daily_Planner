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
    avatar_color  TEXT    NOT NULL DEFAULT '#c2456f',
    remarks       TEXT,
    -- What a new student is looking for — feeds the teacher-match page
    -- (views/teachers.py match()) so matching can start the moment they're
    -- keyed in, without re-typing. All optional.
    match_subject TEXT,
    match_day     INTEGER,                        -- 0=Mon .. 6=Sun
    match_time    TEXT,
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
    -- Optional link to a Teachers-module profile, separate from the free-text
    -- `teacher` name above — only used to auto-compute a per-lesson commission
    -- (see engine.teacher_commission) when that teacher is paid per lesson.
    teacher_id        INTEGER REFERENCES teachers(id) ON DELETE SET NULL,
    location          TEXT,
    fee_model         TEXT    NOT NULL DEFAULT 'monthly',  -- legacy, always 'monthly' now
    pricing           TEXT    NOT NULL DEFAULT 'fixed',    -- legacy ('fixed' | 'per_student')
    default_fee_cents INTEGER NOT NULL DEFAULT 0,          -- legacy monthly fee, unused for billing
    -- ── per-lesson billing (the real model). every fee = amount × lessons that ran ──
    bill_mode         TEXT    NOT NULL DEFAULT 'student_attend',
    -- 'student_attend' : student's own per-lesson rate × that student's present dates (also forced for 1v1)
    -- 'class_ran'      : student's own per-lesson rate × dates the class ran (absence still billed)
    -- 'class_flat'     : one flat rate for the whole class × dates it ran -> billed to a family
    -- 'agent_headcount': (base + per_head × extra students) × dates it ran -> billed to an agent
    --                    where extra students = max(0, N enrolled − base_head_count)
    lesson_fee_cents  INTEGER NOT NULL DEFAULT 0,          -- per-lesson rate / flat class rate
    base_fee_cents    INTEGER NOT NULL DEFAULT 0,          -- agent_headcount: base per lesson
    base_head_count   INTEGER NOT NULL DEFAULT 0,          -- agent_headcount: students the base fee already covers
    per_head_cents    INTEGER NOT NULL DEFAULT 0,          -- agent_headcount: added per student beyond base_head_count
    agent_name        TEXT,
    agent_phone       TEXT,
    billed_family_id  INTEGER REFERENCES families(id) ON DELETE SET NULL,  -- class_flat: who gets the bill
    status            TEXT    NOT NULL DEFAULT 'active',    -- active/inactive
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Whole-class bills (bill_mode 'class_flat' / 'agent_headcount') ────
-- One bill per class per month, not split per student — mirrors the shape of
-- `payments` but keyed by class instead of student.
CREATE TABLE IF NOT EXISTS class_bills (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id       INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    month          TEXT    NOT NULL,                     -- 'YYYY-MM'
    expected_cents INTEGER NOT NULL DEFAULT 0,
    paid_cents     INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'pending',   -- pending/partial/paid/overdue
    payment_date   TEXT,
    method         TEXT,
    reference      TEXT,
    remarks        TEXT,
    receipt_path   TEXT,
    auto_expected  INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(class_id, month)
);
CREATE INDEX IF NOT EXISTS idx_class_bills_month ON class_bills(month);

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

-- ── Prospects (still deciding / trying us out — matching only, no billing,
--    no attendance; "转为正式学生" hands them off to a real students row) ──
CREATE TABLE IF NOT EXISTS prospects (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT    NOT NULL,
    phone                 TEXT,
    age                   INTEGER,
    grade                 TEXT,                     -- 年级, optional — helps level matching
    match_subject         TEXT    NOT NULL DEFAULT '[]',    -- JSON array of subject names
    match_day             INTEGER,                  -- 0=Mon .. 6=Sun — derived: earliest
    match_time            TEXT,                      -- row in prospect_availability
    budget_cents          INTEGER NOT NULL DEFAULT 0,       -- 学费预算 / fee expectation
    budget_unit           TEXT    NOT NULL DEFAULT 'month', -- 'hour' | 'lesson' | 'month'
    start_date            TEXT,                      -- 几时想开始
    -- A specific one-off trial-lesson slot — distinct from the recurring
    -- weekly availability above (prospect_availability).
    trial_date            TEXT,
    trial_time            TEXT,
    notes                 TEXT,
    parent_name           TEXT,
    parent_phone          TEXT,
    parent_relationship   TEXT,
    -- Tentative — who'd likely teach this student. Only for a rough commission
    -- preview (engine has no lessons/attendance yet at this stage); not carried
    -- over automatically when converted to a student.
    teacher_id            INTEGER REFERENCES teachers(id) ON DELETE SET NULL,
    status                TEXT    NOT NULL DEFAULT 'open',  -- open/converted/dropped
    converted_student_id  INTEGER REFERENCES students(id) ON DELETE SET NULL,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prospects_status ON prospects(status);

-- ── Prospect weekly availability (one time range per weekday, same shape as
--    teacher_availability / class_schedule) — "可以的时间"; covers both a
--    trial slot and the eventual regular class. match_day/match_time above
--    are kept in sync with the earliest row here for the match page. ──────
CREATE TABLE IF NOT EXISTS prospect_availability (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id  INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    weekday      INTEGER NOT NULL,
    start_time   TEXT,
    end_time     TEXT,
    UNIQUE(prospect_id, weekday)
);
CREATE INDEX IF NOT EXISTS idx_prospect_avail ON prospect_availability(prospect_id);

-- ── Teachers (profiles for matching new students) ───────────────────
CREATE TABLE IF NOT EXISTS teachers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en          TEXT,
    name_zh          TEXT,
    full_name        TEXT    NOT NULL,               -- derived display name
    phone            TEXT,
    email            TEXT,
    gender           TEXT,
    subjects         TEXT    NOT NULL DEFAULT '[]',  -- JSON array of subject names
    levels           TEXT    NOT NULL DEFAULT '[]',  -- JSON array of grade / level names
    languages        TEXT    NOT NULL DEFAULT '[]',  -- JSON array: 'en' / 'zh' / 'ms'
    experience_years INTEGER,
    rate_cents       INTEGER NOT NULL DEFAULT 0,
    rate_unit        TEXT    NOT NULL DEFAULT 'hour',  -- 'hour' | 'lesson' | 'month'
    bio              TEXT,
    -- ── screening / basic-info intake ("通常会问老师的基本资料") ──
    age                  INTEGER,
    experience_summary   TEXT,   -- e.g. "1V1 小学-英文/数学/科学补习" — what/how, not a year count
    current_work         TEXT,   -- 目前工作/大学
    academic_results     TEXT,   -- 成绩（如果是大学生）
    subjects_notes       TEXT,   -- free text for level+subject combos the chip pickers
                                  -- can't express cleanly, e.g. "小学-All, Form1-3-Math/Science"
    -- 理想时薪 (asking-rate range at interview, RM/hour) — separate from rate_cents/
    -- rate_unit above, which is what they're actually paid once hired/assigned.
    rate_1v1_min_cents   INTEGER,
    rate_1v1_max_cents   INTEGER,
    rate_group_min_cents INTEGER,
    rate_group_max_cents INTEGER,
    has_tablet           INTEGER,   -- 0/1/NULL(unknown) — 有平板吗？
    avatar_color     TEXT    NOT NULL DEFAULT '#c2456f',
    status           TEXT    NOT NULL DEFAULT 'active',  -- active/inactive
    remarks          TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Teacher weekly availability (one time range per weekday) ─────────
CREATE TABLE IF NOT EXISTS teacher_availability (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    weekday     INTEGER NOT NULL,                    -- 0=Mon .. 6=Sun
    start_time  TEXT,                                -- '' / NULL = any time that day
    end_time    TEXT,
    UNIQUE(teacher_id, weekday)
);
CREATE INDEX IF NOT EXISTS idx_teacher_avail ON teacher_availability(teacher_id);

-- ── Audit log ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT    NOT NULL,
    detail      TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
