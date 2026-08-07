"""Services & Comms Agent tools: hostel, library, grievances, email, calendar,
reminders, escalation.

file_grievance and send_email require approval (write + irreversible/
externally-visible). Same PendingAction pattern as register_event: called
without approved=True, no write happens.
"""
import time
import uuid

from sqlalchemy import text

from apps.api.tools.db import Session
from apps.api.tools.exceptions import RecordNotFound
from apps.api.tools.models import (
    CalendarEventResult,
    DraftEmailResult,
    EscalationResult,
    GrievanceResult,
    HostelInfo,
    LibraryLoan,
    LibraryLoansResult,
    PendingAction,
    ReminderResult,
    RenewBookResult,
    SendEmailResult,
)
from apps.api.tools.receipts import write_receipt


def _student_or_404(session, student_id: str):
    row = session.execute(text("SELECT * FROM students WHERE id=:id"), {"id": student_id}).mappings().first()
    if not row:
        raise RecordNotFound(f"No student with id {student_id}")
    return row


def get_hostel_info(student_id: str) -> HostelInfo:
    with Session() as session:
        _student_or_404(session, student_id)
        row = session.execute(
            text("SELECT * FROM hostel_rooms WHERE student_id=:sid"), {"sid": student_id}
        ).mappings().first()
        if not row:
            return HostelInfo(student_id=student_id)
        return HostelInfo(student_id=student_id, block=row["block"], room_number=row["room_number"],
                           no_dues=bool(row["no_dues"]))


def library_loans(student_id: str) -> LibraryLoansResult:
    with Session() as session:
        _student_or_404(session, student_id)
        rows = session.execute(
            text("SELECT * FROM library_loans WHERE student_id=:sid"), {"sid": student_id}
        ).mappings().all()
        loans = [
            LibraryLoan(book_title=r["book_title"], borrowed_at=r["borrowed_at"], due_at=r["due_at"],
                        returned=bool(r["returned"]))
            for r in rows
        ]
        return LibraryLoansResult(student_id=student_id, loans=loans)


def renew_book(student_id: str, book_title: str, actor: str = "") -> RenewBookResult:
    """Not requires_approval — renewal is reversible and low-stakes."""
    with Session() as session:
        row = session.execute(
            text("SELECT * FROM library_loans WHERE student_id=:sid AND book_title=:title AND returned=0"),
            {"sid": student_id, "title": book_title},
        ).mappings().first()
        if not row:
            raise RecordNotFound(f"No active loan of '{book_title}' for {student_id}")

        new_due = time.strftime("%Y-%m-%d", time.localtime(time.time() + 14 * 86400))
        session.execute(
            text("UPDATE library_loans SET due_at=:due WHERE id=:id"), {"due": new_due, "id": row["id"]}
        )
        receipt_id = write_receipt(
            session, actor=actor or student_id, tool="renew_book",
            args={"student_id": student_id, "book_title": book_title}, result={"new_due_at": new_due},
        )
        session.commit()
        return RenewBookResult(receipt_id=receipt_id, book_title=book_title, new_due_at=new_due)


def file_grievance(student_id: str, category: str, description: str, actor: str = "", approved: bool = False):
    """requires_approval."""
    if not approved:
        return PendingAction(
            id=uuid.uuid4().hex[:8], agent="services", tool="file_grievance",
            args={"student_id": student_id, "category": category, "description": description},
            description=f"File a {category} grievance on behalf of {student_id}.",
            risk="medium", reversible=False, preview=description,
        )
    with Session() as session:
        _student_or_404(session, student_id)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        result = session.execute(
            text(
                "INSERT INTO grievances (student_id, category, description, status, filed_at) "
                "VALUES (:sid, :cat, :desc, 'open', :ts)"
            ),
            {"sid": student_id, "cat": category, "desc": description, "ts": now},
        )
        grievance_id = result.lastrowid
        receipt_id = write_receipt(
            session, actor=actor or student_id, tool="file_grievance",
            args={"student_id": student_id, "category": category, "description": description},
            result={"grievance_id": grievance_id}, approved_by=actor or None,
        )
        session.commit()
        return GrievanceResult(receipt_id=receipt_id, grievance_id=grievance_id, status="open")


def draft_email(to: str, subject: str, body: str) -> DraftEmailResult:
    """Not requires_approval — drafting is not a send."""
    return DraftEmailResult(to=to, subject=subject, body=body)


def send_email(to: str, subject: str, body: str, actor: str = "", approved: bool = False):
    """requires_approval."""
    if not approved:
        return PendingAction(
            id=uuid.uuid4().hex[:8], agent="services", tool="send_email",
            args={"to": to, "subject": subject, "body": body},
            description=f"Send an email to {to} — subject: \"{subject}\".",
            risk="medium", reversible=False, preview=body,
        )
    with Session() as session:
        receipt_id = write_receipt(
            session, actor=actor, tool="send_email", args={"to": to, "subject": subject, "body": body},
            result={"status": "sent"}, approved_by=actor or None,
        )
        session.commit()
        return SendEmailResult(receipt_id=receipt_id, to=to, subject=subject, status="sent")


def add_to_calendar(student_id: str, title: str, date: str, start_time: str, end_time: str,
                     source: str = "", actor: str = "") -> CalendarEventResult:
    """Not requires_approval — adding a personal calendar entry is low-stakes/reversible."""
    with Session() as session:
        _student_or_404(session, student_id)
        session.execute(
            text(
                "INSERT INTO calendar_events (student_id, title, date, start_time, end_time, source) "
                "VALUES (:sid, :title, :date, :start, :end, :source)"
            ),
            {"sid": student_id, "title": title, "date": date, "start": start_time, "end": end_time, "source": source},
        )
        receipt_id = write_receipt(
            session, actor=actor or student_id, tool="add_to_calendar",
            args={"student_id": student_id, "title": title, "date": date}, result={"status": "added"},
        )
        session.commit()
        return CalendarEventResult(receipt_id=receipt_id, title=title, date=date, start_time=start_time, end_time=end_time)


def create_reminder(student_id: str, message: str, remind_at: str, actor: str = "") -> ReminderResult:
    """Not requires_approval."""
    with Session() as session:
        _student_or_404(session, student_id)
        session.execute(
            text("INSERT INTO reminders (student_id, message, remind_at) VALUES (:sid, :msg, :at)"),
            {"sid": student_id, "msg": message, "at": remind_at},
        )
        receipt_id = write_receipt(
            session, actor=actor or student_id, tool="create_reminder",
            args={"student_id": student_id, "message": message, "remind_at": remind_at}, result={"status": "created"},
        )
        session.commit()
        return ReminderResult(receipt_id=receipt_id, message=message, remind_at=remind_at)


def escalate_to_human(student_id: str, summary: str, actor: str = "") -> EscalationResult:
    """Not requires_approval — escalating IS the safe fallback, not an
    irreversible action; it hands off to a human rather than acting."""
    with Session() as session:
        receipt_id = write_receipt(
            session, actor=actor or student_id, tool="escalate_to_human",
            args={"student_id": student_id, "summary": summary}, result={"status": "escalated"},
        )
        session.commit()
        return EscalationResult(receipt_id=receipt_id, summary=summary, status="escalated")
