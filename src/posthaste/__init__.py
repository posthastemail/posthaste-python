"""posthaste — the official Python SDK for the Posthaste transactional email API.

No third-party dependencies: `urllib.request` and `hmac`, nothing else.

    from posthaste import Posthaste

    posthaste = Posthaste(api_key=os.environ["POSTHASTE_API_KEY"])
    posthaste.emails.send(
        from_="Acme <billing@acme.example>",
        to="customer@example.com",
        subject="Your receipt",
        text="Thanks for your order.",
    )
"""

from __future__ import annotations

from .client import Posthaste
from .errors import (
    KNOWN_ERROR_TYPES,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AttachmentError,
    AuthenticationError,
    ConflictError,
    ContentBlockedError,
    DomainNotVerifiedError,
    FieldError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    PosthasteError,
    QuotaExhausted,
    RateLimited,
    ScheduleError,
    ServerError,
    StreamError,
    SuppressedError,
    SuppressionInfo,
    TemplateError,
    UnprocessableError,
)
from .http import (
    DEFAULT_BASE_URL,
    SDK_VERSION,
    Headers,
    HttpResponse,
    RequestOptions,
    Transport,
    UrllibTransport,
)
from .pagination import auto_paginate, collect
from .resources import (
    AccountResource,
    ApiKeysResource,
    BillingResource,
    DomainsResource,
    EmailsResource,
    MessagesResource,
    StreamsResource,
    SuppressionsResource,
    AddressesResource,
    TemplatesResource,
    VerificationsResource,
    WebhooksResource,
    ContactsResource,
    ListsResource,
    BroadcastsResource,
)
from .webhooks import (
    ATTEMPT_HEADER,
    DEFAULT_TOLERANCE_SECONDS,
    DELIVERY_ID_HEADER,
    SIGNATURE_HEADER,
    WebhookVerifyResult,
    parse_webhook_event,
    verify_webhook,
)

__version__ = SDK_VERSION

__all__ = [
    "Posthaste",
    "__version__",
    "SDK_VERSION",
    "DEFAULT_BASE_URL",
    # Transport
    "Transport",
    "UrllibTransport",
    "HttpResponse",
    "Headers",
    "RequestOptions",
    # Errors
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
    "FieldError",
    "SuppressionInfo",
    "KNOWN_ERROR_TYPES",
    # Pagination
    "auto_paginate",
    "collect",
    # Webhooks
    "verify_webhook",
    "parse_webhook_event",
    "WebhookVerifyResult",
    "SIGNATURE_HEADER",
    "DELIVERY_ID_HEADER",
    "ATTEMPT_HEADER",
    "DEFAULT_TOLERANCE_SECONDS",
    # Resources, for type annotations
    "AccountResource",
    "ApiKeysResource",
    "BillingResource",
    "DomainsResource",
    "EmailsResource",
    "MessagesResource",
    "StreamsResource",
    "SuppressionsResource",
    "AddressesResource",
    "TemplatesResource",
    "VerificationsResource",
    "WebhooksResource",
    "ContactsResource",
    "ListsResource",
    "BroadcastsResource",
]
