"""Turning a refusal into the right exception."""

from __future__ import annotations

import pytest

from posthaste import (
    APIStatusError,
    AttachmentError,
    AuthenticationError,
    ConflictError,
    ContentBlockedError,
    DomainNotVerifiedError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    PosthasteError,
    QuotaExhausted,
    RateLimited,
    ScheduleError,
    ServerError,
    StreamError,
    SuppressedError,
    TemplateError,
    UnprocessableError,
)
from posthaste.errors import KNOWN_ERROR_TYPES, error_from_response, parse_retry_after


def envelope(error_type: str, **extra: object) -> str:
    import json

    body = {"type": error_type, "message": "refused"}
    body.update(extra)
    return json.dumps({"error": body})


@pytest.mark.parametrize(
    ("status", "error_type", "expected"),
    [
        (401, "unauthorized", AuthenticationError),
        (401, "unauthenticated", AuthenticationError),
        (403, "forbidden", PermissionDeniedError),
        (400, "invalid_request", InvalidRequestError),
        (404, "not_found", NotFoundError),
        (409, "conflict", ConflictError),
        (422, "suppressed", SuppressedError),
        (422, "domain_not_verified", DomainNotVerifiedError),
        (422, "content_blocked", ContentBlockedError),
        (422, "attachments_too_large", AttachmentError),
        (422, "schedule_too_far", ScheduleError),
        (422, "unknown_template", TemplateError),
        (422, "unknown_stream", StreamError),
        (429, "rate_limited", RateLimited),
        (429, "platform_paused", RateLimited),
        (429, "daily_limit_reached", QuotaExhausted),
        (429, "monthly_limit_reached", QuotaExhausted),
        (500, "internal", ServerError),
    ],
)
def test_maps_the_documented_types_onto_classes(
    status: int, error_type: str, expected: type
) -> None:
    error = error_from_response(status, envelope(error_type))
    assert isinstance(error, expected)
    assert isinstance(error, PosthasteError)
    assert error.type == error_type
    assert error.status == status


def test_every_type_it_maps_is_one_the_api_documents() -> None:
    """A mapping entry for a type nobody sends is a mapping nobody exercises."""
    from posthaste.errors import _BY_TYPE

    assert set(_BY_TYPE) <= KNOWN_ERROR_TYPES


def test_rate_limited_and_quota_exhausted_are_siblings_not_relatives() -> None:
    """The single most consequential line in this module.

    Both are 429. If one were a subclass of the other, an `except` clause
    written for the transient throttle would silently swallow the one that
    means "your allowance is gone until the calendar moves" — and vice versa,
    which is how a retry loop ends up hammering a wall for a month.
    """
    assert not issubclass(RateLimited, QuotaExhausted)
    assert not issubclass(QuotaExhausted, RateLimited)
    assert issubclass(RateLimited, APIStatusError)
    assert issubclass(QuotaExhausted, APIStatusError)


@pytest.mark.parametrize("error_type", ["rate_limited", "platform_paused"])
def test_is_rate_limited(error_type: str) -> None:
    error = error_from_response(429, envelope(error_type))
    assert error.is_rate_limited
    assert not error.is_quota_exhausted


@pytest.mark.parametrize("error_type", ["daily_limit_reached", "monthly_limit_reached"])
def test_is_quota_exhausted(error_type: str) -> None:
    error = error_from_response(429, envelope(error_type))
    assert error.is_quota_exhausted
    assert not error.is_rate_limited


def test_platform_paused_is_never_quota() -> None:
    """It is OUR ceiling, not the customer's.

    Telling somebody to upgrade for it would be wrong twice over: nothing about
    their account changes it, and it clears on its own.
    """
    error = error_from_response(429, envelope("platform_paused"))
    assert not error.is_quota_exhausted
    assert isinstance(error, RateLimited)


def test_an_unknown_type_still_arrives_as_a_posthaste_error() -> None:
    """The API is allowed to add refusal reasons. A caller pinned to an older
    SDK must still get a typed exception, not a KeyError."""
    error = error_from_response(422, envelope("something_invented_next_year"))
    assert isinstance(error, UnprocessableError)
    assert error.type == "something_invented_next_year"


