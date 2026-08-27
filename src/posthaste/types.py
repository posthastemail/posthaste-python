"""The wire types.

Requests are Python: snake_case keyword arguments, mapped to the API's
camelCase on the way out. Responses are the API's own JSON, handed back
UNCHANGED — camelCase keys and all.

That asymmetry is deliberate rather than an oversight. Rewriting a response
means deciding what to do with a field this SDK version has never heard of, and
every answer to that is bad: drop it and a caller loses data the API sent them,
keep it under its original name and the object is half-translated. The response
is the API's document, and a document is not ours to rewrite. What this module
adds instead is a `TypedDict` for each shape, so an editor and a type checker
know every key without anything being reshaped at runtime.

Every shape here was written from `packages/sdk/src/types.ts`, which was in
turn written from the handlers that produce it — so a field present here is a
field the server actually sends.

Timestamps are ISO-8601 strings. They are `timestamptz` in Postgres and are
JSON-serialised on the way out, so they arrive as strings however they were
stored; typing them as `datetime` would be a lie that only shows up at runtime.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

__all__ = [
    "MESSAGE_STATUSES",
    "EVENT_TYPES",
    "SUPPRESSION_REASONS",
    "SCOPES",
    "MessageStatus",
    "EventType",
    "SuppressionReason",
    "Scope",
    "DomainStatus",
    "Attachment",
    "TemplateVariable",
    "CursorPage",
    "Page",
    "Account",
    "Usage",
    "ChainVerification",
    "DnsRecord",
    "Domain",
    "CreatedDomain",
    "DomainVerification",
    "DomainSetup",
    "CloudflarePublishResult",
    "SendEmailResult",
    "SentCopy",
    "LintFinding",
    "BatchItemResult",
    "BatchResult",
    "MessageSummary",
    "Message",
    "MessageContent",
    "AttachmentMeta",
    "WaybillEntry",
    "MessageStats",
    "Suppression",
    "CreatedSuppression",
    "Webhook",
    "CreatedWebhook",
    "WebhookEvent",
    "ApiKey",
    "MessageStream",
    "Template",
    "TemplateVersion",
    "TemplatePreview",
    "Billing",
    "BillingEvent",
    "BillingHistory",
    "Invoice",
    "InvoiceDetail",
    "DownloadedAttachment",
]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

#: `message_status` in the database. The lifecycle a message can be in.
MESSAGE_STATUSES = (
    "queued",
    "sending",
    "delivered",
    "bounced",
    "complained",
    "failed",
    "scheduled",
    "canceled",
    "rejected",
)
MessageStatus = str

#: The webhook event types (`event_type` in the database).
#:
#: NOT the same set as the message statuses: `accepted`, `attempted`,
#: `deferred` and `suppressed` are things that happen to a message without
#: changing the status it rests in.
EVENT_TYPES = (
    "accepted",
    "queued",
    "attempted",
    "delivered",
    "deferred",
    "bounced",
    "complained",
    "failed",
    "rejected",
    "suppressed",
    "scheduled",
    "schedule_canceled",
    "schedule_failed",
)
EventType = str

#: `suppression_reason`. Spelled exactly as the server spells it — guessing at
#: `bounce` produces a filter that silently matches nothing rather than an
#: error.
SUPPRESSION_REASONS = ("hard_bounce", "complaint", "manual", "unsubscribe", "spam_trap")
SuppressionReason = str

#: Every scope an API key can be granted.
SCOPES = (
    "account:read",
    "domains:read",
    "domains:write",
    "emails:send",
    "messages:read",
    "analytics:read",
    "suppressions:read",
    "suppressions:write",
    "webhooks:read",
    "webhooks:write",
    "team:read",
    "streams:read",
    "streams:write",
    "templates:read",
    "templates:write",
)
Scope = str

DomainStatus = Literal["pending", "verified", "failed", "disabled"]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class Attachment(TypedDict, total=False):
    """One file on a send.

    Up to 10 files, at most 10 MiB combined once decoded. Executable types
    (`.exe`, `.js`, `.bat`, …) are refused by the API — the big mailbox
    providers bounce them anyway.
    """

    #: Shown to the recipient. No path separators; 255 characters maximum.
    filename: str
    #: `type/subtype` only — no parameters.
    content_type: str
    #: Raw `bytes`, which this SDK base64-encodes, or a `str` that is ALREADY
    #: base64 and is sent through untouched. Encoding a string again would
    #: corrupt the file on arrival, so the type is what decides.
    content: Union[bytes, str]
    disposition: Literal["attachment", "inline"]
    #: Content-ID for an inline part, referenced from the html as `cid:logo`.
    cid: str


class TemplateVariable(TypedDict, total=False):
    name: str
    #: Default true. An optional variable renders as empty when absent.
    required: bool


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class CursorPage(TypedDict):
    """A keyset page carrying a cursor and nothing else.

    The shape of every list that was RETROFITTED with pagination:
    `/v1/domains`, `/v1/webhooks`, `/v1/api-keys`, `/v1/billing/invoices` and
    `/v1/billing/history`. On these `nextCursor` IS the loop condition and it is
    trustworthy — the handler asks the database for one row more than you did
    and sets the cursor only when that extra row came back.
    """

    data: List[Any]
    nextCursor: Optional[str]


class Page(CursorPage):
    """A keyset page that also carries the server's own "is there more?".

    Sent by `/v1/messages` and `/v1/suppressions` — the lists that were
    paginated from the start. Here `hasMore` is the only correct loop
    condition, because `nextCursor` is populated from the last row of a page
    and stays non-null on the final one.
    """

    hasMore: bool


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountPlan(TypedDict):
    id: str
    name: str
    monthlyEmails: int
    #: `None` means unlimited.
    maxDomains: Optional[int]
    retentionDays: int
    dedicatedIp: bool


class AccountSending(TypedDict):
    dailyLimit: int
    sentToday: int
    remainingToday: int
    warmupTier: int
    sentThisMonth: int
    remainingThisMonth: int


class Account(TypedDict):
    id: str
    name: str
    slug: str
    status: str
    environment: Literal["live", "test"]
    scopes: List[str]
    plan: Optional[AccountPlan]
    subscription: Optional[Dict[str, Any]]
    sending: Optional[AccountSending]


class ChainVerification(TypedDict):
    """`GET /v1/account/verify` — a replay of the whole event chain."""

    intact: bool
    #: The sequence number at which the record stops being trustworthy.
    brokenAt: Optional[int]
    problem: Optional[str]
    checked: str


class UsageDay(TypedDict):
    day: str
    sent: int
    delivered: int
    bounced: int
    complained: int


class Usage(TypedDict):
    periodStart: str
    periodEnd: str
    sent: int
    delivered: int
    bounced: int
    complained: int
    #: `None` while nothing has been sent — not zero, which would be a claim.
    deliveryRate: Optional[float]
    complaintRate: Optional[float]
    bounceRate: Optional[float]
    days: List[UsageDay]


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


class DnsRecord(TypedDict, total=False):
    name: str
    type: Literal["TXT", "MX", "CNAME"]
    value: str
    #: TXT values over 255 characters have to be published as split strings.
    chunks: List[str]
    #: Only required records gate verification. Only DKIM is required.
    required: bool
    purpose: str
    #: Present when publishing the record carries a risk (SPF, DMARC).
    warning: str


class Domain(TypedDict):
    id: str
    name: str
    status: DomainStatus
    verifiedAt: Optional[str]
    createdAt: str
    #: Which DKIM key signs this domain's mail, so a rotation is visible.
    selector: str
    #: Messages ever sent from it. Non-zero is what makes deletion refused.
    messagesSent: int
    lastCheckedAt: Optional[str]
    records: List[DnsRecord]


class CreatedDomain(TypedDict):
    id: str
    name: str
    status: Literal["pending"]
    records: List[DnsRecord]


class RecordCheck(TypedDict):
    record: Literal["dkim", "spf", "dmarc"]
    name: str
    required: bool
    status: Literal["pass", "fail", "not_found"]
    detail: str


class DomainVerification(TypedDict):
    id: str
    name: str
    status: Literal["verified", "failed"]
    #: Branch on THIS, not on `status`: `checks` reports SPF and DMARC too, and
    #: neither of them failing stops the domain being usable.
    verified: bool
    checks: List[RecordCheck]


class DomainSetup(TypedDict):
    #: Who runs this domain's DNS, when we could work it out.
    host: Optional[Dict[str, Any]]
    #: Present only when the provider supports one-click publishing.
    oneClick: Optional[Dict[str, str]]


class CloudflarePublishResult(TypedDict):
    published: List[Dict[str, Any]]
    zone: str
    verified: bool
    message: str
    #: SPF and DMARC — deliberately left alone, and said so.
    notPublished: List[Dict[str, str]]


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


class LintFinding(TypedDict, total=False):
    """One thing the pre-send lint noticed, with the evidence behind it."""

    check: str
    severity: Literal["warn", "block"]
    #: Always a sentence you can put in front of a person.
    message: str


class SentCopy(TypedDict, total=False):
    """What happened to one recipient of a multi-recipient send."""

    #: Absent when the recipient was skipped.
    id: str
    to: str
    #: Which field the address was taken from, after de-duplication.
    kind: Literal["to", "cc", "bcc"]
    status: Literal["queued", "scheduled", "duplicate", "suppressed"]
    #: Why it was skipped: `hard_bounce`, `complaint`, `spam_trap`, `manual`.
    reason: str


class SendEmailResult(TypedDict, total=False):
    id: str
    #: `queued` for a new send (HTTP 202), `duplicate` for an idempotency
    #: replay (HTTP 200). Both are success, and both carry the id of the one
    #: real message.
    status: Literal["queued", "scheduled", "duplicate"]
    #: Added by this SDK, read off the body rather than off the HTTP status —
    #: the body survives a proxy that normalises a 202 to a 200.
    duplicate: bool
    scheduledAt: str
    #: Ties the copies of a multi-recipient send together.
    groupId: str
    #: Every requested recipient, exactly once — present only when there was
    #: more than one.
    emails: List[SentCopy]
    #: Recipients skipped because they are suppressed. Repeated here as well as
    #: in `emails` deliberately: a recipient we did not send to is the one
    #: outcome that must be impossible to miss.
    suppressed: List[Dict[str, str]]
    #: What the lint thought of a message it let through. One per call, not one
    #: per recipient.
    warnings: List[LintFinding]


class BatchItemResult(TypedDict, total=False):
    """One item's outcome in a batch. Reported by `index`, in request order."""

    index: int
    status: Literal["accepted", "duplicate", "rejected"]
    id: str
    error: Dict[str, Any]
    emails: List[Dict[str, Any]]


