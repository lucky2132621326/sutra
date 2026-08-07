"""
End-to-end verification through the real HTTP/SSE layer (not the Python
graph directly). Assumes a server is already running on :8000 with
MOCK_LLM=1 so the run is deterministic and costs no provider quota.

Checks:
  A. POST /chat -> GET /stream/{run_id} streams the full event sequence
  B. Every SSE `data:` payload is byte-identical to the corresponding line in
     fixtures/{run_id}.jsonl, in the same order (frontend can replay 1:1)
  C. The approval gate fires and POST /approve resumes the run to completion
  D. Chaos: break the placement service, confirm tool.retry x2 -> tool.fallback
     appears in the stream and the run still produces an answer

Run: python e2e_check.py
"""
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ANANYA = "1602-23-733-042"
HERO_QUERY = ("I'm a third-year CSE student. Am I eligible for the Google internship? "
              "If yes, register me for the placement workshop, add it to my calendar, "
              "and remind me an hour before.")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def stream_events(run_id: str, sink: list, stop_after: float):
    """Read the SSE stream, collecting raw `data:` payload strings."""
    try:
        with urllib.request.urlopen(f"{BASE}/stream/{run_id}", timeout=stop_after) as r:
            for raw in r:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if line.startswith("data: "):
                    sink.append(line[len("data: "):])
    except Exception:
        pass  # stream closes or times out; whatever we collected is what we check


def run_scenario(label: str, query: str, approve: bool = True) -> tuple[str, list]:
    print(f"\n=== {label} ===")
    run_id = post("/chat", {"message": query, "student_id": ANANYA, "role": "student"})["run_id"]
    print(f"  run_id={run_id}")

    sse: list[str] = []
    t = threading.Thread(target=stream_events, args=(run_id, sse, 180), daemon=True)
    t.start()

    # Poll for an approval request in the stream, approve it, then wait for the end.
    approved_ids: set[str] = set()
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(1.0)
        finished = False
        for payload in list(sse):
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if ev["type"] == "approval.requested":
                approval_id = ev["payload"].get("id")
                if approval_id and approval_id not in approved_ids:
                    approved_ids.add(approval_id)
                    decision = "approve" if approve else "reject"
                    post("/approve", {"run_id": run_id, "approval_id": approval_id,
                                       "decision": decision, "edited_args": None})
                    print(f"  -> POST /approve {decision} for {approval_id}")
            if ev["type"] in ("run.finished", "run.error"):
                finished = True
        if finished:
            break
    time.sleep(1.5)  # let the last events land in the fixture
    return run_id, sse


def compare_sse_to_fixture(run_id: str, sse: list) -> None:
    fixture_path = FIXTURES / f"{run_id}.jsonl"
    check("fixture file written", fixture_path.exists(), str(fixture_path))
    if not fixture_path.exists():
        return

    fixture_lines = [l.strip() for l in fixture_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    # The stream may be joined slightly after the run starts, so compare the
    # overlapping suffix: every SSE payload must appear in the fixture, in order.
    check("SSE received events", len(sse) > 0, f"{len(sse)} events")
    if not sse:
        return

    try:
        start = fixture_lines.index(sse[0])
    except ValueError:
        check("first SSE payload found in fixture", False, sse[0][:120])
        return

    window = fixture_lines[start:start + len(sse)]
    identical = window == sse
    check("SSE payloads byte-identical to fixture (same order)", identical,
          f"compared {len(sse)} events starting at fixture line {start + 1}")
    if not identical:
        for i, (a, b) in enumerate(zip(sse, window)):
            if a != b:
                print(f"      first divergence at offset {i}:\n        sse     : {a[:200]}\n        fixture : {b[:200]}")
                break


def event_types(sse: list) -> list[str]:
    out = []
    for p in sse:
        try:
            out.append(json.loads(p)["type"])
        except Exception:
            pass
    return out


def main():
    print("=== /health ===")
    check("server healthy", get("/health").get("status") == "ok")

    # --- A/B/C: hero scenario, SSE/fixture parity, approval resume
    run_id, sse = run_scenario("HERO SCENARIO (approve)", HERO_QUERY, approve=True)
    types = event_types(sse)
    print(f"  event sequence ({len(types)}): {' -> '.join(types)}")

    compare_sse_to_fixture(run_id, sse)
    check("plan.created emitted", "plan.created" in types)
    check("parallel agent work (node.started >= 2)", types.count("node.started") >= 2)
    check("tool actually called", "tool.called" in types)
    check("approval gate fired", "approval.requested" in types)
    check("approval resolved after POST /approve", "approval.resolved" in types)
    check("run reached run.finished", "run.finished" in types)

    # --- D: chaos path
    print("\n=== CHAOS: break placement service ===")
    print("  ", post("/admin/chaos", {"service": "placement", "mode": "error_500"}))
    _, chaos_sse = run_scenario("CHAOS RUN", "Am I eligible for the Google internship?", approve=True)
    chaos_types = event_types(chaos_sse)
    print(f"  event sequence ({len(chaos_types)}): {' -> '.join(chaos_types)}")
    check("tool.retry emitted under chaos", "tool.retry" in chaos_types,
          f"{chaos_types.count('tool.retry')} retries")
    check("tool.fallback emitted under chaos", "tool.fallback" in chaos_types)
    check("run still completed despite injected failure", "run.finished" in chaos_types)
    print("  ", post("/admin/chaos/reset", {}))

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL END-TO-END HTTP/SSE CHECKS PASSED")


if __name__ == "__main__":
    main()
