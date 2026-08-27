"""The request the SDK actually puts on the wire, and the surface it exposes."""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qsl

import pytest

from posthaste import (
    InvalidRequestError,
    NotFoundError,
    Posthaste,
    RequestOptions,
    SuppressedError,
)
from posthaste.http import SDK_VERSION
from stub_transport import SleepRecorder, StubTransport, empty_response, error_response, json_response

KEY = "ph_test_example_key_0000"


def client(transport: StubTransport, **kwargs: object) -> Posthaste:
    return Posthaste(
        KEY, transport=transport, sleep=SleepRecorder(), random=lambda: 1.0, **kwargs  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_an_api_key_is_required() -> None:
    with pytest.raises(InvalidRequestError):
        Posthaste("")


def test_the_default_base_url_is_the_hosted_api() -> None:
    assert client(StubTransport()).base_url == "https://api.posthastemail.dev"


def test_a_self_hosted_base_url_is_honoured_and_trailing_slashes_stripped() -> None:
    transport = StubTransport(json_response(200, {"id": "acc_1"}))
    api = client(transport, base_url="https://mail.internal.example/api/")
    assert api.base_url == "https://mail.internal.example/api"
    api.account.me()
    assert transport.last.url == "https://mail.internal.example/api/v1/me"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def test_sends_the_key_as_a_bearer_token() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport).account.me()
    assert transport.last.headers["authorization"] == f"Bearer {KEY}"


def test_identifies_itself() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport).account.me()
    assert transport.last.headers["user-agent"] == f"posthaste-python/{SDK_VERSION}"


def test_a_custom_user_agent_is_appended_not_substituted() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport, user_agent="acme-billing/2.1").account.me()
    assert transport.last.headers["user-agent"] == f"posthaste-python/{SDK_VERSION} acme-billing/2.1"


def test_client_headers_are_sent_on_every_request() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport, headers={"X-Trace-Id": "abc"}).account.me()
    assert transport.last.headers["x-trace-id"] == "abc"


def test_per_call_headers_are_sent_too() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport).account.me(request_options=RequestOptions(headers={"X-Request-Id": "r1"}))
    assert transport.last.headers["x-request-id"] == "r1"


@pytest.mark.parametrize("spelling", ["authorization", "Authorization", "AUTHORIZATION"])
def test_nothing_can_override_the_authorization_header(spelling: str) -> None:
    """Not even by changing its case.

    Header names are case-insensitive on the wire, so a caller who supplies
    `Authorization` must not end up sending two of them and leaving which one
    wins to the server.
    """
    transport = StubTransport(json_response(200, {}))
    api = client(transport, headers={spelling: "Bearer someone-elses-key"})
    api.account.me()
    sent = [k for k in transport.last.headers if k.lower() == "authorization"]
    assert sent == ["authorization"]
    assert transport.last.headers["authorization"] == f"Bearer {KEY}"


def test_a_per_call_header_cannot_override_authorization_either() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport).account.me(
        request_options=RequestOptions(headers={"Authorization": "Bearer nope"})
    )
    assert transport.last.headers["authorization"] == f"Bearer {KEY}"


def test_a_per_call_timeout_reaches_the_transport() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport, timeout=30.0).account.me(request_options=RequestOptions(timeout=2.0))
    assert transport.last.timeout == 2.0


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def test_unset_filters_are_omitted() -> None:
    transport = StubTransport(json_response(200, {"data": [], "nextCursor": None, "hasMore": False}))
    client(transport).messages.list(limit=10)
    assert dict(parse_qsl(transport.last.query)) == {"limit": "10"}


def test_a_repeated_parameter_is_repeated_not_joined() -> None:
    """`tag=welcome,billing` would be read as ONE filter whose value contains a
    comma, and would silently match nothing."""
    transport = StubTransport(json_response(200, {"data": [], "nextCursor": None, "hasMore": False}))
    client(transport).messages.list(tag=["welcome", "billing"])
    assert parse_qsl(transport.last.query) == [("tag", "welcome"), ("tag", "billing")]


def test_booleans_are_sent_as_json_booleans_not_python_ones() -> None:
    """`str(True)` is "True", which no API parses as a boolean."""
    transport = StubTransport(json_response(200, {}))
    client(transport)._http.request("GET", "/v1/x", query={"flag": True}, idempotent=True)
    assert dict(parse_qsl(transport.last.query)) == {"flag": "true"}


