# Sūtra — Backend Contract

Everything the frontend receives from the backend, and how to consume it.

Written against the real running system; every payload below was extracted from
recorded runs in `fixtures/`, not from memory.

---

## 1. The shape of the system

One HTTP request starts a **run**. The run executes a graph of agents, and the
entire execution is streamed to you as a sequence of **events**. You do not poll
for state — you fold the event stream into state yourself.

```
POST /chat  ──►  { run_id, thread_id }
                      │
GET /stream/{run_id} ─┴──►  event, event, event, …  (Server-Sent Events)
```

The graph inside:

```
intake ──► planner ──► dispatch ──► [ 5 specialist agents, IN PARALLEL ] ──┐
             ▲                                                             │
             │                                                             ▼
             └── (replan, max 2) ◄── conflict_check ◄────────────── (join) ─┘
                                            │
                                            ▼
                                         critic ──► approval_gate ──► synthesize ──► memory_write
```

- **intake** — loads what we already know about the student (memory)
- **planner** — turns the question into a DAG of steps (`depends_on` edges)
- **dispatch** — runs every step whose dependencies are met, *concurrently*
- **5 specialists** — `academic`, `placement`, `events`, `knowledge`, `services`
- **conflict_check** — deterministic rules + an LLM arbiter; can send the plan back
- **critic** — did this actually answer the question?
- **approval_gate** — **pauses the whole run** for human approval of writes
- **synthesize** — the final answer
- **memory_write** — durable facts, *after* the answer

---

## 2. HTTP endpoints

