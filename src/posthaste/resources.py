"""One class per area of the API.

Every method is a thin declaration of a URL, a method, a body and — the only
interesting bit — whether repeating the request is safe. The transport in
`http.py` owns everything else.

Scope: the API-KEY surface only. Endpoints that require a browser session
(sign-in, checkout, profile, minting keys) are deliberately absent, because
they refuse a bearer token and a method that can only ever raise
`PermissionDeniedError` is worse than no method. `/admin/v1/*` is absent for the
same reason, and `/v1/inbound/*` is absent because a customer cannot use it —
the DNS records handed out on domain verification contain no MX, so nothing
would arrive.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Union

from .http import HttpClient, RequestOptions
from .pagination import auto_paginate as _walk
from .pagination import collect as _collect
from .types import (
    Account,
    ApiKey,
    BatchResult,
    Billing,
    BillingEvent,
    BillingHistory,
    ChainVerification,
    CloudflarePublishResult,
    CreatedDomain,
    CreatedSuppression,
    CreatedWebhook,
    CursorPage,
    Domain,
    DomainSetup,
    DomainVerification,
    DownloadedAttachment,
    Invoice,
    InvoiceDetail,
    Message,
    MessageStats,
    MessageStream,
    MessageSummary,
    Page,
    SendEmailResult,
    Suppression,
    Template,
    TemplatePreview,
    TemplateVariable,
    TemplateVersion,
    Usage,
    Webhook,
)

__all__ = [
    "AccountResource",
    "ApiKeysResource",
    "BillingResource",
    "DomainsResource",
    "EmailsResource",
    "MessagesResource",
    "StreamsResource",
    "SuppressionsResource",
    "TemplatesResource",
    "WebhooksResource",
]

#: The default ceiling on every `list_all`. See `pagination.collect` for why
#: there is a ceiling at all.
DEFAULT_MAX_ITEMS = 1000

# The only query parameter whose Python spelling differs from its wire
# spelling. `from` is a keyword, so the method signature says `from_`.
_QUERY_ALIASES = {"from_": "from"}


def _fields(**kwargs: Any) -> Dict[str, Any]:
    """Drop the unset filters and rename the one that needs it."""
    out: Dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        out[_QUERY_ALIASES.get(key, key)] = value
    return out


def _path(*segments: str) -> str:
    """Join path segments, percent-encoding each one.

    `safe=""` on purpose: a suppression is addressed by ADDRESS, and an address
    can contain a `/` in the local part. Leaving it unencoded would send the
    request to a different route entirely.
    """
    return "/".join(urllib.parse.quote(str(s), safe="") for s in segments)


class _Resource:
    __slots__ = ("_http",)

    def __init__(self, http: HttpClient) -> None:
        self._http = http


# ---------------------------------------------------------------------------
# Building a send
# ---------------------------------------------------------------------------

# Every field of a send whose wire name differs from its Python name. Explicit
# rather than a generic snake-to-camel function, because a generic one silently
# invents a wire name for anything it is handed and this map is checkable.
_EMAIL_FIELDS = {
    "from_": "from",
    "reply_to": "replyTo",
    "list_unsubscribe": "listUnsubscribe",
    "template_version": "templateVersion",
    "idempotency_key": "idempotencyKey",
    "scheduled_at": "scheduledAt",
}

_ATTACHMENT_FIELDS = {"content_type": "contentType"}


def _encode_attachment(attachment: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in attachment.items():
        if value is None:
            continue
        out[_ATTACHMENT_FIELDS.get(key, key)] = value
    content = out.get("content")
    # Bytes are base64-encoded here. A `str` is passed through untouched — it
    # is expected to ALREADY be base64, and encoding it again would corrupt the
    # file on arrival with nothing anywhere reporting a problem.
    if isinstance(content, (bytes, bytearray, memoryview)):
        out["content"] = base64.b64encode(bytes(content)).decode("ascii")
    return out


def build_email(params: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn Python keyword arguments into the JSON body the API expects.

    Unknown keys are passed through UNCHANGED rather than rejected. The
    trade-off is deliberate: rejecting them would mean a field added to the API
    after this SDK version was released could not be used at all, and the cost
    of passing them through is that a typo reaches the server — where it comes
    back as `invalid_request` naming the field, which is a perfectly good error
    message.
    """
    body: Dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        name = _EMAIL_FIELDS.get(key, key)
        if name == "attachments":
            body[name] = [_encode_attachment(a) for a in value]
        else:
            body[name] = value
    return body


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountResource(_Resource):
    def me(self, *, request_options: Optional[RequestOptions] = None) -> Account:
        """`GET /v1/me` — who this key belongs to, its scopes, plan and caps."""
        return self._http.request("GET", "/v1/me", idempotent=True, options=request_options)

    def verify(self, *, request_options: Optional[RequestOptions] = None) -> ChainVerification:
        """`GET /v1/account/verify` — replay the account's ENTIRE event chain.

        The strong claim, not the per-message one: nothing in the whole
        delivery history has been altered or removed. Expensive by design; not
        a health check to run on every request.
        """
        return self._http.request(
            "GET", "/v1/account/verify", idempotent=True, options=request_options
        )

    def usage(self, *, request_options: Optional[RequestOptions] = None) -> Usage:
        """`GET /v1/usage` — the current UTC month, with the daily series."""
        return self._http.request("GET", "/v1/usage", idempotent=True, options=request_options)


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


