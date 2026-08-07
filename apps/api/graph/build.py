"""
Wires the LangGraph StateGraph: planner -> parallel specialist dispatch ->
conflict_check -> critic -> approval_gate -> synthesize, with a bounded
replan loop back to planner from either conflict_check or critic.

Checkpointer is SqliteSaver (data/checkpoints.db) rather than an in-memory
saver: HITL approval resume and the cross-session memory test (Section 6.3 /
P11 of the battle plan) both require state to survive a process restart, not
just live in RAM for the current run.

apps/api/main.py (not yet built) uses graph_session() as an async context
manager for the app's lifespan, since AsyncSqliteSaver itself is a context
manager. Scripts/tests that just need one run can use it directly too.
"""
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from apps.api.graph.agents import make_agent_node
from apps.api.graph.nodes import (
    approval_gate_node,
    conflict_check_node,
    critic_node,
    dispatch_node,
    intake_node,
    memory_write_node,
    planner_node,
    route_after_approval,
    route_after_conflict,
    route_after_critic,
    route_after_planner,
    route_ready_steps,
    synthesize_node,
)
from apps.api.graph.state import GraphState
from packages.contracts.plan import AGENT_NAMES

CHECKPOINT_DB = str(Path(__file__).resolve().parents[3] / "data" / "checkpoints.db")


def build_graph(checkpointer):
    g = StateGraph(GraphState)

    g.add_node("intake", intake_node)
    g.add_node("planner", planner_node)
    g.add_node("dispatch", dispatch_node)
    for name in AGENT_NAMES:
        g.add_node(f"agent_{name}", make_agent_node(name))
    g.add_node("conflict_check", conflict_check_node)
    g.add_node("critic", critic_node)
    g.add_node("approval_gate", approval_gate_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("memory_write", memory_write_node)

    g.add_edge(START, "intake")
    g.add_edge("intake", "planner")
    # A greeting or a "what can you do" needs no agents at all, so it skips
    # straight to the answer rather than burning a full orchestration cycle
    # (and producing a verdict the student never asked for).
    g.add_conditional_edges("planner", route_after_planner, ["dispatch", "synthesize"])
    for name in AGENT_NAMES:
        g.add_edge(f"agent_{name}", "dispatch")

    dispatch_targets = [f"agent_{name}" for name in AGENT_NAMES] + ["conflict_check"]
    g.add_conditional_edges("dispatch", route_ready_steps, dispatch_targets)

    g.add_conditional_edges("conflict_check", route_after_conflict, ["planner", "critic"])
    g.add_conditional_edges("critic", route_after_critic, ["planner", "approval_gate"])
    # Back to dispatch after an approval: steps that depend on a gated write are
    # deliberately NOT run beforehand, so they only become runnable once the
    # human has approved and the write actually succeeded.
    g.add_conditional_edges("approval_gate", route_after_approval, ["dispatch", "synthesize"])
    g.add_edge("synthesize", "memory_write")
    g.add_edge("memory_write", END)

    return g.compile(checkpointer=checkpointer)


@asynccontextmanager
async def graph_session(db_path: str = CHECKPOINT_DB):
    """Yield a compiled graph backed by a SqliteSaver open for the block's
    duration. Use this instead of a module-level singleton since
    AsyncSqliteSaver must be entered/exited as a context manager.

    Explicitly allow-lists our own contract types for msgpack (de)serialization —
    without this, checkpointing packages.contracts.plan.Plan/Step falls back to
    an unregistered-type path LangGraph warns will be blocked in a future
    version, which would silently break approval-resume and cross-session
    memory the day it happens.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    serde = JsonPlusSerializer(allowed_msgpack_modules=[("packages.contracts.plan", "Plan"), ("packages.contracts.plan", "Step")])
    async with aiosqlite.connect(db_path) as conn:
        checkpointer = AsyncSqliteSaver(conn, serde=serde)
        yield build_graph(checkpointer)
