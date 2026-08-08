"""
Specialist agent nodes — Academic, Placement, Events, Knowledge/RAG,
Services&Comms. Each is a thin LangGraph node: pull its Step out of the
plan, pick a scoped tool (if any) via one LLM call, execute it for real
against apps/api/tools/, then compose the step's answer from the actual
tool result. Falls back to a pure-LLM answer only when no tool applies
(e.g. general Q&A) or nothing in the registry fits.
"""
import asyncio
import inspect
import time
import uuid

import pydantic

from apps.api.bus import bus
from apps.api.llm.router import call_llm_async
from apps.api.tools.exceptions import ToolError
from apps.api.tools.knowledge import search_policy
from apps.api.tools.models import PendingAction
from apps.api.tools.registry import TOOL_REGISTRY, TOOLS_BY_AGENT, can_invoke
from apps.api.tools.resilience import drain_events
from packages.contracts.events import AgentEvent, EventType
from packages.contracts.plan import Step

AGENT_SYSTEM_PROMPTS = {
    "academic": (
        "You are the Academic Agent in a Smart Campus multi-agent system. "
        "You handle courses, timetables, attendance, and grades. Answer using "
        "only the task and any provided context — if you don't have enough "
        "information, say so instead of guessing."
    ),
    "placement": (
        "You are the Placement Agent in a Smart Campus multi-agent system. "
        "You handle companies, job postings, applications, and placement "
        "drives. Be precise about deadlines, eligibility, and status."
    ),
    "events": (
        "You are the Events Agent in a Smart Campus multi-agent system. You "
        "handle campus events, RSVPs, capacity, and scheduling. Flag any "
        "scheduling collision or capacity issue explicitly."
    ),
    "knowledge": (
        "You are the Knowledge Agent in a Smart Campus multi-agent system. "
        "You answer using retrieved document context provided below. Each "
        "context entry is labelled [doc:N] with its document title, clause "
        "number and page — cite it inline exactly as [doc:N] when you use it. "
        "If the context doesn't cover the question, say so plainly."
    ),
    "services": (
        "You are the Services & Comms Agent in a Smart Campus multi-agent "
        "system. You handle hostel, library, grievances, and general campus "
        "communications. Be practical and cite any relevant policy."
    ),
}

# Prepended to every agent's write-up call.
#
# Asked "what happens if I miss too many classes?", the Academic Agent — which
# has no policy tool — answered from the model's own priors and stated "80%
# attendance in lectures", a rule that does not exist in this institution's
# regulations. A confidently invented number is the single most damaging thing
# this system can produce: everything else it does is auditable, and one
# fabricated figure makes a judge doubt all of it.
GROUNDING_RULES = """GROUND EVERY CLAIM.
Use ONLY the tool results, retrieved clauses, and prior step results given to
you. Copy figures, dates and percentages verbatim — never round, adjust, or
recall them from memory. If you were given no tool result and no retrieved
context, you do not know the answer: say plainly what you could not look up
and stop. Never state a rule, threshold, deadline or number that is not
present in the material above, even if you are confident it is correct."""

QUERY_JSON_INSTRUCTIONS = GROUNDING_RULES + """

Respond with a JSON object with exactly these keys:
- "output": your direct answer/result for this step
- "reasoning": brief explanation of how you derived it
- "data": a JSON object with any structured data relevant to the result (empty object {} if none)"""


def _tool_params(fn) -> list[str]:
    return [p for p in inspect.signature(fn).parameters if p not in ("actor", "approved")]


def _missing_required_args(fn, args: dict) -> list[str]:
    """Parameters with no default that the model failed to supply."""
    return [
        name for name, p in inspect.signature(fn).parameters.items()
        if name not in ("actor", "approved")
        and p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        and name not in args
    ]


def _tool_catalog(agent_name: str) -> str:
    lines = []
    for name in TOOLS_BY_AGENT.get(agent_name, []):
        fn = TOOL_REGISTRY[name]["fn"]
        params = _tool_params(fn)
        doc = (inspect.getdoc(fn) or "").split("\n")[0]
        lines.append(f"- {name}({', '.join(params)}): {doc}")
    return "\n".join(lines)


