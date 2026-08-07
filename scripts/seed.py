"""
Seed data/campus.db from data/schema.sql for a Hyderabad autonomous
engineering college. Deterministic — every run wipes and rebuilds.

The demo scenario (battle plan Section 7) depends on these EXACT rows:
  - Ananya Reddy, 3rd yr CSE, CGPA 8.4, 0 backlogs, roll 1602-23-733-042
  - Ananya's DBMS Lab attendance: 26/37 classes (~70.3%, below the 75% bar)
  - Google SDE Intern: min CGPA 8.0, max backlogs 0, CSE+IT -> Ananya PASSES
  - Goldman Sachs: min CGPA 8.5 -> Ananya FAILS by 0.1
  - "Placement Prep Workshop" Thu 14:00-16:00, 30 seats, 28 taken
  - Ananya's timetable has DBMS Lab Thu 14:00-16:00 -> direct collision
  - Same workshop, Saturday 10:00-12:00 batch, 30 seats, 28 taken (2 left)
  - Rahul Verma, 2nd yr MECH, CGPA 7.2, 2 backlogs -> fails everything

Run: python scripts/seed.py
"""
import random
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "campus.db"
SCHEMA_PATH = ROOT / "data" / "schema.sql"

BRANCHES = ["CSE", "IT", "ECE", "EEE", "MECH"]

FIRST_NAMES = [
    "Sai", "Varun", "Priya", "Kavya", "Arjun", "Meera", "Rohit", "Divya",
    "Karthik", "Sneha", "Vikram", "Anjali", "Nikhil", "Pooja", "Aditya",
    "Lakshmi", "Rahul", "Swathi", "Manoj", "Deepika", "Suresh", "Harika",
    "Kiran", "Nandini", "Praveen", "Bhavya", "Srikanth", "Tanvi", "Ajay",
    "Ritu", "Naveen", "Shreya", "Vishal", "Aparna", "Gautam", "Ishita",
    "Ravi", "Neha", "Sandeep", "Pallavi",
]
LAST_NAMES = [
    "Reddy", "Rao", "Sharma", "Verma", "Kumar", "Naidu", "Gupta", "Iyer",
    "Chowdary", "Reddy", "Prasad", "Singh", "Nair", "Rao", "Reddy", "Mehta",
    "Yadav", "Chandra", "Reddy", "Gowda",
]


def build_students():
    """40 students total: 2 exact demo personas + 38 generated, spread
    across branches/years."""
    students = [
        # id, name, branch, year, cgpa, backlogs
        ("1602-23-733-042", "Ananya Reddy", "CSE", 3, 8.4, 0),
        ("1602-24-736-018", "Rahul Verma", "MECH", 2, 7.2, 2),
    ]
    random.seed(42)  # deterministic across re-seeds
    branch_codes = {"CSE": "733", "IT": "734", "ECE": "735", "EEE": "737", "MECH": "736"}
    used_names = {("Ananya", "Reddy"), ("Rahul", "Verma")}
    n = 1
    while len(students) < 40:
        branch = BRANCHES[n % len(BRANCHES)]
        year = 1 + (n % 4)
        first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        if (first, last) in used_names:
            n += 1
            continue
        used_names.add((first, last))
        admit_year = 27 - year  # rough: current year 2026 batch codes
        roll = f"1602-{admit_year}-{branch_codes[branch]}-{n:03d}"
        cgpa = round(random.uniform(6.2, 9.4), 1)
        backlogs = random.choices([0, 0, 0, 1, 2], weights=[6, 3, 2, 2, 1])[0]
        students.append((roll, f"{first} {last}", branch, year, cgpa, backlogs))
        n += 1
    return students


COURSES = [
    ("CS301", "Database Management Systems", "CSE", 3, 4),
    ("CS301L", "DBMS Lab", "CSE", 3, 2),
    ("CS302", "Operating Systems", "CSE", 3, 4),
    ("CS303", "Computer Networks", "CSE", 3, 3),
    ("CS304", "Software Engineering", "CSE", 3, 3),
    ("CS305", "Machine Learning Foundations", "CSE", 3, 3),
    ("ME301", "Thermodynamics", "MECH", 2, 4),
    ("ME302", "Fluid Mechanics", "MECH", 2, 4),
]

