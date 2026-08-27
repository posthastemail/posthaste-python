"""The retry policy.

Every test here uses an injected `sleep`, so the suite asserts the backoff
rather than waiting it out.
"""

from __future__ import annotations

import pytest

from posthaste import (
    APIConnectionError,
    APITimeoutError,
    ConflictError,
    Posthaste,
    QuotaExhausted,
    RateLimited,
    ServerError,
)
from stub_transport import (
    SleepRecorder,
    StubTransport,
    empty_response,
    error_response,
    json_response,
)

KEY = "ph_test_example_key_0000"


def client(transport: StubTransport, sleeper: SleepRecorder, **kwargs: object) -> Posthaste:
    return Posthaste(
        KEY,
        transport=transport,
        sleep=sleeper,
        # Full jitter multiplied by 1.0 is the ceiling, which makes the backoff
        # deterministic without changing the code path being tested.
        random=lambda: 1.0,
        **kwargs,  # type: ignore[arg-type]
    )


def test_retries_a_500_on_a_read_and_succeeds() -> None:
    transport = StubTransport(
        error_response(500, "internal"),
        json_response(200, {"id": "acc_1"}),
    )
    sleeper = SleepRecorder()
    assert client(transport, sleeper).account.me() == {"id": "acc_1"}
    assert len(transport.requests) == 2
    assert sleeper.calls == [0.5]


def test_backoff_doubles_and_is_capped() -> None:
    transport = StubTransport(
        error_response(500, "internal"),
        error_response(500, "internal"),
        error_response(500, "internal"),
        json_response(200, {"ok": True}),
    )
    sleeper = SleepRecorder()
    client(transport, sleeper, max_retries=3).account.me()
    assert sleeper.calls == [0.5, 1.0, 2.0]


def test_gives_up_after_max_retries_and_raises_the_last_error() -> None:
    transport = StubTransport(*[error_response(500, "internal") for _ in range(3)])
    sleeper = SleepRecorder()
    with pytest.raises(ServerError) as caught:
        client(transport, sleeper).account.me()
    assert caught.value.status == 500
    assert len(transport.requests) == 3


def test_max_retries_zero_turns_retrying_off() -> None:
    transport = StubTransport(error_response(500, "internal"))
    sleeper = SleepRecorder()
    with pytest.raises(ServerError):
        client(transport, sleeper, max_retries=0).account.me()
    assert len(transport.requests) == 1
    assert sleeper.calls == []


def test_a_408_is_retried() -> None:
    transport = StubTransport(
        error_response(408, "request_timeout"), json_response(200, {"ok": True})
    )
    client(transport, SleepRecorder()).account.me()
    assert len(transport.requests) == 2


def test_a_permanent_4xx_is_never_retried() -> None:
    transport = StubTransport(error_response(409, "conflict"))
    sleeper = SleepRecorder()
    with pytest.raises(ConflictError):
        client(transport, sleeper).domains.create("acme.example")
    assert len(transport.requests) == 1
    assert sleeper.calls == []


def test_rate_limited_honours_retry_after_from_the_body() -> None:
    transport = StubTransport(
        error_response(429, "rate_limited", retryAfterSeconds=2),
        json_response(200, {"ok": True}),
    )
    sleeper = SleepRecorder()
    client(transport, sleeper).account.me()
    assert sleeper.calls == [2.0]


def test_platform_paused_is_retried_like_a_throttle_not_like_quota() -> None:
    transport = StubTransport(
        error_response(429, "platform_paused", headers={"retry-after": "5"}),
        json_response(200, {"ok": True}),
    )
    sleeper = SleepRecorder()
    client(transport, sleeper).account.me()
    assert sleeper.calls == [5.0]
    assert len(transport.requests) == 2


def test_quota_exhaustion_is_never_retried_in_process() -> None:
    """However short the Retry-After says the wait is.

    Queueing it, delaying it or alerting on it are all better than blocking a
    request handler until midnight.
    """
    transport = StubTransport(
        error_response(429, "daily_limit_reached", headers={"retry-after": "1"})
    )
    sleeper = SleepRecorder()
    with pytest.raises(QuotaExhausted) as caught:
        client(transport, sleeper).account.me()
    assert len(transport.requests) == 1
    assert sleeper.calls == []
    assert caught.value.retry_after_seconds == 1