class BatchResult(TypedDict):
    accepted: int
    rejected: int
    data: List[BatchItemResult]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

# `from` is a Python keyword, so these two shapes need the functional form —
# there is no way to write `from: str` in a class body.
MessageSummary = TypedDict(
    "MessageSummary",
    {
        "id": str,
        "from": str,
        "to": str,
        "subject": Optional[str],
        "status": MessageStatus,
        "attempts": int,
        "smtpCode": Optional[int],
        "createdAt": str,
        # Empty rather than absent when the message carried none.
        "tags": List[str],
        "metadata": Dict[str, str],
    },
)


class AttachmentMeta(TypedDict):
    """Metadata only — download the bytes with `messages.download_attachment`."""

    #: Bare uuid, used in the download URL.
    id: str
    filename: str
    contentType: str
    disposition: str
    cid: Optional[str]
    sizeBytes: int


class MessageContent(TypedDict):
    text: Optional[str]
    html: Optional[str]
    #: The composed, signed message as it went on the wire.
    raw: Optional[str]
    replyTo: Optional[str]
    headers: Dict[str, str]
    attachments: List[AttachmentMeta]


class WaybillEntry(TypedDict):
    seq: int
    type: EventType
    at: str
    detail: Optional[Dict[str, Any]]
    hash: str
    prevHash: str