def test_from_is_spelled_from_underscore_in_python_and_from_on_the_wire() -> None:
    transport = StubTransport(json_response(200, {"data": [], "nextCursor": None, "hasMore": False}))
    client(transport).messages.list(from_="2026-08-01T00:00:00Z")
    assert dict(parse_qsl(transport.last.query))["from"] == "2026-08-01T00:00:00Z"


def test_values_are_percent_encoded_with_spaces_as_percent_twenty() -> None:
    transport = StubTransport(json_response(200, {"data": [], "nextCursor": None, "hasMore": False}))
    client(transport).messages.list(search="order 42+")
    assert "search=order%2042%2B" in transport.last.query


def test_a_path_segment_is_encoded() -> None:
    """A suppression is addressed by ADDRESS, and a local part may contain a
    slash. Leaving it raw would route the request somewhere else entirely."""
    transport = StubTransport(empty_response(204))
    client(transport).suppressions.delete("odd/name@example.com")
    assert transport.last.path.endswith("/v1/suppressions/odd%2Fname%40example.com")


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_send_maps_python_names_onto_the_wire_names() -> None:
    transport = StubTransport(json_response(202, {"id": "msg_1", "status": "queued"}))
    client(transport).emails.send(
        from_="Acme <billing@acme.example>",
        to="customer@example.com",
        subject="Your receipt",
        html="<p>Thanks.</p>",
        reply_to="support@acme.example",
        list_unsubscribe="<https://acme.example/u/1>",
        idempotency_key="receipt-42",
        scheduled_at="2026-08-27T09:00:00Z",
        template_version=3,
    )
    body = transport.last.json
    assert body["from"] == "Acme <billing@acme.example>"
    assert body["replyTo"] == "support@acme.example"
    assert body["listUnsubscribe"] == "<https://acme.example/u/1>"
    assert body["idempotencyKey"] == "receipt-42"
    assert body["scheduledAt"] == "2026-08-27T09:00:00Z"
    assert body["templateVersion"] == 3


def test_idempotency_is_a_body_field_and_never_a_header() -> None:
    """The `Idempotency-Key` header is CORS-allowlisted but no handler reads it,
    so a client that sends the header and not the field gets no idempotency at
    all and no warning that it has none."""
    transport = StubTransport(json_response(202, {"id": "msg_1", "status": "queued"}))
    client(transport).emails.send(
        from_="a@acme.example", to="customer@example.com", text="hi", idempotency_key="k"
    )
    assert "idempotency-key" not in {k.lower() for k in transport.last.headers}
    assert transport.last.json["idempotencyKey"] == "k"


def test_unset_send_fields_are_not_sent_as_null() -> None:
    transport = StubTransport(json_response(202, {"id": "msg_1", "status": "queued"}))
    client(transport).emails.send(from_="a@acme.example", to="customer@example.com", text="hi")
    assert set(transport.last.json) == {"from", "to", "text"}


def test_a_queued_send_is_not_a_duplicate() -> None:
    transport = StubTransport(json_response(202, {"id": "msg_1", "status": "queued"}))
    result = client(transport).emails.send(
        from_="a@acme.example", to="customer@example.com", text="hi"
    )
    assert result["status"] == "queued"
    assert result["duplicate"] is False


def test_an_idempotency_replay_is_surfaced_rather_than_hidden() -> None:
    """A caller that bills, logs or counts per send needs to know which of the
    two successes happened, and the HTTP status is not something they see."""
    transport = StubTransport(json_response(200, {"id": "msg_1", "status": "duplicate"}))
    result = client(transport).emails.send(
        from_="a@acme.example", to="customer@example.com", text="hi", idempotency_key="k"
    )
    assert result["duplicate"] is True


def test_duplicate_is_read_from_the_body_not_from_the_status() -> None:
    """The body survives a proxy that normalises a 202 to a 200."""
    transport = StubTransport(json_response(200, {"id": "msg_1", "status": "queued"}))
    result = client(transport).emails.send(
        from_="a@acme.example", to="customer@example.com", text="hi", idempotency_key="k"
    )
    assert result["duplicate"] is False


