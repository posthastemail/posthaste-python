"""Errors.

Every failure this SDK can produce is a `PosthasteError`, including the ones
that never touched the server — a socket that would not open, a request that
timed out. One `except` clause catches the lot.

Under that base there is a real exception hierarchy, because that is what a
Python caller reaches for: `except RateLimited` reads better than
`except PosthasteError as e: if e.type == 'rate_limited'`, and it is harder to
get wrong. The `type` string is still on every exception, so an exhaustive
`match err.type` remains possible and a refusal reason this SDK version has
never heard of still arrives as a `PosthasteError` rather than disappearing.

THE ONE DISTINCTION THAT MATTERS MOST is the split between `RateLimited` and
`QuotaExhausted`. Both arrive as HTTP 429 with a `Retry-After`, and the status
alone cannot tell them apart — which is exactly the trap that makes a naive
retry loop hammer a wall for the rest of the month. They are deliberately
SIBLINGS here, not parent and child: no `except` clause should ever catch one
while meaning the other.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Type

from ._redaction import redact

__all__ = [
    "KNOWN_ERROR_TYPES",
    "FieldError",
    "SuppressionInfo",
    "PosthasteError",
    "APIConnectionError",
    "APITimeoutError",
    "APIStatusError",
    "AuthenticationError",
    "PermissionDeniedError",
    "InvalidRequestError",
    "NotFoundError",
    "ConflictError",
    "UnprocessableError",
    "SuppressedError",
    "DomainNotVerifiedError",
    "ContentBlockedError",
    "AttachmentError",
    "ScheduleError",
    "TemplateError",
    "StreamError",
    "RateLimited",
    "QuotaExhausted",
    "ServerError",
    "error_from_response",
    "parse_retry_after",
]


# ---------------------------------------------------------------------------
# The error `type` vocabulary
# ---------------------------------------------------------------------------

#: Every `error.type` the API emits, plus the three this SDK synthesises.
#:
#: Kept as data rather than as a set of classes so a caller can check whether a
#: type is one this SDK version knows about. An unrecognised type is NOT an
#: error: the API is allowed to add refusal reasons, and one that arrives here
#: unknown becomes a plain `PosthasteError` (or the class its HTTP status
#: implies) instead of being mistaken for a documented one.
KNOWN_ERROR_TYPES = frozenset(
    {
        # Authentication and authorisation
        "unauthorized",
        "unauthenticated",
        "forbidden",
        "csrf_failed",
        "email_unverified",
        # Request shape
        "invalid_request",
        "not_found",
        "conflict",
        "address_taken",
        # Sending refusals — 422, well-formed but we will not act on it
        "batch_too_large",
        "fanout_too_large",
        "unknown_template",
        "invalid_template",
        "template_in_use",
        "unknown_stream",
        "reserved_slug",
        "slug_taken",
        "invalid_address",
        "domain_not_found",
        "domain_not_verified",
        "suppressed",
        # Quota and throttling — 429
        "rate_limited",
        "daily_limit_reached",
        "monthly_limit_reached",
        "platform_paused",
        "bulk_send_refused",
        # Attachments — permanent 422s
        "attachments_too_many",
        "attachments_too_large",
        "attachment_type_blocked",
        "attachment_invalid",
        # Scheduling
        "schedule_too_far",
        "not_scheduled",
        # The pre-send content lint
        "content_blocked",
        # Domains
        "domain_limit_reached",
        "domain_in_use",
        "token_required",
        "cloudflare_token_invalid",
        "cloudflare_zone_not_found",
        "cloudflare_write_failed",
        # Suppressions
        "suppression_protected",
        "suppression_platform",
        "suppression_hard_bounce",
        # Billing. Session-only, so an API-key caller never sees them.
        "not_configured",
        "provider_error",
        "already_subscribed",
        # Server-side
        "internal",
        # Synthesised by this SDK, never sent by the API.
        "unknown_error",
        "connection_error",
        "timeout",
    }
)


class FieldError:
    """One entry of `error.fields`, present on some — not all — 400s."""

    __slots__ = ("path", "message")

    def __init__(self, path: str, message: str) -> None:
        #: Dotted path into the request body, e.g. `headers.x-thing`.
        self.path = path
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FieldError(path={self.path!r}, message={self.message!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FieldError):
            return NotImplemented
        return (self.path, self.message) == (other.path, other.message)


class SuppressionInfo:
    """Which address was suppressed, and why."""

    __slots__ = ("address", "reason")

    def __init__(self, address: str, reason: str) -> None:
        self.address = address
        #: `hard_bounce` | `complaint` | `spam_trap` | `manual` | `unsubscribe`.
        #:
        #: The distinction is not cosmetic. A `complaint` or a `spam_trap` is
        #: permanent and must never be retried or cleared; a `hard_bounce` or a
        #: `manual` entry can legitimately go stale.
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SuppressionInfo(address={self.address!r}, reason={self.reason!r})"


# ---------------------------------------------------------------------------
# The hierarchy
# ---------------------------------------------------------------------------


class PosthasteError(Exception):
    """Base class for everything this SDK raises."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        type: str = "unknown_error",  # noqa: A002 - mirrors the API's field name
        fields: Optional[Sequence[FieldError]] = None,
        retry_after_seconds: Optional[float] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        #: The HTTP status. `0` when the request never got a response at all —
        #: a DNS failure, a refused connection, an abort. Checking
        #: `status >= 500` is therefore not a substitute for checking `type`.
        self.status = status
        #: The machine-readable refusal reason. Branch on this, not on `status`.
        self.type = type
        #: Field-level detail, when the server sent any. Present on the 400s
        #: produced by the shared body validator and ABSENT on the several 400s
        #: that are hand-written with a message only — always treat it as
        #: optional.
        self.fields: Tuple[FieldError, ...] = tuple(fields or ())
        #: How long to wait, in seconds, when the server said.
        self.retry_after_seconds = retry_after_seconds
        #: The parsed response body, exactly as it arrived. `None` when there
        #: was nothing parseable.
        self.body = body

    def __str__(self) -> str:
        return f"[{self.status} {self.type}] {self.message}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self)!r})"

    @property
    def is_quota_exhausted(self) -> bool:
        """True for the two refusals that mean "your allowance is spent"."""
        return self.type in ("daily_limit_reached", "monthly_limit_reached")

    @property
    def is_rate_limited(self) -> bool:
        """True for the short, transient throttles.

        The per-key request-rate limiter and the platform-wide daily send
        ceiling. Grouped because a caller treats them identically: honour
        `Retry-After` and come back.

        `platform_paused` must never be folded in with the quota refusals just
        because it is also a 429. It says the PLATFORM's daily total is full,
        not that this account has spent anything — nothing about the account
        changes it, upgrading does not clear it, and it frees as the day's
        total drains rather than on a calendar boundary. Telling a customer to
        upgrade for it would be wrong twice over.
        """
        return self.type in ("rate_limited", "platform_paused")

    @property
    def suppression(self) -> Optional[SuppressionInfo]:
        """The suppressed address and its reason — `None` on every other error."""
        if self.type != "suppressed":
            return None
        envelope = self.body.get("error") if isinstance(self.body, dict) else None
        if not isinstance(envelope, dict):
            return None
        address = envelope.get("address")
        reason = envelope.get("reason")
        if not isinstance(address, str) or not isinstance(reason, str):
            # An older API deployment that predates the structured fields.
            # Absent is the honest answer; a fabricated reason is worse.
            return None
        return SuppressionInfo(address, reason)


