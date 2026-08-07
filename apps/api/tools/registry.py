"""TOOL_REGISTRY — name -> {fn, agent, requires_approval, required_role}.

Section 5.1 of the battle plan is the source of truth for which agent owns
which tools. Wired into apps/api/graph/agents.py's TOOL_REGISTRY so
specialist nodes can actually call tools instead of guessing from the
prompt alone.
"""
from apps.api.tools import academic, events, knowledge, placement, services
from apps.api.tools.exceptions import (
    PermissionDenied,
    RecordNotFound,
    SeatsUnavailable,
)
from apps.api.tools.resilience import resilient

# Domain refusals — a correct "no", not an infrastructure fault. These must
# reach the agent unchanged: retrying them is pointless and burying them in a
# degraded response would turn "that event is full" into "something broke".
DOMAIN_REFUSALS = (SeatsUnavailable, RecordNotFound, PermissionDenied)

# Logical backing service per tool, for chaos toggling. Mirrors how these
# would map to real campus systems once the SQLite mocks are swapped for the
# ERP/placement-portal HTTP calls.
TOOL_SERVICE = {
    "get_timetable": "erp", "get_attendance": "erp",
    "compute_attendance_eligibility": "erp", "check_schedule_conflict": "erp",
    "recommend_electives": "erp",
    "list_companies": "placement", "check_placement_eligibility": "placement",
    "analyze_resume": "placement", "get_prep_plan": "placement",
    "search_events": "events", "get_event_capacity": "events",
    "register_event": "events", "recommend_clubs": "events",
    "search_policy": "rag", "get_document_span": "rag",
    "get_hostel_info": "campus", "library_loans": "library", "renew_book": "library",
    "file_grievance": "campus", "draft_email": "comms", "send_email": "comms",
    "add_to_calendar": "calendar", "create_reminder": "calendar",
    "escalate_to_human": "campus",
}


def _placement_eligibility_fallback(student_id: str, company_id: str, **_):
    """Live placement service is down -> answer from the policy documents
    instead, clearly marked degraded so the agent tells the user to verify."""
    try:
        policy = knowledge.search_policy(f"placement eligibility criteria {company_id}")
        cites = [c.text[:200] for c in policy.citations[:2]]
    except Exception:
        cites = []
    return {
        "degraded": True,
        "degradation_reason": "Placement service unavailable; answered from cached policy documents.",
        "student_id": student_id,
        "company_id": company_id,
        "policy_context": cites,
        "advice": "Please confirm with the placement cell before acting on this.",
    }

TOOL_REGISTRY: dict[str, dict] = {
    # Academic Agent
    "get_timetable": {"fn": academic.get_timetable, "agent": "academic", "requires_approval": False, "required_role": "student"},
    "get_attendance": {"fn": academic.get_attendance, "agent": "academic", "requires_approval": False, "required_role": "student"},
    "compute_attendance_eligibility": {"fn": academic.compute_attendance_eligibility, "agent": "academic", "requires_approval": False, "required_role": "student"},
    "check_schedule_conflict": {"fn": academic.check_schedule_conflict, "agent": "academic", "requires_approval": False, "required_role": "student"},
    "recommend_electives": {"fn": academic.recommend_electives, "agent": "academic", "requires_approval": False, "required_role": "student"},

    # Placement Agent
    "list_companies": {"fn": placement.list_companies, "agent": "placement", "requires_approval": False, "required_role": "student"},
    "check_placement_eligibility": {"fn": placement.check_placement_eligibility, "agent": "placement", "requires_approval": False, "required_role": "student"},
    "analyze_resume": {"fn": placement.analyze_resume, "agent": "placement", "requires_approval": False, "required_role": "student"},
    "get_prep_plan": {"fn": placement.get_prep_plan, "agent": "placement", "requires_approval": False, "required_role": "student"},

    # Events Agent
    "search_events": {"fn": events.search_events, "agent": "events", "requires_approval": False, "required_role": "student"},
    "get_event_capacity": {"fn": events.get_event_capacity, "agent": "events", "requires_approval": False, "required_role": "student"},
    "register_event": {"fn": events.register_event, "agent": "events", "requires_approval": True, "required_role": "student"},
    "recommend_clubs": {"fn": events.recommend_clubs, "agent": "events", "requires_approval": False, "required_role": "student"},

    # Knowledge Agent (RAG)
    "search_policy": {"fn": knowledge.search_policy, "agent": "knowledge", "requires_approval": False, "required_role": "student"},
    "get_document_span": {"fn": knowledge.get_document_span, "agent": "knowledge", "requires_approval": False, "required_role": "student"},

    # Services & Comms Agent
    "get_hostel_info": {"fn": services.get_hostel_info, "agent": "services", "requires_approval": False, "required_role": "student"},
    "library_loans": {"fn": services.library_loans, "agent": "services", "requires_approval": False, "required_role": "student"},
    "renew_book": {"fn": services.renew_book, "agent": "services", "requires_approval": False, "required_role": "student"},
    "file_grievance": {"fn": services.file_grievance, "agent": "services", "requires_approval": True, "required_role": "student"},
    "draft_email": {"fn": services.draft_email, "agent": "services", "requires_approval": False, "required_role": "student"},
    "send_email": {"fn": services.send_email, "agent": "services", "requires_approval": True, "required_role": "student"},
    "add_to_calendar": {"fn": services.add_to_calendar, "agent": "services", "requires_approval": False, "required_role": "student"},
    "create_reminder": {"fn": services.create_reminder, "agent": "services", "requires_approval": False, "required_role": "student"},
    "escalate_to_human": {"fn": services.escalate_to_human, "agent": "services", "requires_approval": False, "required_role": "student"},
}

assert len(TOOL_REGISTRY) == 24, f"expected 24 tools, got {len(TOOL_REGISTRY)}"

# Per-tool fallback chains. Only tools with a genuinely useful degraded answer
# get one; the rest fall through to the generic degraded response so we never
# fabricate data we don't have.
_FALLBACKS = {"check_placement_eligibility": _placement_eligibility_fallback}

# Wrap every tool: chaos hook -> retry x2 -> circuit breaker -> fallback.
# `fn` stays the raw callable under `raw_fn` so tests and the approval gate can
# reach the unwrapped behaviour when they need exact exceptions.
for _name, _spec in TOOL_REGISTRY.items():
    _spec["raw_fn"] = _spec["fn"]
    _spec["service"] = TOOL_SERVICE.get(_name, "campus")
    _spec["fn"] = resilient(
        _name,
        service=_spec["service"],
        fallback=_FALLBACKS.get(_name),
        passthrough=DOMAIN_REFUSALS,
    )(_spec["fn"])

TOOLS_BY_AGENT: dict[str, list[str]] = {}
for _name, _spec in TOOL_REGISTRY.items():
    TOOLS_BY_AGENT.setdefault(_spec["agent"], []).append(_name)


# Role hierarchy — a higher rank may invoke anything its juniors can.
ROLE_RANK = {"student": 0, "faculty": 1, "admin": 2}


def can_invoke(tool_name: str, role: str) -> bool:
    """Whether `role` is permitted to invoke `tool_name`. Unknown roles are
    treated as the least-privileged rather than denied outright, so a typo in
    a request degrades to student access instead of blocking everything."""
    spec = TOOL_REGISTRY.get(tool_name)
    if not spec:
        return False
    required = ROLE_RANK.get(spec.get("required_role", "student"), 0)
    held = ROLE_RANK.get((role or "student").lower(), 0)
    return held >= required
