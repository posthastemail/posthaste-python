"""Keeping the API key out of everything that can be printed.

A key is a bearer credential: whoever holds it can send mail from every
verified domain on the account. The places it escapes from are not the ones
people guard — nobody logs `client.api_key` on purpose. It escapes through a
`repr()` in a debugger session pasted into a ticket, through an exception
message that quoted the request, and through a traceback captured by an error
reporter and shipped to a third party.

So the key is never stored anywhere that a default `repr` will reach, and every
string this package builds for a human to read is passed through `redact`
first.
"""

from __future__ import annotations

import re

__all__ = ["describe_key", "redact"]

REDACTED = "***redacted***"

# `ph_live_…` / `ph_test_…`. The prefix is not secret — it is printed in the
# dashboard beside every key — and it is the one piece that helps somebody
# staring at a 401 work out that they pasted the test key into production.
_PREFIX = re.compile(r"^(ph_(?:live|test)_)")

# Anything key-shaped, wherever it turns up. Used for text this package did not
# build itself — a server message that echoed the credential back, a transport
# error that quoted the request line — where there is no `api_key` to compare
# against.
_KEY_SHAPED = re.compile(r"ph_(?:live|test)_[A-Za-z0-9_\-]{8,}")


def describe_key(api_key: str) -> str:
    """A label for a key that cannot be turned back into the key.

    Deliberately NOT the usual "last four characters" convention. Four
    characters of a token this size is not enough to authenticate with, but it
    is enough to confirm a guess, and the thing it is normally used for —
    telling two keys apart — is served just as well by the environment prefix
    without narrowing the search space at all.
    """
    match = _PREFIX.match(api_key)
    prefix = match.group(1) if match else ""
    return f"{prefix}{REDACTED}"


def redact(text: str, api_key: str = "") -> str:
    """Remove the key from a string meant for a human.

    Two passes on purpose. The first removes this client's own key, which
    catches it however short or oddly formatted it is. The second removes
    anything key-shaped, which catches a *different* account's key echoed back
    by a server or quoted by a library underneath us.
    """
    out = text
    if api_key:
        out = out.replace(api_key, REDACTED)
    return _KEY_SHAPED.sub(REDACTED, out)
