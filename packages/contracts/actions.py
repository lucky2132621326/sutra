"""
PendingAction — returned by a tool instead of executing, whenever the tool
is a write/irreversible action and the step that invoked it was marked
requires_approval. The approval_gate node pauses the graph on these; the
tools/ registry (built in a later step) constructs them.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingAction:
    id: str
    step_id: str
    agent: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    description: str = ""          # human-readable summary for the approval UI

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "step_id": self.step_id,
            "agent": self.agent,
            "tool": self.tool,
            "args": self.args,
            "description": self.description,
        }
