"""Suite-wide guarantees.

Two of them, and both are enforced mechanically rather than by convention,
because both are the kind of rule that holds right up until somebody adds one
test in a hurry:

  NO TEST MAY OPEN A SOCKET. An SDK suite that reaches the real API is a suite
  that can send real mail, and mail sent from a test run is mail sent to
  somebody. The guard below makes a connection attempt raise before DNS is even
  consulted.

  NO TEST MAY NAME AN ADDRESS WE DO NOT OWN. See `test_no_real_recipients.py`.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest


class NetworkAccessAttempted(BaseException):
    """Raised when a test tries to open a socket.

    Deliberately a `BaseException` and not an `Exception`. The SDK's transport
    loop catches `Exception` and turns it into `APIConnectionError`, so an
    ordinary exception here would be swallowed, reported as a connection
    failure, and the test would pass while quietly proving nothing. This one
    cannot be caught by the code under test.
    """


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: Any, **kwargs: Any) -> Any:
        raise NetworkAccessAttempted(
            "the Posthaste Python suite attempted a real network connection; "
            "every test must go through a stub transport"
        )

    # `create_connection` is what `http.client` uses, and it is patched first so
    # the attempt fails before `getaddrinfo` resolves anything. The rest close
    # the paths around it.
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
