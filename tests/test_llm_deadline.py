import time

import pytest

from apps.api.llm import router


@pytest.mark.asyncio
async def test_async_llm_deadline_releases_the_graph(monkeypatch):
    """A blocking provider must become a controlled step failure, not a run
    that stays in Executing forever."""

    def blocked_provider(*_args, **_kwargs):
        time.sleep(0.15)
        return {"too": "late"}

    monkeypatch.setattr(router, "call_llm", blocked_provider)
    monkeypatch.setattr(router, "LLM_CALL_TIMEOUT_S", 0.02)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="execution deadline"):
        await router.call_llm_async("system", [{"role": "user", "content": "hello"}], True)

    assert time.monotonic() - started < 0.1