class APIConnectionError(PosthasteError):
    """The request never produced a response. `status` is 0."""


class APITimeoutError(APIConnectionError):
    """The request opened and the server never answered in time."""


class APIStatusError(PosthasteError):
    """The server answered, and the answer was a refusal."""


class AuthenticationError(APIStatusError):
    """401 — the key is missing, malformed, revoked, or from another account."""


class PermissionDeniedError(APIStatusError):
    """403 — a real key that does not hold the scope this call needs."""


class InvalidRequestError(APIStatusError):
    """400 — the request body or query is wrong. `fields` often says where."""


class NotFoundError(APIStatusError):
    """404 — no such id, on this account."""


class ConflictError(APIStatusError):
    """409 — it already exists. Frequently the correct answer to a retry."""


class UnprocessableError(APIStatusError):
    """422 — well formed, and we will not act on it.

    Permanent by construction: the same bytes get the same answer, so nothing
    under this class is ever retried automatically.
    """


class SuppressedError(UnprocessableError):
    """The recipient is on the suppression list. Read `.suppression`.

    Never work around this. Sending to a suppressed address is how a sending
    IP gets blocklisted, and the block lands on every other customer sharing
    it — which is why the API refuses rather than warns.
    """


class DomainNotVerifiedError(UnprocessableError):
    """The `from` domain has not had its DKIM record verified."""