Message = TypedDict(
    "Message",
    {
        "id": str,
        "from": str,
        "to": str,
        "subject": Optional[str],
        "status": MessageStatus,
        "attempts": int,
        "smtp": Optional[Dict[str, Any]],
        "createdAt": str,
        "updatedAt": str,
        "tags": List[str],
        "metadata": Dict[str, str],
        # The hash chain over this message's own events, re-verified on read.
        "recordIntact": bool,
        # Null once the body has aged out of the plan's retention window.
        "content": Optional[MessageContent],
        # Every hand the message passed through, with its hash linkage.
        "waybill": List[WaybillEntry],
    },
    total=False,
)


class StatsTotals(TypedDict):
    delivered: int
    bounced: int
    complained: int
    failed: int
    rejected: int
    pending: int
    total: int
    #: Everything that has finished. Rates are over this, not over `total`.
    settled: int
    deliveryRate: Optional[float]
    bounceRate: Optional[float]
    complaintRate: Optional[float]


class MessageStats(TypedDict):
    data: List[Dict[str, Any]]
    totals: StatsTotals
    #: The same window immediately before this one, for comparison.
    previous: StatsTotals
    change: Dict[str, Optional[float]]
    busiest: Optional[Dict[str, Any]]
    thresholds: Dict[str, float]
    range: Dict[str, Any]


class DownloadedAttachment(TypedDict):
    """What `messages.download_attachment` returns.

    Not an API shape — the API answers with bytes and headers, and this is the
    three things worth keeping off that response.
    """

    content: bytes
    contentType: str
    filename: Optional[str]


