"""
Non-agent graph nodes: planner, dispatch router (parallel fan-out via Send),
conflict_check, approval_gate, critic, synthesize.
"""
import asyncio
from datetime import datetime, timedelta
import inspect
import os
import re
import time
import uuid

from langgraph.types import Send, interrupt

from apps.api import memory
from apps.api.bus import bus
from apps.api.graph.state import RESET
from apps.api.llm.router import call_llm_async
from apps.api.tools.exceptions import ToolError
from apps.api.tools.models import PendingAction
from apps.api.tools.registry import TOOL_REGISTRY
from packages.contracts.events import AgentEvent, EventType
from packages.contracts.plan import AGENT_NAMES, PLAN_JSON_INSTRUCTIONS, Plan, Step

MAX_REPLAN_ITERATIONS = 2
# Bounded separately from MAX_REPLAN_ITERATIONS — see critic_node.
MAX_REJECTION_REPLANS = 1

# Wall-clock budget for a single turn, after which no further replanning is
# allowed and the run answers with what it already has.
#
# The iteration cap alone is not enough. MEASURED with a live model: a
# speculative question ("can I still apply if my CGPA drops to 7.9?") spent
# 193s inside its allowed two replans, re-running search_policy three times and
# check_placement_eligibility twice. An imperfect answer in 45s is worth far
# more than a better one nobody waited for — and on a demo clock, a run that
# appears to hang is indistinguishable from one that crashed.
TURN_BUDGET_S = float(os.environ.get("TURN_BUDGET_S", "45"))


def _out_of_time(state: dict) -> bool:
    started = state.get("started_ts")
    return bool(started) and (time.time() - started) > TURN_BUDGET_S

def _agent_capabilities() -> str:
    """One line per agent listing the tools it actually owns.

    Generated from TOOL_REGISTRY rather than written by hand, so it cannot
    drift out of step with the code that enforces it.

    Without this the planner routed on topic words instead of capability:
    "register me for the PLACEMENT workshop" went to the placement agent, whose
    catalogue has no register_event. The write was therefore never staged, the
    approval gate never opened, and the run finished having quietly skipped the
    one step that required a human — while the answer still claimed success.
    An agent asked to do something it has no tool for cannot fail loudly; it
    improvises.
    """
    owned: dict[str, list[str]] = {name: [] for name in AGENT_NAMES}
    for tool, entry in TOOL_REGISTRY.items():
        owned.setdefault(entry["agent"], []).append(tool)
    lines = []
    for name in AGENT_NAMES:
        tools = sorted(owned.get(name, []))
        if tools:
            lines.append(f"- {name}: {', '.join(tools)}")
    return "\n".join(lines)


PLANNER_SYSTEM = (
    "You are the orchestrator planner for a Smart Campus multi-agent system. "
    "Break the user's goal into steps assigned to specialist agents: "
    + ", ".join(AGENT_NAMES) + ". Keep steps small and parallelizable — only "
    "add a depends_on edge when a step genuinely needs another step's output.\n\n"
    "ASSIGN BY CAPABILITY, NOT BY TOPIC. Each agent can only call its own "
    "tools; an agent given a task it has no tool for will improvise instead of "
    "failing. Note especially that registering for ANY event — including a "
    "placement workshop — belongs to `events`, not `placement`.\n"
    + _agent_capabilities() + "\n\n"
    "NOT EVERY MESSAGE IS A TASK. If the student is greeting you, thanking you, "
    "making small talk, or asking what you can do, there is nothing to look up: "
    'return {"steps": [], "reply": "<a short, warm, first-person answer>"} and '
    "nothing else. Use `reply` the same way when the request is too vague to act "
    "on — ask the one clarifying question you need. Only build steps when there "
    "is something to actually check, fetch, or do.\n\n"
    "ROUTE RULES, NOT RECORDS, TO KNOWLEDGE. Anything about what the "
    "regulations SAY — attendance thresholds, exam eligibility, condonation, "
    "deadlines, what happens if — is a knowledge step, because only that agent "
    "can search the institutional corpus. The academic agent reads this "
    "student's own records (their timetable, their attendance figures); it "
    "cannot look up a rule, and asking it to will produce an invented one.\n\n"
    + PLAN_JSON_INSTRUCTIONS
)

CAPABILITIES = (
    "I can check your attendance and timetable, work out placement eligibility, "
    "find and register you for campus events, answer questions from the "
    "institutional regulations, and handle calendar entries and reminders."
)


async def _emit(run_id, type_, **kw):
    await bus.emit(AgentEvent(id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=type_, **kw))


async def intake_node(state: dict) -> dict:
    """Tier 2+3 memory read. Loads durable profile facts and semantically
    recalled turn summaries for this student, emits memory.recall, and puts a
    compact block into state for planner/agents to consume.

    Never raises: memory is an enhancement, so a memory failure degrades to
    an empty block rather than failing the user's request.
    """
    run_id = state["run_id"]
    student_id = state.get("student_id", "")
    if not student_id:
        return {"memory_block": "", "conflicts": [RESET], "action_log": [RESET], "citations": [RESET],
        "conversational_reply": None, "started_ts": time.time()}

    try:
        block, facts, recalled = await asyncio.to_thread(
            memory.load_memory_block, student_id, state.get("goal", ""),
        )
    except Exception as e:
        await _emit(run_id, EventType.RUN_ERROR, agent="intake",
                    payload={"error": str(e), "detail": "memory load failed; continuing without memory"})
        return {"memory_block": "", "conflicts": [RESET], "action_log": [RESET], "citations": [RESET],
        "conversational_reply": None, "started_ts": time.time()}

    if facts or recalled:
        await _emit(run_id, EventType.MEMORY_RECALL, agent="intake", payload={
            "profile_facts": facts,
            "recalled": recalled,
            "source": "profile+semantic",
        })
    # Every accumulating channel starts empty for a NEW turn — see state.py.
    return {"memory_block": block, "conflicts": [RESET], "action_log": [RESET],
            "citations": [RESET], "conversational_reply": None, "started_ts": time.time()}


async def memory_write_node(state: dict) -> dict:
    """Tier 2+3 memory write. Runs AFTER synthesize has already emitted
    run.finished, so its two LLM calls sit off the user-visible critical path:
    the answer is on screen before this starts. memory.write therefore arrives
    slightly after run.finished by design."""
    run_id = state["run_id"]
    student_id = state.get("student_id", "")
    answer = state.get("final_answer") or ""
    if not student_id or not answer:
        return {}

    result = await memory.write_turn_memory(
        student_id=student_id,
        thread_id=state.get("run_id", ""),
        user_message=state.get("goal", ""),
        answer=answer,
    )
    if result["facts_written"] or result["summary"]:
        await _emit(run_id, EventType.MEMORY_WRITE, agent="memory", payload=result)
    return {}


