"""Keyset pagination.

The API pages by cursor rather than by offset. Most lists take `limit` +
`before` and answer `{"data", "hasMore", "nextCursor"}` or
`{"data", "nextCursor"}`, where `before` is the id of the last row you received
— which is what `nextCursor` hands you.

THE TWO TRAPS THIS MODULE EXISTS TO CLOSE

They are mirror images, which is why neither `hasMore` nor `nextCursor` alone
is a correct stopping rule across the whole API:

  1. `while next_cursor:` spins for ever on `/v1/inbound/messages`. Its cursor
     is derived from the last row of the page and is set whenever the page has
     any rows at all, including on the final one. A loop over it asks for the
     page after the last, gets an empty page carrying the same cursor, and
     never finishes. Nothing about it looks wrong in a log.

  2. `if not page.get("hasMore"): break` stops after ONE page on `/v1/domains`,
     `/v1/webhooks`, `/v1/api-keys`, `/v1/inbound/addresses`,
     `/v1/billing/invoices` and `/v1/billing/history`. Those endpoints send no
     `hasMore` at all, so the read is `None`, which is falsy, which looks
     exactly like "that was everything". This is the failure that shipped: an
     account with sixty invoices got fifty of them and no indication that ten
     were missing.

So the walker consults BOTH, and treats each as authoritative only where the
server actually sends it: stop when `hasMore` is explicitly `False`, and stop
when the cursor is absent or fails to advance. Every endpoint is covered by at
least one of those, and no endpoint is stopped early by either.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, TypeVar

__all__ = ["auto_paginate", "collect"]

T = TypeVar("T")

#: Which request parameter carries the cursor. `before` everywhere except
#: `/v1/billing/history`, which reads forward from a sequence number in `after`.
CursorParam = str


def auto_paginate(
    fetch_page: Callable[[Dict[str, Any]], Mapping[str, Any]],
    params: Optional[Mapping[str, Any]] = None,
    cursor_param: CursorParam = "before",
) -> Iterator[Any]:
    """Walk every page and yield every item.

    A generator, so `for message in client.messages.auto_paginate(...)` reads
    the way a Python caller expects and only holds one page in memory at a
    time.

    Belt and braces, because an infinite loop in someone's job runner is a bad
    way to learn about a contract change, and a silent truncation is a worse
    one:

      - stops when the server says `hasMore: false`, on the endpoints that say
        it;
      - stops if a page comes back empty, because there is nothing to advance
        to;
      - stops when there is no next cursor, which is how the endpoints without
        `hasMore` signal the end;
      - stops if the cursor does not MOVE, which is what a non-null terminal
        cursor looks like from here.
    """
    base = dict(params or {})
    cursor = base.get(cursor_param)
    seen: Optional[str] = None

    while True:
        request = dict(base)
        request[cursor_param] = cursor
        page = fetch_page(request)

        data: List[Any] = list(page.get("data") or ())
        for item in data:
            yield item

        # `is False`, never `not page.get("hasMore")`: the endpoints that omit
        # the field must fall through to the cursor check rather than be
        # stopped here.
        if page.get("hasMore") is False:
            return
        if not data:
            return

        # Prefer the server's cursor. Fall back to the last row's id — which is
        # what the cursor is anyway on every `before`-paged list — but ONLY when
        # the server has affirmatively said there is another page.
        #
        # That condition is load-bearing, not caution. On the endpoints with no
        # `hasMore`, a null `nextCursor` IS the end-of-list signal; an
        # unconditional fallback would quietly replace it with the last row's
        # id, and the walker would re-request the final page before the "cursor
        # did not move" guard caught it. The fallback exists only for the
        # opposite case — a page that claims `hasMore: true` and sends no
        # cursor — so it is scoped to exactly that.
        #
        # Rows on `/v1/billing/history` have a `seq` and no `id`, so there is
        # nothing to fall back to there in any case. Correct rather than lucky:
        # that endpoint's `nextCursor` is null exactly on the last page.
        next_cursor = page.get("nextCursor")
        if next_cursor is None and page.get("hasMore") is True:
            last = data[-1]
            next_cursor = last.get("id") if isinstance(last, Mapping) else None

        # Either way the NEXT request must ask for something different from the
        # last one.
        if next_cursor is None or next_cursor == seen:
            return

        seen = next_cursor
        cursor = next_cursor


def collect(source: Iterable[T], max_items: int) -> List[T]:
    """Drain an auto-paginated stream into a list.

    `max_items` is not optional garnish. Draining an unbounded list into memory
    is how a helper like this turns into an incident on the one account that
    has four million messages, so there is a ceiling and the caller chooses it.
    """
    out: List[T] = []
    if max_items <= 0:
        return out
    for item in source:
        out.append(item)
        if len(out) >= max_items:
            break
    return out