def test_attachment_bytes_are_base64_encoded() -> None:
    transport = StubTransport(json_response(202, {"id": "msg_1", "status": "queued"}))
    client(transport).emails.send(
        from_="a@acme.example",
        to="customer@example.com",
        text="hi",
        attachments=[
            {"filename": "receipt.pdf", "content_type": "application/pdf", "content": b"%PDF-1.4"}
        ],
    )
    attachment = transport.last.json["attachments"][0]
    assert attachment["contentType"] == "application/pdf"
    assert base64.b64decode(attachment["content"]) == b"%PDF-1.4"


def test_a_string_attachment_is_assumed_to_be_base64_already() -> None:
    """Encoding it again would corrupt the file on arrival, silently."""
    already = base64.b64encode(b"%PDF-1.4").decode("ascii")
    transport = StubTransport(json_response(202, {"id": "msg_1", "status": "queued"}))
    client(transport).emails.send(
        from_="a@acme.example",
        to="customer@example.com",
        text="hi",
        attachments=[
            {"filename": "r.pdf", "content_type": "application/pdf", "content": already}
        ],
    )
    assert transport.last.json["attachments"][0]["content"] == already


def test_a_suppressed_recipient_raises_a_typed_error_carrying_the_reason() -> None:
    transport = StubTransport(
        error_response(
            422,
            "suppressed",
            "Address is suppressed.",
            address="bounced@example.com",
            reason="hard_bounce",
        )
    )
    with pytest.raises(SuppressedError) as caught:
        client(transport).emails.send(
            from_="a@acme.example", to="bounced@example.com", text="hi"
        )
    detail = caught.value.suppression
    assert detail is not None and detail.reason == "hard_bounce"


def test_batch_accepts_either_spelling_of_from() -> None:
    transport = StubTransport(json_response(202, {"accepted": 2, "rejected": 0, "data": []}))
    client(transport).emails.send_batch(
        [
            {"from_": "a@acme.example", "to": "one@example.com", "text": "hi"},
            {"from": "a@acme.example", "to": "two@example.com", "text": "hi"},
        ]
    )
    messages = transport.last.json["messages"]
    assert [m["from"] for m in messages] == ["a@acme.example", "a@acme.example"]


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def test_a_204_becomes_none_rather_than_a_json_parse_failure() -> None:
    transport = StubTransport(empty_response(204))
    assert client(transport).webhooks.delete("whk_1") is None


def test_a_404_is_typed() -> None:
    transport = StubTransport(error_response(404, "not_found", "No such message."))
    with pytest.raises(NotFoundError):
        client(transport).messages.get("msg_missing")


def test_a_redirect_is_reported_rather_than_followed() -> None:
    """Following it would re-send the bearer token to whatever host the
    `Location` names, which is a credential handed to a stranger."""
    transport = StubTransport(
        empty_response(302),
        empty_response(302),
        empty_response(302),
    )
    with pytest.raises(Exception) as caught:
        client(transport).account.me()
    assert getattr(caught.value, "status", None) == 302


def test_attachment_download_returns_bytes_and_the_filename() -> None:
    from posthaste.http import HttpResponse

    transport = StubTransport(
        HttpResponse(
            200,
            {
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="receipt.pdf"',
            },
            b"%PDF-1.4",
        )
    )
    got = client(transport).messages.download_attachment("msg_1", "att_1")
    assert got["content"] == b"%PDF-1.4"
    assert got["contentType"] == "application/pdf"
    assert got["filename"] == "receipt.pdf"


def test_attachment_download_decodes_an_rfc5987_filename() -> None:
    from posthaste.http import HttpResponse

    transport = StubTransport(
        HttpResponse(
            200,
            {"content-disposition": "attachment; filename*=UTF-8''re%C3%A7u.pdf"},
            b"x",
        )
    )
    assert client(transport).messages.download_attachment("m", "a")["filename"] == "reçu.pdf"


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------


def test_every_resource_the_typescript_sdk_exposes_is_here() -> None:
    api = client(StubTransport())
    for name in (
        "account",
        "domains",
        "emails",
        "messages",
        "suppressions",
        "webhooks",
        "api_keys",
        "billing",
        "streams",
        "templates",
    ):
        assert hasattr(api, name), name


