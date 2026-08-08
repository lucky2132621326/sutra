import asyncio

import pytest

from apps.api import main
from apps.api.graph import nodes
from packages.contracts.events import EventType


@pytest.mark.asyncio
async def test_graph_leg_deadline_emits_terminal_error_and_closes_stream(monkeypatch):
    """Even several individually slow steps cannot strand the composer."""

    class NeverFinishes:
        async def ainvoke(self, *_args, **_kwargs):
            await asyncio.sleep(1)

    emitted = []
    closed = []

    async def capture_emit(event):
        emitted.append(event)

    async def capture_close(run_id):
        closed.append(run_id)

    monkeypatch.setattr(main, "GRAPH_LEG_TIMEOUT_S", 0.02)
    monkeypatch.setattr(main.bus, "emit", capture_emit)
    monkeypatch.setattr(main.bus, "close_run", capture_close)

    await main._drive_graph("deadline-run", NeverFinishes(), {}, {})

    assert closed == ["deadline-run"]
    assert len(emitted) == 1
    assert emitted[0].type == EventType.RUN_ERROR
    assert "timeout" in emitted[0].payload["error"].lower()


@pytest.mark.asyncio
async def test_planner_timeout_finishes_without_dispatching_a_fallback_write(monkeypatch):
    async def timed_out(*_args, **_kwargs):
        raise TimeoutError("provider deadline")

    emitted = []

    async def capture_emit(event):
        emitted.append(event)

    monkeypatch.setattr(nodes, "call_llm_async", timed_out)
    monkeypatch.setattr(nodes.bus, "emit", capture_emit)

    result = await nodes.planner_node({
        "run_id": "planner-deadline",
        "goal": "Register me and add it to my calendar",
        "student_id": "1602-23-733-042",
        "iteration": 0,
    })

    assert result["plan"].steps == []
    assert "Nothing was changed" in result["conversational_reply"]
    assert nodes.route_after_planner(result) == "synthesize"
    assert any(event.type == EventType.RUN_ERROR for event in emitted)
