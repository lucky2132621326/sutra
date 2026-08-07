"""
Diagnostic: run the graph with a hard timeout, live-print every bus event
with elapsed time, so we can see exactly where it stalls.
"""
import asyncio
import time
import uuid

from apps.api.bus import bus
from apps.api.graph.build import graph_session

START = time.time()


def log(msg):
    print(f"[{time.time() - START:6.2f}s] {msg}", flush=True)


async def watch(run_id):
    async for event in bus.subscribe(run_id):
        log(f"EVENT {event.type.value} node={event.node_id} agent={event.agent} latency_ms={event.latency_ms}")


async def main():
    run_id = "diag-" + uuid.uuid4().hex[:6]
    watch_task = asyncio.create_task(watch(run_id))

    log(f"opening graph_session, run_id={run_id}")
    async with graph_session() as graph:
        log("graph session open, invoking")
        config = {"configurable": {"thread_id": run_id}}
        try:
            result = await asyncio.wait_for(
                graph.ainvoke({"run_id": run_id, "goal": "What events are happening this week?", "iteration": 0}, config=config),
                timeout=90,
            )
            log(f"ainvoke returned, keys={list(result.keys())}")
        except asyncio.TimeoutError:
            log("TIMED OUT after 90s")

    await bus.close_run(run_id)
    await watch_task


if __name__ == "__main__":
    asyncio.run(main())
