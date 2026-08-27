"""The suite may not name an address we do not own, and may not open a socket.

Both rules are enforced here rather than trusted, because both are the kind of
rule that holds right up until somebody adds one test in a hurry.

An address in a test file is one search-and-replace away from being an address
in a request. Sending to a mailbox we do not control is a spam-trap risk, and
the reputation of the sending IP is shared by every customer on it — so the
allowed domains are the ones RFC 2606 and RFC 6761 reserve for exactly this,
and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from posthaste import Posthaste
from conftest import NetworkAccessAttempted

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# RFC 2606 and RFC 6761. Reserved for documentation and testing, and
# guaranteed never to be delegated to a real mail host.
ALLOWED_DOMAINS = {"example.com", "example.org", "example.net", "localhost"}
ALLOWED_SUFFIXES = (".example", ".invalid", ".test", ".localhost")

# Anything address-shaped: a local part, an @, and a dotted domain.
ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def source_files() -> List[Path]:
    files = sorted(PACKAGE_ROOT.rglob("*.py")) + sorted(PACKAGE_ROOT.rglob("*.md"))
    files += sorted(PACKAGE_ROOT.rglob("*.json"))
    return [f for f in files if "__pycache__" not in f.parts]


def offending(text: str) -> Set[str]:
    found = set()
    for domain in ADDRESS.findall(text):
        lowered = domain.lower()
        if lowered in ALLOWED_DOMAINS or lowered.endswith(ALLOWED_SUFFIXES):
            continue
        found.add(lowered)
    return found


def test_the_scan_finds_files_at_all() -> None:
    """So the check below cannot pass vacuously."""
    files = source_files()
    assert len(files) > 10
    assert any(f.name == "test_client.py" for f in files)


def test_the_scan_would_catch_a_real_domain() -> None:
    """A negative test for the guard itself.

    Without this, a broken regex would make every file below "clean".

    The counterexample is ASSEMBLED rather than written out, so this file is
    not the one exception to the rule it enforces — the scan below reads every
    `.py` in the package, this one included, and a literal here would have to
    be excused with an exclusion that would then hide a real address.
    """
    real = "someone@" + "gmail" + ".com"
    assert offending(f"send to {real}") == {"gmail.com"}
    assert offending("send to customer@example.com") == set()
    assert offending("send to billing@acme.example") == set()


def test_no_file_names_an_address_at_a_domain_we_do_not_control() -> None:
    problems: List[Tuple[str, Set[str]]] = []
    for path in source_files():
        found = offending(path.read_text(encoding="utf-8"))
        if found:
            problems.append((str(path.relative_to(PACKAGE_ROOT)), found))
    assert problems == [], (
        "these files name addresses at domains we do not own — use example.com or "
        "a .example domain (RFC 2606)"
    )


def test_the_default_transport_really_would_open_a_socket() -> None:
    """Proves the no-network guard is load-bearing, not decorative.

    `NetworkAccessAttempted` is a `BaseException`, so the SDK's own
    `except Exception` cannot swallow it and report a tidy connection error
    while a socket was in fact attempted.
    """
    client = Posthaste("ph_test_example_key_0000", max_retries=0)
    with pytest.raises(NetworkAccessAttempted):
        client.account.me()