def test_a_retry_after_longer_than_the_ceiling_comes_back_to_the_caller() -> None:
    """Sleeping through a several-minute wait inside a request handler looks
    exactly like a hang, so the error is raised with the wait attached instead."""
    transport = StubTransport(
        error_response(429, "platform_paused", headers={"retry-after": "600"})
    )
    sleeper = SleepRecorder()
    with pytest.raises(RateLimited) as caught:
        client(transport, sleeper).account.me()
    assert len(transport.requests) == 1
    assert caught.value.retry_after_seconds == 600
    assert caught.value.is_rate_limited


# ---------------------------------------------------------------------------
# Idempotency — the rule that makes retrying safe rather than merely automatic
# ---------------------------------------------------------------------------


def test_a_send_without_an_idempotency_key_is_never_repeated() -> None:
    """Without one, a retry after a lost response sends the email twice."""
    transport = StubTransport(error_response(500, "internal"))
    sleeper = SleepRecorder()
    with pytest.raises(ServerError):
        client(transport, sleeper).emails.send(
            from_="Acme <billing@acme.example>", to="customer@example.com", text="hi"
        )
    assert len(transport.requests) == 1


def test_a_send_with_an_idempotency_key_is_repeated() -> None:
    transport = StubTransport(
        error_response(500, "internal"),
        json_response(202, {"id": "msg_1", "status": "queued"}),
    )
    sleeper = SleepRecorder()
    result = client(transport, sleeper).emails.send(
        from_="Acme <billing@acme.example>",
        to="customer@example.com",
        text="hi",
        idempotency_key="receipt-1",
    )
    assert len(transport.requests) == 2
    assert result["id"] == "msg_1"


def test_creating_a_webhook_is_never_retried() -> None:
    """A duplicate endpoint would receive every event twice, for ever."""
    transport = StubTransport(error_response(500, "internal"))
    with pytest.raises(ServerError):
        client(transport, SleepRecorder()).webhooks.create("https://hooks.example.com/posthaste")
    assert len(transport.requests) == 1


def test_creating_a_stream_is_never_retried() -> None:
    transport = StubTransport(error_response(500, "internal"))
    with pytest.raises(ServerError):
        client(transport, SleepRecorder()).streams.create(slug="broadcast", name="Broadcast")
    assert len(transport.requests) == 1


def test_creating_a_domain_is_retried_because_a_duplicate_is_a_409() -> None:
    transport = StubTransport(error_response(500, "internal"), json_response(201, {"id": "dom_1"}))
    client(transport, SleepRecorder()).domains.create("acme.example")
    assert len(transport.requests) == 2


def test_creating_a_suppression_is_retried_because_it_is_an_upsert() -> None:
    transport = StubTransport(
        error_response(500, "internal"), json_response(201, {"address": "x@example.com"})
    )
    client(transport, SleepRecorder()).suppressions.create("x@example.com")
    assert len(transport.requests) == 2


def test_a_delete_is_retried() -> None:
    transport = StubTransport(error_response(500, "internal"), empty_response(204))
    client(transport, SleepRecorder()).webhooks.delete("whk_1")
    assert len(transport.requests) == 2


# ---------------------------------------------------------------------------
# Transport failures
# ---------------------------------------------------------------------------


def test_a_connection_failure_on_a_read_is_retried() -> None:
    transport = StubTransport(OSError("connection refused"), json_response(200, {"ok": True}))
    sleeper = SleepRecorder()
    client(transport, sleeper).account.me()
    assert len(transport.requests) == 2
    assert sleeper.calls == [0.5]


def test_a_connection_failure_becomes_a_typed_error_with_status_zero() -> None:
    transport = StubTransport(*[OSError("connection refused") for _ in range(3)])
    with pytest.raises(APIConnectionError) as caught:
        client(transport, SleepRecorder()).account.me()
    assert caught.value.status == 0
    assert caught.value.type == "connection_error"


def test_a_timeout_is_told_apart_from_a_refused_connection() -> None:
    """`status` is 0 for both, so only the type distinguishes them — and they
    mean different things: one never opened, the other opened and hung."""
    transport = StubTransport(TimeoutError("timed out"))
    with pytest.raises(APITimeoutError) as caught:
        client(transport, SleepRecorder(), max_retries=0).account.me()
    assert caught.value.type == "timeout"


def test_a_connection_failure_on_a_non_idempotent_send_is_not_retried() -> None:
    """The most dangerous retry there is: the request may well have arrived."""
    transport = StubTransport(OSError("connection reset"))
    with pytest.raises(APIConnectionError):
        client(transport, SleepRecorder()).emails.send(
            from_="Acme <billing@acme.example>", to="customer@example.com", text="hi"
        )
    assert len(transport.requests) == 1