def test_an_unknown_status_with_an_unknown_type() -> None:
    error = error_from_response(418, envelope("teapot"))
    assert type(error) is APIStatusError
    assert error.type == "teapot"


def test_a_fastify_default_500_does_not_crash_the_sdk() -> None:
    """`error` is a STRING on an unhandled 500.

    Reading `body["error"]["type"]` off that raises, which would turn the one
    response where a caller most needs a clear message into a crash inside the
    SDK.
    """
    raw = '{"statusCode":500,"error":"Internal Server Error","message":"boom"}'
    error = error_from_response(500, raw)
    assert isinstance(error, ServerError)
    assert error.type == "unknown_error"
    assert error.message == "boom"


def test_a_proxy_html_page_becomes_unknown_error() -> None:
    error = error_from_response(502, "<html><body>502 Bad Gateway</body></html>")
    assert error.type == "unknown_error"
    assert "502 Bad Gateway" in error.message


def test_an_empty_body_still_produces_a_message() -> None:
    error = error_from_response(503, "")
    assert error.type == "unknown_error"
    assert "503" in error.message
    assert error.body is None


def test_field_errors_are_parsed_when_present() -> None:
    error = error_from_response(
        400,
        envelope("invalid_request", fields=[{"path": "headers.x-thing", "message": "not allowed"}]),
    )
    assert [(f.path, f.message) for f in error.fields] == [("headers.x-thing", "not allowed")]


def test_fields_are_an_empty_tuple_when_absent_never_none_to_index_into() -> None:
    error = error_from_response(400, envelope("invalid_request"))
    assert error.fields == ()


def test_retry_after_in_the_body_beats_the_header() -> None:
    """Only the body value is specific to the refusal you actually got."""
    error = error_from_response(429, envelope("rate_limited", retryAfterSeconds=3), "600")
    assert error.retry_after_seconds == 3


def test_retry_after_falls_back_to_the_header() -> None:
    error = error_from_response(429, envelope("daily_limit_reached"), "43200")
    assert error.retry_after_seconds == 43200


def test_suppression_detail_is_typed_not_dug_out_by_hand() -> None:
    error = error_from_response(
        422, envelope("suppressed", address="bounced@example.com", reason="hard_bounce")
    )
    assert isinstance(error, SuppressedError)
    detail = error.suppression
    assert detail is not None
    assert detail.address == "bounced@example.com"
    assert detail.reason == "hard_bounce"


def test_suppression_is_none_on_an_older_deployment_without_the_fields() -> None:
    """Absent is the honest answer; a fabricated reason would be worse."""
    error = error_from_response(422, envelope("suppressed"))
    assert error.suppression is None


def test_suppression_is_none_on_every_other_error() -> None:
    assert error_from_response(429, envelope("rate_limited")).suppression is None


def test_content_blocked_carries_the_check_and_the_whole_report() -> None:
    error = error_from_response(
        422,
        envelope(
            "content_blocked",
            check="dangerous_link",
            findings=[
                {"check": "dangerous_link", "severity": "block", "message": "javascript: link"},
                {"check": "missing_text_part", "severity": "warn", "message": "no text part"},
            ],
        ),
    )
    assert isinstance(error, ContentBlockedError)
    assert error.check == "dangerous_link"
    # The warnings are there too, so one fix pass can address everything rather
    # than playing whack-a-mole one refusal at a time.
    assert len(error.findings) == 2


def test_str_leads_with_the_status_and_the_type() -> None:
    error = error_from_response(422, envelope("suppressed", message="Address is suppressed."))
    assert str(error) == "[422 suppressed] Address is suppressed."


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, None), ("", None), ("120", 120.0), ("  30  ", 30.0), ("not-a-date", None)],
)
def test_parse_retry_after(header: object, expected: object) -> None:
    assert parse_retry_after(header) == expected  # type: ignore[arg-type]


def test_parse_retry_after_accepts_an_http_date() -> None:
    # A proxy in front of the API may send a date rather than seconds.
    seconds = parse_retry_after("Wed, 21 Oct 2026 07:29:00 GMT", now=1792000000.0)
    assert seconds is not None and seconds >= 0