TOOL_SELECTION_INSTRUCTIONS = """You may call at most one tool to complete this
step. Respond with a JSON object with exactly these keys:
- "tool": the exact tool name to call, or "" if no tool is needed and you can
  answer directly from the task/context
- "args": a JSON object of argument name -> value for the chosen tool
  (omit args you don't have a value for — defaults will be filled in where
  possible, e.g. student_id from the current session)
- "reasoning": one sentence on why this tool (or no tool) is the right choice

Available tools for you:
{catalog}"""


def _can_skip_compose(agent_name: str, tool_data: dict, tool_status: str) -> bool:
    """Whether the step's write-up can be produced without an LLM call.

    Structured successes and deterministic tool errors need no second model
    call. Policy clauses are included: quoting the retrieved clause directly
    is both faster and more faithful than asking a model to paraphrase it.
    """
    return bool(tool_data) and tool_status in ("ok", "degraded", "error")


def _describe_tool_result(tool_name: str, tool_data: dict, tool_status: str) -> str:
    """Deterministic, faithful one-liner for a structured tool result.

    Values are copied verbatim — never paraphrased — so numbers cannot drift.
    Written in SECOND PERSON: these strings are what the student ends up
    reading, and "The student meets the eligibility criteria" is a sentence
    about them rather than to them, which reads like a case file.
    """
    if tool_status == "error":
        return f"I couldn't complete {tool_name or 'this lookup'}: {tool_data.get('error', 'the tool failed')}."

    if tool_status == "degraded":
        reason = tool_data.get("degradation_reason", "the source was unavailable")
        return f"I couldn't reach live data for {tool_name}, so this is from cache: {reason}"

    # Tool-specific phrasings for the cases that carry the demo's key facts.
    if "is_eligible" in tool_data and "breakdown" in tool_data:
        # Company ids are lowercase slugs; "eligible for google" reads like a
        # database row rather than a sentence.
        company = str(tool_data.get("company_id", "this company")).replace("_", " ").title()
        fails = [f"{c['criterion']} (needs {c['required']}, you have {c['actual']})"
                 for c in tool_data["breakdown"] if not c["passed"]]
        if tool_data["is_eligible"]:
            passes = ", ".join(f"{c['criterion']} {c['actual']}" for c in tool_data["breakdown"])
            return f"You're eligible for {company} — {passes}."
        return f"You're not eligible for {company} yet. Short on: {'; '.join(fails)}."

    if "current_pct" in tool_data:
        needed = tool_data.get("classes_needed_for_75")
        tail = (" You're above the 75% bar." if tool_data.get("is_eligible")
                else f" You'd need {needed} more consecutive classes to reach 75%.")
        return (f"Your attendance in {tool_data.get('course_id')} is {tool_data['current_pct']}% "
                f"({tool_data.get('classes_attended')} of {tool_data.get('classes_held')} classes).{tail}")

    if "has_conflict" in tool_data:
        if not tool_data["has_conflict"]:
            return "That slot is free — nothing on your timetable clashes with it."
        return f"That clashes with your timetable. {tool_data.get('detail', '')}".strip()

    if "events" in tool_data:
        items = tool_data["events"]
        if not items:
            return "No matching events found."
        lines = [f"{e['title']} on {e['day_of_week']} {e['date']} {e['start_time']}-{e['end_time']} "
                 f"({e['seats_remaining']} seats left)" for e in items[:5]]
        return f"Found {len(items)} matching event(s): " + "; ".join(lines)

    if "seats_remaining" in tool_data:
        return (f"Event {tool_data.get('event_id')}: {tool_data['seats_remaining']} of "
                f"{tool_data.get('total_seats')} seats remaining.")

    if "receipt_id" in tool_data:
        return f"Done — {tool_name.replace('_', ' ')} (receipt {tool_data['receipt_id']})."

    if "records" in tool_data:
        recs = tool_data["records"]
        if not recs:
            return "No attendance records."
        below = [r for r in recs if r["percentage"] < 75]
        if below:
            risks = "; ".join(f"{r['course_name']} {r['percentage']}%" for r in below)
            safe = "; ".join(f"{r['course_name']} {r['percentage']}%" for r in recs if r["percentage"] >= 75)
            return f"You're below 75% in {risks}. Your other recorded courses are at or above 75%: {safe}."
        return "All your recorded courses are at or above 75%: " + "; ".join(
            f"{r['course_name']} {r['percentage']}%" for r in recs)

    if "citations" in tool_data:
        citations = tool_data["citations"]
        if not citations:
            return "I couldn't find a sufficiently relevant institutional policy for this question."
        citation = citations[0]
        first_sentence = str(citation.get("text", "")).split(". ", 1)[0].strip()
        if first_sentence and not first_sentence.endswith("."):
            first_sentence += "."
        return (f"{citation.get('doc_title', 'The regulations')} clause "
                f"{citation.get('clause', '')}: {first_sentence} [doc:0]")

    if "entries" in tool_data:
        entries = tool_data["entries"]
        return "Timetable: " + "; ".join(
            f"{e['course_name']} {e['day_of_week']} {e['start_time']}-{e['end_time']}" for e in entries[:8])

    if "companies" in tool_data:
        comps = tool_data["companies"]
        return f"{len(comps)} companies: " + "; ".join(
            f"{c['name']} ({c['role']}, min CGPA {c['min_cgpa']})" for c in comps[:6])

    if "recommendations" in tool_data:
        recs = tool_data["recommendations"]
        return f"{len(recs)} recommendation(s): " + "; ".join(
            r.get("name") or r.get("course_name", "") for r in recs[:6])

    return f"{tool_name} returned: {tool_data}"