class ContentBlockedError(UnprocessableError):
    """The pre-send lint refused the CONTENT."""

    @property
    def check(self) -> Optional[str]:
        """Which check refused it, e.g. `dangerous_link` or `empty_body`."""
        envelope = self.body.get("error") if isinstance(self.body, dict) else None
        if isinstance(envelope, dict):
            check = envelope.get("check")
            if isinstance(check, str):
                return check
        return None

    @property
    def findings(self) -> List[Dict[str, Any]]:
        """The whole lint report, warnings included, so one fix pass can
        address everything rather than playing whack-a-mole with one refusal
        at a time."""
        envelope = self.body.get("error") if isinstance(self.body, dict) else None
        if isinstance(envelope, dict) and isinstance(envelope.get("findings"), list):
            return list(envelope["findings"])
        return []


class AttachmentError(UnprocessableError):
    """Too many files, too many bytes, a blocked type, or unreadable content."""


class ScheduleError(UnprocessableError):
    """`scheduled_at` is out of range, or the message already left."""


class TemplateError(UnprocessableError):
    """The named template is unknown, invalid, or still in use."""


class StreamError(UnprocessableError):
    """The named message stream does not exist on this account."""


class RateLimited(APIStatusError):
    """A transient throttle. Honour `retry_after_seconds` and come back.

    Covers `rate_limited` (this key's request rate) and `platform_paused` (the
    platform's own daily send ceiling). Both clear on their own.
    """


class QuotaExhausted(APIStatusError):
    """The account's allowance is spent — `Retry-After` is hours or days.

    A SIBLING of `RateLimited`, never a subclass. Retrying this in process
    burns your own request budget on calls that are all going to be refused
    until the calendar moves. Queue it, or alert.
    """


class ServerError(APIStatusError):
    """5xx. Safe to retry, if the request is one that repeating cannot duplicate."""


# ---------------------------------------------------------------------------
# Mapping a refusal onto a class
# ---------------------------------------------------------------------------

# By TYPE first, because the type is the stable contract and the status is not
# specific enough. Only types that say more than their status does are listed;
# everything else falls through to `_BY_STATUS`, which is the honest answer for
# a refusal reason this SDK version has never seen.
_BY_TYPE: Dict[str, Type[PosthasteError]] = {
    "unauthorized": AuthenticationError,
    "unauthenticated": AuthenticationError,
    "csrf_failed": AuthenticationError,
    "email_unverified": AuthenticationError,
    "forbidden": PermissionDeniedError,
    "invalid_request": InvalidRequestError,
    "not_found": NotFoundError,
    "conflict": ConflictError,
    "address_taken": ConflictError,
    "suppressed": SuppressedError,
    "suppression_protected": UnprocessableError,
    "suppression_platform": UnprocessableError,
    "suppression_hard_bounce": UnprocessableError,
    "domain_not_verified": DomainNotVerifiedError,
    "content_blocked": ContentBlockedError,
    "attachments_too_many": AttachmentError,
    "attachments_too_large": AttachmentError,
    "attachment_type_blocked": AttachmentError,
    "attachment_invalid": AttachmentError,
    "schedule_too_far": ScheduleError,
    "not_scheduled": ScheduleError,
    "unknown_template": TemplateError,
    "invalid_template": TemplateError,
    "template_in_use": TemplateError,
    "unknown_stream": StreamError,
    # The four 429s. Two transient, two not, and nothing about the status says
    # which is which.
    "rate_limited": RateLimited,
    "platform_paused": RateLimited,
    "daily_limit_reached": QuotaExhausted,
    "monthly_limit_reached": QuotaExhausted,
    "internal": ServerError,
    # `connection_error` and `timeout` are deliberately ABSENT. They are
    # synthesised by the transport, which constructs the exception directly, so
    # an entry here would only ever fire on a response that carried one of
    # those strings as an HTTP-level refusal — and mapping a real 408 onto
    # `APIConnectionError` (status 0, "never reached the server") would be a
    # lie about what happened.
}

