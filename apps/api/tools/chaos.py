"""
Fault injection — the "chaos button". Lets a demo deliberately break a
backing service at runtime (no restart) so the retry -> circuit breaker ->
fallback -> degraded-response path can be shown working on purpose.

Each tool is mapped to a logical "service" in registry.py; toggling a
service's mode affects every tool behind it.
"""
import random
import time

from apps.api.tools.exceptions import ServiceUnavailable

MODES = ("healthy", "slow", "error_500", "timeout", "flaky", "empty_response")

_state: dict[str, str] = {}


def set_mode(service: str, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown chaos mode {mode!r}; expected one of {MODES}")
    _state[service] = mode


def get_mode(service: str) -> str:
    return _state.get(service, "healthy")


def status() -> dict[str, str]:
    return dict(_state)


def reset() -> None:
    """Clear all injected faults AND close any breaker they tripped.

    Without the breaker reset, "chaos off" leaves the service failing for up to
    OPEN_DURATION_S more — the injected fault is gone but the protection
    against it is still engaged, which reads as the toggle not working.
    Imported lazily: resilience imports chaos, so a module-level import here
    would be circular.
    """
    _state.clear()
    from apps.api.tools.resilience import reset_circuits

    reset_circuits()


def maybe_fail(service: str) -> None:
    """Raise (or stall) according to the service's current chaos mode.
    Called at the top of the resilient wrapper, before the real tool runs."""
    mode = get_mode(service)
    if mode == "healthy":
        return
    if mode == "slow":
        time.sleep(4)
        return
    if mode == "error_500":
        raise ServiceUnavailable(f"{service}: injected HTTP 500")
    if mode == "timeout":
        raise ServiceUnavailable(f"{service}: injected timeout")
    if mode == "flaky" and random.random() < 0.5:
        raise ServiceUnavailable(f"{service}: injected flaky failure")
    if mode == "empty_response":
        raise ServiceUnavailable(f"{service}: injected empty response")
