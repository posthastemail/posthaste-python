"""The Verify resource, at the wire.

Two properties matter more than the URLs, and both are about RETRIES — this
client retries automatically, so "is this request safe to repeat" is a decision
the SDK makes on the caller's behalf and can be wrong about:

  A CHECK MUST NEVER BE RETRIED. It consumes an attempt whether or not the
  response arrives, so an automatic retry spends somebody's guesses for them —
  a flaky connection would close a verification the person never got wrong.

  A START IS RETRIED ONLY WITH AN IDEMPOTENCY KEY, because a second code
  SUPERSEDES the first: the retry would invalidate the code the user is at that
  moment typing in, and nothing anywhere would report an error.
"""

from __future__ import annotations

import pytest

from posthaste import Posthaste
from posthaste.errors import ConflictError, PosthasteError
from stub_transport import SleepRecorder, StubTransport, json_response

KEY = "ph_test_example_key_0000"


def client(transport: StubTransport) -> Posthaste:
    return Posthaste(
        KEY, transport=transport, sleep=SleepRecorder(), random=lambda: 1.0  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------


def test_start_posts_the_address_and_the_sender() -> None:
    transport = StubTransport(json_response(201, {"id": "ver_x", "status": "pending"}))
    client(transport).verifications.start(to="user@example.test", from_="security@acme.test")

    request = transport.requests[0]
    assert request.method == "POST"
    assert request.path.endswith("/v1/verifications")
    assert request.json == {"to": "user@example.test", "from": "security@acme.test"}


def test_start_renames_from_underscore_to_from() -> None:
    # `from` is a Python keyword, so the parameter carries the underscore and
    # the wire does not. A client that sent `from_` would be rejected by the API
    # with a validation error naming a field the caller never typed.
    transport = StubTransport(json_response(201, {"id": "ver_x"}))
    client(transport).verifications.start(to="user@example.test", from_="security@acme.test")
    assert "from_" not in transport.requests[0].json


def test_start_is_not_retried_without_an_idempotency_key() -> None:
    """The failure this prevents.

    A 500 arrives, the SDK helpfully retries, and the second start supersedes
    the first code — so the user is typing a code our own retry has just killed,
    and nothing reports an error anywhere.
    """
    transport = StubTransport(
        json_response(500, {"error": {"type": "internal", "message": "boom"}}),
        json_response(201, {"id": "ver_x"}),
    )
    with pytest.raises(PosthasteError):
        client(transport).verifications.start(to="user@example.test", from_="a@acme.test")

    assert len(transport.requests) == 1


def test_start_is_retried_when_the_caller_made_it_safe() -> None:
    transport = StubTransport(
        json_response(500, {"error": {"type": "internal", "message": "boom"}}),
        json_response(201, {"id": "ver_x"}),
    )
    started = client(transport).verifications.start(
        to="user@example.test", from_="a@acme.test", idempotency_key="signup-42"
    )

    assert started["id"] == "ver_x"
    assert len(transport.requests) == 2


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


def test_check_sends_the_code_to_the_id_it_belongs_to() -> None:
    transport = StubTransport(json_response(200, {"id": "ver_x", "status": "approved"}))
    client(transport).verifications.check("ver_x", "012345")

    request = transport.requests[0]
    assert request.path.endswith("/v1/verifications/ver_x/check")
    assert request.json == {"code": "012345"}


def test_check_keeps_a_leading_zero() -> None:
    # As an int, `012345` becomes `12345`, the digest stops matching, and one
    # caller in ten can never verify anybody — while every test written with a
    # code starting 1-9 passes.
    transport = StubTransport(json_response(200, {"id": "ver_x", "status": "pending"}))
    client(transport).verifications.check("ver_x", "012345")
    assert transport.requests[0].json["code"] == "012345"


def test_a_wrong_code_returns_rather_than_raises() -> None:
    """The contract this SDK exists to keep pleasant.

    A wrong code is a 200 with `pending`, so the most common outcome in the
    product does not arrive as an exception and does not force the happy path
    into an `except`.
    """
    transport = StubTransport(
        json_response(200, {"id": "ver_x", "status": "pending", "attemptsRemaining": 4})
    )
    result = client(transport).verifications.check("ver_x", "000000")

    assert result["status"] == "pending"
    assert result["attemptsRemaining"] == 4


def test_a_terminal_409_does_raise() -> None:
    transport = StubTransport(
        json_response(
            409, {"error": {"type": "verification_max_attempts", "message": "closed"}}
        )
    )
    with pytest.raises(ConflictError):
        client(transport).verifications.check("ver_x", "000000")


def test_a_check_is_never_retried() -> None:
    """The sharpest of the three.

    An attempt is consumed server-side whether or not the response reaches us.
    """
    transport = StubTransport(
        json_response(500, {"error": {"type": "internal", "message": "boom"}}),
        json_response(200, {"id": "ver_x", "status": "approved"}),
    )
    with pytest.raises(PosthasteError):
        client(transport).verifications.check("ver_x", "318204")

    assert len(transport.requests) == 1


# ---------------------------------------------------------------------------
# Resend, cancel, read
# ---------------------------------------------------------------------------


def test_resend_requires_and_sends_the_address() -> None:
    transport = StubTransport(json_response(200, {"id": "ver_x"}))
    client(transport).verifications.resend("ver_x", "user@example.test")

    assert transport.requests[0].path.endswith("/v1/verifications/ver_x/resend")
    assert transport.requests[0].json == {"to": "user@example.test"}


def test_cancel_is_retried_because_cancelling_twice_is_fine() -> None:
    transport = StubTransport(
        json_response(500, {"error": {"type": "internal", "message": "boom"}}),
        json_response(200, {"id": "ver_x", "status": "canceled"}),
    )
    out = client(transport).verifications.cancel("ver_x")

    assert out["status"] == "canceled"
    assert len(transport.requests) == 2


def test_an_id_is_escaped_rather_than_pasted_into_the_path() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport).verifications.get("ver_x/../../admin")
    assert transport.requests[0].path.endswith("/v1/verifications/ver_x%2F..%2F..%2Fadmin")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_are_put() -> None:
    transport = StubTransport(json_response(200, {"brandName": "Acme", "logoUrl": None}))
    client(transport).verifications.update_settings(brand_name="Acme")

    assert transport.requests[0].method == "PUT"
    assert transport.requests[0].json == {"brandName": "Acme"}