Base URL `http://localhost:8000`. CORS allows **only** `http://localhost:5173`,
so the dev server must run on that exact port.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/chat` | `{message, student_id, role?, thread_id?}` | `{run_id, thread_id}` |
| GET | `/stream/{run_id}` | — | SSE stream (below) |
| POST | `/approve` | `{run_id, thread_id?, approval_id, decision, edited_args?}` | `{status:"resuming"}` |
| POST | `/admin/chaos` | `{service, mode}` | `{service, mode, state}` |
| GET | `/admin/chaos/status` | — | `{state, modes}` |
| POST | `/admin/chaos/reset` | — | `{state:{}}` |
| GET | `/health` | — | `{status:"ok"}` |

**`run_id` vs `thread_id`** — these are different and must not be aliased:
`run_id` scopes ONE execution and its event stream; `thread_id` scopes the
CONVERSATION (checkpoint + memory). Every `/chat` mints a fresh `run_id`; send
the returned `thread_id` back on the next turn to keep context, and pass it to
`/approve` so the right checkpoint resumes.

`role` is `student` \| `faculty` \| `admin` (default `student`).
`decision` is `approve` \| `reject` \| `edit`.
Chaos `mode` is `healthy` \| `slow` \| `error_500` \| `timeout` \| `flaky` \| `empty_response`;
`service` is one of `erp`, `placement`, `events`, `rag`, `campus`, `library`, `comms`, `calendar`.

**There is no endpoint that returns run state, history, or the final answer.**
The SSE stream is the only source. If you need the answer, read `run.finished`.

---

## 3. Reading the stream — read this before writing any client

### Do NOT use `EventSource`

Two independent reasons, both verified:

1. **Every frame is named** (`event: node.started`). `EventSource.onmessage`
   only fires for *unnamed* frames, so a naive client receives **nothing**.
2. The stream ends by **socket close**. `EventSource` auto-reconnects, and the
   server **replays the full history** to every new subscriber → infinite loop.

Use `fetch()` + `ReadableStream`. Reference implementation:
`apps/web/src/transport/sseClient.ts`.

### Wire format

```
event: node.started\n
data: {"id":"1d11e24a","run_id":"...","type":"node.started",...}\n
\n
```

Frames are separated by a blank line. Parse the `data:` line as JSON and ignore
`event:` — the JSON already carries `type`. Buffer across chunk boundaries: a
frame **will** get split mid-JSON.

### History replay is a feature

Connecting late is safe — the server replays everything already emitted for that
`run_id`, then continues live. **Deduplicate on `event.id`** and a reconnect
becomes lossless instead of a double-count.

---

## 4. The event envelope

Every event has all nine keys, always. Nulls are present, not omitted.

```jsonc
{
  "id":        "1d11e24a",   // unique per event — use for dedupe
  "run_id":    "rec-5eb79856",
  "ts":        1786110035.56, // epoch SECONDS (float), not milliseconds
  "type":      "node.started",
  "node_id":   "s1",          // the plan step, or null for orchestration events
  "agent":     "placement",   // who emitted it, or null
  "payload":   { },           // type-specific — section 5
  "latency_ms": null,         // ONLY non-null on node.finished
  "parent_id": null           // always null; ignore it
}
```

---

## 5. Every event type

19 types are actually emitted. `run.started`, `a2a.message`, `token.usage` and
`proactive.alert` exist in the enum but **are never sent** — don't build UI for them.

### Planning

**`plan.created`** · `agent: "planner"` — the plan. This is what you draw.
```jsonc
{
  "goal": "Check Google internship eligibility and register for the placement workshop.",
  "reasoning": "Eligibility and workshop lookup are independent, so they run in parallel…",
  "steps": [
    { "id": "s1", "agent": "placement", "task": "Check Google internship eligibility.",
      "depends_on": [], "expected_output": "Eligibility verdict.", "requires_approval": false },
    { "id": "s3", "agent": "events", "task": "Register for the placement workshop.",
      "depends_on": ["s1","s2"], "expected_output": "…", "requires_approval": true }
  ]
}
```
Steps with `depends_on: []` run **at the same time**. That is where parallelism
comes from — group steps by dependency depth to lay them out in columns.

**`plan.revised`** — ⚠️ **two completely different payloads share this type**:
- `agent: "planner"` → a full plan, same shape as `plan.created` (the plan changed)
- `agent: "critic"` → `{satisfied: false, feedback: "..."}` (the critic objected)

Branch on `Array.isArray(payload.steps)`, not on the type alone.

### Step execution

| Type | `node_id` | Payload |
|---|---|---|
| `node.started` | step id | `{task}` |
| `agent.thinking` | step id | `{}` — from a specialist. From `agent:"critic"` it's `{detail, skipped?}` instead |
| `node.finished` | step id | `{status}` + **`latency_ms` is set here** |
| `node.failed` | step id | `{error}` |

`status` ∈ `ok` · `error` · `degraded` · `rejected` · `permission_denied` ·
`pending_approval` · `cancelled`

`cancelled` means the step never ran because something it depended on was
rejected or not permitted.

**Approval gating:** a step sitting at `pending_approval` does **not** satisfy
its dependents. Steps that depend on a gated write stay undispatched until the
human approves AND the write succeeds — so you will never see a calendar entry
created for a registration that was refused.

`node.failed` is **always followed by** `node.finished` with `status:"error"` —
don't count the failure twice.

### Tools

```jsonc
// tool.called
{ "tool": "check_placement_eligibility",
  "args": { "student_id": "1602-23-733-042", "company_id": "google" } }

// tool.result   ← carries the structured result in `data`
{ "tool": "check_placement_eligibility", "status": "ok",
  "data": { "is_eligible": true,
            "breakdown": [ {"criterion":"CGPA","required":">= 8.0","actual":"8.4","passed":true} ] } }
{ "tool": "…", "status": "degraded", "degradation_reason": "Placement service unavailable…" }
{ "tool": "…", "status": "error", "error": "No seats remaining for Placement Prep Workshop" }
{ "tool": "…", "status": "permission_denied", "required_role": "faculty", "held_role": "student" }
{ "tool": "…", "status": "pending_approval", "approval_id": "9d805fbc" }   // queued, NOT done
{ "tool": "…", "status": "ok", "approval_id": "9d805fbc",
  "data": { "receipt_id": "…", "status": "registered" } }                   // the real post-approval result

// tool.retry
{ "tool": "check_placement_eligibility", "attempt": 1, "error": "placement: injected HTTP 500" }

