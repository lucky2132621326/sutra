"""
EventBus tests, including the late-subscriber race: a short run can finish
before the client's GET /stream connects. Found by e2e_check.py, which
received zero events for a fast chaos run.
"""
import asyncio
import time
import uuid

import pytest

from apps.api.bus import EventBus
from packages.contracts.events import AgentEvent, EventType


def _event(run_id: str, type_: EventType) -> AgentEvent:
    return AgentEvent(id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=type_)


@pytest.mark.asyncio
async def test_late_subscriber_replays_completed_run():
    """The whole run happens BEFORE anyone subscribes. The subscriber must
    still receive every event and then terminate."""
    bus = EventBus()
    run_id = "late-" + uuid.uuid4().hex[:6]

    await bus.emit(_event(run_id, EventType.RUN_STARTED))
    await bus.emit(_event(run_id, EventType.PLAN_CREATED))
    await bus.emit(_event(run_id, EventType.RUN_FINISHED))
    await bus.close_run(run_id)

    seen = [e.type async for e in bus.subscribe(run_id)]
    assert seen == [EventType.RUN_STARTED, EventType.PLAN_CREATED, EventType.RUN_FINISHED]


@pytest.mark.asyncio
async def test_mid_run_subscriber_gets_backlog_then_live_events():
    """Subscribing partway through must yield the backlog AND the events that
    arrive afterwards, with no gap and no duplicates."""
    bus = EventBus()
    run_id = "mid-" + uuid.uuid4().hex[:6]

    await bus.emit(_event(run_id, EventType.RUN_STARTED))
    await bus.emit(_event(run_id, EventType.PLAN_CREATED))

    seen = []

    async def consume():
        async for e in bus.subscribe(run_id):
            seen.append(e.type)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    await bus.emit(_event(run_id, EventType.NODE_STARTED))
    await bus.emit(_event(run_id, EventType.RUN_FINISHED))
    await bus.close_run(run_id)
    await asyncio.wait_for(task, timeout=5)

    assert seen == [
        EventType.RUN_STARTED, EventType.PLAN_CREATED,
        EventType.NODE_STARTED, EventType.RUN_FINISHED,
    ]


@pytest.mark.asyncio
async def test_two_subscribers_both_get_everything():
    bus = EventBus()
    run_id = "multi-" + uuid.uuid4().hex[:6]
    await bus.emit(_event(run_id, EventType.RUN_STARTED))

    a, b = [], []

    async def consume(sink):
        async for e in bus.subscribe(run_id):
            sink.append(e.type)

    ta = asyncio.create_task(consume(a))
    tb = asyncio.create_task(consume(b))
    await asyncio.sleep(0.05)

    await bus.emit(_event(run_id, EventType.RUN_FINISHED))
    await bus.close_run(run_id)
    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=5)

    assert a == [EventType.RUN_STARTED, EventType.RUN_FINISHED]
    assert b == a


@pytest.mark.asyncio
async def test_fixture_and_stream_agree():
    bus = EventBus()
    run_id = "parity-" + uuid.uuid4().hex[:6]
    for t in (EventType.RUN_STARTED, EventType.PLAN_CREATED, EventType.RUN_FINISHED):
        await bus.emit(_event(run_id, t))
    await bus.close_run(run_id)

    streamed = [e.to_json() async for e in bus.subscribe(run_id)]
    replayed = [e.to_json() for e in bus.replay(run_id)]
    assert streamed == replayed