_BY_STATUS: Dict[int, Type[PosthasteError]] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableError,
    # An unrecognised 429 is treated as transient rather than as spent quota.
    # That direction is the safe one: a wrongly-retried throttle costs a few
    # seconds, while a wrongly-abandoned one drops mail that would have gone.
    429: RateLimited,
}


def _class_for(error_type: str, status: int) -> Type[PosthasteError]:
    known = _BY_TYPE.get(error_type)
    if known is not None:
        return known
    if status >= 500:
        return ServerError
    by_status = _BY_STATUS.get(status)
    if by_status is not None:
        return by_status
    return APIStatusError if status > 0 else PosthasteError


def error_from_response(
    status: int,
    raw_body: str,
    retry_after_header: Optional[str] = None,
    *,
    api_key: str = "",
) -> PosthasteError:
    """Turn a response body into the right exception.

    The API's own refusals use `{"error": {"type", "message", ...}}`. Two kinds
    of response do NOT, and both are parsed defensively here rather than
    assumed away:

      An UNHANDLED 500. There is no `setErrorHandler` on the API, so a thrown
      exception is serialised by Fastify's default handler as
      `{"statusCode", "error": "Internal Server Error", "message"}` — `error`
      is a STRING. Reading `body["error"]["type"]` off that raises, which would
      turn the one response where a caller most needs a clear message into a
      crash inside the SDK.

      Anything that never reached the application at all — a proxy's HTML error
      page, an empty body, a truncated response.

    Neither invents a `type`: both become `unknown_error`, so nothing
    downstream can mistake an unclassified failure for a documented one.
    """
    body: Any = None
    if raw_body:
        try:
            body = json.loads(raw_body)
        except ValueError:
            body = None

    envelope = body.get("error") if isinstance(body, dict) else None
    header_retry = parse_retry_after(retry_after_header)

    if isinstance(envelope, dict):
        error_type = envelope["type"] if isinstance(envelope.get("type"), str) else "unknown_error"
        raw_message = envelope.get("message")
        message = (
            raw_message
            if isinstance(raw_message, str) and raw_message
            else f"Posthaste request failed with status {status}"
        )

        fields: List[FieldError] = []
        for entry in envelope.get("fields") or ():
            if not isinstance(entry, Mapping):
                continue
            path = entry.get("path")
            detail = entry.get("message")
            fields.append(
                FieldError(
                    path if isinstance(path, str) else "",
                    detail if isinstance(detail, str) else "",
                )
            )

        # The rate limiter puts its wait INSIDE the error object; the quota
        # refusals put theirs in a Retry-After header. Prefer the body, because
        # only it is specific to this refusal.
        body_retry = envelope.get("retryAfterSeconds")
        retry_after = (
            float(body_retry)
            if isinstance(body_retry, (int, float)) and not isinstance(body_retry, bool)
            else header_retry
        )

        return _class_for(error_type, status)(
            redact(message, api_key),
            status=status,
            type=error_type,
            fields=fields,
            retry_after_seconds=retry_after,
            body=body,
        )

    # Not the envelope. `error` may be a string ("Internal Server Error"), or
    # there may be no body at all. Use whatever human-readable text exists.
    record = body if isinstance(body, dict) else {}
    stripped = raw_body.strip()
    message = (
        (record.get("message") if isinstance(record.get("message"), str) else None)
        or (envelope if isinstance(envelope, str) else None)
        or (stripped if 0 < len(stripped) <= 300 else None)
        or f"Posthaste request failed with status {status}"
    )

    return _class_for("unknown_error", status)(
        redact(message, api_key),
        status=status,
        type="unknown_error",
        retry_after_seconds=header_retry,
        body=body,
    )


def parse_retry_after(header: Optional[str], *, now: Optional[float] = None) -> Optional[float]:
    """`Retry-After` is either a number of seconds or an HTTP date.

    The API sends seconds; a proxy in front of it may not.
    """
    if not header:
        return None
    trimmed = header.strip()
    if trimmed.isdigit():
        return float(trimmed)

    from email.utils import parsedate_to_datetime  # stdlib, imported lazily

    try:
        at = parsedate_to_datetime(trimmed)
    except (TypeError, ValueError):
        return None
    if at is None:  # pragma: no cover - only on very old Pythons
        return None
    if at.tzinfo is None:
        from datetime import timezone

        at = at.replace(tzinfo=timezone.utc)

    import time as _time

    reference = _time.time() if now is None else now
    return max(0.0, at.timestamp() - reference)
