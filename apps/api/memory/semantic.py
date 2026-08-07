"""
Tier 3 — SEMANTIC memory: a vector index of past turn summaries, recalled
against the current query so the system can surface relevant context from
earlier turns and earlier sessions.

DEVIATION FROM THE BATTLE PLAN: that document specifies FAISS. This uses a
separate ChromaDB collection instead, reusing the same local
sentence-transformers embedder the RAG layer already loads. Rationale: it is
behaviourally identical for this purpose (local vector search, no network),
avoids adding faiss-cpu as a dependency, and avoids loading a second
embedding model into memory. The persistence guarantee the acceptance test
cares about — survives a process restart — holds either way, since Chroma
is backed by the on-disk chroma_db/ directory.
"""
import uuid

from apps.api.memory import profile
from apps.api.rag.store import CHROMA_DIR, _get_embedder

COLLECTION_NAME = "agentx_memory"
RECALL_TOP_K = 3
# Chroma returns squared-L2 distance; this converts to a rough similarity in
# [0,1]. 0.4 is the battle plan's suggested floor — a heuristic, not a tuned
# value (see the limitations note in the status report).
MIN_RECALL_SCORE = 0.4

_collection = None


def _get_collection():
    global _collection
    if _collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = client.get_or_create_collection(COLLECTION_NAME)
    return _collection


def add_turn_summary(student_id: str, thread_id: str, summary: str) -> str:
    """Embed and store one turn summary. Also mirrored into memory.db so the
    summary text survives independently of the vector store."""
    if not summary.strip():
        return ""
    summary_id = uuid.uuid4().hex[:12]
    embedding = _get_embedder().encode([summary], show_progress_bar=False).tolist()
    _get_collection().add(
        ids=[summary_id],
        embeddings=embedding,
        documents=[summary],
        metadatas=[{"student_id": student_id, "thread_id": thread_id or ""}],
    )
    profile.save_turn_summary(summary_id, student_id, thread_id, summary)
    return summary_id


def recall(student_id: str, query: str, top_k: int = RECALL_TOP_K) -> list[dict]:
    """Return past turn summaries relevant to `query` for this student.

    Scoped to the student via a metadata filter — memory must never leak
    across users.
    """
    collection = _get_collection()
    if collection.count() == 0 or not query.strip():
        return []

    query_embedding = _get_embedder().encode([query], show_progress_bar=False).tolist()
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=min(top_k, collection.count()),
        where={"student_id": student_id},
    )

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    ids = results.get("ids", [[]])[0]

    recalled = []
    for doc, meta, dist, sid in zip(docs, metas, dists, ids):
        score = max(0.0, 1.0 - dist / 2)
        if score < MIN_RECALL_SCORE:
            continue
        stored = profile.get_turn_summary(sid) or {}
        recalled.append({
            "id": sid, "summary": doc, "score": round(score, 3),
            "thread_id": meta.get("thread_id", ""), "ts": stored.get("ts"),
        })
    return recalled


def clear_all() -> None:
    """Drop the memory collection. Used by reset_demo and tests."""
    global _collection
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    _collection = None
