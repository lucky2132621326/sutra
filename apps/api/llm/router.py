"""
LLM router — provider-agnostic call_llm with automatic fallback.

Extracted from the original engine.py (AgentX skeleton), then adapted for
the hackathon's actual provider mix (2026-08-07). Groq is primary, not
Gemini: the free-tier Gemini key in use here hits its daily quota almost
immediately under multi-agent load, and every failed Gemini call still costs
~3-10s before falling through — measured directly against a running graph.
Groq answers in under a second when it has quota. Gemini stays in the chain
as a fallback. Anthropic/OpenAI are optional legacy fallbacks — they only
enter the chain if a key happens to be set — since neither is free.

Fallback order: Groq -> Gemini -> Ollama (local `ollama serve` first if
reachable, else Ollama Cloud if OLLAMA_API_KEY is set) -> Anthropic -> OpenAI.

MOCK_LLM=1 short-circuits everything above with fast, deterministic,
zero-network responses — see _mock_llm — for testing graph/dispatch
mechanics without spending real provider quota.
"""
import asyncio
import os
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

GEMINI_MODEL = "gemini-flash-latest"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
OLLAMA_LOCAL_MODEL = os.environ.get("OLLAMA_LOCAL_MODEL", "qwen2.5:7b")
OLLAMA_CLOUD_MODEL = os.environ.get("OLLAMA_CLOUD_MODEL", "gpt-oss:20b-cloud")
ANTHROPIC_MODEL = "claude-opus-4-8"
OPENAI_MODEL = "gpt-4o-mini"

ANANYA_ID = "1602-23-733-042"

# Abandon a provider that is merely SLOW, not just one that errors. Measured
# from this machine: warm Groq answers in ~0.15s, but Gemini took ~180s per
# call — long enough that falling back to it was worse than the failure it was
# meant to cover. A bounded wait keeps the fallback chain useful.
PROVIDER_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "25"))
# A provider chain can contain several individually-bounded clients. Without a
# whole-call deadline those bounds add up (Groq -> Ollama -> Gemini), so one
# agent can still sit inside fallback for minutes and prevent node.finished.
# The async graph always enters call_llm through call_llm_async below.
LLM_CALL_TIMEOUT_S = float(os.environ.get("LLM_CALL_TIMEOUT_S", "20"))


def _force_ipv4() -> None:
    """Restrict outbound socket resolution to IPv4.

    MEASURED on this network: IPv6 connects time out (21s each), IPv4 connects
    in 0.05s. generativelanguage.googleapis.com publishes ~8 AAAA records, so
    a client works through them serially before falling back — ~169s on the
    first call, then 0.2s once a connection is pooled. api.groq.com has a
    single AAAA record, which is the only reason Groq appeared healthy.

    This is a network-environment workaround, not a protocol opinion: it is
    scoped to address resolution and changes no request logic. Set
    ALLOW_IPV6=1 to disable it on a network with working IPv6.
    """
    if os.environ.get("ALLOW_IPV6"):
        return
    import socket

    if getattr(socket, "_ipv4_forced", False):
        return
    _original_getaddrinfo = socket.getaddrinfo

    def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        results = _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        # Fall back to the unrestricted lookup for hosts with no A record,
        # so an IPv6-only destination still resolves instead of hard-failing.
        return results or _original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = _ipv4_only
    socket._ipv4_forced = True


_force_ipv4()


