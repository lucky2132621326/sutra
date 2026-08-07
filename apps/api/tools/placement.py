"""Placement Agent tools: company eligibility, resume gap analysis, interview prep.

Eligibility is a deterministic rules engine — no LLM calls — per P3: comparing
CGPA, backlogs, and branch against the company row, with a structured
per-criterion breakdown the UI renders directly.
"""
from sqlalchemy import text

from apps.api.tools.db import Session
from apps.api.tools.exceptions import RecordNotFound
from apps.api.tools.models import (
    Company,
    CompanyList,
    EligibilityCriterion,
    PlacementEligibility,
    PrepPlan,
    ResumeAnalysis,
)


def _student_or_404(session, student_id: str):
    row = session.execute(text("SELECT * FROM students WHERE id=:id"), {"id": student_id}).mappings().first()
    if not row:
        raise RecordNotFound(f"No student with id {student_id}")
    return row


def _company_or_404(session, company_id: str):
    # Case-insensitive: an LLM picking a tool arg is as likely to pass the
    # display name's casing ("Google") as the literal id ("google").
    row = session.execute(
        text("SELECT * FROM companies WHERE LOWER(id)=LOWER(:id)"), {"id": company_id}
    ).mappings().first()
    if not row:
        raise RecordNotFound(f"No company with id {company_id}")
    return row


def list_companies(branch: str = "") -> CompanyList:
    with Session() as session:
        rows = session.execute(text("SELECT * FROM companies")).mappings().all()
        companies = []
        for r in rows:
            branches = r["eligible_branches"].split(",")
            if branch and branch not in branches:
                continue
            companies.append(Company(
                id=r["id"], name=r["name"], role=r["role"], min_cgpa=r["min_cgpa"],
                max_backlogs=r["max_backlogs"], eligible_branches=branches,
                application_deadline=r["application_deadline"],
            ))
        return CompanyList(companies=companies)


def check_placement_eligibility(student_id: str, company_id: str) -> PlacementEligibility:
    with Session() as session:
        student = _student_or_404(session, student_id)
        company = _company_or_404(session, company_id)

        eligible_branches = company["eligible_branches"].split(",")
        breakdown = [
            EligibilityCriterion(
                criterion="CGPA", required=f">= {company['min_cgpa']}", actual=str(student["cgpa"]),
                passed=student["cgpa"] >= company["min_cgpa"],
            ),
            EligibilityCriterion(
                criterion="Backlogs", required=f"<= {company['max_backlogs']}", actual=str(student["backlogs"]),
                passed=student["backlogs"] <= company["max_backlogs"],
            ),
            EligibilityCriterion(
                criterion="Branch", required=",".join(eligible_branches), actual=student["branch"],
                passed=student["branch"] in eligible_branches,
            ),
        ]
        return PlacementEligibility(
            student_id=student_id, company_id=company_id,
            is_eligible=all(c.passed for c in breakdown), breakdown=breakdown,
        )


def analyze_resume(student_id: str, resume_text: str) -> ResumeAnalysis:
    """Heuristic, deterministic gap analysis — keyword presence, not an LLM call."""
    text_lower = resume_text.lower()
    signal_keywords = ["project", "internship", "github", "leadership", "publication"]
    strengths = [k.title() for k in signal_keywords if k in text_lower]
    gaps = [k.title() for k in signal_keywords if k not in text_lower]
    match_score = round(len(strengths) / len(signal_keywords), 2) if signal_keywords else 0.0
    return ResumeAnalysis(student_id=student_id, strengths=strengths, gaps=gaps, match_score=match_score)


def get_prep_plan(student_id: str, company_id: str) -> PrepPlan:
    with Session() as session:
        _student_or_404(session, student_id)
        company = _company_or_404(session, company_id)
        topics = ["Data Structures & Algorithms", "System Design Basics", f"{company['role']} role-specific mock interview"]
        return PrepPlan(student_id=student_id, company_id=company_id, topics=topics, timeline_days=14)
