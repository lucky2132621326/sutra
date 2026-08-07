"""Watch a run's fixture file and POST /approve each approval as it appears.
Used to drive a real-LLM run to completion without a UI.
Usage: python auto_approve.py <run_id> [decision]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"
run_id = sys.argv[1]
decision = sys.argv[2] if len(sys.argv) > 2 else "approve"
fixture = Path(__file__).resolve().parent / "fixtures" / f"{run_id}.jsonl"

seen: set[str] = set()
deadline = time.time() + 900
print(f"watching {fixture.name} for approvals -> will {decision}")

while time.time() < deadline:
    if fixture.exists():
        for line in fixture.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev["type"] == "approval.requested":
                aid = ev["payload"].get("id")
                if aid and aid not in seen:
                    seen.add(aid)
                    body = json.dumps({"run_id": run_id, "approval_id": aid,
                                        "decision": decision, "edited_args": None}).encode()
                    req = urllib.request.Request(BASE + "/approve", data=body,
                                                  headers={"Content-Type": "application/json"}, method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=30) as r:
                            print(f"  approved {aid}: {r.read().decode()}")
                    except Exception as e:
                        print(f"  approve failed for {aid}: {e}")
            if ev["type"] in ("run.finished", "run.error"):
                print(f"  run ended with {ev['type']}")
                sys.exit(0)
    time.sleep(2)

print("watcher timed out")