def _parse_json_response(text):
    """Parse the first JSON value in text, tolerating trailing junk or
    markdown fences that some providers occasionally emit even in JSON mode.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.JSONDecoder().raw_decode(text.strip())[0]


def _call_gemini(system, messages, json_mode):
    from google import genai
    from google.genai import types

    client = _cached("gemini", lambda: genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=PROVIDER_TIMEOUT_S * 1000),  # milliseconds
    ))

    contents = [
        types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=m["content"])])
        for m in messages
    ]

    config = types.GenerateContentConfig(system_instruction=system)
    if json_mode:
        config.response_mime_type = "application/json"

    response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
    text = response.text
    return _parse_json_response(text) if json_mode else text


def groq_keys() -> list[str]:
    """All configured Groq keys, in rotation order: GROQ_API_KEY, then
    GROQ_API_KEY_2, _3, ... Blank entries are skipped so a placeholder line in
    .env is harmless.

    NOTE: Groq enforces rate limits per ORGANISATION, not per key — the 429
    body names the org, not the key. Extra keys minted inside one account
    therefore share a single quota and buy nothing; these must come from
    separate Groq accounts to add real headroom.
    """
    keys = []
    primary = os.environ.get("GROQ_API_KEY", "").strip()
    if primary:
        keys.append(primary)
    i = 2
    while True:
        key = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if not key:
            break
        keys.append(key)
        i += 1
    return keys


# Keys observed to be rate-limited this process, so a run doesn't waste a
# round-trip re-testing a key that just 429'd.
_exhausted_groq_keys: set[str] = set()


def _call_groq(system, messages, json_mode):
    from groq import Groq

    kwargs = {}
    effective_system = system
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        # Groq hard-rejects json_object mode unless the literal word "json"
        # appears somewhere in the messages (400: "'messages' must contain the
        # word 'json'"). Our prompts usually satisfy this by accident; make it
        # deliberate so a reworded prompt can't 400 the whole run.
        if "json" not in effective_system.lower():
            effective_system += "\n\nRespond with valid JSON only."

    keys = groq_keys()
    if not keys:
        raise RuntimeError("no Groq API key configured")

    # Prefer keys not already known to be exhausted, but keep them as a last
    # resort — a daily window can roll over mid-session.
    ordered = [k for k in keys if k not in _exhausted_groq_keys] + \
              [k for k in keys if k in _exhausted_groq_keys]

    last_error = None
    for index, key in enumerate(ordered):
        # max_retries=0 is deliberate and load-bearing.
        #
        # The SDK defaults to 2 internal retries that honour the 429
        # `retry-after` header — so a single rate-limited key SLEEPS inside
        # client.create() before we ever see the error and rotate. MEASURED:
        # one planner call took 103s while the other 19 calls in the same run
        # averaged 0.75s, because it sat in that backoff.
        #
        # Rotating to a different key is our retry strategy, and it is strictly
        # better than waiting: a fresh key answers in ~0.3s, whereas honouring
        # retry-after burns a minute of a live demo doing nothing.
        client = _cached(
            f"groq:{key[-8:]}",
            lambda k=key: Groq(api_key=k, timeout=PROVIDER_TIMEOUT_S, max_retries=0),
        )
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": effective_system}] + messages,
                **kwargs,
            )
            _exhausted_groq_keys.discard(key)
            text = response.choices[0].message.content
            return _parse_json_response(text) if json_mode else text
        except Exception as e:
            last_error = e
            text = str(e).lower()
            key_specific = (
                "rate_limit" in text or "429" in text          # quota exhausted
                or "401" in text or "invalid api key" in text  # wrong/dead key
                or "403" in text                               # key lacks access
            )
            if key_specific:
                # Any of these are faults of THIS key, so another key may well
                # succeed. Notably a pasted-in-error key (e.g. an xAI `xai-`
                # key in a Groq slot) 401s — that must not abort the chain and
                # strand the valid keys behind it.
                _exhausted_groq_keys.add(key)
                continue
            raise  # malformed request etc. — every key would fail identically

    raise RuntimeError(f"all {len(keys)} Groq key(s) unusable; last error: {last_error}")


def _call_ollama(system, messages, json_mode):
    """Prefers a genuinely local `ollama serve` on localhost:11434 — tried
    first regardless of OLLAMA_API_KEY, since a local model has no quota and
    no network dependency. Falls back to Ollama Cloud only if the local
    server isn't reachable and a cloud key is set.
    """
    import ollama

    kwargs = {}
    if json_mode:
        kwargs["format"] = "json"

    try:
        local_client = ollama.Client(timeout=PROVIDER_TIMEOUT_S)  # defaults to http://localhost:11434
        response = local_client.chat(model=OLLAMA_LOCAL_MODEL, messages=[{"role": "system", "content": system}] + messages, **kwargs)
        text = response["message"]["content"]
        return _parse_json_response(text) if json_mode else text
    except Exception as local_error:
        api_key = os.environ.get("OLLAMA_API_KEY")
        if not api_key:
            raise
        cloud_client = ollama.Client(
            host="https://ollama.com", headers={"Authorization": f"Bearer {api_key}"},
            timeout=PROVIDER_TIMEOUT_S,
        )
        response = cloud_client.chat(model=OLLAMA_CLOUD_MODEL, messages=[{"role": "system", "content": system}] + messages, **kwargs)
        text = response["message"]["content"]
        return _parse_json_response(text) if json_mode else text


def _call_anthropic(system, messages, json_mode):
    import anthropic

    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"], timeout=PROVIDER_TIMEOUT_S, max_retries=0,
    )

    effective_system = system
    if json_mode:
        effective_system += "\n\nRespond with valid JSON only. No prose, no markdown fences."

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        system=effective_system,
        messages=messages,
    )
    text = next(b.text for b in response.content if b.type == "text")
    return _parse_json_response(text) if json_mode else text


def _call_openai(system, messages, json_mode):
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"], timeout=PROVIDER_TIMEOUT_S, max_retries=0,
    )

    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system}] + messages,
        **kwargs,
    )
    text = response.choices[0].message.content
    return _parse_json_response(text) if json_mode else text


def _mock_final_answer(user_content: str) -> dict:
    """Compose the mock's final answer from the synthesis payload it was given.

    The payload arrives as `str(dict)` of plain Python values, so it round-trips
    through ast.literal_eval. If that ever fails we fall back to a statement of
    ignorance rather than to a confident summary — the whole point is that this
    function never claims something it cannot see.
    """
    import ast

    try:
        payload = ast.literal_eval(user_content)
        assert isinstance(payload, dict)
    except (ValueError, SyntaxError, AssertionError):
        return {"answer": "I completed the run but could not summarize the results.",
                "citations": []}

    results = payload.get("results") or {}
    not_completed = payload.get("not_completed") or {}

    # Report what the steps actually found, in plan order, excluding the ones
    # that didn't complete (synthesize_node states those separately, and the
    # action ledger states the writes).
    findings = [
        str(out).strip()
        for step_id, out in results.items()
        if step_id not in not_completed and str(out).strip()
    ]
    if findings:
        answer = " ".join(findings)
    else:
        answer = f"I worked on: {payload.get('goal', 'your request')}."

    # No citations are invented here. synthesize_node strips any [doc:N] marker
    # that this empty list cannot resolve.
    return {"answer": answer, "citations": []}


def _mock_llm(system, messages, json_mode):
    """Deterministic, instant, zero-network stand-in for the graph's known
    call sites. Dispatches on distinctive substrings each node's system
    prompt actually contains (see apps/api/graph/nodes.py and agents.py) —
    fragile to prompt rewording by nature of being a mock, but exactly what
    MOCK_LLM=1 is for: verifying dispatch/event-sequence mechanics, not
    prompt correctness.
    """
    user_content = messages[-1]["content"] if messages else ""

    if "orchestrator planner" in system:
        # A revision arrives with the arbiter's feedback in the user message.
        # Re-proposing Thursday would be the planner ignoring the veto — and
        # would re-register the same event, draining its seats. Route to the
        # Saturday batch, which is what the arbitration actually concluded.
        # Scope the match to the explicit revision block. Recalled memory and
        # upstream results can legitimately mention an earlier Saturday run;
        # treating that as fresh arbiter feedback made a new hero run silently
        # skip Thursday, so there was no conflict or visible collaboration.
        revision_marker = "Previous plan needs revision. Feedback:"
        revision_feedback = (
            user_content.split(revision_marker, 1)[1]
            if revision_marker in user_content
            else ""
        )
        if "SCHEDULE_COLLISION" in revision_feedback or "Saturday batch" in revision_feedback:
            return {
                "goal": "Register for the Saturday batch of the Placement Prep Workshop, "
                        "avoiding the DBMS Lab clash.",
                "reasoning": "The Academic Agent vetoed Thursday: it collides with the DBMS Lab "
                             "and attendance there is already below the 75% bar. The Saturday "
                             "batch is clash-free and still has seats, so registration moves there.",
                "steps": [
                    {"id": "s1", "agent": "placement", "task": "Confirm Google internship eligibility.",
                     "depends_on": [], "expected_output": "Eligibility verdict.", "requires_approval": False},
                    {"id": "s2", "agent": "events",
                     "task": "Register for the Saturday batch of the Placement Prep Workshop.",
                     "depends_on": ["s1"], "expected_output": "Registration confirmation.",
                     "requires_approval": True},
                    # Two steps, not one. An agent calls AT MOST ONE tool per
                    # step, so a single "add to calendar and remind an hour
                    # before" step ran add_to_calendar and silently dropped the
                    # reminder — while the graph still displayed a step whose
                    # text promised both.
                    {"id": "s3", "agent": "services",
                     "task": "Add the Saturday workshop to the calendar.",
                     "depends_on": ["s2"], "expected_output": "Calendar entry confirmation.",
                     "requires_approval": True},
                    {"id": "s4", "agent": "services",
                     "task": "Set a reminder an hour before the Saturday workshop.",
                     "depends_on": ["s3"], "expected_output": "Reminder confirmation.",
                     "requires_approval": True},
                ],
            }
        # A goal with no write intent gets a read-only plan. Without this branch
        # the mock returned the registration plan for EVERY goal, so once the
        # conflict preflight became deterministic there was no way to record a
        # genuinely conflict-free fixture — every mock run hit the Thursday
        # clash. This path also exercises the knowledge agent, which is the only
        # producer of rag.retrieved and therefore of citations.
        # Match on the GOAL line only. The planner prepends a MEMORY block of
        # recalled facts, and after a few recorded runs those facts mention
        # registration — which made every goal look like a write request and
        # silently sent the read-only fixture down the registration path.
        _goal_line = user_content.split("User goal:", 1)[-1].split("\n\n", 1)[0]
        _goal = _goal_line.strip().lower()

        # Small talk gets a reply, not a plan. Without this the mock returned
        # the eligibility plan for ANY message, so typing "hello" produced a
        # placement verdict — the system answering a question nobody asked.
        _bare = _goal.strip(" .!?,")
        if _bare in {"thanks", "thank you", "ty", "cheers", "ok", "okay", "cool", "nice", "great"}:
            return {
                "goal": _goal_line.strip(), "reasoning": "Acknowledgement — nothing to look up.",
                "steps": [],
                "reply": "Anytime. Shout if you need anything else.",
            }
        if _bare in {
            "hi", "hey", "hello", "yo", "hiya", "hi there", "hello there",
            "good morning", "good afternoon", "good evening",
        }:
            return {
                "goal": _goal_line.strip(), "reasoning": "Greeting — nothing to look up.",
                "steps": [],
                "reply": "Hey — I'm Sūtra, your campus assistant. I can check your "
                         "attendance and timetable, work out placement eligibility, "
                         "find and register you for events, and answer questions "
                         "straight from the regulations. What do you need?",
            }
        if any(p in _bare for p in ("what can you do", "who are you", "what are you", "help me with")):
            return {
                "goal": _goal_line.strip(), "reasoning": "Capability question — nothing to look up.",
                "steps": [],
                "reply": "I work with five specialist agents over your real campus "
                         "records. I can check attendance and timetable clashes, "
                         "test placement eligibility against a company's criteria, "
                         "register you for events, quote the regulations with the "
                         "clause number, and set calendar entries and reminders. "
                         "Anything that writes stops for your approval first.",
            }

        if "register" not in _goal_line.lower():
            return {
                "goal": "Answer the eligibility and policy question without taking any action.",
                "reasoning": "Both lookups are reads with no dependency on each other, so they "
                             "run in parallel. Nothing here writes, so nothing needs approval.",
                "steps": [
                    {"id": "s1", "agent": "placement", "task": "Check Google internship eligibility.",
                     "depends_on": [], "expected_output": "Eligibility verdict.", "requires_approval": False},
                    {"id": "s2", "agent": "knowledge",
                     "task": "What is the minimum attendance percentage required to sit for exams?",
                     "depends_on": [], "expected_output": "The rule, with the clause it comes from.",
                     "requires_approval": False},
                ],
            }

        return {
            "goal": "Check Google internship eligibility and register for the placement workshop.",
            "reasoning": "Eligibility and workshop lookup are independent, so they run in parallel. "
                         "Registration depends on both, and the calendar entry depends on the "
                         "registration succeeding.",
            "steps": [
                {"id": "s1", "agent": "placement", "task": "Check Google internship eligibility.",
                 "depends_on": [], "expected_output": "Eligibility verdict.", "requires_approval": False},
                {"id": "s2", "agent": "events", "task": "Get placement workshop details.",
                 "depends_on": [], "expected_output": "Workshop schedule.", "requires_approval": False},
                {"id": "s3", "agent": "events", "task": "Register for the placement workshop.",
                 "depends_on": ["s1", "s2"], "expected_output": "Registration confirmation.", "requires_approval": True},
                {"id": "s4", "agent": "services", "task": "Add the workshop to the calendar.",
                 "depends_on": ["s3"], "expected_output": "Calendar entry confirmation.", "requires_approval": True},
                {"id": "s5", "agent": "services", "task": "Set a reminder an hour before the workshop.",
                 "depends_on": ["s4"], "expected_output": "Reminder confirmation.", "requires_approval": True},
            ],
        }

    if "extract durable facts" in system:
        return {"facts": [
            {"key": "preference.schedule", "value": "morning sessions", "confidence": 0.9},
            {"key": "interest.domain", "value": "machine learning", "confidence": 0.85},
        ]}

    if "Summarize this exchange" in system:
        return {"summary": "The student asked about Google internship eligibility and workshop "
                            "registration. They were confirmed eligible and registered."}

    if "conflict arbiter" in system:
        # Conflict injection is OPT-IN via MOCK_CONFLICT=1. Defaulting to
        # "always conflict" made every mock run take the worst-case path
        # (2 replans, ~3x the LLM calls), which hid the real happy-path cost.
        if os.environ.get("MOCK_CONFLICT"):
            return {
                "conflicts": [{
                    "type": "SCHEDULE_COLLISION",
                    "step_id": "s3",
                    "detail": "Step s3 registers for the Placement Prep Workshop on Thursday "
                              "14:00-16:00, which overlaps the DBMS Lab (CS301L) on the student's "
                              "timetable. Attendance in that lab is 70.3% (26/37), already below "
                              "the 75% bar in Academic Regulations R22 clause 4.2.",
                }],
                "rationale": "The Academic Agent holds veto power over actions that endanger exam "
                             "eligibility, so the Thursday registration is blocked. The Saturday "
                             "batch (2026-08-15, 10:00-12:00) does not collide and has 2 seats "
                             "remaining, so the plan should route there instead.",
            }
        return {"conflicts": [], "rationale": ""}

    if "critic for a multi-agent" in system:
        return {"satisfied": True, "feedback": ""}

    if "final answer for a multi-agent" in system:
        # This used to return one fixed, flattering paragraph. It claimed "I
        # booked the Saturday batch instead" on runs where the human had
        # REJECTED the registration, and cited [doc:0] on runs that retrieved
        # nothing — so the recorded fixtures, the demo's fallback, contained
        # statements contradicted by their own event streams.
        #
        # The mock now reads the payload it was handed and says only what is
        # in it. It is still a mock (no fluent prose), but it cannot lie about
        # what happened, which is the property the fixtures need.
        return _mock_final_answer(user_content)

    if "at most one tool" in system:
        if "Academic Agent" in system:
            return {"tool": "get_attendance", "args": {"student_id": ANANYA_ID},
                    "reasoning": "Attendance determines whether missing a lab is affordable."}
        if "Placement Agent" in system:
            return {"tool": "check_placement_eligibility", "args": {"student_id": ANANYA_ID, "company_id": "google"},
                    "reasoning": "Eligibility is a deterministic rules check against the company's criteria."}
        if "Events Agent" in system:
            # Choose the batch from this step's task, not from the whole
            # prompt. The upstream search result lists both Thursday and
            # Saturday; matching the whole prompt therefore always selected
            # Saturday and silently bypassed the real conflict preflight.
            _task = user_content.split("Task:", 1)[-1].lower()
            if "register" in _task:
                saturday = "saturday" in _task
                return {
                    "tool": "register_event",
                    "args": {"student_id": ANANYA_ID,
                              "event_id": "evt_workshop_sat" if saturday else "evt_workshop_thu"},
                    "reasoning": ("Routing to the Saturday batch, which does not clash with the lab."
                                   if saturday else
                                   "Registration is a write action, so it will pause for approval."),
                }
            return {"tool": "search_events", "args": {"query": "placement workshop"},
                    "reasoning": "Need the workshop's schedule and remaining seats."}
        if "Knowledge Agent" in system:
            return {"tool": "search_policy", "args": {"query": user_content[:50]},
                    "reasoning": "Answer must be grounded in the institutional regulations."}
        if "Services & Comms Agent" in system:
            # Branch on the step's own task line ONLY.
            #
            # Matching "remind" anywhere in the prompt caught the upstream
            # results and the original goal too ("remind me an hour before"),
            # so BOTH services steps took the reminder branch: the reminder was
            # created twice and the calendar entry never at all — the mirror of
            # the bug this branch was added to fix.
            _task = user_content.split("Task:", 1)[-1].lower()
            if "remind" in _task:
                return {"tool": "create_reminder",
                        "args": {"student_id": ANANYA_ID,
                                  "message": "Placement Prep Workshop starts in an hour.",
                                  "remind_at": "2026-08-15 09:00"},
                        "reasoning": "The student asked to be reminded an hour before."}
            saturday = "saturday" in _task
            return {"tool": "add_to_calendar",
                    "args": {"student_id": ANANYA_ID, "title": "Placement Prep Workshop",
                              "date": "2026-08-15" if saturday else "2026-08-13",
                              "start_time": "10:00" if saturday else "14:00",
                              "end_time": "12:00" if saturday else "16:00"},
                    "reasoning": "Calendar entry follows a confirmed registration."}
        return {"tool": "", "args": {},
                "reasoning": "This step can be answered from context without a tool."}

    # Fallback: agent step's compose call (QUERY_JSON_INSTRUCTIONS shape).
    # NOTE: deliberately free of the word "mock" — these strings are rendered
    # verbatim in the UI, and recorded fixtures are shown on a projector.

    # The knowledge agent's compose call is handed a "Retrieved context:" block
    # of real clauses. Quoting the first one (and marking it [doc:0]) is what
    # makes the recorded clean fixture demonstrate grounding rather than assert
    # it — the marker resolves to a clause the run genuinely retrieved.
    if "Retrieved context:" in user_content and "[doc:0]" in user_content:
        quoted = user_content.split("[doc:0]", 1)[1].split("[doc:1]", 1)[0]
        quoted = quoted.split(")", 1)[-1].strip().split("\n")[0][:240].strip()
        grounded = f"{quoted} [doc:0]"
        if json_mode:
            return {"output": grounded,
                    "reasoning": "Quoted directly from the retrieved regulation.", "data": {}}
        return grounded

    if json_mode:
        return {"output": "Step completed using campus records.",
                "reasoning": "Derived from the tool result above.", "data": {}}
    return "Step completed using campus records."


# Instrumentation: how many LLM round-trips a run actually costs. Read by the
# latency tests to prove the call-count reductions, and cheap enough to leave on.
CALL_COUNT = {"total": 0}

# Provider clients are cached per process. Constructing one per call meant a
# fresh TLS handshake every time — measured at ~5.5s of fixed overhead per
# request against Groq from this machine, dwarfing actual inference time.
_clients: dict[str, object] = {}


def _cached(key: str, factory):
    if key not in _clients:
        _clients[key] = factory()
    return _clients[key]


def reset_call_count() -> None:
    CALL_COUNT["total"] = 0


def call_llm(system, messages, json_mode=False):
    """Call the LLM with automatic provider fallback.

    system: system prompt string
    messages: list of {"role": "user"|"assistant", "content": str}
    json_mode: if True, ask for and parse a JSON response

    Returns a str (or dict if json_mode=True). Raises RuntimeError if every
    configured provider fails.
    """
    CALL_COUNT["total"] += 1

    if os.environ.get("MOCK_LLM"):
        return _mock_llm(system, messages, json_mode)

    providers = []
    if os.environ.get("GROQ_API_KEY"):
        providers.append(("groq", _call_groq))
    # Local Ollama outranks Gemini deliberately. Measured per-call latency from
    # this machine: local Ollama ~13-60s, Gemini ~180s. When Groq rate-limits
    # mid-demo, the local model is the faster recovery, and it needs no quota.
    if not os.environ.get("OLLAMA_DISABLE"):
        providers.append(("ollama", _call_ollama))
    if os.environ.get("GEMINI_API_KEY"):
        providers.append(("gemini", _call_gemini))
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(("anthropic", _call_anthropic))
    if os.environ.get("OPENAI_API_KEY"):
        providers.append(("openai", _call_openai))

    if not providers:
        raise RuntimeError(
            "No LLM provider available (GEMINI_API_KEY / GROQ_API_KEY / "
            "ANTHROPIC_API_KEY / OPENAI_API_KEY all unset, and OLLAMA_DISABLE is set)."
        )

    errors = []
    for name, fn in providers:
        try:
            return fn(system, messages, json_mode)
        except Exception as e:
            errors.append(f"{name}: {e}")

    raise RuntimeError("All LLM providers failed:\n" + "\n".join(errors))


async def call_llm_async(system, messages, json_mode=False):
    """Run one complete provider chain without allowing it to strand a graph.

    asyncio cannot forcibly stop a synchronous SDK call already running in a
    worker thread, but the provider-level HTTP timeouts above ensure that
    worker exits shortly afterward. The graph itself is released immediately,
    records the step as degraded/error, and continues to run.finished.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(call_llm, system, messages, json_mode),
            timeout=LLM_CALL_TIMEOUT_S,
        )
    except TimeoutError as error:
        raise TimeoutError(
            f"LLM call exceeded the {LLM_CALL_TIMEOUT_S:g}s execution deadline"
        ) from error
