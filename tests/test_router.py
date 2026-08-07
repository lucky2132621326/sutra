"""
LLM router tests: multi-key Groq rotation, client caching, and the JSON-mode
prompt guard. All exercised without network calls.
"""
import os

import pytest

from apps.api.llm import router


@pytest.fixture(autouse=True)
def clean_router_state(monkeypatch):
    """Isolate from the real .env — otherwise the developer's actual
    GROQ_API_KEY_4/_5 leak into tests that only declare keys 1-3, and the
    assertions silently describe the wrong world."""
    router._clients.clear()
    router._exhausted_groq_keys.clear()
    for name in list(os.environ):
        if name.startswith("GROQ_API_KEY"):
            monkeypatch.delenv(name, raising=False)
    yield
    router._clients.clear()
    router._exhausted_groq_keys.clear()


# --- Key discovery ---

def test_groq_keys_collects_numbered_keys_in_order(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "key-one")
    monkeypatch.setenv("GROQ_API_KEY_2", "key-two")
    monkeypatch.setenv("GROQ_API_KEY_3", "key-three")
    assert router.groq_keys() == ["key-one", "key-two", "key-three"]


def test_groq_keys_stops_at_first_gap(monkeypatch):
    """A blank placeholder line in .env must not hide later keys from view —
    it simply ends the sequence rather than erroring."""
    monkeypatch.setenv("GROQ_API_KEY", "key-one")
    monkeypatch.setenv("GROQ_API_KEY_2", "")
    monkeypatch.setenv("GROQ_API_KEY_3", "key-three")
    assert router.groq_keys() == ["key-one"]


def test_groq_keys_ignores_whitespace_only(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    assert router.groq_keys() == []


# --- Rotation on 429 ---

class _FakeGroq:
    """Stand-in Groq client. Keys listed in `limited` raise a 429-style error."""
    limited: set = set()
    calls: list = []

    def __init__(self, api_key, timeout=None, max_retries=None):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, outer):
            self.completions = _FakeGroq._Completions(outer)

    class _Completions:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            _FakeGroq.calls.append(self.outer.api_key)
            if self.outer.api_key in _FakeGroq.limited:
                raise Exception("Error code: 429 - rate_limit_exceeded for organization org_x")
            class R:
                choices = [type("C", (), {"message": type("M", (), {"content": '{"ok": true}'})()})()]
            return R()


@pytest.fixture
def fake_groq(monkeypatch):
    import groq
    _FakeGroq.limited = set()
    _FakeGroq.calls = []
    monkeypatch.setattr(groq, "Groq", _FakeGroq)
    return _FakeGroq


def test_rotates_past_a_rate_limited_key(monkeypatch, fake_groq):
    monkeypatch.setenv("GROQ_API_KEY", "dead-key")
    monkeypatch.setenv("GROQ_API_KEY_2", "live-key")
    fake_groq.limited = {"dead-key"}

    result = router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)

    assert result == {"ok": True}
    assert fake_groq.calls == ["dead-key", "live-key"], "should have failed over to the second key"
    assert "dead-key" in router._exhausted_groq_keys


def test_exhausted_key_is_skipped_on_the_next_call(monkeypatch, fake_groq):
    monkeypatch.setenv("GROQ_API_KEY", "dead-key")
    monkeypatch.setenv("GROQ_API_KEY_2", "live-key")
    fake_groq.limited = {"dead-key"}

    router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)
    fake_groq.calls.clear()
    router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)

    assert fake_groq.calls == ["live-key"], "must not re-test a known-exhausted key first"


def test_all_keys_limited_raises_clearly(monkeypatch, fake_groq):
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEY_2", "k2")
    fake_groq.limited = {"k1", "k2"}

    with pytest.raises(RuntimeError, match="all 2 Groq key"):
        router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)


