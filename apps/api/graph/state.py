"""
GraphState — the shared state LangGraph threads through every node.

Several keys are written concurrently by parallel specialist-agent branches
in the same superstep (step_results, conflicts, pending_approvals), so they
use reducers instead of plain overwrite — otherwise LangGraph raises
InvalidUpdateError the first time two branches finish in the same tick.
"""
import operator
from typing import Annotated, Any, Optional, TypedDict

from packages.contracts.plan import Plan


# Merge/append reducers can only ever GROW. Returning an empty value from a
# node is therefore a silent no-op, not a clear. Both accumulating channels
# need an explicit reset sentinel so the planner can genuinely discard the
# previous plan's state when it issues a new one.
#
# This mattered more than it looks: without the step_results reset, a replan
# left every old step id in `done`, so route_ready_steps saw the whole plan as
# already complete and dispatched nothing — the replan loop regenerated plans
# but never re-executed them. Without the approvals reset, actions the arbiter
# had just vetoed stayed queued and still ran.
RESET = "__reset__"


def _merge_dicts(left: dict, right: dict) -> dict:
    if right.get(RESET):
        return {k: v for k, v in right.items() if k != RESET}
    merged = dict(left)
    merged.update(right)
    return merged


def _append_or_reset(left: list, right: list) -> list:
    if right and right[0] == RESET:
        return list(right[1:])
    return list(left) + list(right)


RESET_APPROVALS = RESET


class StepResult(TypedDict):
    step_id: str
    agent: str
    output: str
    reasoning: str
    data: dict[str, Any]
    # "ok" | "error" | "pending_approval" | "rejected" | "permission_denied" | "degraded"
    status: str


class GraphState(TypedDict, total=False):
    run_id: str
    student_id: str
    role: str
    goal: str
    # Compact profile+semantic memory block built by intake_node and injected
    # into planner/agent prompts (tier 2+3; tier 1 is the checkpointer itself).
    memory_block: str
    plan: Plan
    step_results: Annotated[dict[str, StepResult], _merge_dicts]
    # These three accumulate WITHIN a turn (parallel branches append to them)
    # but must start empty on every NEW turn. State is checkpointed per thread,
    # so with a plain append reducer a second question inherited the first's
    # conflicts, receipts and citations — the answer's "Actions taken" claimed
    # a registration from a previous turn, and [doc:N] markers indexed into a
    # citation list that had shifted underneath them. intake_node clears all
    # three with the RESET sentinel.
    conflicts: Annotated[list[dict], _append_or_reset]
    pending_approvals: Annotated[list[dict], _append_or_reset]
    approval_decisions: dict[str, str]           # approval id -> "approve" | "reject" | "edit"
    # Set when the planner decided the message needed no agent work at all
    # (a greeting, a thank-you, "what can you do"). Its presence routes the run
    # straight to the answer, skipping dispatch, arbitration and the critic.
    # Wall-clock start of this turn, stamped by intake. Read by the replan
    # gates so a run can spend its remaining budget answering rather than
    # re-planning — see TURN_BUDGET_S.
    started_ts: Optional[float]
    conversational_reply: Optional[str]
    critic_feedback: Optional[str]
    iteration: int
    # Increments on EVERY planner run. Step ids are reused across revisions for
    # different tasks (revised s2 may be a different action than original s2),
    # so an id alone cannot identify a stale approval — the version can.
    plan_version: int
    # Bounded separately from `iteration`: a rejection is a human decision, not
    # a defect to re-solve, so it gets its own (smaller) replan budget.
    rejection_replans: int
    # Append-only ledger of every gated action the approval gate actually
    # resolved: what was proposed, what the human decided, whether it ran, and
    # the receipt if it did. Deliberately NOT derived from step_results at
    # synthesis time — a replan resets step_results, so by the time the answer
    # is written the record of a rejected Thursday registration would be gone.
    # This is the only thing the final answer's "Actions taken" section is
    # allowed to be built from.
    action_log: Annotated[list[dict], _append_or_reset]
    final_answer: Optional[str]
    # The clauses actually retrieved this run ({text, doc_title, doc_number,
    # clause, page, score}), appended by whichever knowledge steps ran. This is
    # the authority for the answer's citation list — not whatever the
    # synthesizer LLM echoes back, which is a claim rather than a record.
    citations: Annotated[list[dict], _append_or_reset]
