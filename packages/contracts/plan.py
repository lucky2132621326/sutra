"""
Plan/Step contract — the planner's structured output and the unit of work
the dispatcher fans out to specialist agents.

Pydantic models, not dataclasses: LangGraph's checkpoint serializer
(ormsgpack) only has native, forward-compatible support for Pydantic —
arbitrary dataclasses go through a fallback path that logs "will be blocked
in a future version" on every checkpoint read/write. Since these are
checkpointed as part of GraphState, that fallback would eventually break
approval-resume and cross-session memory outright.
"""
from pydantic import BaseModel, Field

AGENT_NAMES = ["academic", "placement", "events", "knowledge", "services"]


class Step(BaseModel):
    id: str
    agent: str                              # one of AGENT_NAMES
    task: str
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str = ""
    requires_approval: bool = False

    def to_dict(self) -> dict:
        return self.model_dump()


class Plan(BaseModel):
    goal: str
    steps: list[Step]
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {"goal": self.goal, "reasoning": self.reasoning, "steps": [s.to_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        steps = [Step(**s) for s in d.get("steps", [])]
        return cls(goal=d.get("goal", ""), steps=steps, reasoning=d.get("reasoning", ""))


# JSON-schema-shaped instructions handed to the LLM in json_mode — kept here
# so the planner prompt and the parser agree on one source of truth.
PLAN_JSON_INSTRUCTIONS = """Respond with a JSON object with exactly these keys:
- "goal": a one-sentence restatement of the user's goal
- "reasoning": brief explanation of how you decomposed the goal into steps
- "steps": a list of step objects, each with exactly these keys:
  - "id": short unique string (e.g. "s1", "s2")
  - "agent": one of "academic", "placement", "events", "knowledge", "services"
  - "task": a specific, self-contained instruction for that agent
  - "depends_on": list of step ids that must complete before this one (empty list if none)
  - "expected_output": one sentence describing what this step should produce
  - "requires_approval": true only if this step performs an irreversible or
    write action (booking, cancelling, sending a message, modifying a record);
    false for read-only/informational steps

Steps with no shared dependencies should be independent (empty depends_on) so
they can run in parallel."""
