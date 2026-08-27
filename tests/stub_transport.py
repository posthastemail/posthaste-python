"""A scripted transport, and the helpers for building responses.

`StubTransport` satisfies `posthaste.http.Transport` structurally — it inherits
from nothing, which is the point of the protocol being a protocol.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from posthaste.http import HttpResponse


class RecordedRequest:
    __slots__ = ("method", "url", "headers", "body", "timeout")

    def __init__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: Optional[float],
    ) -> None:
        self.method = method
        self.url = url
        self.headers = dict(headers)
        self.body = body
        self.timeout = timeout

    @property
    def json(self) -> Any:
        assert self.body is not None, "this request had no body"
        return json.loads(self.body.decode("utf-8"))

    @property
    def path(self) -> str:
        return self.url.split("?", 1)[0]

    @property
    def query(self) -> str:
        return self.url.split("?", 1)[1] if "?" in self.url else ""

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return f"RecordedRequest({self.method} {self.url})"


class StubTransport:
    """Answers each request with the next scripted response.

    A scripted entry that is an `Exception` instance is RAISED instead, which
    is how a connection failure is simulated.
    """

    def __init__(self, *responses: Union[HttpResponse, Exception]) -> None:
        self.scripted: List[Union[HttpResponse, Exception]] = list(responses)
        self.requests: List[RecordedRequest] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: Optional[float],
    ) -> HttpResponse:
        self.requests.append(RecordedRequest(method, url, headers, body, timeout))
        if not self.scripted:
            raise AssertionError(
                f"unscripted request: {method} {url} (this is test-setup drift, not a bug "
                f"in the SDK)"
            )
        nxt = self.scripted.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @property
    def last(self) -> RecordedRequest:
        return self.requests[-1]


def json_response(
    status: int, payload: Any, headers: Optional[Mapping[str, str]] = None
) -> HttpResponse:
    merged: Dict[str, str] = {"content-type": "application/json"}
    merged.update(headers or {})
    return HttpResponse(status, merged, json.dumps(payload).encode("utf-8"))


def error_response(
    status: int,
    error_type: str,
    message: str = "refused",
    *,
    headers: Optional[Mapping[str, str]] = None,
    **extra: Any,
) -> HttpResponse:
    body: Dict[str, Any] = {"type": error_type, "message": message}
    body.update(extra)
    return json_response(status, {"error": body}, headers)


def empty_response(status: int = 204) -> HttpResponse:
    return HttpResponse(status, {}, b"")


def sleeps() -> "SleepRecorder":
    return SleepRecorder()


class SleepRecorder:
    """Stands in for `time.sleep`, so backoff is asserted rather than waited out."""

    def __init__(self) -> None:
        self.calls: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def paged(items: Sequence[Any], cursor: Optional[str], has_more: Optional[bool] = None) -> Any:
    page: Dict[str, Any] = {"data": list(items), "nextCursor": cursor}
    if has_more is not None:
        page["hasMore"] = has_more
    return page