async def _emit_resilience_events(run_id: str, node_id: str, agent_name: str) -> None:
    """Drain retry/fallback notices recorded by @resilient in the worker
    thread and emit them on the event loop, so the UI can show the
    attempt -> retry -> circuit-break -> fallback story as it happened."""
    type_map = {"tool.retry": EventType.TOOL_RETRY, "tool.fallback": EventType.TOOL_FALLBACK}
    for ev in drain_events():
        event_type = type_map.get(ev["kind"])
        if not event_type:
            continue
        await bus.emit(AgentEvent(
            id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=event_type,
            node_id=node_id, agent=agent_name, payload=ev["payload"],
        ))


def _serialize_tool_result(result) -> tuple[dict, str]:
    """Returns (data_dict, status) for a tool's return value."""
    if isinstance(result, PendingAction):
        return result.model_dump(), "pending_approval"
    if isinstance(result, pydantic.BaseModel):
        return result.model_dump(), "ok"
    if isinstance(result, dict) and result.get("degraded"):
        return result, "degraded"
    return {"value": result}, "ok"


async def run_agent_step(agent_name: str, step: Step, state: dict) -> dict:
    """Execute one Step: pick a tool (if any), run it for real, compose the
    step's answer from the actual result. Never raises — failures become an
    error StepResult so one bad step doesn't take down the whole dispatch.
    """
    run_id = state["run_id"]
    start = time.time()
    await bus.emit(AgentEvent(
        id=uuid.uuid4().hex[:8], run_id=run_id, ts=start, type=EventType.NODE_STARTED,
        node_id=step.id, agent=agent_name, payload={"task": step.task},
    ))

    # Hoisted out of the try so the post-amble below can read them even on
    # the exception path.
    tool_name = ""
    tool_data: dict = {}
    retrieved: list[dict] = []
    knowledge_abstained = False

    try:
        context_block = ""
        # Carried out of this function so the graph can put the ACTUAL retrieved
        # clauses into state. The final answer's citation list used to be
        # whatever the synthesizer LLM echoed back, which is a claim about
        # sources rather than a record of them — a model that invented [doc:0]
        # got a citation list to match. This is the record.
        if agent_name == "knowledge":
            # search_policy(), not retrieve(): retrieve() queries the generic
            # `agentx_docs` collection (ad-hoc uploads), while the clause-level
            # institutional corpus lives in `agentx_policies`. Using retrieve()
            # here fed the Knowledge agent unrelated documents and made
            # clause-accurate citation impossible.
            result = await asyncio.to_thread(search_policy, step.task)
            citations = [] if result.no_relevant_context else result.citations
            retrieved = [c.model_dump() for c in citations]

            await bus.emit(AgentEvent(
                id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.RAG_RETRIEVED,
                node_id=step.id, agent=agent_name,
                payload={
                    # `chunks` retained so previously recorded fixtures and any
                    # consumer keyed on the old shape still parse.
                    "chunks": len(citations),
                    "query": step.task,
                    "abstained": result.no_relevant_context,
                    "citations": retrieved,
                },
            ))

            if citations:
                # Index matches the [doc:N] markers the agent is told to cite,
                # so a citation in the answer resolves to a real clause.
                context_block = "Retrieved context:\n" + "\n\n".join(
                    f"[doc:{i}] ({c.doc_title} clause {c.clause}, p.{c.page}) {c.text}"
                    for i, c in enumerate(citations)
                ) + "\n\n"
            elif result.no_relevant_context:
                knowledge_abstained = True
                context_block = (
                    "No institutional document was sufficiently relevant to this question. "
                    "Say so plainly rather than guessing.\n\n"
                )

        upstream = {dep: state["step_results"][dep]["output"]
                    for dep in step.depends_on if dep in state.get("step_results", {})}
        upstream_block = f"Prior step results: {upstream}\n\n" if upstream else ""
        session_block = f"Current student_id: {state['student_id']}\n\n" if state.get("student_id") else ""
        memory_block = state.get("memory_block") or ""
        if memory_block:
            session_block += f"MEMORY (what we already know about this student):\n{memory_block}\n\n"

        await bus.emit(AgentEvent(
            id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.AGENT_THINKING,
            node_id=step.id, agent=agent_name, payload={},
        ))

        # An abstention is already the truthful final result. Asking another
        # model to improvise after retrieval found no relevant clause both
        # wastes a provider call and creates a hallucination opportunity. More
        # importantly, this was one of the paths that could strand the run in
        # "Executing" when the fallback model was slow.
        if knowledge_abstained:
            message = "I couldn't find a sufficiently relevant institutional policy for this question."
            result = {
                "step_id": step.id, "agent": agent_name, "output": message,
                "reasoning": "Policy retrieval abstained rather than guessing.",
                "data": {"abstained": True}, "status": "degraded",
            }
            latency_ms = (time.time() - start) * 1000
            await bus.emit(AgentEvent(
                id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.NODE_FINISHED,
                node_id=step.id, agent=agent_name,
                payload={"status": "degraded"}, latency_ms=latency_ms,
            ))
            return result

        tool_status = "ok"
        catalog = _tool_catalog(agent_name)

        if agent_name == "knowledge":
            # The policy retrieval above already called the correct tool and
            # produced the grounded clauses. Do not ask a model to select and
            # call search_policy a second time, then ask another model merely
            # to repeat the first clause.
            tool_name = "search_policy"
            tool_data = {
                "query": step.task,
                "citations": retrieved,
                "no_relevant_context": knowledge_abstained,
            }
            await bus.emit(AgentEvent(
                id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.TOOL_CALLED,
                node_id=step.id, agent=agent_name,
                payload={"tool": tool_name, "args": {"query": step.task}},
            ))
            await bus.emit(AgentEvent(
                id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.TOOL_RESULT,
                node_id=step.id, agent=agent_name,
                payload={"tool": tool_name, "status": "ok", "data": tool_data},
            ))
        elif catalog:
            if step.tool:
                selection = {
                    "tool": step.tool,
                    "args": dict(step.tool_args),
                    "reasoning": "Verified deterministic recovery plan.",
                }
            else:
                selection_system = AGENT_SYSTEM_PROMPTS[agent_name] + "\n\n" + TOOL_SELECTION_INSTRUCTIONS.format(catalog=catalog)
                selection_user = f"{session_block}{context_block}{upstream_block}Task: {step.task}"
                selection = await call_llm_async(
                    selection_system, [{"role": "user", "content": selection_user}], json_mode=True,
                )
            tool_name = selection.get("tool") or ""

            role = state.get("role", "student")
            if tool_name and tool_name in TOOL_REGISTRY and not can_invoke(tool_name, role):
                # Explicit, surfaced refusal — never a silent drop. The step
                # ends here with a status synthesize is required to report.
                required = TOOL_REGISTRY[tool_name].get("required_role", "student")
                message = (
                    f"Skipped: this step needs the '{tool_name}' tool, which requires the "
                    f"'{required}' role, and you are signed in as '{role}'."
                )
                await bus.emit(AgentEvent(
                    id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.TOOL_RESULT,
                    node_id=step.id, agent=agent_name,
                    payload={"tool": tool_name, "status": "permission_denied",
                              "required_role": required, "held_role": role},
                ))
                result = {
                    "step_id": step.id, "agent": agent_name, "output": message,
                    "reasoning": f"Role '{role}' is below the required '{required}'.",
                    "data": {"required_role": required, "held_role": role, "tool": tool_name},
                    "status": "permission_denied",
                }
                latency_ms = (time.time() - start) * 1000
                await bus.emit(AgentEvent(
                    id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.NODE_FINISHED,
                    node_id=step.id, agent=agent_name,
                    payload={"status": "permission_denied"}, latency_ms=latency_ms,
                ))
                return result

            if tool_name and tool_name in TOOL_REGISTRY and TOOL_REGISTRY[tool_name]["agent"] == agent_name:
                fn = TOOL_REGISTRY[tool_name]["fn"]
                params = _tool_params(fn)
                args = dict(selection.get("args") or {})
                # The session owns student identity, never the model. Smaller
                # models reliably mangle the roll number (e.g. dropping the
                # dashes into an int), and letting a model choose *whose*
                # record to touch is a privacy hole regardless of accuracy.
                if "student_id" in params and state.get("student_id"):
                    args["student_id"] = state["student_id"]
                args = {k: v for k, v in args.items() if k in params}

                missing = _missing_required_args(fn, args)
                if missing:
                    # Calling anyway would TypeError, get retried twice for an
                    # error that can never succeed, and then break the fallback
                    # too. Fail fast with something the agent can act on.
                    tool_data = {"error": f"missing required argument(s): {', '.join(missing)}"}
                    tool_status = "error"
                    await bus.emit(AgentEvent(
                        id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.TOOL_RESULT,
                        node_id=step.id, agent=agent_name,
                        payload={"tool": tool_name, "status": "error", "error": tool_data["error"]},
                    ))
                    args = None  # signal: do not execute

                if args is not None:
                    await bus.emit(AgentEvent(
                        id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.TOOL_CALLED,
                        node_id=step.id, agent=agent_name, payload={"tool": tool_name, "args": args},
                    ))
                    try:
                        tool_result = await asyncio.to_thread(fn, **args)
                        tool_data, tool_status = _serialize_tool_result(tool_result)
                        if tool_status == "pending_approval":
                            tool_data["step_id"] = step.id
                            tool_data["args"] = args
                        await _emit_resilience_events(run_id, step.id, agent_name)
                        await bus.emit(AgentEvent(
                            id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.TOOL_RESULT,
                            node_id=step.id, agent=agent_name,
                            payload={
                                "tool": tool_name, "status": tool_status,
                                # The structured result itself. Without this the
                                # UI can only say "it worked" — it cannot show
                                # WHY (the eligibility breakdown, the attendance
                                # numbers, the seats left), which is the whole
                                # basis of verifiable evidence.
                                "data": tool_data,
                                **({"approval_id": tool_data.get("id")}
                                   if tool_status == "pending_approval" else {}),
                                **({"degradation_reason": tool_data.get("degradation_reason")}
                                   if tool_status == "degraded" else {}),
                            },
                        ))
                    except ToolError as e:
                        # A domain refusal (seats full, no such record, permission
                        # denied) — passed through by @resilient on purpose.
                        tool_data, tool_status = {"error": str(e)}, "error"
                        await _emit_resilience_events(run_id, step.id, agent_name)
                        await bus.emit(AgentEvent(
                            id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.TOOL_RESULT,
                            node_id=step.id, agent=agent_name, payload={"tool": tool_name, "status": "error", "error": str(e)},
                        ))

        if tool_status == "pending_approval":
            action = PendingAction(**{**tool_data, "agent": agent_name, "tool": tool_name})
            result = {
                "step_id": step.id, "agent": agent_name, "output": f"Awaiting approval: {action.description}",
                "reasoning": "This action requires human approval before executing.",
                "data": action.model_dump(), "status": "pending_approval",
            }
        elif _can_skip_compose(agent_name, tool_data, tool_status):
            # The tool already returned structured ground truth, and
            # synthesize_node writes the user-facing prose at the end anyway.
            # Paying an LLM call here to re-word a Pydantic object is pure
            # latency AND an extra chance to hallucinate over exact numbers,
            # so summarise it deterministically instead.
            result = {
                "step_id": step.id, "agent": agent_name,
                "output": _describe_tool_result(tool_name, tool_data, tool_status),
                "reasoning": (
                    f"{tool_name} returned a deterministic error; no model interpretation applied."
                    if tool_status == "error"
                    else f"Read directly from {tool_name}; no interpretation applied."
                ),
                "data": {"tool_result": tool_data},
                "status": tool_status,
            }
        else:
            compose_system = AGENT_SYSTEM_PROMPTS[agent_name] + "\n\n" + QUERY_JSON_INSTRUCTIONS
            tool_block = f"Tool result (ground truth — use this, don't invent numbers): {tool_data}\n\n" if tool_data else ""
            compose_user = f"{session_block}{context_block}{upstream_block}{tool_block}Task: {step.task}"
            parsed = await call_llm_async(
                compose_system, [{"role": "user", "content": compose_user}], json_mode=True,
            )

            result = {
                "step_id": step.id, "agent": agent_name,
                "output": parsed.get("output", ""), "reasoning": parsed.get("reasoning", ""),
                "data": {**parsed.get("data", {}), **({"tool_result": tool_data} if tool_data else {})},
                "status": "ok" if tool_status != "error" else "error",
            }
    except Exception as e:
        result = {
            "step_id": step.id, "agent": agent_name, "output": f"Step failed: {e}",
            "reasoning": "", "data": {}, "status": "error",
        }
        await bus.emit(AgentEvent(
            id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.NODE_FAILED,
            node_id=step.id, agent=agent_name, payload={"error": str(e)},
        ))

    latency_ms = (time.time() - start) * 1000
    await bus.emit(AgentEvent(
        id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.NODE_FINISHED,
        node_id=step.id, agent=agent_name, payload={"status": result["status"]}, latency_ms=latency_ms,
    ))
    if retrieved:
        result["_retrieved"] = retrieved

    # A receipt means a write was committed. Gated writes are recorded by the
    # approval gate, but UNGATED ones (calendar entries, reminders) were not
    # recorded anywhere — so "Actions taken" listed the registration and stayed
    # silent about the two writes that followed it. Every committed write
    # belongs in the ledger; whether it needed a human is a separate fact.
    receipt = tool_data.get("receipt_id") if isinstance(tool_data, dict) else None
    if receipt and result["status"] in ("ok", "degraded"):
        result["_write"] = {
            "approval_id": None, "step_id": step.id, "agent": agent_name,
            "tool": tool_name, "args": {}, "description": step.task,
            "decision": None, "outcome": "executed",
            "receipt_id": receipt, "error": None, "gated": False,
        }
    return result


def make_agent_node(agent_name: str):
    """Build a LangGraph node fn bound to one agent. Send() carries the
    specific step to run in state["_active_step_id"]."""

    async def node(state: dict) -> dict:
        step_id = state["_active_step_id"]
        step = next(s for s in state["plan"].steps if s.id == step_id)
        result = await run_agent_step(agent_name, step, state)
        # Lifted out of the StepResult before it reaches state: step_results is
        # stringified into later prompts, and a full clause dump there would
        # bloat every downstream call for no benefit. Citations belong in their
        # own channel.
        retrieved = result.pop("_retrieved", None)
        write = result.pop("_write", None)
        update: dict = {"step_results": {step_id: result}}
        if retrieved:
            update["citations"] = retrieved
        if write:
            update["action_log"] = [write]
        if result["status"] == "pending_approval":
            update["pending_approvals"] = [result["data"]]
        return update

    node.__name__ = f"agent_{agent_name}"
    return node
