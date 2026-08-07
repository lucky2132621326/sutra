"""Pydantic return models for every tool — structured, not prose, per P3."""
from typing import Any, Optional

from pydantic import BaseModel


class TimetableEntry(BaseModel):
    course_id: str
    course_name: str
    day_of_week: str
    start_time: str
    end_time: str
    session_type: str


class TimetableResult(BaseModel):
    student_id: str
    entries: list[TimetableEntry]


class AttendanceRecord(BaseModel):
    course_id: str
    course_name: str
    classes_held: int
    classes_attended: int
    percentage: float


class AttendanceResult(BaseModel):
    student_id: str
    records: list[AttendanceRecord]


class AttendanceEligibility(BaseModel):
    student_id: str
    course_id: str
    current_pct: float
    classes_attended: int
    classes_held: int
    classes_needed_for_75: int
    is_eligible: bool
    condonation_possible: bool  # true if pct >= 65


class AttendanceImpact(BaseModel):
    """What missing further sessions would do to a course's attendance."""
    student_id: str
    course_id: str
    course_name: str
    current_pct: float
    projected_pct: float
    delta_pct: float
    classes_attended: int
    classes_held: int
    sessions_missed: int
    crosses_threshold: bool
    """True when this is what pushes the student from safe to unsafe."""
    already_below: bool
    """True when they were already under the bar before this."""
    sessions_needed_to_recover: int


class ScheduleConflict(BaseModel):
    has_conflict: bool
    conflicting_course_id: Optional[str] = None
    conflicting_session: Optional[str] = None
    detail: str = ""


class ElectiveRecommendation(BaseModel):
    course_id: str
    course_name: str
    reason: str


class ElectiveRecommendations(BaseModel):
    student_id: str
    recommendations: list[ElectiveRecommendation]


class Company(BaseModel):
    id: str
    name: str
    role: str
    min_cgpa: float
    max_backlogs: int
    eligible_branches: list[str]
    application_deadline: Optional[str] = None


class CompanyList(BaseModel):
    companies: list[Company]


class EligibilityCriterion(BaseModel):
    criterion: str
    required: str
    actual: str
    passed: bool


class PlacementEligibility(BaseModel):
    student_id: str
    company_id: str
    is_eligible: bool
    breakdown: list[EligibilityCriterion]


class ResumeAnalysis(BaseModel):
    student_id: str
    strengths: list[str]
    gaps: list[str]
    match_score: float


class PrepPlan(BaseModel):
    student_id: str
    company_id: str
    topics: list[str]
    timeline_days: int


class EventSummary(BaseModel):
    id: str
    title: str
    description: str
    day_of_week: str
    date: str
    start_time: str
    end_time: str
    seats_remaining: int
    category: str


class EventSearchResult(BaseModel):
    events: list[EventSummary]


class EventCapacity(BaseModel):
    event_id: str
    total_seats: int
    seats_taken: int
    seats_remaining: int


class PendingAction(BaseModel):
    id: str
    step_id: Optional[str] = None
    agent: str
    tool: str
    args: dict[str, Any]
    description: str
    risk: str = "low"
    reversible: bool = True
    preview: str = ""


class EventRegistrationResult(BaseModel):
    receipt_id: str
    event_id: str
    student_id: str
    status: str


class ClubRecommendation(BaseModel):
    club_id: str
    name: str
    category: str
    reason: str


class ClubRecommendations(BaseModel):
    student_id: str
    recommendations: list[ClubRecommendation]


class Citation(BaseModel):
    text: str
    doc_title: str
    doc_number: str
    clause: str
    page: int
    score: float


class PolicySearchResult(BaseModel):
    query: str
    citations: list[Citation]
    no_relevant_context: bool = False


class DocumentSpan(BaseModel):
    doc_title: str
    doc_number: str
    clause: str
    text: str


class HostelInfo(BaseModel):
    student_id: str
    block: Optional[str] = None
    room_number: Optional[str] = None
    no_dues: bool = True


class LibraryLoan(BaseModel):
    book_title: str
    borrowed_at: str
    due_at: str
    returned: bool


class LibraryLoansResult(BaseModel):
    student_id: str
    loans: list[LibraryLoan]


class RenewBookResult(BaseModel):
    receipt_id: str
    book_title: str
    new_due_at: str


class GrievanceResult(BaseModel):
    receipt_id: str
    grievance_id: int
    status: str


class DraftEmailResult(BaseModel):
    to: str
    subject: str
    body: str


class SendEmailResult(BaseModel):
    receipt_id: str
    to: str
    subject: str
    status: str


class CalendarEventResult(BaseModel):
    receipt_id: str
    title: str
    date: str
    start_time: str
    end_time: str


class ReminderResult(BaseModel):
    receipt_id: str
    message: str
    remind_at: str


class EscalationResult(BaseModel):
    receipt_id: str
    summary: str
    status: str


# Every write tool result may carry these when wrapped by @resilient(...)
# (apps/api/tools/resilience.py, built alongside the tools).
class Degradable(BaseModel):
    degraded: bool = False
    degradation_reason: str = ""