// tool.fallback
{ "tool": "…", "from": "live", "to": "fallback", "reason": "placement: injected HTTP 500" }
```

⚠️ **`tool.retry` / `tool.fallback` can carry the wrong `node_id`.** They're
buffered in a process-global list and drained per node, so with steps running in
parallel one step's retry can be attributed to another. **Fix client-side**:
remember which `node_id` issued each tool name on `tool.called`, and route
retry/fallback by tool name instead of by `node_id`.

### RAG / citations

**`rag.retrieved`** · `agent: "knowledge"` — real clause-level citations.
```jsonc
{
  "chunks": 4,
  "query": "What is the minimum attendance required…",
  "abstained": false,          // true ⇒ nothing relevant; the agent refuses to guess
  "citations": [
    { "text": "A candidate shall be required to put in a minimum of seventy-five percent (75%)…",
      "doc_title": "Academic Regulations R22", "doc_number": "VCE/ACAD/R22/2022",
      "clause": "4.2", "page": 1, "score": 0.707 }
  ]
}
```
**This is the only place real citation objects appear.** Index order matches the
`[doc:N]` markers in the final answer, so `citations[0]` is `[doc:0]`.

### Conflict / arbitration

Before any gated registration is offered for approval, the backend runs a
**deterministic preflight** against the real timetable. Two events announce that
work, and both arrive *before* `conflict.detected`:

**`schedule.checked`** · `agent: "academic"`, `node_id` = the step
```jsonc
{ "event_id": "evt_workshop_thu", "event_title": "Placement Prep Workshop",
  "day": "Thursday", "start": "14:00", "end": "16:00",
  "has_conflict": true, "conflicting_course_id": "CS301L",
  "detail": "DBMS Lab (lab) on Thursday 14:00-16:00 overlaps the requested window." }
```

**`attendance.impact.calculated`** · `agent: "academic"` — only when a clash was found
```jsonc
{ "course_id": "CS301L", "course_name": "DBMS Lab",
  "current_pct": 70.27, "projected_pct": 68.42, "delta_pct": -1.85,
  "classes_attended": 26, "classes_held": 37, "sessions_missed": 1,
  "crosses_threshold": false, "already_below": true,
  "sessions_needed_to_recover": 10 }
