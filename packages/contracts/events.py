"""
AgentEvent contract — the single event shape every part of the multi-agent
system (planner, dispatch, agent nodes, tools, approval gate, memory) emits
through the EventBus. This is what apps/api/bus.py fans out to SSE
subscribers and what the frontend timeline/graph view renders.
"""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional
import json


class EventType(str, Enum):
    RUN_STARTED = "run.started"
    PLAN_CREATED = "plan.created"
    PLAN_REVISED = "plan.revised"
    NODE_STARTED = "node.started"
    NODE_FINISHED = "node.finished"
    NODE_FAILED = "node.failed"
    AGENT_THINKING = "agent.thinking"
    A2A_MESSAGE = "a2a.message"
    # Deterministic preflight checks run before a gated write is offered for
    # approval. Added only because there is a real emitter — see
    # conflict_check_node's _preflight_conflicts.
    SCHEDULE_CHECKED = "schedule.checked"
    ATTENDANCE_IMPACT_CALCULATED = "attendance.impact.calculated"
    CONFLICT_DETECTED = "conflict.detected"
    CONFLICT_RESOLVED = "conflict.resolved"
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"
    TOOL_RETRY = "tool.retry"
    TOOL_FALLBACK = "tool.fallback"
    RAG_RETRIEVED = "rag.retrieved"
    MEMORY_WRITE = "memory.write"
    MEMORY_RECALL = "memory.recall"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    TOKEN_USAGE = "token.usage"
    RUN_FINISHED = "run.finished"
    RUN_ERROR = "run.error"
    PROACTIVE_ALERT = "proactive.alert"


@dataclass
class AgentEvent:
    id: str
    run_id: str
    ts: float                      # epoch seconds, set by the caller (bus does not stamp time)
    type: EventType
    node_id: Optional[str] = None
    agent: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    latency_ms: Optional[float] = None
    parent_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value if isinstance(self.type, EventType) else self.type
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentEvent":
        d = dict(d)
        d["type"] = EventType(d["type"])
        return cls(**d)
