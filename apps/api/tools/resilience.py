"""@resilient(...) — retry + circuit breaker + fallback wrapper for tools.

Never raises to the graph: on exhausted retries with no fallback, returns a
dict with degraded=True and degradation_reason set instead of propagating
the exception. Per-tool fallback chains are declared in registry.py.
"""
import random
import threading
import time
from dataclasses import dataclass, field
from functools import wraps

from apps.api.tools import chaos

# Tools execute in worker threads (asyncio.to_thread), so they cannot await
# bus.emit directly. Retry/fallback notices are buffered here and drained by
# the calling graph node, which emits them on the event loop.
_event_buffer: list[dict] = []
_buffer_lock = threading.Lock()


def record_event(kind: str, payload: dict) -> None:
    with _buffer_lock:
        _event_buffer.append({"kind": kind, "payload": payload})


def drain_events() -> list[dict]:
    with _buffer_lock:
        drained, _event_buffer[:] = list(_event_buffer), []
    return drained


@dataclass
class _CircuitState:
    failures: list = field(default_factory=list)  # timestamps of recent failures
    open_until: float = 0.0


_circuits: dict[str, _CircuitState] = {}

FAILURE_WINDOW_S = 30
FAILURE_THRESHOLD = 3
OPEN_DURATION_S = 20


def reset_circuits() -> None:
    """Force every breaker closed.

    Breakers are module-level and stay open for OPEN_DURATION_S. Turning chaos
    off therefore does NOT restore service — the next call still short-circuits
    to a degraded response for up to 20 more seconds. That is correct for a
    real outage and wrong for a demo toggle: it made the run right after a
    chaos recording come out degraded, and would make a judge who switches
    chaos off see the failure persist with no explanation.
    """
    _circuits.clear()


def _circuit_for(name: str) -> _CircuitState:
    return _circuits.setdefault(name, _CircuitState())


def _circuit_is_open(name: str) -> bool:
    return time.time() < _circuit_for(name).open_until


def _record_failure(name: str):
    state = _circuit_for(name)
    now = time.time()
    state.failures = [t for t in state.failures if now - t < FAILURE_WINDOW_S] + [now]
    if len(state.failures) >= FAILURE_THRESHOLD:
        state.open_until = now + OPEN_DURATION_S


def _record_success(name: str):
    _circuit_for(name).failures = []


def resilient(name: str, service: str = "", retries: int = 2, backoff_base: float = 0.3,
              fallback=None, passthrough=()):
    """Wrap a sync tool function with retry -> circuit breaker -> fallback.

    Never raises to the graph EXCEPT for exception types listed in
    `passthrough`: domain refusals like SeatsUnavailable/PermissionDenied are
    correct answers, not infrastructure faults, so retrying them would be
    wrong and swallowing them into a degraded blob would hide a real "no".

    Emits tool.retry / tool.fallback through the thread-safe buffer above.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if _circuit_is_open(name):
                record_event("tool.fallback", {"tool": name, "from": "live", "to": "fallback",
                                                "reason": "circuit_open"})
                if fallback:
                    return fallback(*args, **kwargs)
                return {"degraded": True, "degradation_reason": f"{name}: circuit open, no fallback available"}

            last_error = None
            for attempt in range(retries + 1):
                try:
                    if service:
                        chaos.maybe_fail(service)
                    result = fn(*args, **kwargs)
                    _record_success(name)
                    return result
                except passthrough:
                    raise
                except TypeError as e:
                    # Bad call signature — deterministic, so retrying it just
                    # burns time on an error that cannot succeed.
                    last_error = e
                    _record_failure(name)
                    break
                except Exception as e:
                    last_error = e
                    _record_failure(name)
                    if attempt < retries:
                        record_event("tool.retry", {"tool": name, "attempt": attempt + 1, "error": str(e)})
                        time.sleep(backoff_base * (2 ** attempt) + random.uniform(0, 0.05))

            if fallback:
                record_event("tool.fallback", {"tool": name, "from": "live", "to": "fallback",
                                                "reason": str(last_error)})
                try:
                    return fallback(*args, **kwargs)
                except Exception as fe:
                    return {"degraded": True,
                            "degradation_reason": f"{name} and its fallback both failed: {fe}"}

            record_event("tool.fallback", {"tool": name, "from": "live", "to": "degraded",
                                            "reason": str(last_error)})
            return {"degraded": True, "degradation_reason": f"{name} is unavailable: {last_error}"}

        return wrapper
    return decorator
