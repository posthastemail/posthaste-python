"""The API key must not appear in anything a human or a log can read.

A key is a bearer credential: whoever holds it can send mail from every
verified domain on the account. The ways it escapes are not the ones people
guard — nobody logs the key on purpose. It escapes through a `repr()` pasted
into a ticket, through an exception message that quoted the request, and
through a traceback an error reporter shipped to a third party.

So this file checks the places a credential actually leaks from, rather than
checking that we remembered not to print it.
"""

from __future__ import annotations

import traceback

import pytest

from posthaste import APIConnectionError, PosthasteError, Posthaste
from posthaste._redaction import describe_key, redact
from stub_transport import SleepRecorder, StubTransport, error_response, json_response

KEY = "ph_live_example_key_5Kq2Wm8ZtR4bXn6VcLd1"


def api(transport: StubTransport) -> Posthaste:
    return Posthaste(KEY, transport=transport, sleep=SleepRecorder(), random=lambda: 1.0)


def test_the_client_repr_does_not_contain_the_key() -> None:
    text = repr(api(StubTransport()))
    assert KEY not in text
    # The environment prefix is not secret and is the one thing that helps
    # somebody staring at a 401 realise they pasted the test key into
    # production.
    assert "ph_live_" in text


def test_the_http_client_repr_does_not_contain_the_key() -> None:
    assert KEY not in repr(api(StubTransport())._http)


def test_no_attribute_on_the_client_holds_the_key_in_plain_sight() -> None:
    """`vars()` is what a debugger, a crash reporter and `pprint` all walk."""
    client = api(StubTransport())
    assert KEY not in repr(vars(client))


def test_describe_key_cannot_be_reversed() -> None:
    label = describe_key(KEY)
    assert KEY not in label
    # Not the usual "last four characters" convention: four characters of a
    # token this size is not enough to authenticate with, but it is enough to
    # confirm a guess.
    assert KEY[-4:] not in label


def test_an_api_error_message_never_carries_the_key() -> None:
    """Even when the server echoes it back — which a badly written 401 does."""
    transport = StubTransport(
        error_response(401, "unauthorized", f"The key {KEY} is not valid.")
    )
    with pytest.raises(PosthasteError) as caught:
        api(transport).account.me()
    assert KEY not in str(caught.value)
    assert KEY not in repr(caught.value)
    assert "***redacted***" in str(caught.value)


def test_a_transport_error_message_never_carries_the_key() -> None:
    """The message comes from a library underneath us and can quote the request."""
    transport = StubTransport(
        OSError(f"failed to send: Authorization: Bearer {KEY}"),
        OSError(f"failed to send: Authorization: Bearer {KEY}"),
        OSError(f"failed to send: Authorization: Bearer {KEY}"),
    )
    with pytest.raises(APIConnectionError) as caught:
        api(transport).account.me()
    assert KEY not in str(caught.value)


def test_a_formatted_traceback_never_carries_the_key() -> None:
    """The whole point: this is what an error reporter uploads."""
    transport = StubTransport(error_response(403, "forbidden", f"key {KEY} lacks emails:send"))
    try:
        api(transport).emails.send(
            from_="Acme <billing@acme.example>", to="customer@example.com", text="hi"
        )
    except PosthasteError as error:
        text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        assert KEY not in text
    else:  # pragma: no cover - the stub always refuses
        pytest.fail("expected the send to be refused")


def test_redaction_also_removes_a_key_that_is_not_ours() -> None:
    """A server that echoes ANOTHER account's key back is still a leak."""
    other = "ph_live_example_key_someoneelse999"
    assert other not in redact(f"conflict with {other}", KEY)


def test_the_key_still_reaches_the_server() -> None:
    """The redaction must not have broken authentication itself."""
    transport = StubTransport(json_response(200, {}))
    api(transport).account.me()
    assert transport.last.headers["authorization"] == f"Bearer {KEY}"