# ---------------------------------------------------------------------------
# Suppressions
# ---------------------------------------------------------------------------


class Suppression(TypedDict):
    id: str
    address: str
    reason: SuppressionReason
    detail: Optional[str]
    #: The RFC 3463 enhanced status the receiving server gave, e.g. `5.1.1`.
    smtpStatus: Optional[str]
    #: The message whose bounce or complaint caused this. `None` for an entry
    #: added by hand, and for one whose message has aged out of retention — a
    #: suppression outlives the evidence for it, deliberately.
    message: Optional[Dict[str, Any]]
    createdAt: str
    #: A `platform` entry is not the account's to remove.
    scope: Literal["platform", "account"]


class CreatedSuppression(TypedDict):
    #: Normalised — lower-cased and trimmed by the server.
    address: str
    reason: Literal["manual"]


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class Webhook(TypedDict):
    id: str
    url: str
    eventTypes: List[EventType]
    status: Literal["active", "disabled"]
    createdAt: str


class CreatedWebhook(TypedDict):
    id: str
    url: str
    eventTypes: List[EventType]
    status: Literal["active"]
    #: Shown exactly ONCE, on creation, and never retrievable afterwards.
    #: Store it before you do anything else with the response — nobody,
    #: including us, can recover it.
    signingSecret: str


class WebhookEvent(TypedDict):
    """The JSON body delivered to a webhook endpoint."""

    #: Identical to the `posthaste-delivery-id` header. Deduplicate on it.
    id: str
    type: EventType
    createdAt: str
    data: Dict[str, Any]


# ---------------------------------------------------------------------------
# API keys, streams, templates
# ---------------------------------------------------------------------------


class ApiKey(TypedDict):
    id: str
    name: str
    environment: Literal["live", "test"]
    scopes: List[str]
    #: Enough to recognise which key this is, not enough to use it.
    hint: str
    createdAt: str
    lastUsedAt: Optional[str]
    revokedAt: Optional[str]


class MessageStream(TypedDict):
    id: str
    #: What a send names. The id is the handle; this is the name in your code.
    slug: str
    name: str
    #: Exactly one per account. A send that names no stream lands here.
    isDefault: bool
    createdAt: str


class TemplateVersion(TypedDict):
    version: int
    subject: Optional[str]
    html: Optional[str]
    text: Optional[str]
    variables: List[TemplateVariable]
    publishedAt: Optional[str]
    createdAt: str


class Template(TypedDict):
    id: str
    #: What a send names.
    slug: str
    name: str
    createdAt: str
    updatedAt: str
    #: The version a send would use right now.
    latestVersion: Optional[TemplateVersion]


class TemplatePreview(TypedDict):
    version: int
    subject: Optional[str]
    html: Optional[str]
    text: Optional[str]
    variables: List[TemplateVariable]
    #: `lint["blocked"]` says the send would be refused — the one thing an
    #: editor has to show before somebody schedules a campaign on it.
    lint: Dict[str, Any]


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


class Billing(TypedDict):
    plan: Dict[str, Any]
    subscription: Optional[Dict[str, Any]]
    profile: Optional[Dict[str, Any]]
    payments: List[Dict[str, Any]]
    plans: List[Dict[str, Any]]
    #: False when the deployment has no payment provider configured.
    configured: bool


class BillingEvent(TypedDict):
    seq: int
    type: str
    actor: str
    reason: Optional[str]
    detail: Dict[str, Any]
    occurredAt: str
    hash: str


class BillingHistory(TypedDict):
    #: Oldest first — the opposite of every other list.
    data: List[BillingEvent]
    #: The `seq` of the last event on this page, as a string. Pass it as
    #: `after`.
    nextCursor: Optional[str]
    #: Verified over the WHOLE account history, not over the page, so it means
    #: the same thing on page four as on page one.
    chain: Dict[str, Any]


class Invoice(TypedDict):
    id: str
    #: The number quoted to an accountant, e.g. `PH-2026-27-0001`.
    number: str
    issuedAt: str
    currency: str
    subtotalMinor: int
    taxMinor: int
    taxRateBp: Optional[int]
    totalMinor: int
    paidMinor: int
    plan: str
    periodStart: Optional[str]
    periodEnd: Optional[str]
    buyer: Dict[str, Any]
    payment: Dict[str, Any]


class InvoiceDetail(Invoice):
    """The single-invoice fetch carries the supplier block the list omits."""

    supplier: Dict[str, Any]
