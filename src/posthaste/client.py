"""The client."""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from ._redaction import describe_key
from .http import DEFAULT_BASE_URL, HttpClient, Transport
from .resources import (
    AccountResource,
    ApiKeysResource,
    BillingResource,
    DomainsResource,
    EmailsResource,
    MessagesResource,
    StreamsResource,
    SuppressionsResource,
    TemplatesResource,
    WebhooksResource,
)

__all__ = ["Posthaste"]


class Posthaste:
    """The Posthaste API client.

    ::

        from posthaste import Posthaste

        posthaste = Posthaste(api_key=os.environ["POSTHASTE_API_KEY"])

        sent = posthaste.emails.send(
            from_="Acme <billing@acme.example>",
            to="customer@example.com",
            subject="Your receipt",
            html="<p>Thanks for your order.</p>",
            idempotency_key=f"receipt-{order_id}",
        )

    :param api_key: a `ph_live_…` or `ph_test_…` key, sent as a bearer token.
        Server-side only — a key carries no user identity and must never reach
        a browser.
    :param base_url: point this at your own deployment when self-hosting.
        Trailing slashes are stripped.
    :param transport: anything satisfying `posthaste.http.Transport`. The
        default uses `urllib` and no third-party packages; pass your own to
        route through httpx, requests or a proxy.
    :param timeout: per ATTEMPT, not per call — a call that retries twice may
        take longer than this. `None` disables it.
    :param max_retries: retries AFTER the first attempt. `0` turns retrying off
        entirely.
    :param max_retry_delay: the longest server-requested wait this SDK will
        actually sit through, in seconds. A `Retry-After` beyond it is honoured
        by NOT retrying: a quota that frees at midnight is not something to
        block a request handler on, and sleeping through it would look like a
        hang.
    :param headers: extra headers on every request. Cannot override
        `authorization`.
    :param user_agent: appended to the SDK's own `user-agent`.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: Optional[Transport] = None,
        timeout: Optional[float] = 30.0,
        max_retries: int = 2,
        max_retry_delay: float = 60.0,
        headers: Optional[Mapping[str, str]] = None,
        user_agent: Optional[str] = None,
        sleep: Optional[Callable[[float], Any]] = None,
        random: Optional[Callable[[], float]] = None,
    ) -> None:
        kwargs: Dict[str, Any] = {}
        # Only forwarded when overridden, so the transport keeps ownership of
        # its own defaults. These two exist so tests can assert the backoff
        # without spending real seconds on it.
        if sleep is not None:
            kwargs["sleep"] = sleep
        if random is not None:
            kwargs["random"] = random

        self._http = HttpClient(
            api_key,
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            max_retries=max_retries,
            max_retry_delay=max_retry_delay,
            headers=headers,
            user_agent=user_agent,
            **kwargs,
        )
        self._key_label = describe_key(api_key)

        self.account = AccountResource(self._http)
        self.domains = DomainsResource(self._http)
        self.emails = EmailsResource(self._http)
        self.messages = MessagesResource(self._http)
        self.suppressions = SuppressionsResource(self._http)
        self.webhooks = WebhooksResource(self._http)
        self.api_keys = ApiKeysResource(self._http)
        self.billing = BillingResource(self._http)
        self.streams = StreamsResource(self._http)
        self.templates = TemplatesResource(self._http)

    @property
    def base_url(self) -> str:
        return self._http.base_url

    def __repr__(self) -> str:
        # No `api_key=` here, and none anywhere else either. A default dataclass
        # or attrs `repr` would have printed it, which is the whole reason this
        # class is written out by hand. The label is computed once at
        # construction so this class never needs a way to read the key back.
        return f"Posthaste(base_url={self.base_url!r}, api_key={self._key_label!r})"
