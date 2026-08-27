"""The transport.

No third-party dependencies: `urllib.request` and nothing else. Everything
interesting lives here — authentication, query building, timeouts, the retry
policy and the error mapping — so every resource method is a one-liner and none
of them can quietly disagree about how a failure is handled.

The HTTP client is swappable. `Transport` is a `Protocol`, so httpx, requests,
a proxy-aware wrapper, a tracing wrapper or a test double all satisfy it
without inheriting from anything.
"""

from __future__ import annotations

import json as _json
import random as _random
import socket as _socket
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypeVar,
    Union,
    cast,
)

from ._redaction import describe_key, redact
from .errors import (
    APIConnectionError,
    APITimeoutError,
    InvalidRequestError,
    PosthasteError,
    error_from_response,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "SDK_VERSION",
    "Headers",
    "HttpResponse",
    "Transport",
    "UrllibTransport",
    "RequestOptions",
    "HttpClient",
]

#: Kept in step with pyproject.toml — the only place the version is stated twice.
SDK_VERSION = "0.1.1"

DEFAULT_BASE_URL = "https://api.posthastemail.dev"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_RETRY_DELAY = 60.0

#: First backoff step, in seconds. Doubles per attempt, capped at 8s, with full
#: jitter applied on top.
BASE_BACKOFF = 0.5
MAX_BACKOFF = 8.0

QueryValue = Union[str, int, float, bool, Sequence[str], None]

# The decoded shape of one response.
#
# `request` is generic over it and INFERS it from the return type of whatever
# resource method called it — `-> Account` there makes `_T` `Account` here. The
# alternative is `-> Any`, which propagates: every resource method would then be
# returning `Any` from a signature that promises a `TypedDict`, and a type
# checker would have nothing left to check on the caller's side. The `cast` is
# honest about what is happening: the JSON is validated by the server, not here.
_T = TypeVar("_T")


