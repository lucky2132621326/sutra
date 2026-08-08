"""Academic Agent tools: timetable, attendance, eligibility, schedule conflicts, electives."""
import math

from sqlalchemy import text

from apps.api.tools.db import Session
from apps.api.tools.exceptions import RecordNotFound
from apps.api.tools.models import (
    AttendanceImpact,
    AttendanceEligibility,
    AttendanceRecord,
    AttendanceResult,
    ElectiveRecommendation,
    ElectiveRecommendations,
    ScheduleConflict,
    TimetableEntry,
    TimetableResult,
)

REQUIRED_ATTENDANCE_PCT = 75.0
CONDONATION_FLOOR_PCT = 65.0


def _student_or_404(session, student_id: str):
    row = session.execute(text("SELECT * FROM students WHERE id=:id"), {"id": student_id}).mappings().first()
    if not row:
        raise RecordNotFound(f"No student with id {student_id}")
    return row


def get_timetable(student_id: str) -> TimetableResult:
    with Session() as session:
        student = _student_or_404(session, student_id)
        rows = session.execute(
            text(
                "SELECT t.course_id, c.name AS course_name, t.day_of_week, t.start_time, t.end_time, t.session_type "
                "FROM timetable t JOIN courses c ON c.id = t.course_id "
                "WHERE t.branch=:branch AND t.year=:year "
                "ORDER BY CASE t.day_of_week "
                "WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 "
                "WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END, t.start_time"
            ),
            {"branch": student["branch"], "year": student["year"]},
        ).mappings().all()
        return TimetableResult(
            student_id=student_id,
            entries=[TimetableEntry(**dict(r)) for r in rows],
        )


def get_attendance(student_id: str) -> AttendanceResult:
    with Session() as session:
        _student_or_404(session, student_id)
        rows = session.execute(
            text(
                "SELECT a.course_id, c.name AS course_name, a.classes_held, a.classes_attended "
                "FROM attendance a JOIN courses c ON c.id = a.course_id WHERE a.student_id=:sid"
            ),
            {"sid": student_id},
        ).mappings().all()
        records = [
            AttendanceRecord(
                course_id=r["course_id"], course_name=r["course_name"],
                classes_held=r["classes_held"], classes_attended=r["classes_attended"],
                percentage=round(100 * r["classes_attended"] / r["classes_held"], 1) if r["classes_held"] else 0.0,
            )
            for r in rows
        ]
        return AttendanceResult(student_id=student_id, records=records)


def compute_attendance_eligibility(student_id: str, course_id: str) -> AttendanceEligibility:
    with Session() as session:
        _student_or_404(session, student_id)
        course_id = _resolve_course(session, student_id, course_id)
        row = session.execute(
            text("SELECT classes_held, classes_attended FROM attendance WHERE student_id=:sid AND course_id=:cid"),
            {"sid": student_id, "cid": course_id},
        ).mappings().first()
        if not row:
            raise RecordNotFound(f"No attendance record for {student_id} in {course_id}")

        held, attended = row["classes_held"], row["classes_attended"]
        pct = round(100 * attended / held, 1) if held else 0.0

        # classes needed (n more, all attended) so that (attended+n)/(held+n) >= 75%
        needed = 0
        if pct < REQUIRED_ATTENDANCE_PCT:
            needed = max(0, math.ceil((0.75 * held - attended) / 0.25))

        return AttendanceEligibility(
            student_id=student_id, course_id=course_id, current_pct=pct,
            classes_attended=attended, classes_held=held, classes_needed_for_75=needed,
            is_eligible=pct >= REQUIRED_ATTENDANCE_PCT,
            condonation_possible=CONDONATION_FLOOR_PCT <= pct < REQUIRED_ATTENDANCE_PCT,
        )


