"""
Smoke test for the extracted apps/api/llm/router.py and apps/api/rag/store.py.
Run from the repo root: python smoke_test.py
"""
import json

from apps.api.llm import router
from apps.api.rag import store


def test_llm_plain():
    print("=== router.call_llm (plain text) ===")
    reply = router.call_llm(
        system="You are a terse assistant.",
        messages=[{"role": "user", "content": "Say hello in 5 words or fewer."}],
    )
    print(reply)


def test_llm_json():
    print("\n=== router.call_llm (json_mode) ===")
    reply = router.call_llm(
        system="Respond only with JSON.",
        messages=[{"role": "user", "content": 'Return {"greeting": "hi"} exactly.'}],
        json_mode=True,
    )
    print(reply)
    print("type:", type(reply))


def test_ingest_retrieve():
    print("\n=== store.ingest(sample.csv) ===")
    result = store.ingest("sample.csv")
    print({"file_id": result["file_id"], "chunks": result["chunks"]})

    print("\n=== store.retrieve('laptop stand price') ===")
    chunks = store.retrieve("laptop stand price")
    for i, c in enumerate(chunks):
        print(f"--- chunk {i} ---")
        print(c)


if __name__ == "__main__":
    test_llm_plain()
    test_llm_json()
    test_ingest_retrieve()
    print("\nALL SMOKE TESTS PASSED")
