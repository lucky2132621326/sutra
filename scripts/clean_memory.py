"""
Purge test and scratch turns from the semantic memory store.

The Memory panel is user-facing: it shows what Sūtra remembers about this
student across conversations. Every pytest run and every ad-hoc probe wrote a
turn summary into the same collection, so by demo time it held dozens of
`test-*` threads and near-duplicate summaries of the same scripted question —
which reads as noise, or worse, as the system remembering things that never
happened to the student.

Removes anything whose thread_id looks like a test or scratch run, plus exact
duplicate summaries (keeping the earliest). Real conversation memory is left
alone.

    python scripts/clean_memory.py            # report only
    python scripts/clean_memory.py --apply    # delete
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Thread-id prefixes minted by tests, probes and fixture recordings.
SCRATCH_PREFIXES = (
    "test-", "budget-", "rec-", "cv-", "real-", "t-", "p-", "q-", "f-", "g-",
    "d-", "nc", "ord-", "dbg-", "exp-", "b-", "nocflict-", "approve-", "reject-",
)


def main() -> int:
    apply = "--apply" in sys.argv
    from apps.api.memory.semantic import _get_collection

    col = _get_collection()
    got = col.get(include=["metadatas", "documents"])
    ids = got.get("ids") or []
    metas = got.get("metadatas") or []
    docs = got.get("documents") or []
    print(f"{len(ids)} memories in the store")

    doomed: list[tuple[str, str]] = []
    seen_summaries: dict[tuple[str, str], str] = {}

    for mid, meta, doc in zip(ids, metas, docs):
        thread = str((meta or {}).get("thread_id", ""))
        student = str((meta or {}).get("student_id", ""))
        if thread.startswith(SCRATCH_PREFIXES):
            doomed.append((mid, f"scratch thread {thread}"))
            continue
        key = (student, (doc or "").strip())
        if key in seen_summaries:
            doomed.append((mid, "duplicate summary"))
        else:
            seen_summaries[key] = mid

    if not doomed:
        print("nothing to clean")
        return 0

    reasons: dict[str, int] = {}
    for _, why in doomed:
        label = why.split(" thread ")[0]
        reasons[label] = reasons.get(label, 0) + 1
    for label, n in sorted(reasons.items()):
        print(f"  {n:4}  {label}")

    if not apply:
        print(f"\n{len(doomed)} would be removed. Re-run with --apply.")
        return 0

    col.delete(ids=[mid for mid, _ in doomed])
    print(f"\nremoved {len(doomed)}; {len(ids) - len(doomed)} remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