def _workflow_plan_for_event(state: dict, event, reasoning: str) -> Plan:
    """Build structured tool steps for a verified workshop session."""
    previous_plan: Plan | None = state.get("plan")
    previous_steps = previous_plan.steps if previous_plan else []
    goal_lower = state.get("goal", "").lower()
    needs_eligibility = "eligib" in goal_lower or any(
        step.agent == "placement" and "eligib" in step.task.lower()
        for step in previous_steps
    )
    needs_calendar = "calendar" in goal_lower or any("calendar" in step.task.lower() for step in previous_steps)
    needs_reminder = "remind" in goal_lower or any("remind" in step.task.lower() for step in previous_steps)

    student_id = state.get("student_id", "")
    company = "Google"
    for result in state.get("step_results", {}).values():
        tool_result = ((result.get("data") or {}).get("tool_result") or {})
        if tool_result.get("company_id"):
            company = str(tool_result["company_id"])
            break

    steps: list[Step] = []
    dependency: list[str] = []
    next_id = 1
    if needs_eligibility:
        eligibility_id = f"s{next_id}"
        steps.append(Step(
            id=eligibility_id, agent="placement",
            task=f"Confirm {company} internship eligibility for student {student_id}.",
            expected_output="Eligibility verdict from campus records.",
            tool="check_placement_eligibility",
            tool_args={"student_id": student_id, "company_id": company},
        ))
        dependency = [eligibility_id]
        next_id += 1

    registration_id = f"s{next_id}"
    steps.append(Step(
        id=registration_id, agent="events",
        task=(f"Register student {student_id} for event id {event.id}: "
              f"'{event.title}' on {event.day_of_week} {event.date} "
              f"{event.start_time}-{event.end_time}."),
        depends_on=dependency,
        expected_output="Registration confirmation after human approval.",
        requires_approval=True,
        tool="register_event",
        tool_args={"student_id": student_id, "event_id": event.id},
    ))
    next_id += 1
    last_id = registration_id

    if needs_calendar:
        calendar_id = f"s{next_id}"
        steps.append(Step(
            id=calendar_id, agent="services",
            task=(f"Add '{event.title}' to student {student_id}'s calendar on "
                  f"{event.date} from {event.start_time} to {event.end_time}."),
            depends_on=[last_id], expected_output="Calendar receipt.",
            tool="add_to_calendar",
            tool_args={
                "student_id": student_id, "title": event.title,
                "date": event.date, "start_time": event.start_time,
                "end_time": event.end_time,
            },
        ))
        last_id = calendar_id
        next_id += 1

    if needs_reminder:
        starts_at = datetime.fromisoformat(f"{event.date}T{event.start_time}")
        remind_at = (starts_at - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        steps.append(Step(
            id=f"s{next_id}", agent="services",
            task=(f"Create a reminder for student {student_id} one hour before "
                  f"'{event.title}' on {event.date} at {event.start_time}."),
            depends_on=[last_id], expected_output="Reminder receipt.",
            tool="create_reminder",
            tool_args={
                "student_id": student_id,
                "message": f"{event.title} starts in one hour.",
                "remind_at": remind_at,
            },
        ))

    return Plan(
        goal=f"Use the {event.day_of_week} session for {event.title}.",
        reasoning=reasoning,
        steps=steps,
    )


async def _deterministic_initial_workshop_plan(state: dict) -> Plan | None:
    """Fast, data-backed plan for the showcased eligibility+workshop mission."""
    goal = state.get("goal", "").lower()
    # Require the eligibility phrase too, not just "register"+"workshop".
    #
    # MEASURED regression: with only those two words required, the approval-
    # gating tests' goal ("register me for the placement workshop" — no
    # eligibility, calendar or reminder mentioned) also matched. needs_calendar
    # and needs_reminder in _workflow_plan_for_event then came back False, so
    # the deterministic plan silently dropped the calendar step those tests
    # depend on — a one-line trigger reaching past the flagship mission it was
    # built for and clipping an unrelated scenario's plan.
    if "register" not in goal or "workshop" not in goal or "eligib" not in goal:
        return None

    from apps.api.tools import events as events_tool

    try:
        result = await asyncio.to_thread(events_tool.search_events, "placement workshop")
    except Exception:
        return None
    candidates = sorted(
        (event for event in result.events
         if event.seats_remaining > 0 and "placement prep workshop" in event.title.lower()),
        key=lambda event: (event.date, event.start_time),
    )
    if not candidates:
        return None
    event = candidates[0]
    return _workflow_plan_for_event(
        state, event,
        "Eligibility is checked before registration; calendar and reminder depend on an approved registration.",
    )


async def _deterministic_schedule_revision(state: dict) -> Plan | None:
    """Build a safe alternative plan when the revision model times out."""
    collision = next(
        (c for c in state.get("conflicts", []) if c.get("type") == "SCHEDULE_COLLISION"),
        None,
    )
    if not collision:
        return None

    from apps.api.tools import academic, events as events_tool

    evidence = collision.get("evidence") or {}
    blocked_event = evidence.get("event") or {}
    blocked_id = blocked_event.get("id")
    title = str(blocked_event.get("title") or "placement workshop")
    query = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()

    try:
        candidates = await asyncio.to_thread(events_tool.search_events, query)
    except Exception:
        return None

    student_id = state.get("student_id", "")
    alternative = None
    for candidate in candidates.events:
        if candidate.id == blocked_id or candidate.seats_remaining <= 0:
            continue
        try:
            clash = await asyncio.to_thread(
                academic.check_schedule_conflict,
                student_id, candidate.day_of_week, candidate.start_time, candidate.end_time,
            )
        except Exception:
            continue
        if not clash.has_conflict:
            alternative = candidate
            break

    if alternative is None:
        return None

    return _workflow_plan_for_event(
        state, alternative,
        (f"The original session conflicts with the student's timetable. "
         f"{alternative.title} on {alternative.day_of_week} "
         f"{alternative.start_time}-{alternative.end_time} has seats and no timetable clash."),
    )


async def planner_node(state: dict) -> dict:
    """Never raises — if every LLM provider is down, degrade to a single
    catch-all step (routed to Services & Comms) rather than crashing the
    whole run. That step's own LLM call will also fail, but run_agent_step
    already turns that into a controlled 'error' StepResult, so the graph
    still reaches synthesize with something to say instead of a stack trace.
    """
    run_id, goal = state["run_id"], state["goal"]
    feedback = state.get("critic_feedback")
    is_revision = feedback is not None

    memory_block = state.get("memory_block") or ""
    user_message = f"User goal: {goal}"
    if memory_block:
        user_message = f"MEMORY (what we already know about this student):\n{memory_block}\n\n" + user_message
    if is_revision:
        user_message += f"\n\nPrevious plan needs revision. Feedback: {feedback}"

    deterministic = (
        await _deterministic_schedule_revision(state)
        if is_revision
        else await _deterministic_initial_workshop_plan(state)
    )
    if deterministic is not None:
        await _emit(run_id, EventType.AGENT_THINKING, agent="planner", payload={
            "detail": ("verified conflict-free session selected from campus records"
                       if is_revision else
                       "known multi-agent workshop workflow selected"),
            "deterministic": True,
        })
        await _emit(run_id, EventType.PLAN_REVISED if is_revision else EventType.PLAN_CREATED,
                    agent="planner", payload=deterministic.to_dict())
        return {
            "plan": deterministic,
            "iteration": state.get("iteration", 0) + (1 if is_revision else 0),
            "plan_version": state.get("plan_version", 0) + 1,
            "critic_feedback": None,
            "conversational_reply": None,
            "step_results": {RESET: True},
            "pending_approvals": [RESET],
            "conflicts": [RESET] if is_revision else [],
        }

    try:
        parsed = await call_llm_async(
            PLANNER_SYSTEM, [{"role": "user", "content": user_message}], json_mode=True,
        )
        # Conversational fast path. Spinning up five agents, a conflict arbiter
        # and a critic to answer "hello" is not just wasteful — it produced an
        # eligibility verdict nobody asked for, which reads as the system not
        # listening. No steps means nothing to orchestrate, so reply and stop.
        if not parsed.get("steps"):
            reply = str(parsed.get("reply") or "").strip() or (
                f"I'm Sūtra, your campus assistant. {CAPABILITIES} What would you like to do?"
            )
            await _emit(run_id, EventType.AGENT_THINKING, agent="planner", payload={
                "detail": "conversational message — no agent work required", "conversational": True,
            })
            return {"plan": Plan(goal=goal, reasoning="Conversational reply; no steps needed.", steps=[]),
                    "conversational_reply": reply,
                    "plan_version": state.get("plan_version", 0) + 1,
                    "critic_feedback": None,
                    "step_results": {RESET: True},
                    "pending_approvals": [RESET]}

        plan = Plan.from_dict(parsed)
    except Exception as e:
        recovered = await _deterministic_schedule_revision(state) if is_revision else None
        if recovered is not None:
            await _emit(run_id, EventType.RUN_ERROR, agent="planner", payload={
                "error": str(e),
                "detail": "planner LLM timed out; recovered with a verified conflict-free session",
            })
            plan = recovered
        else:
            await _emit(run_id, EventType.RUN_ERROR, agent="planner",
                        payload={"error": str(e), "detail": "planner LLM call failed; ending safely without actions"})
            message = (
                "I couldn't plan this request because the reasoning service timed out. "
                "Nothing was changed. Please try again."
            )
            return {
                "plan": Plan(goal=goal, reasoning="Planner failed; no actions were dispatched.", steps=[]),
                "conversational_reply": message,
                "plan_version": state.get("plan_version", 0) + 1,
                "critic_feedback": None,
                "step_results": {RESET: True},
                "pending_approvals": [RESET],
                "conflicts": [RESET],
            }

    await _emit(run_id, EventType.PLAN_REVISED if is_revision else EventType.PLAN_CREATED,
                agent="planner", payload=plan.to_dict())

    return {
        "plan": plan,
        "iteration": state.get("iteration", 0) + (1 if is_revision else 0),
        "plan_version": state.get("plan_version", 0) + 1,
        "critic_feedback": None,
        # MUST be cleared, not merely left unset. State is checkpointed per
        # THREAD, so a value written by an earlier turn survives into this one:
        # after a single "hello", every subsequent question in that conversation
        # found a stale reply here, short-circuited straight to synthesize, and
        # returned "I'm doing well, thanks for asking!" while its own step sat
        # unexecuted. Not writing a key is not the same as clearing it.
        "conversational_reply": None,
        # A fresh plan supersedes both prior results AND any approvals the old
        # plan queued. Both channels need the explicit RESET sentinel: their
        # reducers merge/append, so an empty value would be a no-op and the
        # stale state would survive (see state.py).
        "step_results": {RESET: True},
        "pending_approvals": [RESET],
        "conflicts": [RESET] if is_revision else [],
    }


def route_after_planner(state: dict) -> str:
    """A conversational turn has nothing to dispatch, arbitrate or criticise."""
    return "synthesize" if state.get("conversational_reply") else "dispatch"


async def dispatch_node(state: dict) -> dict:
    """No-op merge point. Every agent node routes here via a plain edge
    instead of each having its own conditional edge to route_ready_steps —
    when several agent nodes finish in the same superstep (the whole point
    of parallel Send dispatch), each independently evaluating the same
    routing function would each compute the same 'newly ready' steps and
    each re-Send them, causing N-way duplicate LLM calls per step. Funneling
    through one shared node means the routing decision is made exactly once
    per tick, off the fully-merged state.
    """
    return {}


# A step that reached one of these has produced its outcome, so anything
# depending on it may proceed.
#
# Three statuses are deliberately ABSENT:
#   pending_approval — the action hasn't happened; it's queued for a human
#   rejected         — the human said no; dependent writes must not proceed
#   permission_denied— the step never ran, so dependents have nothing to build on
# `error` IS included: the work was attempted and failed, and a dependent step
# can legitimately report around it rather than hanging the run.
SATISFYING_STATUSES = {"ok", "degraded", "error"}

# Dependents of these are cancelled outright rather than left pending forever.
BLOCKING_STATUSES = {"rejected", "permission_denied"}


def _attempted(state: dict) -> set[str]:
    """Steps already dispatched — never dispatch these again."""
    return set(state.get("step_results", {}).keys())


def _satisfied(state: dict) -> set[str]:
    """Steps whose outcome is known, so dependents may run.

    Critically excludes `pending_approval`. Treating a queued-but-unapproved
    write as satisfied let dependents execute before the human decided — the
    calendar entry was being written seconds BEFORE the approval was even
    requested, and would have survived a rejection. The gate must actually gate.
    """
    return {
        sid for sid, r in state.get("step_results", {}).items()
        if r.get("status") in SATISFYING_STATUSES
    }


def route_ready_steps(state: dict):
    """Conditional edge: Send() every step whose dependencies are satisfied and
    which hasn't been dispatched yet, in parallel. When nothing is ready, fall
    through to conflict_check — steps still blocked on an approval will be
    picked up again after approval_gate resumes.
    """
    plan: Plan = state["plan"]
    attempted = _attempted(state)
    satisfied = _satisfied(state)
    ready = [s for s in plan.steps if s.id not in attempted and set(s.depends_on) <= satisfied]

    if ready:
        return [Send(f"agent_{s.agent}", {**state, "_active_step_id": s.id}) for s in ready]

    return "conflict_check"


def route_after_approval(state: dict) -> str:
    """After the gate resolves, dependents that were blocked on a now-approved
    step become runnable — so go back through dispatch. Only head to synthesize
    once nothing further can run."""
    plan: Plan = state.get("plan")
    if not plan:
        return "synthesize"
    attempted = _attempted(state)
    satisfied = _satisfied(state)
    runnable = [s for s in plan.steps if s.id not in attempted and set(s.depends_on) <= satisfied]
    return "dispatch" if runnable else "synthesize"


async def _preflight_conflicts(state: dict) -> tuple[list[dict], list[dict], set[str]]:
    """Deterministic safety checks on writes that are QUEUED but not yet approved.

    This is what makes the Academic Agent's veto real rather than something an
    LLM happened to say. Before a registration is offered for approval we
    check the proposed event against the student's actual timetable, and if it
    collides we quantify what missing that session would do to their
    attendance. No model is involved, so it fires identically every run.
    """
    from apps.api.tools import academic, events as events_tool

    run_id = state["run_id"]
    student_id = state.get("student_id")
    found: list[dict] = []
    cited: list[dict] = []
    checked: set[str] = set()
    if not student_id:
        return found, cited, checked

    for action in state.get("pending_approvals", []):
        if not isinstance(action, dict) or action.get("tool") != "register_event":
            continue
        event_id = (action.get("args") or {}).get("event_id")
        step_id = action.get("step_id")
        if not event_id:
            continue

        try:
            # Resolve display titles through the same forgiving lookup used by
            # register_event. Live models commonly pass "Placement Prep
            # Workshop" instead of the canonical evt_workshop_thu id.
            capacity = await asyncio.to_thread(events_tool.get_event_capacity, event_id)
            canonical_event_id = capacity.event_id
            matches = [e for e in events_tool.search_events().events if e.id == canonical_event_id]
            if not matches:
                continue
            event = matches[0]
            event_id = canonical_event_id
            clash = await asyncio.to_thread(
                academic.check_schedule_conflict,
                student_id, event.day_of_week, event.start_time, event.end_time,
            )
        except Exception:
            continue  # a preflight failure must never break the run

        await _emit(run_id, EventType.SCHEDULE_CHECKED, agent="academic", node_id=step_id, payload={
            "event_id": event_id, "event_title": event.title,
            "day": event.day_of_week, "start": event.start_time, "end": event.end_time,
            "has_conflict": clash.has_conflict,
            "conflicting_course_id": clash.conflicting_course_id,
            "detail": clash.detail,
        })
        if step_id:
            checked.add(step_id)
        if not clash.has_conflict:
            continue

        evidence: dict = {
            "event": {"id": event.id, "title": event.title, "day": event.day_of_week,
                       "start": event.start_time, "end": event.end_time,
                       "seats_remaining": event.seats_remaining},
            "collides_with": {"course_id": clash.conflicting_course_id,
                               "session_type": clash.conflicting_session,
                               "detail": clash.detail},
        }
        detail = (f"Step {step_id} registers for '{event.title}' on {event.day_of_week} "
                  f"{event.start_time}-{event.end_time}, which overlaps {clash.detail}")

        if clash.conflicting_course_id:
            try:
                impact = await asyncio.to_thread(
                    academic.project_attendance_impact, student_id, clash.conflicting_course_id, 1)
                evidence["attendance_impact"] = impact.model_dump()
                await _emit(run_id, EventType.ATTENDANCE_IMPACT_CALCULATED, agent="academic",
                            node_id=step_id, payload=impact.model_dump())
                detail += (f" Attendance in {impact.course_name} is {impact.current_pct}% "
                           f"({impact.classes_attended}/{impact.classes_held}) and would fall to "
                           f"{impact.projected_pct}% — {'already' if impact.already_below else 'now'} "
                           f"below the 75% bar in Academic Regulations R22 clause 4.2.")
            except Exception:
                pass

        # Cite the rule the veto invokes, or stop invoking it.
        #
        # The rationale asserted "the 75% required by R22 clause 4.2" on runs
        # that retrieved nothing — a clause number with no citation behind it,
        # in a panel built to show citations. Fetch it for real: the claim
        # becomes checkable AND the Citations panel gains the clause the
        # evidence card points at.
        try:
            from apps.api.tools.knowledge import search_policy

            policy = await asyncio.to_thread(
                search_policy, "minimum attendance percentage required to appear for examinations")
            clause = next((c for c in policy.citations if not policy.no_relevant_context), None)
            if clause:
                evidence["rule"] = clause.model_dump()
                cited.append(clause.model_dump())
                await _emit(run_id, EventType.RAG_RETRIEVED, agent="academic", node_id=step_id, payload={
                    "chunks": 1, "query": "attendance required to appear for examinations",
                    "abstained": False, "citations": [clause.model_dump()],
                })
        except Exception:
            pass  # a missing citation weakens the card; it must not break the run

        found.append({
            "type": "SCHEDULE_COLLISION", "step_id": step_id, "step_ids": [step_id] if step_id else [],
            "detail": detail, "evidence": evidence,
        })

    return found, cited, checked


def _explain_conflicts(conflicts: list[dict]) -> str:
    """The arbiter's rationale, derived from evidence instead of generated.

    Used in place of the LLM arbiter when the preflight already proved the
    conflict. Every clause here is traceable to a value read out of campus.db,
    so the explanation the judge reads is the explanation the code computed.
    """
    lines: list[str] = []
    for c in conflicts:
        evidence = c.get("evidence") or {}
        event, collides = evidence.get("event"), evidence.get("collides_with")
        impact = evidence.get("attendance_impact")

        if c.get("type") == "SCHEDULE_COLLISION" and event and collides:
            line = (f"'{event['title']}' is scheduled {event['day']} "
                    f"{event['start']}-{event['end']}, which is when "
                    f"{collides.get('course_id')} meets.")
            if impact:
                line += (f" Attending it means missing that session, taking "
                         f"{impact['course_name']} from {impact['current_pct']}% to "
                         f"{impact['projected_pct']}% ({impact['delta_pct']}). ")
                line += ("That is already under the 75% required by R22 clause 4.2"
                         if impact.get("already_below") else
                         "That crosses below the 75% required by R22 clause 4.2"
                         if impact.get("crosses_threshold") else
                         "That stays above the 75% required by R22 clause 4.2")
                line += ", so a non-clashing slot should be used instead."
            lines.append(line)
        else:
            lines.append(str(c.get("detail") or c))
    return " ".join(lines)


async def conflict_check_node(state: dict) -> dict:
    """Deterministic checks + an LLM arbiter over the collected step results."""
    run_id = state["run_id"]
    results = state.get("step_results", {})
    plan: Plan = state["plan"]

    deterministic_conflicts, preflight_citations, preflighted_steps = await _preflight_conflicts(state)
    for step_id, result in results.items():
        if result["status"] == "error":
            deterministic_conflicts.append({
                "type": "step_failure", "step_id": step_id, "step_ids": [step_id],
                "detail": result["output"], "evidence": {},
            })

    arbiter_input = {
        "goal": plan.goal,
        "steps": [s.to_dict() for s in plan.steps],
        "results": {k: {"output": v["output"], "status": v["status"]} for k, v in results.items()},
        "deterministic_conflicts": deterministic_conflicts,
    }
    pending_registration_steps = {
        action.get("step_id") for action in state.get("pending_approvals", [])
        if isinstance(action, dict) and action.get("tool") == "register_event" and action.get("step_id")
    }
    registrations_verified = (
        bool(pending_registration_steps)
        and pending_registration_steps <= preflighted_steps
    )
    if deterministic_conflicts:
        # The preflight already PROVED a conflict against campus.db (a real
        # timetable overlap, a real attendance projection). Asking an LLM to
        # then opine on whether that is a conflict adds a call, adds latency,
        # and puts a stochastic verdict in front of arithmetic that cannot be
        # wrong. Explain it deterministically instead.
        llm_conflicts = []
        rationale = _explain_conflicts(deterministic_conflicts)
    elif registrations_verified:
        # A real timetable lookup cleared every staged registration. Do not
        # ask a model to second-guess that arithmetic or invent vague risks.
        llm_conflicts = []
        rationale = "Every pending registration passed the deterministic timetable preflight."
    else:
        system = (
            "You are the conflict arbiter for a multi-agent campus system. Look for "
            "schedule collisions, capacity overruns, attendance risk, or permission "
            "problems across these step results. Respond with JSON: "
            '{"conflicts": [{"type": str, "detail": str}], "rationale": str}. '
            'Empty conflicts list if none found.'
        )
        try:
            parsed = await call_llm_async(
                system, [{"role": "user", "content": str(arbiter_input)}], json_mode=True,
            )
            llm_conflicts = parsed.get("conflicts", [])
            rationale = parsed.get("rationale", "")
        except Exception as e:
            await _emit(run_id, EventType.RUN_ERROR, agent="conflict_arbiter",
                        payload={"error": str(e), "detail": "arbiter LLM call failed; using deterministic checks only"})
            llm_conflicts, rationale = [], ""

    all_conflicts = deterministic_conflicts + llm_conflicts
    if all_conflicts:
        await _emit(run_id, EventType.CONFLICT_DETECTED, agent="conflict_arbiter",
                    payload={"conflicts": all_conflicts, "rationale": rationale})
    else:
        await _emit(run_id, EventType.CONFLICT_RESOLVED, agent="conflict_arbiter", payload={})

    update: dict = {"conflicts": all_conflicts}
    if preflight_citations:
        update["citations"] = preflight_citations
    if all_conflicts and _out_of_time(state):
        await _emit(run_id, EventType.AGENT_THINKING, agent="conflict_arbiter", payload={
            "detail": f"turn budget of {TURN_BUDGET_S:.0f}s spent; reporting the conflict "
                      "instead of replanning around it", "budget_exhausted": True,
        })
    elif all_conflicts and state.get("iteration", 0) < MAX_REPLAN_ITERATIONS:
        # Route back to planner via the same critic_feedback field critic_node
        # uses — planner_node's is_revision check (and the iteration bump
        # that depends on it) only fires off that field, so without setting
        # it here a conflict-triggered replan looked like a fresh plan and
        # never advanced the iteration counter, making the cap a no-op.
        # Defensive str() coercion: smaller/local models don't always follow
        # the requested schema exactly (e.g. a bool where a string field was
        # expected), unlike Groq/Gemini in testing so far.
        def _conflict_text(c):
            if not isinstance(c, dict):
                return str(c)
            # Include the TYPE, not just the prose. The planner needs to know
            # what KIND of problem it is to choose a different strategy — with
            # only the detail text it kept re-proposing the same clashing slot.
            kind = str(c.get("type") or "conflict")
            return f"[{kind}] {c.get('detail') or c}"

        update["critic_feedback"] = (
            "Conflicts were found in the previous plan's results: "
            + "; ".join(_conflict_text(c) for c in all_conflicts)
            + (f". Arbiter rationale: {rationale}" if rationale else "")
        )
    return update


def route_after_conflict(state: dict) -> str:
    if state.get("critic_feedback"):
        return "planner"
    return "critic"


def _validated_edited_args(fn, tool_name: str, edited_args) -> dict:
    """Whitelist edited args against the tool's real signature.

    A human editing an approval payload in the UI is trusted to change
    *values*, not to introduce arbitrary keyword arguments — unknown keys
    would either TypeError at the call site or silently reach a parameter
    the gate never previewed. Unknown/extra keys are rejected loudly rather
    than dropped, since a silently-ignored edit looks approved but isn't.
    """
    if not isinstance(edited_args, dict):
        raise ValueError(f"edited_args for {tool_name} must be an object, got {type(edited_args).__name__}")
    allowed = set(inspect.signature(fn).parameters) - {"actor", "approved"}
    unknown = set(edited_args) - allowed
    if unknown:
        raise ValueError(
            f"edited_args for {tool_name} contains unknown field(s): {sorted(unknown)}; allowed: {sorted(allowed)}"
        )
    return dict(edited_args)


def _action_signature(entry: dict) -> tuple:
    """Identify an action by what it DOES, not by its approval id.

    A replan re-proposes the same work under a fresh id, so comparing ids would
    never match. Args are sorted and stringified so the signature is stable.
    """
    args = entry.get("args") or {}
    return (entry.get("tool"), tuple(sorted((str(k), str(v)) for k, v in args.items())))


async def approval_gate_node(state: dict) -> dict:
    """Pause the graph before any step flagged requires_approval. Resumes via
    Command(resume=...) from POST /approve, which LangGraph feeds back as the
    interrupt()'s return value.

    On "approve"/"edit", re-invokes the actual tool with approved=True (using
    edited_args in place of the original args for "edit") — the agent node
    deliberately did NOT perform the write when it first hit a
    requires_approval tool, so this is the only place the write happens. On
    "reject", the step is marked rejected so critic/synthesize can react to
    it instead of silently dropping it.

    The interrupt() resume value is expected to be a dict:
    {"decision": "approve"|"reject"|"edit", "edited_args": {...}|None} — POST
    /approve in apps/api/main.py builds this shape. A bare string is also
    accepted (treated as the decision with no edits) for scripts/tests that
    call Command(resume="approve") directly.
    """
    run_id = state["run_id"]
    pending = state.get("pending_approvals", [])
    if not pending:
        return {}

    decisions = dict(state.get("approval_decisions", {}))
    step_updates = {}
    ledger: list[dict] = []

    # Actions the human already declined this run, keyed by what they actually
    # do rather than by approval id (a replan mints new ids for the same work).
    # The critic is TOLD not to retry a rejection and the replan budget bounds
    # how often it can try, but neither is a guarantee: within the allowed
    # replan the planner could re-propose the identical registration and the
    # gate would ask again. Being asked twice to approve the thing you just
    # refused reads as the veto not working.
    declined = {
        _action_signature(entry)
        for entry in state.get("action_log", [])
        if entry.get("outcome") == "not_executed"
    }

    def _record(action, decision, outcome, receipt=None, error=None):
        """Append one row to the authoritative record of what actually happened."""
        ledger.append({
            "approval_id": action.get("id"), "step_id": action.get("step_id"),
            "agent": action.get("agent", ""), "tool": action.get("tool"),
            "args": action.get("args", {}),
            "description": action.get("description", action.get("tool", "")),
            "decision": decision, "outcome": outcome,
            "receipt_id": receipt, "error": error,
        })

    for action in pending:
        if action["id"] in decisions:
            continue

        if _action_signature(action) in declined:
            # Do not ask a second time. Carry the earlier "no" forward.
            step_id = action.get("step_id")
            decisions[action["id"]] = "reject"
            if step_id:
                step_updates[step_id] = {
                    "step_id": step_id, "agent": action.get("agent", ""),
                    "output": f"Not proposed again — you already declined: "
                              f"{action.get('description', action.get('tool'))}",
                    "reasoning": "The same action was rejected earlier in this run.",
                    "data": {}, "status": "rejected",
                }
            _record(action, "reject", "not_executed", error="already declined earlier in this run")
            await _emit(run_id, EventType.APPROVAL_RESOLVED, agent="approval_gate",
                        payload={"id": action["id"], "step_id": step_id,
                                  "decision": "reject", "outcome": "not_executed",
                                  "auto": True,
                                  "reason": "identical action already declined in this run"})
            continue

        await _emit(run_id, EventType.APPROVAL_REQUESTED, agent="approval_gate", payload=action)
        raw_decision = interrupt({"pending_action": action})
        if isinstance(raw_decision, dict):
            decision = raw_decision.get("decision", "reject")
            edited_args = raw_decision.get("edited_args")
            # Refuse a decision aimed at a different action. LangGraph resumes
            # whichever interrupt is pending, so without this check a stale or
            # mis-routed approval would silently resolve the wrong one — the
            # UI could show "approved X" while the graph executed Y.
            target = raw_decision.get("approval_id")
            if target and target != action["id"]:
                decision, edited_args = "reject", None
                await _emit(run_id, EventType.RUN_ERROR, agent="approval_gate", payload={
                    "error": f"approval_id mismatch: decision targeted {target}, pending is {action['id']}",
                    "detail": "rejected for safety; the pending action was not the one the user acted on",
                })
        else:
            decision = raw_decision
            edited_args = None
        decisions[action["id"]] = decision
        step_id = action.get("step_id")
        tool_name = action.get("tool")
        if not step_id or tool_name not in TOOL_REGISTRY:
            _record(action, decision, "skipped", error="no step_id or unknown tool")
            await _emit(run_id, EventType.APPROVAL_RESOLVED, agent="approval_gate",
                        payload={"id": action["id"], "step_id": step_id,
                                  "decision": decision, "outcome": "skipped"})
            continue

        if decision in ("approve", "edit"):
            fn = TOOL_REGISTRY[tool_name]["fn"]
            args = action.get("args", {})
            try:
                if decision == "edit" and edited_args is not None:
                    args = _validated_edited_args(fn, tool_name, edited_args)
                    # Re-run the tool's own pre-checks with the EDITED args by
                    # calling it unapproved first. That path performs the same
                    # existence/capacity/eligibility validation that produced
                    # the original PendingAction, so an edited payload can't
                    # smuggle past the check that opened the gate (e.g.
                    # swapping in a full event to bypass the seat check).
                    recheck = await asyncio.to_thread(fn, **args, approved=False)
                    if not isinstance(recheck, PendingAction):
                        raise ToolError(
                            f"Edited arguments did not re-validate for {tool_name}; refusing to execute."
                        )

                result = await asyncio.to_thread(
                    fn, **args, actor=state.get("student_id", ""), approved=True,
                )
                data = result.model_dump() if hasattr(result, "model_dump") else {"value": result}
                step_updates[step_id] = {
                    "step_id": step_id, "agent": action.get("agent", ""),
                    "output": f"Approved and executed: {action.get('description', tool_name)}",
                    "reasoning": "Human approved this action.", "data": data, "status": "ok",
                }
                _record(action, decision, "executed", receipt=data.get("receipt_id"))
                # The REAL result, only now that it has actually happened. The
                # earlier tool.result for this step said "pending_approval" and
                # carried no receipt — this is what the UI may show as done.
                await _emit(run_id, EventType.TOOL_RESULT, agent=action.get("agent", ""),
                            node_id=step_id,
                            payload={"tool": tool_name, "status": "ok", "data": data,
                                      "approval_id": action["id"]})
                # The step's LAST node.finished said "pending_approval", and the
                # UI derives node status from that event — so an approved,
                # executed, receipted write went on displaying NEEDS APPROVAL
                # forever. Announce the real terminal status now that it has one.
                await _emit(run_id, EventType.NODE_FINISHED, agent=action.get("agent", ""),
                            node_id=step_id, payload={"status": "ok", "after_approval": True})
                await _emit(run_id, EventType.APPROVAL_RESOLVED, agent="approval_gate",
                            payload={"id": action["id"], "step_id": step_id,
                                      "decision": decision, "outcome": "executed"})
            except (ToolError, ValueError, TypeError) as e:
                step_updates[step_id] = {
                    "step_id": step_id, "agent": action.get("agent", ""),
                    "output": f"Approved but execution failed: {e}", "reasoning": "", "data": {}, "status": "error",
                }
                _record(action, decision, "failed", error=str(e))
                await _emit(run_id, EventType.TOOL_RESULT, agent=action.get("agent", ""),
                            node_id=step_id,
                            payload={"tool": tool_name, "status": "error", "error": str(e),
                                      "approval_id": action["id"]})
                await _emit(run_id, EventType.APPROVAL_RESOLVED, agent="approval_gate",
                            payload={"id": action["id"], "step_id": step_id,
                                      "decision": decision, "outcome": "failed"})
        else:
            step_updates[step_id] = {
                "step_id": step_id, "agent": action.get("agent", ""),
                "output": f"Rejected by user: {action.get('description', tool_name)}",
                "reasoning": "Human rejected this action; it was not executed.", "data": {}, "status": "rejected",
            }
            _record(action, decision, "not_executed")
            await _emit(run_id, EventType.APPROVAL_RESOLVED, agent="approval_gate",
                        payload={"id": action["id"], "step_id": step_id,
                                  "decision": decision, "outcome": "not_executed"})

    # A rejected (or denied) step cancels everything downstream of it. Without
    # this the calendar entry and reminder would still be written after the
    # human refused the registration they depend on.
    blocked = {sid for sid, r in step_updates.items() if r["status"] in BLOCKING_STATUSES}
    if blocked and state.get("plan"):
        merged = {**state.get("step_results", {}), **step_updates}
        changed = True
        while changed:  # transitive: cancel dependents of cancelled steps too
            changed = False
            for step in state["plan"].steps:
                if step.id in merged or step.id in step_updates:
                    continue
                if set(step.depends_on) & blocked:
                    reason = "a step it depends on was rejected or not permitted"
                    step_updates[step.id] = {
                        "step_id": step.id, "agent": step.agent,
                        "output": f"Cancelled — {reason}.",
                        "reasoning": f"Depends on {sorted(set(step.depends_on) & blocked)}.",
                        "data": {}, "status": "cancelled",
                    }
                    blocked.add(step.id)
                    changed = True
                    ledger.append({
                        "approval_id": None, "step_id": step.id, "agent": step.agent,
                        "tool": None, "args": {}, "description": step.task,
                        "decision": None, "outcome": "cancelled",
                        "receipt_id": None,
                        "error": f"depends on {sorted(set(step.depends_on) & blocked)}",
                    })

    update: dict = {"approval_decisions": decisions}
    if step_updates:
        update["step_results"] = step_updates
    if ledger:
        update["action_log"] = ledger
    return update


async def critic_node(state: dict) -> dict:
    run_id = state["run_id"]
    plan: Plan = state["plan"]
    results = state.get("step_results", {})

    rejected = [r for r in results.values() if r.get("status") == "rejected"]
    rejection_replans = state.get("rejection_replans", 0)

    # A staged write is not a planning defect. Once the arbiter has cleared
    # the revised plan, the only valid next actor is the human approval gate.
    # Asking the critic to judge an intentionally pending action caused it to
    # demand another replan instead of ever showing Approve / Reject.
    pending = [r for r in results.values() if r.get("status") == "pending_approval"]
    if pending and not state.get("conflicts"):
        await _emit(run_id, EventType.AGENT_THINKING, agent="critic", payload={
            "detail": "verified action is pending human approval; routing directly to the gate",
            "skipped": True,
        })
        return {"critic_feedback": None}

    # Deterministic shortcut: if every step succeeded and the arbiter found no
    # conflicts, there is nothing for the critic to object to. Skipping the
    # call saves a round-trip on the happy path — the case a live demo runs.
    all_ok = results and all(r.get("status") == "ok" for r in results.values())
    if all_ok and not state.get("conflicts"):
        await _emit(run_id, EventType.AGENT_THINKING, agent="critic",
                    payload={"detail": "all steps succeeded and no conflicts were raised; "
                                        "skipping critic LLM call", "skipped": True})
        return {"critic_feedback": None}

    # Out of time: whatever the critic would object to, another planning round
    # costs more than it can now recover. Answer with what we have.
    if _out_of_time(state):
        await _emit(run_id, EventType.AGENT_THINKING, agent="critic", payload={
            "detail": f"turn budget of {TURN_BUDGET_S:.0f}s spent; answering with the "
                      "results already gathered rather than replanning",
            "budget_exhausted": True, "skipped": True,
        })
        return {"critic_feedback": None}

    # A rejection is a human decision, not a defect — replanning around it
    # forever would re-propose what the user just refused (and would re-open
    # the approval gate each pass). Give it one bounded retry, then accept and
    # let synthesize report the rejection honestly.
    if rejected and rejection_replans >= MAX_REJECTION_REPLANS:
        await _emit(run_id, EventType.AGENT_THINKING, agent="critic",
                    payload={"detail": "rejection replan budget exhausted; proceeding with the user's decision",
                              "rejected_steps": [r["step_id"] for r in rejected]})
        return {"critic_feedback": None}

    system = (
        "You are the critic for a multi-agent campus system's output. Judge "
        "whether the collected step results fully satisfy the original goal. "
        "A step the user explicitly REJECTED is a final decision — do NOT ask "
        "to retry or work around it; treat the goal as satisfied in that "
        "respect. A step skipped for insufficient role permissions likewise "
        "cannot be retried.\n"
        'Respond with JSON: {"satisfied": bool, "feedback": str}. feedback '
        "should be empty if satisfied is true, otherwise a concrete instruction "
        "for what the planner should change."
    )
    payload = {"goal": plan.goal,
               "results": {k: {"output": v["output"], "status": v["status"]} for k, v in results.items()}}
    try:
        parsed = await call_llm_async(
            system, [{"role": "user", "content": str(payload)}], json_mode=True,
        )
    except Exception as e:
        # Can't judge without an LLM — default to satisfied rather than looping forever.
        await _emit(run_id, EventType.RUN_ERROR, agent="critic",
                    payload={"error": str(e), "detail": "critic LLM call failed; treating plan as satisfied"})
        return {"critic_feedback": None}

    if not parsed.get("satisfied", True) and state.get("iteration", 0) < MAX_REPLAN_ITERATIONS:
        await _emit(run_id, EventType.PLAN_REVISED, agent="critic", payload=parsed)
        update = {"critic_feedback": parsed.get("feedback", "")}
        if rejected:
            update["rejection_replans"] = rejection_replans + 1
        return update

    return {"critic_feedback": None}


def route_after_critic(state: dict) -> str:
    return "planner" if state.get("critic_feedback") else "approval_gate"


# Deliberately matches any [doc:...] token, not just [doc:<number>]. A live
# model invents markers of its own shape — [doc:s1] (a step id) was observed —
# and a stripper that only understood integers left those on screen.
_CITATION_MARKER = re.compile(r"\s*\[doc:([^\]]*)\]")


def _strip_unresolvable_citations(answer: str, citations: list) -> str:
    """Remove [doc:N] markers that point at a citation the run never retrieved.

    A citation marker is a promise the UI can resolve to a clause. When the
    model emits [doc:0] on a run that produced no rag.retrieved event, the
    marker reads as grounding while pointing at nothing — worse than having no
    citation at all, because it invites a judge to click through to a source
    that does not exist.
    """
    n = len(citations)

    def keep(match: re.Match) -> str:
        ref = match.group(1).strip()
        return match.group(0) if ref.isdigit() and int(ref) < n else ""

    return _CITATION_MARKER.sub(keep, answer)


def _render_action_log(action_log: list[dict]) -> str:
    """The 'Actions taken' section, written from the ledger rather than by the
    model. Whatever the prose above claims, this part cannot be wrong: every
    line corresponds to an approval the gate actually resolved."""
    if not action_log:
        return ""

    lines = []
    for entry in action_log:
        what = entry.get("description") or entry.get("tool") or "action"
        outcome = entry.get("outcome")
        if outcome == "executed":
            receipt = entry.get("receipt_id")
            lines.append(f"- DONE — {what}" + (f" (receipt {receipt})" if receipt else ""))
        elif outcome == "not_executed":
            lines.append(f"- NOT DONE — {what}: you declined this, so nothing was written.")
        elif outcome == "cancelled":
            lines.append(f"- NOT DONE — {what}: cancelled because {entry.get('error')}.")
        elif outcome == "failed":
            lines.append(f"- FAILED — {what}: {entry.get('error')}")
        else:
            lines.append(f"- SKIPPED — {what}: {entry.get('error') or 'not attempted'}")
    return "\n\nActions taken:\n" + "\n".join(lines)


async def synthesize_node(state: dict) -> dict:
    run_id = state["run_id"]
    plan: Plan = state["plan"]
    results = state.get("step_results", {})

    # Conversational turn: the planner already wrote the whole answer, and
    # there are no results, citations or actions to reconcile against it.
    direct = state.get("conversational_reply")
    if direct:
        await _emit(run_id, EventType.RUN_FINISHED, agent="synthesizer",
                    payload={"answer": direct, "citations": [], "actions": []})
        return {"final_answer": direct}

    # Steps the system did NOT complete must be stated, not quietly omitted —
    # a user who asked for an action deserves to know it didn't happen and why.
    not_done = {
        step_id: r for step_id, r in results.items()
        if r.get("status") in ("rejected", "permission_denied", "error", "degraded", "cancelled")
    }

    system = (
        "You write the final answer for a multi-agent campus system, synthesizing "
        "all step results into one coherent response for the user. Cite sources "
        "inline as [doc:N] where step results include them. List any actions "
        "taken (approved/rejected) at the end.\n"
        "WRITE TO THE STUDENT, NOT ABOUT THEM. Say \"you\" and \"your\" — never "
        "\"the student\". Never mention step ids (s1, s2), agent names, or that "
        "you had steps at all: those are how the answer was produced, not part "
        "of the answer. Lead with what they asked for. Be concise and warm.\n"
        "CRITICAL: any step listed under 'not_completed' MUST be stated plainly "
        "in your answer — say what did not happen and why (rejected by the user, "
        "insufficient role permissions, an error, or degraded data). Never imply "
        "an action succeeded when it is listed there.\n\n"
        'Respond with JSON: {"answer": str, "citations": [str]}'
    )
    action_log = state.get("action_log", [])
    payload = {
        "goal": plan.goal,
        "results": {k: v["output"] for k, v in results.items()},
        "approvals": state.get("approval_decisions", {}),
        "not_completed": {k: {"status": v["status"], "output": v["output"]} for k, v in not_done.items()},
        # The ledger is the ground truth about writes. Given to the model so its
        # prose matches reality, and re-appended deterministically below so the
        # answer is correct even when the prose doesn't.
        "actions_actually_taken": action_log,
    }
    try:
        parsed = await call_llm_async(
            system, [{"role": "user", "content": str(payload)}], json_mode=True,
        )
        answer = parsed.get("answer", "")
    except Exception as e:
        await _emit(run_id, EventType.RUN_ERROR, agent="synthesizer",
                    payload={"error": str(e), "detail": "synthesize LLM call failed; concatenating raw step outputs"})
        answer = "I couldn't compose a full answer right now, but here's what each step found:\n" + "\n".join(
            f"- {r['output']}" for r in results.values() if r.get("output")
        )

    # Deterministic backstop: don't rely on the LLM having obeyed the
    # not_completed instruction — append anything it failed to mention. Steps
    # the action ledger covers are skipped, since it states them more precisely
    # (and appending both reads as two different failures, not one).
    logged_steps = {e.get("step_id") for e in action_log if e.get("step_id")}
    unmentioned = [
        r for step_id, r in not_done.items()
        if r["output"] and r["output"] not in answer and step_id not in logged_steps
    ]
    if unmentioned:
        answer += "\n\nNot completed:\n" + "\n".join(f"- {r['output']}" for r in unmentioned)

    # Citations come from what the knowledge steps ACTUALLY retrieved, in the
    # order the [doc:N] markers were built from, so a surviving marker resolves
    # to the clause it names.
    citations = state.get("citations", [])
    answer = _strip_unresolvable_citations(answer, citations)
    answer += _render_action_log(action_log)

    # Ship citations on the event too, not just into state: the UI's only view
    # of a finished run is this event, and the inline [doc:N] markers in the
    # answer are unresolvable without them.
    await _emit(run_id, EventType.RUN_FINISHED, agent="synthesizer",
                payload={"answer": answer, "citations": citations, "actions": action_log})

    # citations is already in state via the append reducer; returning it here
    # would append the whole list to itself.
    return {"final_answer": answer}