```

**`conflict.detected`** · `agent: "conflict_arbiter"`
```jsonc
{
  "conflicts": [
    { "type": "SCHEDULE_COLLISION", "step_id": "s2", "step_ids": ["s2"],
      "detail": "Step s2 registers for 'Placement Prep Workshop' on Thursday 14:00-16:00, which overlaps DBMS Lab (lab)… Attendance in DBMS Lab is 70.27% (26/37) and would fall to 68.42% — already below the 75% bar in Academic Regulations R22 clause 4.2.",
      "evidence": {
        "event":            { "id": "evt_workshop_thu", "title": "…", "day": "Thursday", "start": "14:00", "end": "16:00", "seats_remaining": 2 },
        "collides_with":    { "course_id": "CS301L", "session_type": "lab", "detail": "…" },
        "attendance_impact": { /* the payload shown above, verbatim */ }
      } }
  ],
  "rationale": "'Placement Prep Workshop' is scheduled Thursday 14:00-16:00, which is when CS301L meets. Attending it means missing that session, taking DBMS Lab from 70.27% to 68.42% (-1.85). That is already under the 75% required by R22 clause 4.2, so a non-clashing slot should be used instead."
}
```
`evidence` is present **only on deterministic conflicts** — it is the preflight's
working, so every number in `rationale` can be checked against it. That is what
an evidence card should render. When the preflight finds a conflict the LLM
arbiter is skipped entirely, so `rationale` is generated from the evidence and
is byte-identical across runs.

⚠️ `conflicts[]` may still contain LLM-generated entries (when no deterministic
conflict was found) which are **not validated** — those carry no `evidence` and
may be plain strings rather than objects. Coerce defensively.
⚠️ `step_id` refers to the plan **as it was when the conflict was raised**. Step
ids are reused across revisions for different tasks, so tie each conflict to the
plan version it came from or you'll highlight the wrong node.

**`conflict.resolved`** — payload `{}`, and it fires on **every clean pass**.
It means *"checked, found nothing"*, **not** *"a conflict was fixed"*.

### Human approval

**`approval.requested`** · `agent: "approval_gate"` — the run is now **blocked**.
```jsonc
{
  "id": "9d805fbc",           // ← the approval id you send back
  "step_id": "s2",
  "agent": "events",          // the specialist (note: event.agent is "approval_gate")
  "tool": "register_event",
  "args": { "student_id": "1602-23-733-042", "event_id": "evt_workshop_thu" },
  "description": "Register 1602-23-733-042 for 'Placement Prep Workshop' (2026-08-13 14:00-16:00).",
  "risk": "low",              // "low" | "medium"
  "reversible": true,
  "preview": "Placement Prep Workshop — 2026-08-13 14:00-16:00, 2 seat(s) left."
}
```
⚠️ **The same approval is emitted more than once** (observed 5 emissions for 3
approvals): every resume re-runs the gate and re-announces anything still
pending. Each has a *new* `event.id` but the *same* `payload.id`.
**Dedupe on `payload.id`**, and never re-open one you've already resolved.

Only three tools ever gate: `register_event`, `file_grievance`, `send_email`.

**`approval.resolved`** → `{id, step_id, decision, outcome}`
- `decision` — `approve` \| `reject` \| `edit`
- `outcome` — `executed` \| `not_executed` \| `failed` \| `skipped`

`decision` is what the human chose; `outcome` is what happened to the action.
They are separate on purpose — an approved action can still end `failed`.

An auto-resolved rejection carries two extra fields and **no preceding
`approval.requested`**: `{"auto": true, "reason": "identical action already
declined in this run"}`. This fires when a replan re-proposes something the
human already refused; the gate carries the earlier "no" forward instead of
asking again. Render it as a rejection, not as a pending approval.

Only `outcome: "executed"` means the action actually happened. On approval a
second `tool.result` follows for that step carrying the real `data` and a
`receipt_id`; treat *that* as the completion, not the earlier
`pending_approval` one.

Note: if the plan is revised while an approval is queued, that approval is
discarded server-side and simply never reaches you — you will not see a
`requested` without an eventual `resolved` for the plan that is actually running.

**Replying:**
```js
POST /approve { run_id, approval_id, decision: "approve" | "reject" | "edit", edited_args }
```
- `edit` re-validates `edited_args` server-side. **Only existing keys are
  allowed** — unknown fields are rejected loudly, and the tool's own checks
  (seat availability, etc.) re-run, so an edit can't sneak past the gate.
- Keep **one approval in flight at a time**.

### Memory

**`memory.recall`** · `agent: "intake"` — fires at the start, only if anything is known.
```jsonc
{
  "profile_facts": [ { "key":"student.year", "value":"third-year",
                       "confidence":1.0, "evidence_turn":"ac974322818b", "updated_at":1786098871.1 } ],
  "recalled":     [ { "id":"…", "summary":"…", "score":0.73, "thread_id":"session-A", "ts":… } ],
  "source": "profile+semantic"
}
```

**`memory.write`** · `agent: "memory"`
```jsonc
{ "facts_written": [ {"key":"preference.schedule","value":"morning sessions",
                      "confidence":0.9,"stored":true} ],
  "summary": "The student asked about Google internship eligibility…" }
```
⚠️ **This arrives AFTER `run.finished`** — deliberately off the critical path so
the answer shows first. `run.finished` is *not* the last event.

### Finish

**`run.finished`** · `agent: "synthesizer"`
```jsonc
{
  "answer": "You are eligible for the Google SDE internship…\n\nActions taken:\n- NOT DONE — Register … : you declined this, so nothing was written.",
  "citations": [ { "text": "…", "doc_title": "Academic Regulations R22",
                   "doc_number": "…", "clause": "4.2", "page": 1, "score": 0.41 } ],
  "actions": [
    { "approval_id": "9d805fbc", "step_id": "s2", "agent": "events",
      "tool": "register_event", "args": { "…": "…" },
      "description": "Register … for 'Placement Prep Workshop (Saturday Batch)'…",
      "decision": "reject", "outcome": "not_executed",
      "receipt_id": null, "error": null }
  ]
}
```

`citations` is now the list of **citation objects actually retrieved this run**,
in the same order as the `[doc:N]` markers — so `citations[0]` is `[doc:0]`.
(It used to be marker strings echoed by the model; it is now a record of what
was retrieved, and markers the list cannot resolve are stripped from `answer`
before it is sent. If you see `[doc:N]` in an answer, `citations[N]` exists.)

`actions` is the **authoritative ledger of every gated action** — one row per
approval the gate resolved, plus `outcome: "cancelled"` rows for steps dropped
because a dependency was refused. Build any "what did it actually do" UI from
this, never from the prose: the prose is model-written, this is not.
`outcome` ∈ `executed` \| `not_executed` \| `cancelled` \| `failed` \| `skipped`;
only `executed` carries a `receipt_id`.

The answer itself ends with a rendered `\n\nActions taken:\n- …` block (and,
where relevant, a `\n\nNot completed:\n- …` block). Both are generated
deterministically from the ledger and step results, not by the model. Split
them out and render them distinctly — they are deliberately honest and should
not be buried.

**`run.error`** — ⚠️ **usually NOT fatal.**
- Non-fatal (5 sources): has **both** `agent` and `payload.detail` — a degradation
  notice, e.g. `{error, detail:"critic LLM call failed; treating plan as satisfied"}`.
  Render as a warning; the run continues.
- Fatal (1 source): `agent` is `null` **and** there is no `detail`. Only this one
  ends the run.

---

## 6. Typical order

```
memory.recall           ← what we know about this student
plan.created            ← draw the DAG now
  node.started  s1  ┐
  node.started  s2  ├─ same instant = running in parallel
  agent.thinking    │
  tool.called       │
  tool.result       │
  node.finished s2  ┘
  node.started  s3      ← depended on s1+s2
  …
schedule.checked        ← real timetable check on the queued registration
attendance.impact.calculated
conflict.detected       ← the clash, with evidence
plan.revised            ← new plan; steps re-execute
  …
conflict.resolved       ← later pass came back clean
approval.requested      ← RUN IS BLOCKED until you POST /approve
approval.resolved
run.finished            ← the answer + the action ledger
memory.write            ← AFTER the answer
(socket closes)
```

Note the ordering: the human is asked to approve **only after** the conflict has
been found and the plan revised to a safe alternative. There is never an
`approval.requested` for the clashing Thursday slot.

---

## 7. Gotchas, collected

1. `EventSource` will not work. Use `fetch` + `ReadableStream`.
2. Dedupe events by `event.id` — history is replayed to new subscribers.
3. Dedupe approvals by `payload.id` — they're re-emitted on every resume.
4. `plan.revised` has two incompatible payloads; branch on `payload.steps`.
5. `run.finished` is not the last event; `memory.write` follows.
6. `run.error` is usually a warning, not a failure.
7. `conflict.resolved` means "none found", not "fixed".
8. `conflicts[]` may contain raw strings.
9. Conflict `step_id` belongs to the plan version it was raised against.
10. `tool.retry` / `tool.fallback` may carry the wrong `node_id`; route by tool name.
11. `ts` is seconds, not milliseconds. `latency_ms` is milliseconds and only on `node.finished`.
12. `tool.result` for a gated tool fires TWICE: once as `pending_approval` (no receipt), then again after approval with the real `data`.
13. `run.finished.citations` are now real citation objects (was: marker strings). Every surviving `[doc:N]` resolves into it.
14. `run_id` is per-turn, `thread_id` is per-conversation. Send `thread_id` back to continue a conversation.
15. Registration is idempotent — re-registering returns `status:"already_registered"` and consumes no second seat.
16. Build "what did it do" from `run.finished.actions`, never from the answer prose.
17. `conflict.detected` entries carry `evidence` when the conflict was found deterministically — that is the evidence card's data.
18. An `approval.resolved` with `auto: true` has no matching `approval.requested`; it is a rejection carried forward, not a missed event.

---

## 8. Testing without the backend

Four recorded runs are committed in `fixtures/` (and copied to
`apps/web/public/fixtures/`). They're plain JSONL — one event per line, byte
identical to what the SSE stream sends — so you can build the entire UI with no
backend and no LLM quota.

| File | Shows |
|---|---|
| `golden_clean.jsonl` | happy path, parallel dispatch, approvals |
| `golden_conflict.jsonl` | Academic Agent veto → replan → Saturday batch |
| `golden_chaos.jsonl` | retry ×2 → fallback → degraded, still answers |
| `golden_reject.jsonl` | human rejects; system carries on |

Regenerate any time, free and deterministic:
```bash
python scripts/record_fixtures.py
```

Run the backend for real:
```bash
uvicorn apps.api.main:app --port 8000
```
Add `MOCK_LLM=1` to run the graph with zero network calls and zero quota.
