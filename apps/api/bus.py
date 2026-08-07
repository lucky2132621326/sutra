"""
EventBus — per-run pub/sub for AgentEvents, backed by asyncio.Queue, with
every event persisted to fixtures/{run_id}.jsonl for replay/debugging.

One bus instance is shared process-wide (see `bus` singleton at the bottom).
The orchestrator, agent nodes, tools, and the approval gate all call
bus.emit(...); GET /stream/{run_id} calls bus.subscribe(run_id) to get an
async iterator of events for SSE.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

from packages.contracts.events import AgentEvent

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"

# Sentinel pushed onto a run's queue to signal "no more events, close the
# stream" — subscribers stop iterating when they see it.
_CLOSE = object()


class EventBus:
    def __init__(self, fixtures_dir: Path = FIXTURES_DIR):
        self._fixtures_dir = fixtures_dir
        self._queues: dict[str, list[asyncio.Queue]] = {}
        # Per-run event history. POST /chat starts the graph immediately, so a
        # short run can finish before the client's GET /stream connects — and
        # a UI reconnect has the same problem. Without a replayable history a
        # late subscriber silently sees nothing. History is kept in memory for
        # the process lifetime; fixtures/{run_id}.jsonl is the durable copy.
        self._history: dict[str, list[AgentEvent]] = {}
        self._closed: set[str] = set()
        self._lock = asyncio.Lock()

    async def emit(self, event: AgentEvent) -> None:
        """Broadcast an event to all subscribers of its run_id and persist it."""
        self._append_to_fixture(event)
        async with self._lock:
            self._history.setdefault(event.run_id, []).append(event)
            subscribers = list(self._queues.get(event.run_id, []))
        for q in subscribers:
            await q.put(event)

    async def close_run(self, run_id: str) -> None:
        """Signal all subscribers of run_id that the stream is done."""
        async with self._lock:
            self._closed.add(run_id)
            subscribers = list(self._queues.get(run_id, []))
        for q in subscribers:
            await q.put(_CLOSE)

    async def subscribe(self, run_id: str) -> AsyncIterator[AgentEvent]:
        """Async-iterate events for a run_id, replaying anything already
        emitted before yielding live ones.

        The backlog snapshot and queue registration happen under one lock, so
        an event emitted concurrently lands in exactly one of the two — never
        dropped, never duplicated. Ends when close_run(run_id) is called, or
        immediately after the backlog if the run already finished.
        """
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            backlog = list(self._history.get(run_id, []))
            already_closed = run_id in self._closed
            self._queues.setdefault(run_id, []).append(q)
        try:
            for event in backlog:
                yield event
            if already_closed:
                return
            while True:
                item = await q.get()
                if item is _CLOSE:
                    break
                yield item
        finally:
            async with self._lock:
                subs = self._queues.get(run_id)
                if subs and q in subs:
                    subs.remove(q)
                if subs is not None and not subs:
                    self._queues.pop(run_id, None)

    async def forget_run(self, run_id: str) -> None:
        """Drop a finished run's in-memory history. Not called automatically —
        a UI may reconnect after the run ends — so long-lived processes should
        call this once a run's events are no longer needed."""
        async with self._lock:
            self._history.pop(run_id, None)
            self._closed.discard(run_id)

    def _append_to_fixture(self, event: AgentEvent) -> None:
        self._fixtures_dir.mkdir(parents=True, exist_ok=True)
        path = self._fixtures_dir / f"{event.run_id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(event.to_json() + "\n")

    def replay(self, run_id: str) -> list[AgentEvent]:
        """Read back every event persisted for a run_id, in order."""
        path = self._fixtures_dir / f"{run_id}.jsonl"
        if not path.exists():
            return []
        events = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(AgentEvent.from_dict(json.loads(line)))
        return events


# Process-wide singleton — imported by graph nodes, tools, and main.py.
bus = EventBus()
