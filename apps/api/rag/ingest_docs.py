"""
Clause-aware ingestion of data/docs/*.md into a dedicated Chroma collection.

Unlike the generic rag/store.py chunker (which splits on character windows
and only knows a filename), this preserves the structure the Knowledge Agent
must cite: document title, document number, clause number, and a synthetic
page number. Citations like "R22 clause 4.2, p.3" are only possible because
the metadata is captured here at ingest time.

Run: python -m apps.api.rag.ingest_docs
"""
import re
from pathlib import Path

from apps.api.rag.store import CHROMA_DIR, _get_embedder

DOCS_DIR = Path(__file__).resolve().parents[3] / "data" / "docs"
POLICY_COLLECTION = "agentx_policies"
WORDS_PER_PAGE = 500

# "4.2 A candidate shall ..." — a numbered clause at the start of a line.
CLAUSE_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\s+(.*)$")
HEADING_RE = re.compile(r"^##\s+(.*)$")

_collection = None


def _get_policy_collection():
    global _collection
    if _collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = client.get_or_create_collection(POLICY_COLLECTION)
    return _collection


def parse_document(path: Path) -> tuple[dict, list[dict]]:
    """Split one markdown policy doc into clause-level chunks.

    Returns (doc_meta, chunks) where each chunk carries its own clause number
    and page. Text before the first numbered clause (title block, preamble)
    is attached to the section heading it sits under, so nothing is dropped.
    """
    lines = path.read_text(encoding="utf-8").splitlines()

    title = ""
    doc_number = ""
    for line in lines[:12]:
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        m = re.match(r"^Document Number:\s*(.+)$", line.strip())
        if m:
            doc_number = m.group(1).strip()

    chunks: list[dict] = []
    section = ""
    current_clause = ""
    buffer: list[str] = []
    words_so_far = 0

    def flush():
        nonlocal buffer, current_clause, words_so_far
        text = " ".join(" ".join(buffer).split())
        if text and current_clause:
            page = words_so_far // WORDS_PER_PAGE + 1
            chunks.append({
                "text": text, "clause": current_clause, "section": section, "page": page,
            })
            words_so_far += len(text.split())
        buffer = []

    for line in lines:
        heading = HEADING_RE.match(line)
        if heading:
            flush()
            section = heading.group(1).strip()
            current_clause = ""
            continue

        clause = CLAUSE_RE.match(line.strip())
        if clause:
            flush()
            current_clause = clause.group(1)
            buffer = [clause.group(2)]
            continue

        if line.strip():
            buffer.append(line.strip())

    flush()
    return {"title": title, "doc_number": doc_number, "filename": path.name}, chunks


def ingest_all(reset: bool = True) -> dict:
    """(Re-)ingest every markdown doc under data/docs/."""
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    if reset:
        try:
            client.delete_collection(POLICY_COLLECTION)
        except Exception:
            pass
    global _collection
    _collection = client.get_or_create_collection(POLICY_COLLECTION)

    summary = {}
    embedder = _get_embedder()

    for path in sorted(DOCS_DIR.glob("*.md")):
        doc_meta, chunks = parse_document(path)
        if not chunks:
            summary[path.name] = 0
            continue

        texts = [f"[{doc_meta['title']} clause {c['clause']}] {c['text']}" for c in chunks]
        embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
        _collection.add(
            ids=[f"{path.stem}-{c['clause']}-{i}" for i, c in enumerate(chunks)],
            embeddings=embeddings,
            documents=[c["text"] for c in chunks],
            metadatas=[{
                "doc_title": doc_meta["title"],
                "doc_number": doc_meta["doc_number"],
                "filename": doc_meta["filename"],
                "clause": c["clause"],
                "section": c["section"],
                "page": c["page"],
            } for c in chunks],
        )
        summary[path.name] = len(chunks)

    return summary


if __name__ == "__main__":
    result = ingest_all()
    total = sum(result.values())
    print(f"Ingested {total} clause-level chunks from {len(result)} documents into '{POLICY_COLLECTION}':")
    for name, count in result.items():
        print(f"  {name:<40} {count} clauses")