# Ananya's timetable — DBMS Lab Thursday 14:00-16:00 is the collision anchor.
ANANYA_TIMETABLE = [
    ("CS301L", "CSE", 3, "Thursday", "14:00", "16:00", "lab"),
    ("CS301", "CSE", 3, "Monday", "09:00", "10:00", "lecture"),
    ("CS302", "CSE", 3, "Monday", "11:00", "12:00", "lecture"),
    ("CS303", "CSE", 3, "Tuesday", "09:00", "10:00", "lecture"),
    ("CS304", "CSE", 3, "Wednesday", "10:00", "11:00", "lecture"),
    ("CS305", "CSE", 3, "Friday", "09:00", "10:00", "lecture"),
]

COMPANIES = [
    ("google", "Google", "SDE Intern", 8.0, 0, "CSE,IT", "2026-09-15"),
    ("goldman", "Goldman Sachs", "Technology Analyst", 8.5, 0, "CSE,IT,ECE", "2026-09-20"),
    ("microsoft", "Microsoft", "SDE Intern", 7.5, 1, "CSE,IT,ECE,EEE", "2026-09-10"),
    ("tcs", "TCS", "Assistant System Engineer", 6.0, 2, "CSE,IT,ECE,EEE,MECH", "2026-10-01"),
    ("infosys", "Infosys", "Systems Engineer", 6.5, 2, "CSE,IT,ECE,EEE,MECH", "2026-10-05"),
    ("amazon", "Amazon", "SDE Intern", 7.8, 0, "CSE,IT", "2026-09-18"),
    ("deloitte", "Deloitte", "Analyst", 7.0, 1, "CSE,IT,ECE,EEE", "2026-09-25"),
    ("bosch", "Bosch", "Graduate Engineer Trainee", 6.8, 1, "MECH,EEE,ECE", "2026-10-10"),
]

EVENTS = [
    ("evt_workshop_thu", "Placement Prep Workshop", "Resume building and mock interviews for placement season.",
     "Thursday", "2026-08-13", "14:00", "16:00", 30, 28, "workshop"),
    ("evt_workshop_sat", "Placement Prep Workshop (Saturday Batch)",
     "Same content as Thursday's session, alternate batch.", "Saturday", "2026-08-15", "10:00", "12:00", 30, 28, "workshop"),
    ("evt_ai_ml_1", "Intro to Generative AI", "Hands-on workshop on LLMs and prompt engineering.",
     "Monday", "2026-08-10", "15:00", "17:00", 60, 22, "workshop"),
    ("evt_ai_ml_2", "Deep Learning with PyTorch", "Building and training neural networks from scratch.",
     "Wednesday", "2026-08-12", "14:00", "17:00", 50, 41, "workshop"),
    ("evt_ai_ml_3", "AI Agents & LangGraph", "Multi-agent systems and orchestration patterns.",
     "Friday", "2026-08-14", "16:00", "18:00", 40, 33, "workshop"),
    ("evt_hackathon_1", "Smart Campus Hackathon", "24-hour build sprint on campus-tech problem statements.",
     "Saturday", "2026-08-22", "09:00", "23:59", 200, 145, "hackathon"),
    ("evt_club_ml", "ML Club Weekly Meetup", "Paper reading and project showcase.",
     "Tuesday", "2026-08-11", "17:00", "18:30", 40, 19, "club"),
    ("evt_club_robotics", "Robotics Club Build Night", "Line-follower bot assembly session.",
     "Thursday", "2026-08-13", "17:00", "19:00", 30, 12, "club"),
    ("evt_career_fair", "Autumn Career Fair", "20+ companies on campus for internship and full-time roles.",
     "Wednesday", "2026-09-03", "10:00", "16:00", 500, 210, "workshop"),
    ("evt_cloud_ws", "Cloud Computing with AWS", "Hands-on AWS fundamentals workshop.",
     "Monday", "2026-08-17", "15:00", "17:00", 45, 30, "workshop"),
    ("evt_cyber_ws", "Cybersecurity Basics", "Intro to ethical hacking and network security.",
     "Tuesday", "2026-08-18", "14:00", "16:00", 40, 15, "workshop"),
    ("evt_alumni_talk", "Alumni Talk: Life at a Startup", "Panel discussion with three alumni founders.",
     "Friday", "2026-08-21", "17:00", "18:30", 100, 40, "workshop"),
]

