"""
Smoke test for packages/contracts/events.py + apps/api/bus.py.
Run from the repo root: python smoke_test_bus.py
"""
import asyncio
import time
import uuid

from packages.contracts.events import AgentEvent, EventType
from apps.api.bus import bus, FIXTURES_DIR


async def main():
    run_id = "smoke-" + uuid.uuid4().hex[:8]

    async def subscriber():
        seen = []
        async for event in bus.subscribe(run_id):
            seen.append(event)
        return seen

    sub_task = asyncio.create_task(subscriber())
    await asyncio.sleep(0.05)  # let subscriber register before we emit

    events = [
        AgentEvent(id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.RUN_STARTED,
                   payload={"goal": "smoke test"}),
        AgentEvent(id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.PLAN_CREATED,
                   agent="planner", payload={"steps": 2}),
        AgentEvent(id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.NODE_STARTED,
                   node_id="n1", agent="Academic"),
        AgentEvent(id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.NODE_FINISHED,
                   node_id="n1", agent="Academic", latency_ms=123.4),
        AgentEvent(id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.RUN_FINISHED),
    ]
    for e in events:
        await bus.emit(e)
    await bus.close_run(run_id)

    seen = await sub_task
    print(f"=== subscriber received {len(seen)} events (expected {len(events)}) ===")
    for e in seen:
        print(f"  {e.type.value:<16} node={e.node_id} agent={e.agent}")
    assert len(seen) == len(events)
    assert [e.type for e in seen] == [e.type for e in events]

    print("\n=== replay from fixtures/{run_id}.jsonl ===")
    replayed = bus.replay(run_id)
    print(f"replayed {len(replayed)} events from {FIXTURES_DIR / (run_id + '.jsonl')}")
    assert len(replayed) == len(events)
    assert replayed[0].type == EventType.RUN_STARTED
    assert replayed[-1].type == EventType.RUN_FINISHED

    print("\nALL BUS SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