@pytest.mark.parametrize(
    ("call", "method", "path"),
    [
        (lambda a: a.account.me(), "GET", "/v1/me"),
        (lambda a: a.account.verify(), "GET", "/v1/account/verify"),
        (lambda a: a.account.usage(), "GET", "/v1/usage"),
        (lambda a: a.domains.create("acme.example"), "POST", "/v1/domains"),
        (lambda a: a.domains.verify("dom_1"), "POST", "/v1/domains/dom_1/verify"),
        (lambda a: a.domains.setup("dom_1"), "GET", "/v1/domains/dom_1/setup"),
        (lambda a: a.domains.delete("dom_1"), "DELETE", "/v1/domains/dom_1"),
        (
            lambda a: a.domains.connect_cloudflare("dom_1", token="t"),
            "POST",
            "/v1/domains/dom_1/cloudflare",
        ),
        (lambda a: a.domains.disconnect_cloudflare(), "DELETE", "/v1/account/cloudflare"),
        (lambda a: a.emails.cancel_schedule("msg_1"), "DELETE", "/v1/emails/msg_1/schedule"),
        (lambda a: a.messages.get("msg_1"), "GET", "/v1/messages/msg_1"),
        (lambda a: a.messages.stats(days=7), "GET", "/v1/stats/messages"),
        (lambda a: a.suppressions.create("x@example.com"), "POST", "/v1/suppressions"),
        (lambda a: a.suppressions.delete("x@example.com"), "DELETE", "/v1/suppressions/x%40example.com"),
        (lambda a: a.webhooks.create("https://hooks.example.com/p"), "POST", "/v1/webhooks"),
        (lambda a: a.webhooks.delete("whk_1"), "DELETE", "/v1/webhooks/whk_1"),
        (lambda a: a.api_keys.list(), "GET", "/v1/api-keys"),
        (lambda a: a.billing.get(), "GET", "/v1/billing"),
        (lambda a: a.billing.invoice("inv_1"), "GET", "/v1/billing/invoices/inv_1"),
        (lambda a: a.streams.list(), "GET", "/v1/streams"),
        (lambda a: a.streams.create(slug="s", name="S"), "POST", "/v1/streams"),
        (lambda a: a.templates.list(), "GET", "/v1/templates"),
        (lambda a: a.templates.get("tpl_1"), "GET", "/v1/templates/tpl_1"),
        (lambda a: a.templates.versions("tpl_1"), "GET", "/v1/templates/tpl_1/versions"),
        (lambda a: a.templates.preview("tpl_1"), "POST", "/v1/templates/tpl_1/preview"),
        (lambda a: a.templates.create(slug="s", name="S"), "POST", "/v1/templates"),
        (lambda a: a.templates.update("tpl_1", name="S"), "PATCH", "/v1/templates/tpl_1"),
        (lambda a: a.templates.delete("tpl_1"), "DELETE", "/v1/templates/tpl_1"),
    ],
)
def test_each_method_hits_the_endpoint_it_claims_to(call, method: str, path: str) -> None:
    transport = StubTransport(json_response(200, {"data": []}))
    call(client(transport))
    assert transport.last.method == method
    assert transport.last.path == f"https://api.posthastemail.dev{path}"


def test_send_posts_to_v1_emails() -> None:
    transport = StubTransport(json_response(202, {"id": "msg_1", "status": "queued"}))
    client(transport).emails.send(from_="a@acme.example", to="customer@example.com", text="hi")
    assert transport.last.method == "POST"
    assert transport.last.path == "https://api.posthastemail.dev/v1/emails"


def test_send_batch_posts_to_v1_emails_batch() -> None:
    transport = StubTransport(json_response(202, {"accepted": 0, "rejected": 0, "data": []}))
    client(transport).emails.send_batch([{"from_": "a@acme.example", "to": "x@example.com"}])
    assert transport.last.path == "https://api.posthastemail.dev/v1/emails/batch"


def test_the_json_body_is_sent_with_a_json_content_type() -> None:
    transport = StubTransport(json_response(202, {"id": "m", "status": "queued"}))
    client(transport).emails.send(from_="a@acme.example", to="x@example.com", text="hi")
    assert transport.last.headers["content-type"] == "application/json"
    assert json.loads(transport.last.body.decode())["to"] == "x@example.com"


def test_a_get_carries_no_body() -> None:
    transport = StubTransport(json_response(200, {}))
    client(transport).account.me()
    assert transport.last.body is None
    assert "content-type" not in transport.last.headers