def test_an_omitted_field_is_left_alone_and_none_clears_it() -> None:
    """Why the defaults are a sentinel rather than None.

    Collapsing the two would make "remove my logo" inexpressible, and would mean
    a customer fixing a typo in their name silently dropped their logo.
    """
    transport = StubTransport(json_response(200, {}), json_response(200, {}))
    verifications = client(transport).verifications

    verifications.update_settings(brand_name="Acme")
    assert transport.requests[0].json == {"brandName": "Acme"}

    verifications.update_settings(logo=None)
    assert transport.requests[1].json == {"logo": None}


# ---------------------------------------------------------------------------
# Address checks
# ---------------------------------------------------------------------------


def test_one_address_is_posted_not_put_in_a_query_string() -> None:
    # An address in a URL is personal data in every access log between the
    # client and us — theirs, ours, and any proxy in between.
    transport = StubTransport(json_response(200, {"address": "sam@example.test"}))
    client(transport).addresses.check("sam@example.test")

    request = transport.requests[0]
    assert request.method == "POST"
    assert request.path.endswith("/v1/address-checks")
    assert request.json == {"address": "sam@example.test"}


def test_a_batch_is_one_request_which_is_what_makes_the_dedupe_possible() -> None:
    transport = StubTransport(json_response(200, {"data": [], "summary": {}}))
    client(transport).addresses.check_many(["a@example.test", "b@example.test"])

    assert len(transport.requests) == 1
    assert transport.requests[0].json == {"addresses": ["a@example.test", "b@example.test"]}


def test_an_address_check_is_retried_because_it_changes_nothing() -> None:
    transport = StubTransport(
        json_response(500, {"error": {"type": "internal", "message": "boom"}}),
        json_response(200, {"address": "sam@example.test", "verdict": "deliverable"}),
    )
    result = client(transport).addresses.check("sam@example.test")

    assert result["verdict"] == "deliverable"
    assert len(transport.requests) == 2