class DomainsResource(_Resource):
    def create(
        self, name: str, *, request_options: Optional[RequestOptions] = None
    ) -> CreatedDomain:
        """`POST /v1/domains` — 201 with the DNS records to publish.

        Safe to repeat: a second create for the same name is refused with
        `409 conflict` rather than producing a second domain, so a retry after
        a lost response cannot leave duplicates behind.
        """
        return self._http.request(
            "POST", "/v1/domains", body={"name": name}, idempotent=True, options=request_options
        )

    def _page(self, params: Mapping[str, Any], options: Optional[RequestOptions]) -> CursorPage:
        return self._http.request(
            "GET", "/v1/domains", query=params, idempotent=True, options=options
        )

    def list(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> CursorPage:
        """`GET /v1/domains` — one keyset page, newest first.

        `limit` defaults to 100, which is also its maximum, so on most accounts
        the first page is every domain. It is still a page: past a hundred
        domains the rest are behind `nextCursor`, and there is no `hasMore`
        here to tell you so.
        """
        return self._page(_fields(limit=limit, before=before), request_options)

    def auto_paginate(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Iterator[Domain]:
        """Every domain, across every page."""
        return _walk(lambda p: self._page(p, request_options), _fields(limit=limit, before=before))

    def list_all(self, max_items: int = DEFAULT_MAX_ITEMS, **filters: Any) -> List[Domain]:
        """Drain `auto_paginate` into a list. Same filters as `list`."""
        return _collect(self.auto_paginate(**filters), max_items)

    def verify(
        self, domain_id: str, *, request_options: Optional[RequestOptions] = None
    ) -> DomainVerification:
        """`POST /v1/domains/:id/verify` — look for the records, record the result.

        Branch on `verified`, not on `status`: `checks` reports SPF and DMARC
        too, and neither of them failing stops the domain being usable.
        """
        return self._http.request(
            "POST",
            f"/v1/domains/{_path(domain_id)}/verify",
            idempotent=True,
            options=request_options,
        )

    def delete(self, domain_id: str, *, request_options: Optional[RequestOptions] = None) -> None:
        """`DELETE /v1/domains/:id` — 204.

        Refused with `409 domain_in_use` while any message references it. That
        is deliberate: the delivery record is the product, and deleting the
        domain would take its history with it.
        """
        self._http.request_void(
            "DELETE", f"/v1/domains/{_path(domain_id)}", idempotent=True, options=request_options
        )

    def setup(
        self, domain_id: str, *, request_options: Optional[RequestOptions] = None
    ) -> DomainSetup:
        """`GET /v1/domains/:id/setup` — who runs this domain's DNS."""
        return self._http.request(
            "GET",
            f"/v1/domains/{_path(domain_id)}/setup",
            idempotent=True,
            options=request_options,
        )

    def connect_cloudflare(
        self,
        domain_id: str,
        *,
        token: Optional[str] = None,
        remember: Optional[bool] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> CloudflarePublishResult:
        """`POST /v1/domains/:id/cloudflare` — publish DKIM on the customer's behalf.

        Only DKIM is written. SPF and DMARC come back under `notPublished`,
        untouched, because overwriting either is worse than asking somebody to
        copy a string by hand.

        Safe to repeat: the record write is an upsert and the token store is an
        `on conflict do update`.
        """
        return self._http.request(
            "POST",
            f"/v1/domains/{_path(domain_id)}/cloudflare",
            body=_fields(token=token, remember=remember),
            idempotent=True,
            options=request_options,
        )

    def disconnect_cloudflare(self, *, request_options: Optional[RequestOptions] = None) -> None:
        """`DELETE /v1/account/cloudflare` — forget the stored Cloudflare token.

        Account-scoped rather than domain-scoped (one token serves every
        domain), but it lives here because it is part of the DNS story. Always
        204, whether a token was stored or not.
        """
        self._http.request_void(
            "DELETE", "/v1/account/cloudflare", idempotent=True, options=request_options
        )


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


class EmailsResource(_Resource):
    def send(
        self,
        *,
        from_: str,
        to: Union[str, Sequence[str]],
        cc: Optional[Sequence[str]] = None,
        bcc: Optional[Sequence[str]] = None,
        subject: Optional[str] = None,
        text: Optional[str] = None,
        html: Optional[str] = None,
        reply_to: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        list_unsubscribe: Optional[str] = None,
        stream: Optional[str] = None,
        template: Optional[str] = None,
        template_version: Optional[int] = None,
        variables: Optional[Mapping[str, str]] = None,
        tags: Optional[Sequence[str]] = None,
        metadata: Optional[Mapping[str, str]] = None,
        idempotency_key: Optional[str] = None,
        attachments: Optional[Sequence[Mapping[str, Any]]] = None,
        scheduled_at: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> SendEmailResult:
        """`POST /v1/emails`.

        TWO success statuses, and they mean different things:

          202 `{"status": "queued"}`    — accepted, a new message exists.
          200 `{"status": "duplicate"}` — an idempotency replay. No new message
                                          was created; the id is the original.

        Both come back as a result carrying a `duplicate` boolean rather than
        collapsed into "it worked". A caller that bills, logs or counts per send
        needs to know which of the two happened, and finding out from an HTTP
        status they never see is not a reasonable ask.

        RETRIES. A send is only repeated automatically when `idempotency_key`
        is set, because without one a retry after a lost response sends the
        email twice. Setting it is the single most useful thing you can do
        here.

        IDEMPOTENCY IS A BODY FIELD. The `Idempotency-Key` HTTP header is in
        the API's CORS allowlist but no handler reads it, so a client that
        sends the header and not the field gets no idempotency at all and no
        warning that it has none. This SDK never sends the header.
        """
        body = build_email(
            {
                "from_": from_,
                "to": to,
                "cc": cc,
                "bcc": bcc,
                "subject": subject,
                "text": text,
                "html": html,
                "reply_to": reply_to,
                "headers": headers,
                "list_unsubscribe": list_unsubscribe,
                "stream": stream,
                "template": template,
                "template_version": template_version,
                "variables": variables,
                "tags": tags,
                "metadata": metadata,
                "idempotency_key": idempotency_key,
                "attachments": attachments,
                "scheduled_at": scheduled_at,
            }
        )

        status, raw = self._http.send(
            "POST",
            "/v1/emails",
            body=body,
            # The whole rule, in one expression.
            idempotent=bool(idempotency_key),
            options=request_options,
        )
        parsed: Dict[str, Any] = json.loads(raw) if raw else {}
        # Read from the BODY, not from `status == 200`. The body is the
        # authoritative answer and survives a proxy that normalises a 202 to a
        # 200; the status is a corroborating signal, not the source of truth.
        parsed["duplicate"] = parsed.get("status") == "duplicate"
        return parsed  # type: ignore[return-value]

    def send_batch(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        request_options: Optional[RequestOptions] = None,
    ) -> BatchResult:
        """`POST /v1/emails/batch` — up to 100 messages, per-item outcomes.

        Each entry takes the same keys as `send`, as a mapping — `from_` or
        `from`, both work.

        Partial success is the contract: one refused item does not take the
        rest down, and the accepted ones are durably accepted rather than
        rolled back alongside it. Read `data` by `index` — the order matches
        your request.

        202 when at least one was accepted, 422 when none were. NOT
        auto-retried: repeating a batch without per-item `idempotency_key`s
        sends everything twice. With them, a retry replays instead.
        """
        return self._http.request(
            "POST",
            "/v1/emails/batch",
            body={"messages": [build_email(m) for m in messages]},
            idempotent=False,
            options=request_options,
        )

    def cancel_schedule(
        self, message_id: str, *, request_options: Optional[RequestOptions] = None
    ) -> Dict[str, Any]:
        """`DELETE /v1/emails/:id/schedule` — cancel a scheduled send.

        Succeeds until the message is released to delivery, which happens
        within about a minute of its scheduled time. A message that already
        released raises `ScheduleError` carrying its current status; a cancel
        retried after a lost response returns success again rather than
        erroring.
        """
        return self._http.request(
            "DELETE",
            f"/v1/emails/{_path(message_id)}/schedule",
            idempotent=True,
            options=request_options,
        )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

_FILENAME_STAR = re.compile(r"filename\*=UTF-8''([^;]+)", re.IGNORECASE)
_FILENAME_PLAIN = re.compile(r'filename="([^"]*)"', re.IGNORECASE)


class MessagesResource(_Resource):
    def _page(self, params: Mapping[str, Any], options: Optional[RequestOptions]) -> Page:
        return self._http.request(
            "GET", "/v1/messages", query=params, idempotent=True, options=options
        )

    def list(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        status: Optional[str] = None,
        stream: Optional[str] = None,
        tag: Optional[Union[str, Sequence[str]]] = None,
        metadata: Optional[str] = None,
        to: Optional[str] = None,
        search: Optional[str] = None,
        domain: Optional[str] = None,
        from_: Optional[str] = None,
        until: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Page:
        """`GET /v1/messages` — one keyset page, newest first.

        `tag` is narrowing, never widening: two tags means messages carrying
        both. `metadata` is one exact `key:value` pair.
        """
        return self._page(
            _fields(
                limit=limit,
                before=before,
                status=status,
                stream=stream,
                tag=tag,
                metadata=metadata,
                to=to,
                search=search,
                domain=domain,
                from_=from_,
                until=until,
            ),
            request_options,
        )

    def auto_paginate(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        status: Optional[str] = None,
        stream: Optional[str] = None,
        tag: Optional[Union[str, Sequence[str]]] = None,
        metadata: Optional[str] = None,
        to: Optional[str] = None,
        search: Optional[str] = None,
        domain: Optional[str] = None,
        from_: Optional[str] = None,
        until: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Iterator[MessageSummary]:
        """Every message matching the filter, across every page.

        A generator: one page is in memory at a time, and breaking out of the
        loop stops fetching.
        """
        return _walk(
            lambda p: self._page(p, request_options),
            _fields(
                limit=limit,
                before=before,
                status=status,
                stream=stream,
                tag=tag,
                metadata=metadata,
                to=to,
                search=search,
                domain=domain,
                from_=from_,
                until=until,
            ),
        )

    def list_all(self, max_items: int = DEFAULT_MAX_ITEMS, **filters: Any) -> List[MessageSummary]:
        """Drain `auto_paginate` into a list. Same filters as `list`."""
        return _collect(self.auto_paginate(**filters), max_items)

    def get(self, message_id: str, *, request_options: Optional[RequestOptions] = None) -> Message:
        """`GET /v1/messages/:id` — the waybill.

        Content, every event, and the hash linkage, with the chain re-verified
        on read (`recordIntact`).
        """
        return self._http.request(
            "GET", f"/v1/messages/{_path(message_id)}", idempotent=True, options=request_options
        )

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        *,
        request_options: Optional[RequestOptions] = None,
    ) -> DownloadedAttachment:
        """`GET /v1/messages/:id/attachments/:attachmentId` — the stored bytes.

        The attachment id comes from `get(id)["content"]["attachments"][n]["id"]`.
        Returns the exact bytes that were sent; the server serves them with
        `Content-Disposition: attachment` and only ever an inert content type,
        echoed here for callers who re-serve the file.
        """
        response = self._http.send_binary(
            "GET",
            f"/v1/messages/{_path(message_id)}/attachments/{_path(attachment_id)}",
            idempotent=True,
            options=request_options,
        )
        disposition = response.headers.get("content-disposition", "")
        star = _FILENAME_STAR.search(disposition)
        plain = _FILENAME_PLAIN.search(disposition)
        filename = (
            urllib.parse.unquote(star.group(1))
            if star
            else (plain.group(1) if plain else None)
        )
        return {
            "content": response.body,
            "contentType": response.headers.get("content-type", "application/octet-stream"),
            "filename": filename,
        }

    def stats(
        self,
        *,
        days: Optional[int] = None,
        domain: Optional[str] = None,
        tz: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> MessageStats:
        """`GET /v1/stats/messages` — daily volume, with the previous window.

        Rates here are PERCENTAGES over settled mail (`total` minus `pending`).
        `GET /v1/usage` reports its rates as fractions over everything sent —
        the two endpoints genuinely differ, and dividing one by 100 to compare
        them is not enough.
        """
        return self._http.request(
            "GET",
            "/v1/stats/messages",
            query=_fields(days=days, domain=domain, tz=tz),
            idempotent=True,
            options=request_options,
        )


# ---------------------------------------------------------------------------
# Suppressions
# ---------------------------------------------------------------------------


class SuppressionsResource(_Resource):
    def _page(self, params: Mapping[str, Any], options: Optional[RequestOptions]) -> Page:
        return self._http.request(
            "GET", "/v1/suppressions", query=params, idempotent=True, options=options
        )

    def list(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        search: Optional[str] = None,
        reason: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Page:
        """`GET /v1/suppressions` — one keyset page. `limit` caps at 200."""
        return self._page(
            _fields(limit=limit, before=before, search=search, reason=reason), request_options
        )

    def auto_paginate(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        search: Optional[str] = None,
        reason: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Iterator[Suppression]:
        """Every suppression matching the filter, across every page."""
        return _walk(
            lambda p: self._page(p, request_options),
            _fields(limit=limit, before=before, search=search, reason=reason),
        )

    def list_all(self, max_items: int = DEFAULT_MAX_ITEMS, **filters: Any) -> List[Suppression]:
        """Drain `auto_paginate` into a list. Same filters as `list`."""
        return _collect(self.auto_paginate(**filters), max_items)

    def create(
        self,
        address: str,
        *,
        reason: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> CreatedSuppression:
        """`POST /v1/suppressions` — 201.

        `reason` here is free text and is stored as the entry's DETAIL. The
        entry's own reason is always `manual`; only the platform creates
        `hard_bounce`, `complaint`, `unsubscribe` and `spam_trap` entries.

        Safe to repeat: the insert is `on conflict do nothing`, and re-adding an
        address answers 201 again.
        """
        return self._http.request(
            "POST",
            "/v1/suppressions",
            body=_fields(address=address, reason=reason),
            idempotent=True,
            options=request_options,
        )

    def delete(self, address: str, *, request_options: Optional[RequestOptions] = None) -> None:
        """`DELETE /v1/suppressions/:address` — 204.

        Addressed by ADDRESS, not by `sup_` id, and this SDK percent-encodes it
        for you. Two entries are refused: a `complaint` (`suppression_protected`
        — sending again is what gets an IP blocklisted) and a platform-wide
        entry (`suppression_platform`).
        """
        self._http.request_void(
            "DELETE",
            f"/v1/suppressions/{_path(address)}",
            idempotent=True,
            options=request_options,
        )


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class WebhooksResource(_Resource):
    def create(
        self,
        url: str,
        *,
        event_types: Optional[Sequence[str]] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> CreatedWebhook:
        """`POST /v1/webhooks` — 201, carrying `signingSecret`.

        The secret is shown exactly ONCE and is not retrievable afterwards,
        because a signing secret that can be re-read is one that anybody with a
        stolen API key can read too. Store it before you do anything else with
        the response.

        NOT auto-retried: repeating this creates a second webhook, and the
        duplicate would then receive every event twice, for ever.
        """
        return self._http.request(
            "POST",
            "/v1/webhooks",
            body=_fields(url=url, eventTypes=list(event_types) if event_types else None),
            idempotent=False,
            options=request_options,
        )

    def _page(self, params: Mapping[str, Any], options: Optional[RequestOptions]) -> CursorPage:
        return self._http.request(
            "GET", "/v1/webhooks", query=params, idempotent=True, options=options
        )

    def list(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> CursorPage:
        """`GET /v1/webhooks` — one keyset page, newest first. Never any secrets.

        There is no `hasMore` on this endpoint; `nextCursor` is the end-of-list
        signal.
        """
        return self._page(_fields(limit=limit, before=before), request_options)

    def auto_paginate(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Iterator[Webhook]:
        """Every webhook, across every page."""
        return _walk(lambda p: self._page(p, request_options), _fields(limit=limit, before=before))

    def list_all(self, max_items: int = DEFAULT_MAX_ITEMS, **filters: Any) -> List[Webhook]:
        """Drain `auto_paginate` into a list. Same filters as `list`."""
        return _collect(self.auto_paginate(**filters), max_items)

    def delete(self, webhook_id: str, *, request_options: Optional[RequestOptions] = None) -> None:
        """`DELETE /v1/webhooks/:id` — 204, or 404 for an unknown id."""
        self._http.request_void(
            "DELETE", f"/v1/webhooks/{_path(webhook_id)}", idempotent=True, options=request_options
        )


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


class ApiKeysResource(_Resource):
    """Read only, and that is the whole resource.

    Creating and revoking keys requires a signed-in owner or admin and refuses
    a bearer key outright: a server-side credential that could mint more
    credentials would make every narrow key one request away from a full one.
    """

    def _page(self, params: Mapping[str, Any], options: Optional[RequestOptions]) -> CursorPage:
        return self._http.request(
            "GET", "/v1/api-keys", query=params, idempotent=True, options=options
        )

    def list(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> CursorPage:
        """`GET /v1/api-keys` — one keyset page, revoked keys included.

        Revoked keys count towards `limit`, so an account that has rotated its
        credentials a few times will have more than one page. No `hasMore`;
        `nextCursor` is the end-of-list signal.
        """
        return self._page(_fields(limit=limit, before=before), request_options)

    def auto_paginate(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Iterator[ApiKey]:
        """Every key, across every page."""
        return _walk(lambda p: self._page(p, request_options), _fields(limit=limit, before=before))

    def list_all(self, max_items: int = DEFAULT_MAX_ITEMS, **filters: Any) -> List[ApiKey]:
        """Drain `auto_paginate` into a list. Same filters as `list`."""
        return _collect(self.auto_paginate(**filters), max_items)


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------


class StreamsResource(_Resource):
    """Separating traffic that shares a sending IP.

    The stream a message is sent on decides which suppressions apply to it. A
    complaint or an unsubscribe is confined to the stream that earned it, so
    somebody who reports your newsletter still receives their password reset. A
    hard bounce or a spam trap is account-wide regardless — a dead mailbox is a
    fact about the address, and no stream is a way around it.
    """

    def list(
        self, *, request_options: Optional[RequestOptions] = None
    ) -> Dict[str, List[MessageStream]]:
        """`GET /v1/streams` — every stream on the account, default first."""
        return self._http.request("GET", "/v1/streams", idempotent=True, options=request_options)

    def create(
        self, *, slug: str, name: str, request_options: Optional[RequestOptions] = None
    ) -> MessageStream:
        """`POST /v1/streams` — 201.

        NOT auto-retried: a 409 for a slug that is already taken is the answer,
        not a transient failure to paper over.
        """
        return self._http.request(
            "POST",
            "/v1/streams",
            body={"slug": slug, "name": name},
            idempotent=False,
            options=request_options,
        )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class TemplatesResource(_Resource):
    """Stored content, sent by name.

    A published version never changes: editing publishes a new one, and a send
    records which version it used. That is what lets a delivery record still say
    what a message contained after the template has moved on.
    """

    def list(self, *, request_options: Optional[RequestOptions] = None) -> Dict[str, List[Template]]:
        """`GET /v1/templates`."""
        return self._http.request("GET", "/v1/templates", idempotent=True, options=request_options)

    def get(
        self, template_id: str, *, request_options: Optional[RequestOptions] = None
    ) -> Template:
        """`GET /v1/templates/:id`."""
        return self._http.request(
            "GET", f"/v1/templates/{_path(template_id)}", idempotent=True, options=request_options
        )

    def versions(
        self, template_id: str, *, request_options: Optional[RequestOptions] = None
    ) -> Dict[str, List[TemplateVersion]]:
        """`GET /v1/templates/:id/versions` — newest first."""
        return self._http.request(
            "GET",
            f"/v1/templates/{_path(template_id)}/versions",
            idempotent=True,
            options=request_options,
        )

    def preview(
        self,
        template_id: str,
        *,
        version: Optional[int] = None,
        variables: Optional[Mapping[str, str]] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> TemplatePreview:
        """`POST /v1/templates/:id/preview` — render it. Sends nothing.

        Fails exactly the way a send fails, with the same error type and the
        same list of missing variables, so a preview that passes means the send
        will. To test for real, send it to yourself with
        `emails.send(template=...)` — that path is the one with the suppression
        check, the throttle and the signing on it.
        """
        return self._http.request(
            "POST",
            f"/v1/templates/{_path(template_id)}/preview",
            body=_fields(version=version, variables=variables),
            idempotent=True,
            options=request_options,
        )

    def create(
        self,
        *,
        slug: str,
        name: str,
        subject: Optional[str] = None,
        html: Optional[str] = None,
        text: Optional[str] = None,
        variables: Optional[Sequence[TemplateVariable]] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Template:
        """`POST /v1/templates` — 201, published at version 1.

        `variables` is DECLARED, not inferred. Inferring the list from the body
        means a typo silently becomes a new optional variable and the render
        then succeeds with a blank where a name should be.

        Not auto-retried: a 409 for a slug already taken is the answer.
        """
        return self._http.request(
            "POST",
            "/v1/templates",
            body=_fields(
                slug=slug,
                name=name,
                subject=subject,
                html=html,
                text=text,
                variables=list(variables) if variables is not None else None,
            ),
            idempotent=False,
            options=request_options,
        )

    def update(
        self,
        template_id: str,
        *,
        name: Optional[str] = None,
        subject: Optional[str] = None,
        html: Optional[str] = None,
        text: Optional[str] = None,
        variables: Optional[Sequence[TemplateVariable]] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Template:
        """`PATCH /v1/templates/:id` — content publishes a NEW version.

        A name on its own is a rename. A published version is never edited — a
        delivery record points at it, and "what did this message say" has to
        keep having an answer.
        """
        return self._http.request(
            "PATCH",
            f"/v1/templates/{_path(template_id)}",
            body=_fields(
                name=name,
                subject=subject,
                html=html,
                text=text,
                variables=list(variables) if variables is not None else None,
            ),
            idempotent=False,
            options=request_options,
        )

    def delete(
        self, template_id: str, *, request_options: Optional[RequestOptions] = None
    ) -> None:
        """`DELETE /v1/templates/:id` — refused once anything has been sent from it."""
        self._http.request_void(
            "DELETE",
            f"/v1/templates/{_path(template_id)}",
            idempotent=True,
            options=request_options,
        )


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


class BillingResource(_Resource):
    def get(self, *, request_options: Optional[RequestOptions] = None) -> Billing:
        """`GET /v1/billing` — plan, subscription, profile, payments, price list.

        All four billing reads accept `billing:read` OR `account:read`; an API
        key cannot hold `billing:read`, so in practice `account:read` is the one
        that gets you in. Money is always in minor units.
        """
        return self._http.request("GET", "/v1/billing", idempotent=True, options=request_options)

    def _history_page(
        self, params: Mapping[str, Any], options: Optional[RequestOptions]
    ) -> BillingHistory:
        return self._http.request(
            "GET", "/v1/billing/history", query=params, idempotent=True, options=options
        )

    def history(
        self,
        *,
        limit: Optional[int] = None,
        after: Optional[Union[str, int]] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> BillingHistory:
        """`GET /v1/billing/history` — one page of the hash-chained record.

        THE ODD ONE OUT, in two ways a caller who assumes the house style will
        get wrong:

          - It reads FORWARD. Events come back oldest first and the page
            resumes from `after`, not `before`. That is not stylistic: a hash
            chain is verified from its start, and a record that renders in a
            different order than it verifies in is one people stop trusting.
          - `after` is a SEQUENCE NUMBER — a `seq` — not a prefixed id. Passing
            an id gets `400 invalid_request`. `nextCursor` is that number
            rendered as a string, so passing it straight back is correct.
        """
        return self._history_page(_fields(limit=limit, after=after), request_options)

    def auto_paginate_history(
        self,
        *,
        limit: Optional[int] = None,
        after: Optional[Union[str, int]] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Iterator[BillingEvent]:
        """Every billing event, oldest first, across every page.

        Yields the EVENTS only. `chain` is per-response, so if you need the
        verification as well, call `history` and read it from there.
        """
        # `after`, not `before` — the whole reason this endpoint needs a helper.
        return _walk(
            lambda p: self._history_page(p, request_options),
            _fields(limit=limit, after=after),
            "after",
        )

    def list_all_history(
        self, max_items: int = DEFAULT_MAX_ITEMS, **filters: Any
    ) -> List[BillingEvent]:
        """Drain `auto_paginate_history` into a list."""
        return _collect(self.auto_paginate_history(**filters), max_items)

    def _invoices_page(
        self, params: Mapping[str, Any], options: Optional[RequestOptions]
    ) -> CursorPage:
        return self._http.request(
            "GET", "/v1/billing/invoices", query=params, idempotent=True, options=options
        )

    def invoices(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> CursorPage:
        """`GET /v1/billing/invoices` — one keyset page, newest first.

        `limit` defaults to 50 and there is no `hasMore` here. An account past
        its fiftieth invoice that reads only `data` is silently short of its own
        financial record — follow `nextCursor`, or use `auto_paginate_invoices`.
        """
        return self._invoices_page(_fields(limit=limit, before=before), request_options)

    def auto_paginate_invoices(
        self,
        *,
        limit: Optional[int] = None,
        before: Optional[str] = None,
        request_options: Optional[RequestOptions] = None,
    ) -> Iterator[Invoice]:
        """Every invoice, across every page."""
        return _walk(
            lambda p: self._invoices_page(p, request_options), _fields(limit=limit, before=before)
        )

    def list_all_invoices(self, max_items: int = DEFAULT_MAX_ITEMS, **filters: Any) -> List[Invoice]:
        """Drain `auto_paginate_invoices` into a list."""
        return _collect(self.auto_paginate_invoices(**filters), max_items)

    def invoice(
        self, invoice_id: str, *, request_options: Optional[RequestOptions] = None
    ) -> InvoiceDetail:
        """`GET /v1/billing/invoices/:id` — the same document plus the supplier block."""
        return self._http.request(
            "GET",
            f"/v1/billing/invoices/{_path(invoice_id)}",
            idempotent=True,
            options=request_options,
        )
