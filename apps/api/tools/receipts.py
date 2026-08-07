"""Shared receipt-writing helper — every write tool inserts one row here."""
import json
import time
import uuid

from sqlalchemy import text


def write_receipt(session, *, actor: str, tool: str, args: dict, result: dict, approved_by: str | None = None) -> str:
    receipt_id = uuid.uuid4().hex[:12]
    session.execute(
        text(
            "INSERT INTO receipts (id, actor, tool, args_json, result_json, ts, approved_by) "
            "VALUES (:id, :actor, :tool, :args, :result, :ts, :approved_by)"
        ),
        {
            "id": receipt_id, "actor": actor, "tool": tool,
            "args": json.dumps(args), "result": json.dumps(result),
            "ts": time.time(), "approved_by": approved_by,
        },
    )
    return receipt_id
