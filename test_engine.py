"""
Manual CLI harness for engine.py — no pytest, just quick sanity checks
during hackathon dev. Run with no args to see usage.

Usage:
    python test_engine.py llm            # test call_llm() plain text
    python test_engine.py llm_json       # test call_llm() json_mode
    python test_engine.py ingest <path>  # test ingest() on a txt/pdf/csv file
    python test_engine.py retrieve "<query>"
    python test_engine.py query "<input text>"
    python test_engine.py upload <path>  # test process_upload()
    python test_engine.py all <path>     # run everything using <path> as the sample file
"""
import sys
import json

import engine


def test_llm():
    print("=== call_llm (plain text) ===")
    reply = engine.call_llm(
        system="You are a terse assistant.",
        messages=[{"role": "user", "content": "Say hello in 5 words or fewer."}],
    )
    print(reply)


def test_llm_json():
    print("=== call_llm (json_mode) ===")
    reply = engine.call_llm(
        system="Respond only with JSON.",
        messages=[{"role": "user", "content": 'Return {"greeting": "hi"} exactly.'}],
        json_mode=True,
    )
    print(reply)
    print("type:", type(reply))


def test_ingest(path):
    print(f"=== ingest({path}) ===")
    result = engine.ingest(path)
    print(result)


def test_retrieve(query):
    print(f"=== retrieve({query!r}) ===")
    chunks = engine.retrieve(query)
    for i, c in enumerate(chunks):
        print(f"--- chunk {i} ---")
        print(c)


def test_query(user_input):
    print(f"=== run_query({user_input!r}) ===")
    result = engine.run_query(user_input, context=None)
    print(json.dumps(result, indent=2))
    assert set(result.keys()) >= {"result", "reasoning", "data", "status"}, "missing contract keys"


def test_upload(path):
    print(f"=== process_upload({path}) ===")
    with open(path, "rb") as f:
        result = engine.process_upload(f, filename=path.split("/")[-1].split("\\")[-1])
    print(json.dumps(result, indent=2))
    assert set(result.keys()) >= {"file_id", "summary", "status"}, "missing contract keys"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "llm":
        test_llm()
    elif cmd == "llm_json":
        test_llm_json()
    elif cmd == "ingest":
        test_ingest(sys.argv[2])
    elif cmd == "retrieve":
        test_retrieve(sys.argv[2])
    elif cmd == "query":
        test_query(sys.argv[2])
    elif cmd == "upload":
        test_upload(sys.argv[2])
    elif cmd == "all":
        path = sys.argv[2]
        test_llm()
        test_llm_json()
        test_upload(path)
        test_retrieve("test query")
        test_query("What did the uploaded file say?")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
