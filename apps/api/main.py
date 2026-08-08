"""
FastAPI entrypoint — POST /chat, GET /stream/{run_id} (SSE), POST /approve,
GET /health. Replaces engine.py as the entrypoint (run_query/process_upload
retired per the Sept 5.1 multi-agent rebuild).

Run: uvicorn apps.api.main:app --reload
"""
import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from apps.api.bus import bus
from apps.api.calendar import CalendarResponse, build_calendar
from apps.api.graph.build import graph_session
from apps.api.inbox import InboxResponse, build_inbox
from apps.api.llm.router import call_llm_async, warm_local_ollama
from apps.api.rag.store import _get_embedder
from apps.api.tools import chaos
from apps.api.tools.exceptions import RecordNotFound
from packages.contracts.events import AgentEvent, EventType

_background_tasks: set[asyncio.Task] = set()
# One graph leg is either the initial run or the continuation after an
# approval. Individual model calls are bounded in llm/router.py; this outer
# deadline is the final guarantee that a sequence of slow fallbacks cannot
# leave the frontend in "Executing" indefinitely.
GRAPH_LEG_TIMEOUT_S = float(os.environ.get("GRAPH_LEG_TIMEOUT_S", "60"))


async def _warm_up() -> None:
    """Pay the one-off costs at startup instead of during the first query.

    The provider client's TLS handshake alone was measured at ~6.6s, against
    ~0.17s for every subsequent call on the reused connection; the local
    embedding model likewise takes tens of seconds to load. Doing both here
    means the first real question is fast, which matters on a demo clock.
    """
    # Start all three cold loads together. Sequential warm-up could take over
    # half a minute (provider chain, then Ollama, then embeddings), during
    # which /health was green but the first policy query paid the full model
    # loading cost. Failures remain isolated because all three are optional
    # accelerators, not API readiness requirements.
    await asyncio.gather(
        call_llm_async(
            "Reply with JSON.", [{"role": "user", "content": 'Return {"ready":true} as JSON.'}],
            True,
        ),
        asyncio.to_thread(warm_local_ollama),
        asyncio.to_thread(_get_embedder),
        return_exceptions=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with graph_session() as graph:
        app.state.graph = graph
        app.state.warmup = asyncio.create_task(_warm_up())
        yield


app = FastAPI(title="Sūtra — Smart Campus Multi-Agent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    student_id: str
    role: str = "student"
    thread_id: str | None = None


class ChatResponse(BaseModel):
    run_id: str
    """Unique per turn — subscribe to /stream/{run_id}."""
    thread_id: str
    """Stable across the conversation — send it back on the next turn."""


class ApproveRequest(BaseModel):
    run_id: str
    """Which event stream to keep writing to."""
    thread_id: str | None = None
    """Which checkpoint to resume. Defaults to run_id for older single-turn
    callers, but multi-turn clients must send the thread_id from /chat."""
    approval_id: str
    decision: str  # "approve" | "reject" | "edit"
    edited_args: dict | None = None


def _config_for(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def _drive_graph(run_id: str, graph, invoke_arg, config: dict) -> None:
    """Runs one leg of the graph (initial invoke or a resume) and closes the
    SSE stream iff the run actually finished — an interrupt() pause leaves
    the stream open so the eventual /approve resume can keep emitting to it.
    """
    try:
        result = await asyncio.wait_for(
            graph.ainvoke(invoke_arg, config=config),
            timeout=GRAPH_LEG_TIMEOUT_S,
        )
        if "__interrupt__" not in result:
            await bus.close_run(run_id)
    except Exception as e:
        error_message = (
            f"Run timeout: execution exceeded the {GRAPH_LEG_TIMEOUT_S:g}s deadline"
            if isinstance(e, TimeoutError)
            else str(e)
        )
        await bus.emit(AgentEvent(
            id=uuid.uuid4().hex[:8], run_id=run_id, ts=time.time(), type=EventType.RUN_ERROR,
            payload={"error": error_message},
        ))
        await bus.close_run(run_id)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    # run_id and thread_id are DIFFERENT things and must never be aliased:
    #   run_id    — one execution; scopes the event stream and its fixture file
    #   thread_id — the conversation; scopes the checkpointer and memory
    # Previously run_id WAS thread_id, so a second turn on the same thread
    # appended to the first turn's stream and replayed all of turn 1 to any new
    # subscriber. A fresh run_id per turn keeps streams clean while the stable
    # thread_id still carries conversational state forward.
    run_id = uuid.uuid4().hex[:12]
    thread_id = req.thread_id or uuid.uuid4().hex[:12]
    initial_state = {
        "run_id": run_id, "student_id": req.student_id, "role": req.role,
        "goal": req.message, "iteration": 0,
    }
    _spawn(_drive_graph(run_id, app.state.graph, initial_state, _config_for(thread_id)))
    return ChatResponse(run_id=run_id, thread_id=thread_id)


@app.get("/stream/{run_id}")
async def stream(run_id: str):
    async def event_stream():
        async for event in bus.subscribe(run_id):
            yield f"event: {event.type.value}\ndata: {event.to_json()}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@app.post("/approve")
async def approve(req: ApproveRequest):
    # approval_id is forwarded so the gate resolves THE action the user acted
    # on. Without it the gate applied the decision to whichever interrupt
    # happened to be pending — indistinguishable with a single approval, but
    # wrong the moment a plan queues two.
    resume_value = {
        "decision": req.decision,
        "edited_args": req.edited_args,
        "approval_id": req.approval_id,
    }
    # Events keep flowing to run_id's stream; the checkpoint to resume is keyed
    # by thread_id.
    _spawn(_drive_graph(
        req.run_id, app.state.graph, Command(resume=resume_value),
        _config_for(req.thread_id or req.run_id),
    ))
    return {"status": "resuming"}


class ChaosRequest(BaseModel):
    service: str
    mode: str  # healthy | slow | error_500 | timeout | flaky | empty_response


@app.post("/admin/chaos")
async def set_chaos(req: ChaosRequest):
    """Break a backing service on purpose, at runtime, no restart — this is
    the demo's chaos button."""
    try:
        chaos.set_mode(req.service, req.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"service": req.service, "mode": req.mode, "state": chaos.status()}


@app.get("/admin/chaos/status")
async def chaos_status():
    return {"state": chaos.status(), "modes": list(chaos.MODES)}


@app.post("/admin/chaos/reset")
async def chaos_reset():
    chaos.reset()
    return {"state": chaos.status()}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/inbox/{student_id}", response_model=InboxResponse)
async def inbox(student_id: str):
    """Fast, quota-free campus alerts derived from current records."""
    try:
        return await asyncio.to_thread(build_inbox, student_id)
    except RecordNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/calendar/{student_id}", response_model=CalendarResponse)
async def calendar(student_id: str, start: str | None = None, end: str | None = None):
    """Verified timetable, approved registrations, calendar writes and reminders."""
    try:
        start_date = date.fromisoformat(start) if start else None
        end_date = date.fromisoformat(end) if end else None
        return await asyncio.to_thread(
            build_calendar, student_id, range_start=start_date, range_end=end_date,
        )
    except RecordNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