class Headers(Mapping[str, str]):
    """A case-insensitive view of response headers.

    HTTP header names are not case-sensitive, and the three sources this SDK
    reads them from disagree about casing: `urllib` preserves whatever the
    server sent, httpx lower-cases, and a hand-written test double does
    whatever its author typed. Normalising once here means `retry-after` is
    found however it arrived.
    """

    __slots__ = ("_items",)

    def __init__(self, items: Union[Mapping[str, str], Iterable[Tuple[str, str]], None] = None):
        source: Iterable[Tuple[str, str]]
        if items is None:
            source = ()
        elif isinstance(items, Mapping):
            source = items.items()
        else:
            source = items
        self._items: Dict[str, str] = {k.lower(): v for k, v in source}

    def __getitem__(self, key: str) -> str:
        return self._items[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Headers({self._items!r})"


class HttpResponse:
    """What a `Transport` hands back. Deliberately just three fields."""

    __slots__ = ("status", "headers", "body")

    def __init__(
        self,
        status: int,
        headers: Union[Mapping[str, str], Iterable[Tuple[str, str]], None],
        body: bytes,
    ) -> None:
        self.status = status
        self.headers = Headers(headers)
        self.body = body

    @property
    def text(self) -> str:
        # `errors="replace"` rather than strict: a proxy's error page is not
        # always valid UTF-8, and a decoding crash while building an error
        # message would replace a useful diagnostic with a useless one.
        return self.body.decode("utf-8", errors="replace")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"HttpResponse(status={self.status}, bytes={len(self.body)})"


class Transport(Protocol):
    """Anything that can perform one HTTP request.

    Implement this to route the SDK through httpx, requests, a corporate proxy
    or a recorded fixture. It is a `Protocol`, so nothing has to inherit from
    it — a class with a matching `request` method already satisfies it.

    It must NOT follow redirects. See `UrllibTransport` for why.
    """

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: Optional[float],
    ) -> HttpResponse: ...


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects.

    Not a stylistic choice. `urllib` re-sends the ORIGINAL headers to whatever
    host the `Location` names, and our headers contain a bearer token that is
    good for every verified domain on the account. A misconfigured proxy, a
    hijacked DNS answer or a compromised edge could therefore collect the API
    key with a single 302. Refusing to follow means a redirect surfaces to the
    caller as a 3xx they can look at, instead of as a credential someone else
    now holds.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibTransport:
    """The default transport: `urllib.request`, no dependencies, no redirects."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirects)

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: Optional[float],
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, method=method)
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return HttpResponse(response.status, response.headers.items(), response.read())
        except urllib.error.HTTPError as exc:
            # An HTTPError IS the response. Returning it rather than letting it
            # propagate is what lets the retry policy read `error.type` off the
            # body — a 429 is not a transport failure and must not be treated
            # as one.
            return HttpResponse(exc.code, exc.headers.items(), exc.read())


class RequestOptions:
    """Per-call overrides. Every resource method takes one."""

    __slots__ = ("headers", "timeout")

    def __init__(
        self,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        #: Extra headers for this one call. Cannot override `authorization`.
        self.headers = dict(headers or {})
        #: Per-attempt timeout for this one call, in seconds.
        self.timeout = timeout


class HttpClient:
    """Authentication, retries, and the one place a failure becomes an exception."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        transport: Optional["Transport"] = None,
        timeout: Optional[float] = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_retry_delay: float = DEFAULT_MAX_RETRY_DELAY,
        headers: Optional[Mapping[str, str]] = None,
        user_agent: Optional[str] = None,
        sleep: Callable[[float], Any] = _time.sleep,
        random: Callable[[], float] = _random.random,
    ) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise InvalidRequestError(
                "A Posthaste API key is required: Posthaste(api_key=...).",
                status=0,
                type="invalid_request",
            )

        # Stored on a private attribute AND kept out of `__repr__`. See
        # `_redaction` for why that matters more than it looks like it does.
        self._api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._transport: "Transport" = transport if transport is not None else UrllibTransport()
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_retry_delay = max_retry_delay
        self._extra_headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.user_agent = (
            f"posthaste-python/{SDK_VERSION} {user_agent}"
            if user_agent
            else f"posthaste-python/{SDK_VERSION}"
        )
        self._sleep = sleep
        self._random = random

    def __repr__(self) -> str:
        return (
            f"HttpClient(base_url={self.base_url!r}, api_key={describe_key(self._api_key)!r}, "
            f"max_retries={self.max_retries})"
        )

    # -- public surface ----------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, QueryValue]] = None,
        body: Any = None,
        idempotent: bool,
        options: Optional[RequestOptions] = None,
    ) -> _T:  # type: ignore[type-var]
        """Send, and parse a JSON body.

        `_T` appears only in the return position, which mypy warns about
        because in isolation there is nothing to solve it from. Here there
        always is: every caller is a resource method with a declared return
        type, and that is what mypy infers `_T` from. The suppression buys the
        alternative being much worse — declaring `-> Any` would make all forty
        resource methods return `Any` from signatures that promise a
        `TypedDict`, and a caller's type checker would have nothing left to
        check.
        """
        status, text = self.send(
            method, path, query=query, body=body, idempotent=idempotent, options=options
        )
        if status == 204 or not text:
            return cast(_T, None)
        try:
            return cast(_T, _json.loads(text))
        except ValueError as cause:
            raise PosthasteError(
                f"Posthaste returned a {status} whose body is not JSON.",
                status=status,
                type="unknown_error",
            ) from cause

    def request_void(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, QueryValue]] = None,
        body: Any = None,
        idempotent: bool,
        options: Optional[RequestOptions] = None,
    ) -> None:
        """A request whose 204 or empty body is expected."""
        self.send(method, path, query=query, body=body, idempotent=idempotent, options=options)

    def send(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, QueryValue]] = None,
        body: Any = None,
        idempotent: bool,
        options: Optional[RequestOptions] = None,
    ) -> Tuple[int, str]:
        """Send, retry, and map a failure onto a `PosthasteError`.

        Returns the status alongside the body text because the send path needs
        the status itself — 202 and 200 are both success there and mean
        different things — and a caller cannot recover it from the JSON.
        """
        response = self._perform(method, path, query=query, body=body, idempotent=idempotent, options=options)
        return response.status, response.text

    def send_binary(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, QueryValue]] = None,
        idempotent: bool,
        options: Optional[RequestOptions] = None,
    ) -> HttpResponse:
        """A GET that returns bytes rather than JSON — attachment downloads."""
        return self._perform(
            method, path, query=query, body=None, idempotent=idempotent, options=options, json_accept=False
        )

    # -- the loop ----------------------------------------------------------

    def _perform(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, QueryValue]],
        body: Any,
        idempotent: bool,
        options: Optional[RequestOptions],
        json_accept: bool = True,
    ) -> HttpResponse:
        url = self._build_url(path, query)
        headers = self._build_headers(options, has_body=body is not None, json_accept=json_accept)
        payload = _json.dumps(body).encode("utf-8") if body is not None else None
        timeout = options.timeout if options is not None and options.timeout is not None else self.timeout

        attempt = 0
        while True:
            try:
                response = self._transport.request(method, url, headers, payload, timeout)
            except Exception as cause:  # noqa: BLE001 - every transport failure is one of ours
                error = self._transport_error(cause)
                # A connection that never produced a response is safe to repeat
                # only under the same rule as everything else: the request has
                # to be one that repeating cannot duplicate.
                if idempotent and attempt < self.max_retries:
                    self._sleep(self._jittered_backoff(attempt))
                    attempt += 1
                    continue
                raise error from cause

            # `< 300`, not `< 400`. The transport refuses to follow redirects
            # (it would leak the bearer token to whatever host the Location
            # names), so a 3xx arrives here as an unhandled answer rather than
            # as a success — and reporting it is far better than handing back
            # an empty body as though the call had worked.
            if response.status < 300:
                return response

            error = error_from_response(
                response.status,
                response.text,
                response.headers.get("retry-after"),
                api_key=self._api_key,
            )

            wait = self._retry_delay_for(error, idempotent=idempotent, attempt=attempt)
            if wait is None:
                raise error

            self._sleep(wait)
            attempt += 1

    def _retry_delay_for(
        self, error: PosthasteError, *, idempotent: bool, attempt: int
    ) -> Optional[float]:
        """How long to wait before repeating, or `None` for "do not".

        The decision branches on `error.type` and NOT on the status, which is
        the whole point. All FOUR of these are 429 and they mean four different
        things:

          `rate_limited`         the per-key request limiter. Transient,
                                 measured in seconds, exactly what a retry is
                                 for.
          `platform_paused`      the platform-wide daily send ceiling. Also
                                 transient, and NOT a statement about this
                                 account: nothing the customer does clears it
                                 and upgrading does not help.
          `daily_limit_reached`  the account's warmup cap. `Retry-After` is the
                                 seconds until midnight UTC.
          `monthly_limit_reached` the plan allowance. `Retry-After` can be
                                 weeks.

        A retry loop written against the status treats the last two as a hiccup
        and hammers a wall it cannot get through until the calendar moves —
        burning the caller's own rate limit on requests that are all going to
        be refused.

        `platform_paused` is retryable here. Whether it is retried IN PROCESS
        is settled below by `max_retry_delay` on the honest length of the wait
        rather than by the type: a several-minute `Retry-After` exceeds the 60s
        default, so the error comes back to the caller to schedule.
        """
        if attempt >= self.max_retries:
            return None
        if not idempotent:
            return None
        # Quota exhaustion. Never in process, whatever Retry-After says.
        if error.is_quota_exhausted:
            return None

        retryable = (
            error.is_rate_limited
            or error.status in (408, 429)
            or error.status >= 500
        )
        if not retryable:
            return None

        if error.retry_after_seconds is not None:
            requested = error.retry_after_seconds
            # Honour it — unless honouring it would mean blocking for longer
            # than a caller could reasonably want, in which case hand the error
            # back and let them decide.
            if requested > self.max_retry_delay:
                return None
            return max(0.0, requested)

        return self._jittered_backoff(attempt)

    def _jittered_backoff(self, attempt: int) -> float:
        """Exponential with FULL jitter.

        Without jitter every client that failed on the same upstream blip
        retries in the same millisecond, and the recovery attempt is itself a
        thundering herd against a service that has only just come back.
        """
        ceiling = min(MAX_BACKOFF, BASE_BACKOFF * (2.0**attempt))
        return self._random() * ceiling

    # -- request construction ---------------------------------------------

    def _build_headers(
        self, options: Optional[RequestOptions], *, has_body: bool, json_accept: bool
    ) -> Dict[str, str]:
        headers: Dict[str, str] = dict(self._extra_headers)
        if options is not None:
            headers.update({k.lower(): v for k, v in options.headers.items()})
        if json_accept:
            headers["accept"] = "application/json"
        headers["user-agent"] = self.user_agent
        if has_body:
            headers["content-type"] = "application/json"
        # Last, and on a key already lower-cased everywhere above, so that
        # neither the per-client nor the per-call headers can replace it —
        # not even by spelling it `Authorization`.
        headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_url(self, path: str, query: Optional[Mapping[str, QueryValue]]) -> str:
        pairs: List[Tuple[str, str]] = []
        for key, value in (query or {}).items():
            # `None` means "not asked for". An empty string is a real, if odd,
            # filter and is sent.
            if value is None:
                continue
            if isinstance(value, bool):
                # `str(True)` is "True", which no API on earth parses as a
                # boolean. This branch has to come before the numeric one,
                # because a bool IS an int in Python.
                pairs.append((key, "true" if value else "false"))
            elif isinstance(value, (str, int, float)):
                pairs.append((key, str(value)))
            else:
                # A sequence is REPEATED, not joined. `",".join(...)` would be
                # read by the API as one filter whose value happens to contain
                # a comma, so `tag=["welcome", "billing"]` would silently match
                # nothing instead of matching both.
                for item in value:
                    pairs.append((key, str(item)))

        if not pairs:
            return f"{self.base_url}{path}"
        # `quote_via=quote` rather than the default `quote_plus`: a literal
        # `+` in a tag or a metadata value must survive as `%2B`, and a space
        # as `%20`, which is what the API's parser expects.
        encoded = urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote, safe="")
        return f"{self.base_url}{path}?{encoded}"

    def _transport_error(self, cause: BaseException) -> PosthasteError:
        """Wrap whatever the transport raised, with the key scrubbed out.

        The message comes from a library underneath us and can quote the
        request. Passing it through `redact` unconditionally is cheap, and it
        is the difference between a traceback an error reporter can safely
        store and one that hands a third party a live credential.
        """
        # `socket.timeout` is an alias of TimeoutError from 3.10 on but NOT
        # before — on 3.9 it is a plain OSError subclass, so checking only
        # TimeoutError would report every timeout on the oldest supported
        # Python as `connection_error`. `URLError` wraps the original in
        # `.reason`, which is where a timeout raised inside the socket layer
        # actually ends up.
        clock = (TimeoutError, _socket.timeout)
        timed_out = isinstance(cause, clock) or isinstance(
            getattr(cause, "reason", None), clock
        )
        message = redact(str(cause) or type(cause).__name__, self._api_key)
        if timed_out:
            return APITimeoutError(
                "The Posthaste request timed out before the server responded.",
                status=0,
                type="timeout",
            )
        return APIConnectionError(
            f"Could not reach Posthaste: {message}",
            status=0,
            type="connection_error",
        )
