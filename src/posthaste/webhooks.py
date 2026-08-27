"""Webhook signature verification.

A webhook endpoint is a URL on the public internet. Anyone who learns it can
POST to it, so without a signature you cannot tell our delivery from a forgery
— and a forged `bounced` event would make you suppress an address that is
perfectly fine, while a forged `delivered` would hide a failure.

The scheme:

    posthaste-signature: t=<unix seconds>,v1=<hex>
    signed payload      = f"{t}.{raw_body}"
    signature           = HMAC-SHA256(secret, signed payload), lower-case hex

Signing the body alone would be replayable for ever. Binding the timestamp is
what lets a receiver reject something stale, and `v1=` is a version tag so the
scheme can change without breaking every receiver on the same day.

This is a REIMPLEMENTATION of `packages/core/src/webhook-signature.ts`, not a
port that trusts itself. The test suite verifies signatures produced by the
real TypeScript signer, checked in as fixtures, so the two cannot drift apart
silently — a customer verifying a genuine delivery is exactly the case that a
test signing with this same file would fail to cover.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, Optional, Union

__all__ = [
    "SIGNATURE_HEADER",
    "DELIVERY_ID_HEADER",
    "ATTEMPT_HEADER",
    "DEFAULT_TOLERANCE_SECONDS",
    "WebhookVerifyResult",
    "verify_webhook",
    "parse_webhook_event",
]

#: The header the signature arrives in. Lower-cased; HTTP header names are not
#: case-sensitive.
SIGNATURE_HEADER = "posthaste-signature"

#: Stable across every retry of the same event. Deduplicate on it.
DELIVERY_ID_HEADER = "posthaste-delivery-id"

#: Which attempt this is, starting at 1.
ATTEMPT_HEADER = "posthaste-attempt"

_SCHEME_VERSION = "v1"
DEFAULT_TOLERANCE_SECONDS = 300

_DIGITS = re.compile(r"^\d+$")


class WebhookVerifyResult:
    """The answer, with the reason attached when it is "no".

    Truthy when valid, so the natural spelling works::

        if not verify_webhook(raw, header, secret):
            return Response(status=400)

    and the reason is there when you want to log it. A valid result carries no
    reason at all, so there is nothing to accidentally log on the happy path.
    """

    __slots__ = ("valid", "reason")

    #: One of `malformed_header`, `unsupported_version`, `timestamp_too_old`,
    #: `timestamp_in_future`, `signature_mismatch` — or `None` when valid.
    reason: Optional[str]

    def __init__(self, valid: bool, reason: Optional[str] = None) -> None:
        self.valid = valid
        self.reason = reason

    def __bool__(self) -> bool:
        return self.valid

    def __repr__(self) -> str:
        if self.valid:
            return "WebhookVerifyResult(valid=True)"
        return f"WebhookVerifyResult(valid=False, reason={self.reason!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WebhookVerifyResult):
            return NotImplemented
        return (self.valid, self.reason) == (other.valid, other.reason)


def _fail(reason: str) -> WebhookVerifyResult:
    return WebhookVerifyResult(False, reason)


def verify_webhook(
    raw_body: Union[str, bytes, bytearray],
    signature_header: Optional[str],
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now_seconds: Optional[int] = None,
) -> WebhookVerifyResult:
    """Verify a webhook delivery.

    `raw_body` MUST be the exact bytes we sent — `request.get_data()` in Flask,
    `await request.body()` in FastAPI, `request.body` in Django. A
    parsed-then-re-serialised object will NEVER verify: `json.dumps` is free to
    reorder keys, change separators and re-escape strings, and the signature
    covers the bytes, not the object they decode to. This is the single most
    common reason verification "mysteriously" fails, and no amount of correct
    key handling rescues it.

    :param raw_body: the request body, as bytes or as the exact string they
        decode to.
    :param signature_header: the `posthaste-signature` header value.
    :param secret: the `signingSecret` returned once when the webhook was
        created.
    """
    if not isinstance(signature_header, str) or not signature_header:
        return _fail("malformed_header")
    if not isinstance(secret, str) or not secret:
        return _fail("signature_mismatch")

    now = int(time.time()) if now_seconds is None else now_seconds

    # Parsed BY KEY, never positionally.
    #
    # `v1=` is a version tag precisely so a `v2=` can be added beside it. A
    # parser that reads "the second comma-separated field" breaks the day the
    # order changes or a field is inserted — and breaks by reading the wrong
    # value into the signature comparison, which fails closed but for the wrong
    # reason and is maddening to debug.
    parts = {}
    for piece in signature_header.split(","):
        index = piece.find("=")
        if index > 0:
            parts[piece[:index].strip()] = piece[index + 1 :].strip()

    stamp = parts.get("t")
    provided = parts.get(_SCHEME_VERSION)

    if not stamp or not _DIGITS.match(stamp):
        return _fail("malformed_header")
    if not provided:
        # A timestamp with no v1 is either a newer scheme we do not implement
        # or a truncated header. Neither is acceptable, and they are worth
        # telling apart.
        return _fail("unsupported_version" if len(parts) > 1 else "malformed_header")

    timestamp = int(stamp)
    if now - timestamp > tolerance_seconds:
        return _fail("timestamp_too_old")
    # A future timestamp is rejected too, and by the same tolerance.
    #
    # It is tempting to be generous about this on the grounds of clock skew. Do
    # not: a signature dated an hour ahead is not skew, it is an attacker
    # buying themselves an hour-long replay window with a request you would
    # otherwise accept the whole time.
    if timestamp - now > tolerance_seconds:
        return _fail("timestamp_in_future")

    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    mac = hmac.new(secret.encode("utf-8"), b"", hashlib.sha256)
    mac.update(f"{timestamp}.".encode("utf-8"))
    mac.update(body)
    expected = mac.hexdigest()

    # Constant time. A `==` here would leak how many leading hex characters
    # matched, which is enough to forge a signature one character at a time.
    #
    # Compared as BYTES. `compare_digest` raises TypeError on a `str` that is
    # not ASCII-only, and `provided` is attacker-controlled — so comparing the
    # strings directly would turn a forged signature containing one non-ASCII
    # character into a 500 from the caller's webhook handler instead of a
    # clean rejection.
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("ascii")):
        return _fail("signature_mismatch")

    return WebhookVerifyResult(True)


def parse_webhook_event(
    raw_body: Union[str, bytes, bytearray],
    signature_header: Optional[str],
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now_seconds: Optional[int] = None,
) -> Optional[Any]:
    """Verify and parse in one step.

    Returns the decoded event on success and `None` on any failure, for the
    common case where the only thing a handler does with a bad delivery is
    answer 400. Use `verify_webhook` directly when the reason matters.
    """
    result = verify_webhook(
        raw_body,
        signature_header,
        secret,
        tolerance_seconds=tolerance_seconds,
        now_seconds=now_seconds,
    )
    if not result.valid:
        return None
    text = raw_body if isinstance(raw_body, str) else bytes(raw_body).decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return None