def test_invalid_key_is_skipped_not_fatal(monkeypatch, fake_groq):
    """A wrong key pasted into a slot (e.g. an xAI `xai-` key in a Groq slot)
    401s. That must skip to the next key rather than stranding the valid keys
    behind it."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_good_one")
    monkeypatch.setenv("GROQ_API_KEY_2", "xai_wrong_product")
    monkeypatch.setenv("GROQ_API_KEY_3", "gsk_good_two")

    class Auth(_FakeGroq._Completions):
        def create(self, **kwargs):
            _FakeGroq.calls.append(self.outer.api_key)
            if self.outer.api_key == "xai_wrong_product":
                raise Exception("Error code: 401 - {'error': {'message': 'Invalid API Key'}}")
            if self.outer.api_key == "gsk_good_one":
                raise Exception("Error code: 429 - rate_limit_exceeded")
            class R:
                choices = [type("C", (), {"message": type("M", (), {"content": '{"ok": true}'})()})()]
            return R()

    monkeypatch.setattr(_FakeGroq._Chat, "__init__",
                        lambda self, outer: setattr(self, "completions", Auth(outer)))

    result = router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)
    assert result == {"ok": True}
    assert fake_groq.calls == ["gsk_good_one", "xai_wrong_product", "gsk_good_two"]


def test_non_quota_error_does_not_burn_other_keys(monkeypatch, fake_groq):
    """A malformed request fails the same way on every key — rotating would
    just multiply the same error."""
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEY_2", "k2")

    class Boom(_FakeGroq._Completions):
        def create(self, **kwargs):
            _FakeGroq.calls.append("boom")
            raise Exception("Error code: 400 - invalid request")

    monkeypatch.setattr(_FakeGroq._Chat, "__init__",
                        lambda self, outer: setattr(self, "completions", Boom(outer)))

    with pytest.raises(Exception, match="400"):
        router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)
    assert fake_groq.calls == ["boom"], "should not have tried the second key"


# --- Client caching (the 39x latency fix) ---

def test_clients_are_cached_per_key(monkeypatch, fake_groq):
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)

    router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)
    router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)

    groq_clients = [k for k in router._clients if k.startswith("groq:")]
    assert len(groq_clients) == 1, "a new client per call means a new TLS handshake per call"


def test_the_sdk_is_told_not_to_retry_internally(monkeypatch, fake_groq):
    """Key rotation is our retry strategy, and it must get the chance to run.

    The Groq SDK defaults to retrying a 429 while honouring `retry-after`, so
    a rate-limited key SLEEPS inside create() before we ever see the error.
    MEASURED: one planner call took 103s while the other 19 in the same run
    averaged 0.75s. Rotating to a fresh key answers in ~0.3s instead.
    """
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)

    router._call_groq("Respond with JSON.", [{"role": "user", "content": "hi"}], True)

    client = next(v for k, v in router._clients.items() if k.startswith("groq:"))
    assert client.max_retries == 0, (
        "the SDK will back off on a 429 before our own key rotation gets a turn"
    )


# --- IPv4 forcing (the 169s -> 0.8s fix) ---

def test_ipv4_forcing_is_active_and_filters_out_ipv6():
    """Broken IPv6 on this network cost 21s per address; Google publishes ~8
    AAAA records, so the first request burned ~169s working through them
    before falling back to IPv4. Resolution must return IPv4 only."""
    import socket

    assert getattr(socket, "_ipv4_forced", False), "IPv4 forcing was never applied"
    infos = socket.getaddrinfo("generativelanguage.googleapis.com", 443)
    families = {i[0] for i in infos}
    assert families == {socket.AF_INET}, f"expected IPv4 only, got {families}"


def test_ipv4_forcing_falls_back_for_ipv6_only_hosts(monkeypatch):
    """A host with no A record must still resolve rather than hard-fail."""
    import socket

    real = socket.getaddrinfo
    v6 = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))]

    def only_v6(host, port, family=0, *a, **kw):
        if family == socket.AF_INET:
            return []          # no IPv4 available
        return v6

    monkeypatch.setattr(socket, "getaddrinfo", only_v6)
    # Mirrors the wrapper's logic: prefer IPv4, but fall back rather than
    # returning an empty result that would surface as a resolution failure.
    results = only_v6("ipv6-only.example", 443, socket.AF_INET) or only_v6("ipv6-only.example", 443, 0)
    assert results == v6, "should fall back to the unrestricted lookup"
    socket.getaddrinfo = real


def test_allow_ipv6_escape_hatch_exists():
    """A network with working IPv6 must be able to opt out."""
    import inspect

    assert "ALLOW_IPV6" in inspect.getsource(router._force_ipv4)


# --- JSON-mode prompt guard ---

def test_json_mode_injects_the_word_json_when_absent(monkeypatch, fake_groq):
    """Groq 400s in json_object mode unless 'json' appears in the messages."""
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    captured = {}

    class Capture(_FakeGroq._Completions):
        def create(self, **kwargs):
            captured.update(kwargs)
            _FakeGroq.calls.append("x")
            class R:
                choices = [type("C", (), {"message": type("M", (), {"content": '{"ok": true}'})()})()]
            return R()

    monkeypatch.setattr(_FakeGroq._Chat, "__init__",
                        lambda self, outer: setattr(self, "completions", Capture(outer)))

    router._call_groq("Be terse.", [{"role": "user", "content": "hi"}], True)
    system_text = captured["messages"][0]["content"]
    assert "json" in system_text.lower()
