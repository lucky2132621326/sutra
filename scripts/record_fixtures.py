"""
Record the demo fixtures the frontend replays.

Produces deterministic, zero-quota traces with MOCK_LLM=1. Each is recorded
into a freshly seeded database so seat counts and attendance are the exact
values the demo narrative claims.

    python scripts/record_fixtures.py

Outputs into fixtures/:
    golden_clean.jsonl     read-only question: parallel dispatch, RAG citations, no approvals
    golden_conflict.jsonl  Academic Agent vetoes Thursday, replan books Saturday
    golden_chaos.jsonl     placement service broken -> retry x2 -> fallback -> degraded
    golden_reject.jsonl    human REJECTS the approval; nothing downstream is written

golden_clean uses a read-only goal on purpose. Any goal that registers for the
placement workshop now collides with the DBMS lab for real, so "clean" has to
mean "asked for nothing that writes" rather than "the conflict flag was off".
"""
import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MOCK_LLM"] = "1"

FIXTURES = ROOT / "fixtures"
ANANYA = "1602-23-733-042"
HERO = ("I'm a third-year CSE student. Am I eligible for the Google internship? "
        "If yes, register me for the placement workshop, add it to my calendar, "
        "and remind me an hour before.")
# A read-only question. Needed as its own goal now that the Thursday conflict is
# detected from real timetable data: any goal that registers for the workshop
# WILL collide, so a conflict-free trace cannot be recorded from HERO.
READONLY = ("I'm a third-year CSE student. Am I eligible for the Google internship, "
            "and what attendance do I need to sit for exams?")


def reseed():
    """Fresh DB per recording so seats/attendance match the narrative."""
    from apps.api.tools.db import engine
    engine.dispose()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "seed.py")],
                   cwd=ROOT, check=True, capture_output=True)
    for stale in ("checkpoints.db",):
        try:
            (ROOT / "data" / stale).unlink(missing_ok=True)
        except PermissionError:
            # A running API server holds this open on Windows. Recording uses
            # fresh thread ids anyway, so stale checkpoints are inert — no
            # reason to make the developer stop their server to re-record.
            pass


async def record(name: str, *, goal: str, decision: str = "approve", chaos: str | None = None):
    from langgraph.types import Command

    from apps.api.graph.build import graph_session
    from apps.api.tools import chaos as chaos_mod

    reseed()
    chaos_mod.reset()
    if chaos:
        chaos_mod.set_mode(chaos, "error_500")
    # MOCK_CONFLICT is deliberately never set. The Thursday clash is now found
    # by a real check against the seeded timetable, so a recorded conflict is
    # evidence of a working check rather than of a flag being on.
    os.environ.pop("MOCK_CONFLICT", None)

    run_id = f"rec-{uuid.uuid4().hex[:8]}"
    async with graph_session() as graph:
        config = {"configurable": {"thread_id": run_id}}
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": ANANYA, "role": "student", "goal": goal, "iteration": 0},
            config=config,
        )
        hops = 0
        while "__interrupt__" in result and hops < 8:
            result = await graph.ainvoke(Command(resume={"decision": decision}), config=config)
            hops += 1

    chaos_mod.reset()
    src = FIXTURES / f"{run_id}.jsonl"
    dst = FIXTURES / f"{name}.jsonl"
    shutil.move(src, dst)
    lines = sum(1 for _ in dst.open(encoding="utf-8"))
    answer = (result.get("final_answer") or "").replace("\n", " ")[:110]
    print(f"  {name + '.jsonl':<26} {lines:>3} events   {answer}")
    return dst


async def main():
    FIXTURES.mkdir(exist_ok=True)
    for old in FIXTURES.glob("*.jsonl"):
        old.unlink()

    print("Recording demo fixtures (MOCK_LLM=1, zero quota):\n")
    await record("golden_clean", goal=READONLY)
    await record("golden_conflict", goal=HERO)
    # Chaos on the READ-ONLY goal, deliberately. On HERO the retries land on the
    # pre-replan plan, and the replan rebuilds the DAG — so the retry -> fallback
    # -> degraded story is wiped from the graph the moment it becomes relevant.
    # Isolating it on a single-plan run keeps it visible, and keeps the
    # resilience story from being tangled with the conflict story.
    await record("golden_chaos", goal=READONLY, chaos="placement")
    await record("golden_reject", goal=HERO, decision="reject")
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
