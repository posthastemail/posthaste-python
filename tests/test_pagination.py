"""Cursor pagination — and the two ways a hand-written loop gets it wrong."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from posthaste import Posthaste, auto_paginate, collect
from stub_transport import SleepRecorder, StubTransport, json_response, paged

KEY = "ph_test_example_key_0000"


def client(transport: StubTransport) -> Posthaste:
    return Posthaste(KEY, transport=transport, sleep=SleepRecorder(), random=lambda: 1.0)


def row(identifier: str) -> Dict[str, Any]:
    return {"id": identifier}


# ---------------------------------------------------------------------------
# The walker itself
# ---------------------------------------------------------------------------


def test_walks_every_page_using_has_more() -> None:
    pages = [
        paged([row("a"), row("b")], "b", has_more=True),
        paged([row("c")], "c", has_more=False),
    ]
    seen: List[Dict[str, Any]] = []

    def fetch(params: Dict[str, Any]) -> Any:
        seen.append(dict(params))
        return pages.pop(0)

    assert [r["id"] for r in auto_paginate(fetch, {"limit": 2})] == ["a", "b", "c"]
    assert [p.get("before") for p in seen] == [None, "b"]


def test_a_non_null_cursor_on_the_last_page_does_not_loop_for_ever() -> None:
    """Trap one. `/v1/inbound/messages` derives its cursor from the last row and
    sets it whenever the page has any rows at all — including the final one. A
    `while next_cursor` loop asks for the page after the last, gets an empty
    page carrying the same cursor, and spins for ever without erroring."""
    pages = [
        paged([row("a")], "a", has_more=True),
        paged([], "a", has_more=False),
    ]
    assert [r["id"] for r in auto_paginate(lambda p: pages.pop(0), {})] == ["a"]


def test_an_endpoint_with_no_has_more_is_not_stopped_after_one_page() -> None:
    """Trap two, and the one that actually shipped: an account with sixty
    invoices got fifty of them and no indication that ten were missing.

    `page.get("hasMore")` is `None` on these endpoints, which is falsy, which
    looks exactly like "that was everything"."""
    pages = [paged([row("a"), row("b")], "b"), paged([row("c")], None)]
    assert [r["id"] for r in auto_paginate(lambda p: pages.pop(0), {})] == ["a", "b", "c"]


def test_stops_when_the_cursor_does_not_move() -> None:
    """The last-resort guard: an endpoint that keeps handing back the same cursor.

    A `while next_cursor` loop over this never terminates. The walker asks
    twice — it cannot know the second page is a repeat until it has one — and
    then stops, rather than spinning for ever in somebody's job runner.
    """
    fetches = []

    def fetch(params: Dict[str, Any]) -> Any:
        fetches.append(dict(params))
        return paged([row("a")], "a")

    assert len([*auto_paginate(fetch, {})]) == 2
    assert len(fetches) == 2


def test_falls_back_to_the_last_row_id_only_when_has_more_is_true() -> None:
    """A page claiming there is more and sending no cursor is the only case the
    fallback exists for. Applying it unconditionally would override the
    end-of-list signal on every endpoint that has no `hasMore`."""
    pages = [paged([row("a")], None, has_more=True), paged([row("b")], None, has_more=False)]
    seen: List[Dict[str, Any]] = []

    def fetch(params: Dict[str, Any]) -> Any:
        seen.append(dict(params))
        return pages.pop(0)

    assert [r["id"] for r in auto_paginate(fetch, {})] == ["a", "b"]
    assert seen[1]["before"] == "a"


def test_an_empty_first_page_terminates() -> None:
    assert [*auto_paginate(lambda p: paged([], None), {})] == []


def test_the_cursor_parameter_is_configurable() -> None:
    """`/v1/billing/history` reads FORWARD from a sequence number in `after`."""
    pages = [
        {"data": [{"seq": 1}, {"seq": 2}], "nextCursor": "2"},
        {"data": [{"seq": 3}], "nextCursor": None},
    ]
    seen: List[Dict[str, Any]] = []

    def fetch(params: Dict[str, Any]) -> Any:
        seen.append(dict(params))
        return pages.pop(0)

    assert [e["seq"] for e in auto_paginate(fetch, {}, "after")] == [1, 2, 3]
    assert seen[1]["after"] == "2"
    assert "before" not in seen[1]


def test_collect_stops_at_the_ceiling() -> None:
    def endless() -> Any:
        n = 0
        while True:
            yield {"id": str(n)}
            n += 1

    assert len(collect(endless(), 5)) == 5


def test_collect_with_a_zero_ceiling_fetches_nothing() -> None:
    fetched = []

    def once() -> Any:
        fetched.append(1)
        yield {"id": "a"}

    assert collect(once(), 0) == []
    assert fetched == []


# ---------------------------------------------------------------------------
# Wired up to the resources
# ---------------------------------------------------------------------------


def test_messages_auto_paginate_is_a_generator_a_for_loop_consumes() -> None:
    transport = StubTransport(
        json_response(200, paged([row("msg_1"), row("msg_2")], "msg_2", has_more=True)),
        json_response(200, paged([row("msg_3")], "msg_3", has_more=False)),
    )
    ids = [m["id"] for m in client(transport).messages.auto_paginate(status="bounced")]
    assert ids == ["msg_1", "msg_2", "msg_3"]
    assert "status=bounced" in transport.requests[0].query
    assert "before=msg_2" in transport.requests[1].query


def test_breaking_out_of_the_loop_stops_fetching() -> None:
    transport = StubTransport(
        json_response(200, paged([row("msg_1"), row("msg_2")], "msg_2", has_more=True)),
        json_response(200, paged([row("msg_3")], None, has_more=False)),
    )
    for message in client(transport).messages.auto_paginate():
        if message["id"] == "msg_1":
            break
    assert len(transport.requests) == 1


def test_list_all_drains_into_a_list_with_a_ceiling() -> None:
    transport = StubTransport(
        json_response(200, paged([row("msg_1"), row("msg_2")], "msg_2", has_more=True)),
    )
    assert len(client(transport).messages.list_all(max_items=2)) == 2
    assert len(transport.requests) == 1


def test_invoices_page_past_the_first_fifty() -> None:
    """`/v1/billing/invoices` sends no `hasMore` at all."""
    transport = StubTransport(
        json_response(200, paged([row("inv_1")], "inv_1")),
        json_response(200, paged([row("inv_2")], None)),
    )
    assert [i["id"] for i in client(transport).billing.list_all_invoices()] == ["inv_1", "inv_2"]


def test_billing_history_pages_forward_from_after() -> None:
    transport = StubTransport(
        json_response(200, {"data": [{"seq": 1}], "nextCursor": "1", "chain": {"valid": True}}),
        json_response(200, {"data": [{"seq": 2}], "nextCursor": None, "chain": {"valid": True}}),
    )
    assert [e["seq"] for e in client(transport).billing.auto_paginate_history()] == [1, 2]
    assert "after=1" in transport.requests[1].query
    assert "before" not in transport.requests[1].query


@pytest.mark.parametrize(
    "resource", ["domains", "webhooks", "api_keys", "suppressions", "messages"]
)
def test_every_paged_resource_offers_the_same_three_methods(resource: str) -> None:
    api = client(StubTransport())
    obj = getattr(api, resource)
    assert callable(obj.list)
    assert callable(obj.auto_paginate)
    assert callable(obj.list_all)
