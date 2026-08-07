-- Smart Campus schema for Sūtra (AgentX 2026). SQLite.
-- Matches packages/contracts/actions.py (PendingAction) and the tools/
-- registry's write-tool contract: every write also inserts a `receipts` row.

CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,               -- roll number, e.g. 1602-23-733-042
    name TEXT NOT NULL,
    branch TEXT NOT NULL,              -- CSE, IT, ECE, EEE, MECH
    year INTEGER NOT NULL,             -- 1-4
    cgpa REAL NOT NULL,
    backlogs INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'student'  -- student | faculty | admin
);

CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,               -- e.g. CS301
    name TEXT NOT NULL,
    branch TEXT NOT NULL,
    year INTEGER NOT NULL,
    credits INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(id),
    course_id TEXT NOT NULL REFERENCES courses(id)
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(id),
    course_id TEXT NOT NULL REFERENCES courses(id),
    classes_held INTEGER NOT NULL,
    classes_attended INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL REFERENCES courses(id),
    branch TEXT NOT NULL,
    year INTEGER NOT NULL,
    day_of_week TEXT NOT NULL,         -- Monday..Sunday
    start_time TEXT NOT NULL,          -- "14:00"
    end_time TEXT NOT NULL,            -- "16:00"
    session_type TEXT NOT NULL DEFAULT 'lecture'  -- lecture | lab
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL REFERENCES courses(id),
    exam_type TEXT NOT NULL,           -- internal | semester_end
    date TEXT NOT NULL,
    start_time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    min_cgpa REAL NOT NULL,
    max_backlogs INTEGER NOT NULL,
    eligible_branches TEXT NOT NULL,   -- comma-separated, e.g. "CSE,IT"
    application_deadline TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(id),
    company_id TEXT NOT NULL REFERENCES companies(id),
    status TEXT NOT NULL DEFAULT 'applied',
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    day_of_week TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    total_seats INTEGER NOT NULL,
    seats_taken INTEGER NOT NULL DEFAULT 0,
    category TEXT                       -- workshop | hackathon | club
);

CREATE TABLE IF NOT EXISTS event_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(id),
    student_id TEXT NOT NULL REFERENCES students(id),
    registered_at TEXT,
    -- Belt-and-braces behind the application-level idempotency check in
    -- tools/events.py: a double registration must be impossible, not merely
    -- unlikely, so a retried approval can never consume two seats.
    UNIQUE (event_id, student_id)
);

CREATE TABLE IF NOT EXISTS clubs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS hostel_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT REFERENCES students(id),
    block TEXT NOT NULL,
    room_number TEXT NOT NULL,
    no_dues INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS library_loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(id),
    book_title TEXT NOT NULL,
    borrowed_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    returned INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scholarships (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    min_cgpa REAL,
    max_income REAL,
    deadline TEXT
);

CREATE TABLE IF NOT EXISTS grievances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(id),
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    filed_at TEXT
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(id),
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    source TEXT                        -- e.g. "event:evt_workshop_sat"
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(id),
    message TEXT NOT NULL,
    remind_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,               -- student_id or "system"
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    ts REAL NOT NULL,
    approved_by TEXT
);

CREATE TABLE IF NOT EXISTS memory_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL REFERENCES students(id),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_turn TEXT,
    updated_at REAL NOT NULL
);
