# posthaste

The official Python SDK for the [Posthaste](https://posthastemail.dev) transactional email API.

**No third-party dependencies.** `urllib.request` and `hmac`, nothing else — so it installs
instantly, adds nothing to your lockfile, and brings no transitive supply chain for your security
team to audit.

```bash
pip install posthaste-email
```

The distribution is `posthaste-email`; the import stays `posthaste`, so no code changes either
way. Install the full name — the bare `posthaste` on PyPI is an unrelated 2013 OpenStack utility
that still holds the namespace, so the short form installs the wrong package without erroring.

Requires Python 3.9 or newer. Fully typed, with a `py.typed` marker, so mypy and pyright see every
annotation.

---

## Quickstart

```python
import os
from posthaste import Posthaste

posthaste = Posthaste(api_key=os.environ["POSTHASTE_API_KEY"])

sent = posthaste.emails.send(
    from_="Acme <billing@acme.example>",
    to="customer@example.com",
    subject="Your receipt",
    html="<p>Thanks for your order.</p>",
    text="Thanks for your order.",
    idempotency_key=f"receipt-{order_id}",
)

sent["id"]         # 'msg_AZLm3kQ8T2Sf9pXbNc7HrQ'
sent["status"]     # 'queued' | 'duplicate'
sent["duplicate"]  # False — a new send, not an idempotency replay
```

`from_` has the trailing underscore because `from` is a Python keyword. It is the only field whose
Python name differs for that reason, and the SDK maps it back to `from` on the wire.

## Snake in, camel out

Requests are Python: snake_case keyword arguments, mapped to the API's camelCase on the way out.
Responses come back as the API's own JSON, **unchanged** — camelCase keys and all.

That asymmetry is deliberate. Rewriting a response means deciding what to do with a field this SDK
version has never heard of, and every answer is bad: drop it and you lose data the API sent you;
keep it under its original name and the object is half-translated. The response is the API's
document, and a document is not ours to rewrite. What the SDK adds instead is a `TypedDict` for
every shape, so your editor and type checker know the keys without anything being reshaped at
runtime.

## Authentication

Posthaste authenticates with an API key sent as a bearer token. Create one in the dashboard under
**Settings → API keys**; it is shown exactly once.

```python
posthaste = Posthaste(
    api_key=os.environ["POSTHASTE_API_KEY"],   # ph_live_… or ph_test_…
    base_url="https://api.posthastemail.dev",  # the default
    timeout=30.0,                              # per ATTEMPT, not per call
    max_retries=2,                             # retries after the first attempt
    max_retry_delay=60.0,                      # longest Retry-After it will sit through
    headers={"x-trace-id": trace_id},          # added to every request
)
```

A key is server-side only: it carries no user identity and must never reach a browser. It carries
**scopes**, and a call that needs one the key does not hold raises `PermissionDeniedError` naming
the missing scope.

**The key is never printed.** It is not in `repr(client)`, not in any exception message, and not in
a formatted traceback — including when a server echoes it back in an error. That matters more than
it sounds: a credential leaks through a `repr` pasted into a ticket and through a traceback an error
reporter uploaded, far more often than through a deliberate log line.

### Self-hosting

Point `base_url` at your own deployment. Trailing slashes are stripped.

```python
posthaste = Posthaste(api_key=key, base_url="https://mail.internal.example/api")
```

### Bringing your own HTTP client

`Transport` is a `Protocol`, so anything with a matching `request` method works — no inheritance
required. Useful behind a corporate proxy, for tracing, and in tests.

```python
import httpx
from posthaste import Posthaste
from posthaste.http import HttpResponse

class HttpxTransport:
    def __init__(self, **kwargs):
        # follow_redirects stays OFF: a redirect would re-send the bearer token
        # to whatever host the Location names.
        self._client = httpx.Client(follow_redirects=False, **kwargs)

    def request(self, method, url, headers, body, timeout):
        response = self._client.request(
            method, url, headers=headers, content=body, timeout=timeout
        )
        return HttpResponse(response.status_code, response.headers, response.content)

posthaste = Posthaste(api_key=key, transport=HttpxTransport(proxy="http://proxy.internal:8080"))
```

## Errors

Every failure raises a `PosthasteError`, including the ones that never reached a server. Under that
base there is a real hierarchy, so `except RateLimited` reads the way a Python caller expects.

```python
from posthaste import (
    ContentBlockedError,
    DomainNotVerifiedError,
    QuotaExhausted,
    RateLimited,
    SuppressedError,
    PosthasteError,
)

try:
    posthaste.emails.send(from_=sender, to=recipient, text=body)

except SuppressedError as error:
    # Permanent. error.suppression.reason is 'hard_bounce' | 'complaint' |
    # 'spam_trap' | 'manual' | 'unsubscribe'.
    drop(error.suppression)

except DomainNotVerifiedError:
    ask_them_to_publish_the_dkim_record()

except ContentBlockedError as error:
    # error.check names the check; error.findings is the whole lint report,
    # warnings included, so one fix pass can address everything.
    show(error.findings)

except RateLimited as error:
    # Transient, measured in seconds. Covers `rate_limited` (your request rate)
    # and `platform_paused` (our own daily send ceiling, which is not yours and
    # which no plan change touches).
    retry_after(error.retry_after_seconds or 60)

except QuotaExhausted as error:
    # Hours or days away. Not a retry — an alert.
    alert_ops(error)

except PosthasteError as error:
    log.warning("posthaste refused: %s %s", error.status, error.type)
```

**Branch on the class or the type, never on the status.** Four different refusals arrive as `429`.
`RateLimited` and `QuotaExhausted` are deliberately siblings rather than parent and child, because
an `except` clause written for one must never catch the other — that confusion is exactly what makes
a naive retry loop hammer a wall for the rest of the month.

Every exception carries `status`, `type`, `message`, `fields` when the server sent them,
`retry_after_seconds` when the server said, and `body` for the extra keys a refusal carries. Two
types are synthesised by the SDK and never sent by the API: `APIConnectionError` (the request never
opened) and `APITimeoutError` (it opened and never answered). Both have `status == 0`.

| Exception                                | Covers                                                |
| ---------------------------------------- | ----------------------------------------------------- |
| `AuthenticationError`                    | `unauthorized`, `unauthenticated`, `email_unverified` |
| `PermissionDeniedError`                  | `forbidden` — a real key missing a scope              |
| `InvalidRequestError`                    | `invalid_request`; read `.fields`                     |
| `NotFoundError`                          | `not_found`                                           |
| `ConflictError`                          | `conflict`, `address_taken`                           |
| `UnprocessableError`                     | every other 422 — permanent, never retried            |
| `SuppressedError`                        | `suppressed`; read `.suppression`                     |
| `DomainNotVerifiedError`                 | `domain_not_verified`                                 |
| `ContentBlockedError`                    | `content_blocked`; read `.check` and `.findings`      |
| `AttachmentError`                        | the four attachment refusals                          |
| `ScheduleError`                          | `schedule_too_far`, `not_scheduled`                   |
| `TemplateError` / `StreamError`          | unknown or unusable template / stream                 |
| `RateLimited`                            | `rate_limited`, `platform_paused`                     |
| `QuotaExhausted`                         | `daily_limit_reached`, `monthly_limit_reached`        |
| `ServerError`                            | 5xx                                                   |
| `APIConnectionError` / `APITimeoutError` | never reached the server                              |

A refusal reason added to the API after your SDK version still arrives as a typed exception — the
class is chosen from the HTTP status when the type is unrecognised, never from a `KeyError`.

## Retries

The SDK retries `408`, `429` and `5xx`, and connection failures, with exponential backoff and full
jitter, honouring `Retry-After`. Two rules make that safe rather than merely automatic.

- **Quota is never retried in process.** `daily_limit_reached` and `monthly_limit_reached` raise
  immediately with the wait attached, so you can queue, delay or alert instead of hammering a wall
  the calendar has to move before it opens.
- **Nothing is repeated that repeating could duplicate.** A send is retried _only_ when you supplied
  an `idempotency_key`; `webhooks.create` and `streams.create` are never retried. Reads, deletes,
  `domains.create` (a duplicate is a `409`) and `suppressions.create` (an upsert) are all safe, and
  are retried.

A `Retry-After` longer than `max_retry_delay` (60s by default) is honoured by **not** retrying: a
quota that frees at midnight is not something to block a request handler on, and sleeping through it
would look like a hang.

## Pagination

Every list on the API pages. `list()` returns one page; `auto_paginate()` is a generator over every
page; `list_all()` drains it into a list up to a ceiling you choose.

```python
# One page.
page = posthaste.messages.list(limit=50, status="bounced")
page["data"]        # list[MessageSummary]
page["hasMore"]
page["nextCursor"]  # pass as `before` on the next request

# Every page, as an ordinary for loop. Breaking out stops fetching.
for message in posthaste.messages.auto_paginate(status="bounced"):
    suppress(message["to"])

# Or into a list, with a ceiling you choose. 1,000 by default.
recent = posthaste.messages.list_all(max_items=500, status="bounced")

# Every list has the same three, including the ones that send no `hasMore` at
# all — domains, webhooks, API keys and invoices. This is all sixty invoices,
# not the first fifty.
invoices = posthaste.billing.list_all_invoices()

# Billing history is the odd one out: it pages FORWARD from a sequence number
# in `after`, oldest first. Hence the different names — the helper knows.
for event in posthaste.billing.auto_paginate_history():
    record(event["seq"], event["type"])
```

**Neither stopping rule is correct on its own, which is why the helper exists.** `while
next_cursor:` never terminates on the endpoints that return a non-null cursor on their last page.
And `if not page.get("hasMore"): break` stops after ONE page on `/v1/domains`, `/v1/webhooks`,
`/v1/api-keys`, `/v1/billing/invoices` and `/v1/billing/history`, because those send no `hasMore` at
all — the read is `None`, which is falsy, which looks exactly like "that was everything".
`auto_paginate` consults both signals and treats each as authoritative only where the server
actually sends it.

The `max_items` ceiling on `list_all` is not garnish. Draining an unbounded list into memory is how
a convenience helper becomes an incident on the one account with four million messages, so the
caller chooses the bound.

## Verifying webhooks

`verify_webhook` parses the signature header **by key** rather than positionally (`v1=` exists so a
`v2=` can be added beside it), compares in constant time, and rejects a timestamp more than 300
seconds old _or_ more than 300 seconds in the future — a future timestamp is not clock skew to be
generous about, it is an attacker buying an unlimited replay window.

```python
# Flask
from flask import Flask, request
from posthaste import DELIVERY_ID_HEADER, SIGNATURE_HEADER, verify_webhook

app = Flask(__name__)

@app.post("/hooks/posthaste")
def posthaste_webhook():
    # THE RAW BYTES. request.get_json() would hand you an object, and the
    # signature covers the bytes we sent — not the object they decode to.
    result = verify_webhook(
        request.get_data(),
        request.headers.get(SIGNATURE_HEADER),
        os.environ["POSTHASTE_WEBHOOK_SECRET"],
    )

    if not result:
        # 'malformed_header' | 'unsupported_version' | 'timestamp_too_old'
        # | 'timestamp_in_future' | 'signature_mismatch'
        app.logger.warning("rejected webhook: %s", result.reason)
        return "", 400

    event = json.loads(request.get_data())

    # Deduplicate on the delivery id — a retry is not a second event.
    enqueue(request.headers.get(DELIVERY_ID_HEADER), event)

    # Acknowledge fast; do the work elsewhere. We time out after 10 seconds.
    return "", 204
```

```python
# FastAPI
from fastapi import FastAPI, Request, Response
from posthaste import parse_webhook_event

app = FastAPI()

@app.post("/hooks/posthaste")
async def posthaste_webhook(request: Request):
    raw = await request.body()          # never await request.json()
    event = parse_webhook_event(
        raw,
        request.headers.get("posthaste-signature"),
        os.environ["POSTHASTE_WEBHOOK_SECRET"],
    )
    if event is None:
        return Response(status_code=400)
    await enqueue(request.headers.get("posthaste-delivery-id"), event)
    return Response(status_code=204)
```

```python
# Django
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from posthaste import verify_webhook

@csrf_exempt
def posthaste_webhook(request):
    if not verify_webhook(
        request.body,                   # bytes, exactly as received
        request.headers.get("Posthaste-Signature"),
        settings.POSTHASTE_WEBHOOK_SECRET,
    ):
        return HttpResponse(status=400)
    enqueue(request.headers.get("Posthaste-Delivery-Id"), json.loads(request.body))
    return HttpResponse(status=204)
```

**Verify over the raw bytes.** A body that has been parsed and re-serialised will never verify —
`json.loads` followed by `json.dumps` is not a byte-level round trip, and the signature covers the
bytes we sent, not the object they decode to. This is the single most common reason verification
"mysteriously" fails, and no amount of correct key handling rescues it.

`parse_webhook_event` verifies and JSON-parses in one step, returning `None` on any failure, for the
common case where a bad delivery just gets a `400`. Use `verify_webhook` directly when the reason
matters. The signing secret is the one returned once when the endpoint was created.

## Every method

```text
account.me()                                  GET    /v1/me
account.verify()                              GET    /v1/account/verify
account.usage()                               GET    /v1/usage

domains.create(name)                          POST   /v1/domains
domains.list(...)                             GET    /v1/domains
domains.auto_paginate(...)                    GET    /v1/domains          (all pages)
domains.list_all(...)                         GET    /v1/domains          (all pages)
domains.verify(domain_id)                     POST   /v1/domains/:id/verify
domains.delete(domain_id)                     DELETE /v1/domains/:id
domains.setup(domain_id)                      GET    /v1/domains/:id/setup
domains.connect_cloudflare(domain_id, ...)    POST   /v1/domains/:id/cloudflare
domains.disconnect_cloudflare()               DELETE /v1/account/cloudflare

emails.send(...)                              POST   /v1/emails
emails.send_batch(messages)                   POST   /v1/emails/batch
emails.cancel_schedule(message_id)            DELETE /v1/emails/:id/schedule

messages.list(...)                            GET    /v1/messages
messages.auto_paginate(...)                   GET    /v1/messages         (all pages)
messages.list_all(...)                        GET    /v1/messages         (all pages)
messages.get(message_id)                      GET    /v1/messages/:id
messages.download_attachment(msg, att)        GET    /v1/messages/:id/attachments/:attachmentId
messages.stats(...)                           GET    /v1/stats/messages

suppressions.list(...)                        GET    /v1/suppressions
suppressions.auto_paginate(...)               GET    /v1/suppressions     (all pages)
suppressions.list_all(...)                    GET    /v1/suppressions     (all pages)
suppressions.create(address, reason=...)      POST   /v1/suppressions
suppressions.delete(address)                  DELETE /v1/suppressions/:address

webhooks.create(url, event_types=...)         POST   /v1/webhooks
webhooks.list(...)                            GET    /v1/webhooks
webhooks.auto_paginate(...)                   GET    /v1/webhooks         (all pages)
webhooks.list_all(...)                        GET    /v1/webhooks         (all pages)
webhooks.delete(webhook_id)                   DELETE /v1/webhooks/:id

templates.list()                              GET    /v1/templates
templates.get(template_id)                    GET    /v1/templates/:id
templates.versions(template_id)               GET    /v1/templates/:id/versions
templates.preview(template_id, ...)           POST   /v1/templates/:id/preview
templates.create(...)                         POST   /v1/templates
templates.update(template_id, ...)            PATCH  /v1/templates/:id
templates.delete(template_id)                 DELETE /v1/templates/:id

streams.list()                                GET    /v1/streams
streams.create(slug=..., name=...)            POST   /v1/streams

api_keys.list(...)                            GET    /v1/api-keys
api_keys.auto_paginate(...)                   GET    /v1/api-keys         (all pages)
api_keys.list_all(...)                        GET    /v1/api-keys         (all pages)

billing.get()                                 GET    /v1/billing
billing.history(...)                          GET    /v1/billing/history
billing.auto_paginate_history(...)            GET    /v1/billing/history  (all pages)
billing.list_all_history(...)                 GET    /v1/billing/history  (all pages)
billing.invoices(...)                         GET    /v1/billing/invoices
billing.auto_paginate_invoices(...)           GET    /v1/billing/invoices (all pages)
billing.list_all_invoices(...)                GET    /v1/billing/invoices (all pages)
billing.invoice(invoice_id)                   GET    /v1/billing/invoices/:id
```

**Deliberately absent:** creating and revoking API keys, and everything else that requires a
signed-in person rather than a key — checkout, plan changes, profile edits, team management. Those
endpoints refuse a bearer token outright, and a method that can only ever raise
`PermissionDeniedError` is worse than no method. `/admin/v1/*` is not part of the public surface at
all, and `/v1/inbound/*` is absent because a customer cannot use it: the DNS records handed out on
domain verification contain no MX, so nothing would arrive.

Also absent, and for a different reason: `/v1/analytics/delivery`, `/v1/audit/*` and
`/v1/sub-accounts`. Those are real API-key endpoints that the TypeScript SDK does not cover either;
this package is at parity with it rather than ahead of it, and adding them here first would put the
two clients out of step.

## Development

```bash
python3 -m pytest packages/sdk-python
```

The suite makes no network calls — a socket attempt raises before DNS is consulted, and a test
asserts that the guard is load-bearing rather than decorative. It also refuses to let any file name
an address at a domain we do not own.

The webhook fixtures are generated by the **TypeScript** signer that signs live deliveries, so the
Python verifier is checked against real signatures rather than against itself:

```bash
pnpm exec tsx packages/sdk-python/scripts/generate-webhook-fixtures.ts
```

## Licence

MIT.