def _resolve_course(session, student_id: str, course_id: str) -> str:
    """Accept a course NAME where an id was expected.

    The caller is a language model reading an answer that says "DBMS Lab", not
    a schema that says "CS301L" — so it passes the name, the lookup misses, and
    the run reports "no attendance record for DBMS Lab" in the same breath as
    printing that course's attendance. Resolving the name here is far more
    honest than surfacing a contradiction.
    """
    row = session.execute(
        text("SELECT course_id FROM attendance "
             "WHERE student_id=:sid AND LOWER(course_id)=LOWER(:cid)"),
        {"sid": student_id, "cid": course_id},
    ).mappings().first()
    if row:
        return row["course_id"]

    needle = course_id.strip().lower()
    candidates = session.execute(
        text("SELECT a.course_id, c.name FROM attendance a JOIN courses c ON c.id = a.course_id "
             "WHERE a.student_id=:sid"),
        {"sid": student_id},
    ).mappings().all()
    for r in candidates:
        if r["name"].strip().lower() == needle:
            return r["course_id"]
    # Fall back to a containment match ("DBMS" -> "DBMS Lab"), preferring the
    # shortest name so "DBMS" doesn't silently pick a longer unrelated course.
    partial = sorted(
        (r for r in candidates if needle and (needle in r["name"].lower() or r["name"].lower() in needle)),
        key=lambda r: len(r["name"]),
    )
    return partial[0]["course_id"] if partial else course_id


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def check_schedule_conflict(student_id: str, day_of_week: str, start_time: str, end_time: str) -> ScheduleConflict:
    """Does a proposed [start_time, end_time) window on day_of_week overlap any
    class already on the student's timetable?"""
    with Session() as session:
        student = _student_or_404(session, student_id)
        rows = session.execute(
            text(
                "SELECT t.course_id, c.name AS course_name, t.start_time, t.end_time, t.session_type "
                "FROM timetable t JOIN courses c ON c.id = t.course_id "
                "WHERE t.branch=:branch AND t.year=:year AND t.day_of_week=:day"
            ),
            {"branch": student["branch"], "year": student["year"], "day": day_of_week},
        ).mappings().all()

        req_start, req_end = _to_minutes(start_time), _to_minutes(end_time)
        for r in rows:
            existing_start, existing_end = _to_minutes(r["start_time"]), _to_minutes(r["end_time"])
            if req_start < existing_end and existing_start < req_end:
                return ScheduleConflict(
                    has_conflict=True, conflicting_course_id=r["course_id"],
                    conflicting_session=r["session_type"],
                    detail=f"{r['course_name']} ({r['session_type']}) on {day_of_week} "
                           f"{r['start_time']}-{r['end_time']} overlaps the requested window.",
                )
        return ScheduleConflict(has_conflict=False, detail="No conflict found.")


def project_attendance_impact(student_id: str, course_id: str, sessions_missed: int = 1) -> AttendanceImpact:
    """What missing `sessions_missed` more sessions would do to this course.

    Turns a schedule clash from a vibe ("you'd miss a lab") into a checkable
    number ("70.3% -> 68.4%, and you'd need 19 consecutive sessions to recover"),
    which is what makes the arbitration verifiable rather than assertive.
    """
    with Session() as session:
        _student_or_404(session, student_id)
        course_id = _resolve_course(session, student_id, course_id)
        row = session.execute(
            text("SELECT a.classes_held, a.classes_attended, c.name AS course_name "
                 "FROM attendance a JOIN courses c ON c.id = a.course_id "
                 "WHERE a.student_id=:sid AND a.course_id=:cid"),
            {"sid": student_id, "cid": course_id},
        ).mappings().first()
        if not row:
            raise RecordNotFound(f"No attendance record for {student_id} in {course_id}")

        held, attended = row["classes_held"], row["classes_attended"]
        current = round(100 * attended / held, 2) if held else 0.0
        # Missing a session raises the denominator but not the numerator.
        projected_held = held + sessions_missed
        projected = round(100 * attended / projected_held, 2) if projected_held else 0.0

        needed = 0
        if projected < REQUIRED_ATTENDANCE_PCT:
            needed = max(0, math.ceil((0.75 * projected_held - attended) / 0.25))

        return AttendanceImpact(
            student_id=student_id, course_id=course_id, course_name=row["course_name"],
            current_pct=current, projected_pct=projected,
            delta_pct=round(projected - current, 2),
            classes_attended=attended, classes_held=held,
            sessions_missed=sessions_missed,
            crosses_threshold=current >= REQUIRED_ATTENDANCE_PCT > projected,
            already_below=current < REQUIRED_ATTENDANCE_PCT,
            sessions_needed_to_recover=needed,
        )


def recommend_electives(student_id: str, interest: str = "") -> ElectiveRecommendations:
    with Session() as session:
        student = _student_or_404(session, student_id)
        rows = session.execute(
            text("SELECT id, name FROM courses WHERE branch=:branch AND year=:year"),
            {"branch": student["branch"], "year": student["year"]},
        ).mappings().all()
        recs = []
        for r in rows:
            if interest and interest.lower() not in r["name"].lower():
                continue
            recs.append(ElectiveRecommendation(
                course_id=r["id"], course_name=r["name"],
                reason=f"Matches your stated interest in {interest}." if interest else "Offered for your branch/year.",
            ))
        if not recs:
            recs = [
                ElectiveRecommendation(course_id=r["id"], course_name=r["name"], reason="Offered for your branch/year.")
                for r in rows
            ]
        return ElectiveRecommendations(student_id=student_id, recommendations=recs)