CLUBS = [
    ("club_ml", "Machine Learning Club", "Technical", "Weekly ML paper reading and Kaggle projects."),
    ("club_robotics", "Robotics Club", "Technical", "Bot-building and autonomous navigation projects."),
    ("club_coding", "Competitive Coding Club", "Technical", "Weekly contests and interview prep."),
    ("club_music", "Music Club", "Cultural", "Campus band and open-mic nights."),
    ("club_dance", "Dance Club", "Cultural", "Classical and contemporary dance troupe."),
    ("club_debate", "Debate & Literary Society", "Cultural", "Debates, MUNs, and creative writing."),
    ("club_photography", "Photography Club", "Cultural", "Campus event coverage and photo-walks."),
    ("club_entrepreneur", "Entrepreneurship Cell", "Technical", "Startup pitching and mentorship sessions."),
]


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        try:
            DB_PATH.unlink()
        except PermissionError:
            # Windows refuses to unlink a file another process has open, so
            # re-seeding while the API server is running failed outright and
            # took the whole test suite down with it. Dropping the tables is
            # equivalent for our purposes and needs no exclusive lock — the
            # schema is recreated immediately below.
            conn = sqlite3.connect(DB_PATH)
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for name in names:
                conn.execute(f'DROP TABLE IF EXISTS "{name}"')
            conn.commit()
            conn.close()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text())

    students = build_students()
    conn.executemany(
        "INSERT INTO students (id, name, branch, year, cgpa, backlogs) VALUES (?,?,?,?,?,?)",
        students,
    )

    conn.executemany(
        "INSERT INTO courses (id, name, branch, year, credits) VALUES (?,?,?,?,?)",
        COURSES,
    )

    # Ananya's timetable + attendance
    for course_id, branch, year, day, start, end, session_type in ANANYA_TIMETABLE:
        conn.execute(
            "INSERT INTO timetable (course_id, branch, year, day_of_week, start_time, end_time, session_type) "
            "VALUES (?,?,?,?,?,?,?)",
            (course_id, branch, year, day, start, end, session_type),
        )
        conn.execute("INSERT INTO enrollments (student_id, course_id) VALUES (?,?)", ("1602-23-733-042", course_id))
        held, attended = (37, 26) if course_id == "CS301L" else (34, 30)
        conn.execute(
            "INSERT INTO attendance (student_id, course_id, classes_held, classes_attended) VALUES (?,?,?,?)",
            ("1602-23-733-042", course_id, held, attended),
        )

    conn.executemany(
        "INSERT INTO companies (id, name, role, min_cgpa, max_backlogs, eligible_branches, application_deadline) "
        "VALUES (?,?,?,?,?,?,?)",
        COMPANIES,
    )

    conn.executemany(
        "INSERT INTO events (id, title, description, day_of_week, date, start_time, end_time, "
        "total_seats, seats_taken, category) VALUES (?,?,?,?,?,?,?,?,?,?)",
        EVENTS,
    )
    for eid, _, _, _, _, _, _, _, taken, _ in EVENTS:
        for i in range(taken):
            conn.execute(
                "INSERT INTO event_registrations (event_id, student_id, registered_at) VALUES (?,?,?)",
                (eid, f"filler-{eid}-{i}", "2026-08-01T00:00:00"),
            )

    conn.executemany(
        "INSERT INTO clubs (id, name, category, description) VALUES (?,?,?,?)",
        CLUBS,
    )

    conn.execute(
        "INSERT INTO hostel_rooms (student_id, block, room_number, no_dues) VALUES (?,?,?,?)",
        ("1602-23-733-042", "B-Block", "214", 1),
    )
    conn.execute(
        "INSERT INTO library_loans (student_id, book_title, borrowed_at, due_at, returned) VALUES (?,?,?,?,?)",
        ("1602-23-733-042", "Database System Concepts", "2026-07-20", "2026-08-20", 0),
    )
    conn.execute(
        "INSERT INTO scholarships (id, name, min_cgpa, max_income, deadline) VALUES (?,?,?,?,?)",
        ("sch_merit_2026", "State Merit Scholarship 2026", 8.0, 600000, "2026-09-30"),
    )

    conn.commit()

    print("Seeded data/campus.db:")
    for table in ["students", "courses", "timetable", "attendance", "companies",
                   "events", "event_registrations", "clubs", "hostel_rooms",
                   "library_loans", "scholarships"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<20} {count}")

    ananya = conn.execute("SELECT * FROM students WHERE id=?", ("1602-23-733-042",)).fetchone()
    dbms_att = conn.execute(
        "SELECT classes_attended, classes_held FROM attendance WHERE student_id=? AND course_id='CS301L'",
        ("1602-23-733-042",),
    ).fetchone()
    print(f"\nAnanya Reddy: {ananya}")
    print(f"DBMS Lab attendance: {dbms_att[0]}/{dbms_att[1]} = {100 * dbms_att[0] / dbms_att[1]:.1f}%")

    conn.close()


if __name__ == "__main__":
    main()
