"""Contacts, lists and broadcasts, at the wire.

Mostly URLs and bodies, plus the one decision per method this client makes on
the caller's behalf: whether a failed request is repeated. A broadcast is one
request that mails a whole list, so the retry choices are the part worth
pinning down.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from posthaste import Posthaste
from posthaste.errors import ConflictError, PosthasteError
from stub_transport import SleepRecorder, StubTransport, empty_response, json_response, paged

KEY = "ph_test_example_key_0000"


def client(transport: StubTransport) -> Posthaste:
    return Posthaste(
        KEY, transport=transport, sleep=SleepRecorder(), random=lambda: 1.0  # type: ignore[arg-type]
    )


def boom() -> Any:
    return json_response(500, {"error": {"type": "internal", "message": "boom"}})


def contact(contact_id: str) -> Dict[str, Any]:
    return {"id": contact_id, "email": f"{contact_id}@example.com", "suppressed": False}


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


def test_list_contacts_sends_the_list_filter_under_its_wire_name() -> None:
    transport = StubTransport(json_response(200, {**paged([], None, False), "total": 0}))
    page = client(transport).contacts.list(limit=10, search="ada", list_id="lst_a")

    assert transport.last.method == "GET"
    assert transport.last.path.endswith("/v1/contacts")
    assert transport.last.query == "limit=10&search=ada&list=lst_a"
    assert page["total"] == 0


def test_contacts_auto_paginate_follows_before_until_has_more_is_false() -> None:
    transport = StubTransport(
        json_response(200, paged([contact("con_b"), contact("con_a")], "con_a", True)),
        json_response(200, paged([contact("con_0")], None, False)),
    )
    ids = [c["id"] for c in client(transport).contacts.auto_paginate(limit=2)]

    assert ids == ["con_b", "con_a", "con_0"]
    assert len(transport.requests) == 2
    assert "before=con_a" in transport.requests[1].query


def test_create_contact_maps_list_ids_to_lists_and_is_retried() -> None:
    # Safe to repeat: a second create for the same address is a 409, never a
    # second contact.
    transport = StubTransport(boom(), json_response(201, contact("con_x")))
    created = client(transport).contacts.create(
        email="ada@example.com", consent="signed_up", list_ids=["lst_a"]
    )

    assert created["id"] == "con_x"
    assert len(transport.requests) == 2
    assert transport.requests[0].json == {
        "email": "ada@example.com",
        "consent": "signed_up",
        "lists": ["lst_a"],
    }


def test_contact_exists_is_a_conflict_carrying_the_existing_id() -> None:
    transport = StubTransport(
        json_response(
            409, {"error": {"type": "contact_exists", "message": "exists", "id": "con_old"}}
        )
    )
    with pytest.raises(ConflictError) as caught:
        client(transport).contacts.create(email="ada@example.com", consent="customer")

    assert caught.value.type == "contact_exists"
    assert caught.value.body["error"]["id"] == "con_old"


def test_import_posts_the_rows_and_returns_the_report_unchanged() -> None:
    report = {
        "created": 1,
        "updated": 0,
        "invalid": [{"index": 1, "email": "nope", "reason": "not an email address"}],
        "overLimit": 0,
        "suppressed": 0,
        "addedToList": 1,
    }
    transport = StubTransport(json_response(200, report))
    out = client(transport).contacts.import_(
        [{"email": "ada@example.com", "fields": {"plan": "pro"}}, {"email": "nope"}],
        consent="imported_with_consent",
        list_id="lst_a",
    )

    assert transport.last.method == "POST"
    assert transport.last.path.endswith("/v1/contacts/import")
    assert transport.last.json == {
        "contacts": [{"email": "ada@example.com", "fields": {"plan": "pro"}}, {"email": "nope"}],
        "consent": "imported_with_consent",
        "list": "lst_a",
    }
    assert out == report


def test_update_contact_distinguishes_omitted_from_none() -> None:
    transport = StubTransport(
        json_response(200, contact("con_x")), json_response(200, contact("con_x"))
    )
    posthaste = client(transport)
    posthaste.contacts.update("con_x", fields={"plan": "pro"})
    posthaste.contacts.update("con_x", name=None)

    assert transport.requests[0].method == "PATCH"
    # Omitted: the name is left alone, so it must not reach the wire.
    assert transport.requests[0].json == {"fields": {"plan": "pro"}}
    # `None`: the name is cleared, so it must.
    assert transport.requests[1].json == {"name": None}


def test_get_and_delete_contact_escape_the_id() -> None:
    transport = StubTransport(json_response(200, contact("con_x")), empty_response())
    posthaste = client(transport)
    posthaste.contacts.get("con_x/..")
    posthaste.contacts.delete("con_x")

    assert transport.requests[0].path.endswith("/v1/contacts/con_x%2F..")
    assert transport.requests[1].method == "DELETE"
    assert transport.requests[1].path.endswith("/v1/contacts/con_x")


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_lists_are_returned_whole() -> None:
    transport = StubTransport(json_response(200, {"data": []}))
    assert client(transport).lists.list() == {"data": []}
    assert transport.last.path.endswith("/v1/lists")


def test_create_list_is_not_retried() -> None:
    # A repeat after a lost response would be `name_taken` about the list the
    # first attempt made — a failure report carrying no id to recover it by.
    transport = StubTransport(boom(), json_response(201, {"id": "lst_x"}))
    with pytest.raises(PosthasteError):
        client(transport).lists.create(name="Customers")

    assert len(transport.requests) == 1
    assert transport.requests[0].json == {"name": "Customers"}


def test_update_list_can_clear_the_description() -> None:
    transport = StubTransport(json_response(200, {"id": "lst_a"}))
    client(transport).lists.update("lst_a", description=None)
    assert transport.last.method == "PATCH"
    assert transport.last.json == {"description": None}


def test_add_members_is_retried_because_it_is_idempotent() -> None:
    transport = StubTransport(
        boom(), json_response(200, {"added": 1, "alreadyMembers": 1, "notFound": ["con_gone"]})
    )
    out = client(transport).lists.add_members("lst_a", ["con_a", "con_b", "con_gone"])

    assert len(transport.requests) == 2
    assert transport.requests[0].path.endswith("/v1/lists/lst_a/members")
    assert transport.requests[0].json == {"contacts": ["con_a", "con_b", "con_gone"]}
    assert out["notFound"] == ["con_gone"]


def test_remove_member_and_delete_list() -> None:
    transport = StubTransport(empty_response(), empty_response())
    posthaste = client(transport)
    posthaste.lists.remove_member("lst_a", "con_b")
    posthaste.lists.delete("lst_a")

    assert transport.requests[0].method == "DELETE"
    assert transport.requests[0].path.endswith("/v1/lists/lst_a/members/con_b")
    assert transport.requests[1].path.endswith("/v1/lists/lst_a")


# ---------------------------------------------------------------------------
# Broadcasts
# ---------------------------------------------------------------------------


def test_create_broadcast_renames_fields_and_is_never_retried() -> None:
    transport = StubTransport(boom(), json_response(201, {"id": "brd_x"}))
    with pytest.raises(PosthasteError):
        client(transport).broadcasts.create(
            name="September outage notice",
            list_id="lst_a",
            from_="Acme <status@example.com>",
            reply_to="support@example.com",
            subject="We were down for 20 minutes",
            text="Hi {{name}}, here is what happened.",
        )

    # Each call is another draft, so a retry would leave a duplicate behind.
    assert len(transport.requests) == 1
    assert transport.requests[0].path.endswith("/v1/broadcasts")
    assert transport.requests[0].json == {
        "name": "September outage notice",
        "list": "lst_a",
        "from": "Acme <status@example.com>",
        "replyTo": "support@example.com",
        "subject": "We were down for 20 minutes",
        "text": "Hi {{name}}, here is what happened.",
    }


def test_send_now_posts_an_empty_body_and_is_not_retried() -> None:
    transport = StubTransport(boom(), json_response(202, {"id": "brd_x"}))
    with pytest.raises(PosthasteError):
        client(transport).broadcasts.send("brd_x")

    assert len(transport.requests) == 1
    assert transport.requests[0].path.endswith("/v1/broadcasts/brd_x/send")
    assert transport.requests[0].json == {}


def test_send_later_carries_scheduled_at() -> None:
    transport = StubTransport(json_response(202, {"id": "brd_x", "status": "scheduled"}))
    out = client(transport).broadcasts.send("brd_x", scheduled_at="2026-10-01T09:00:00Z")
    assert transport.last.json == {"scheduledAt": "2026-10-01T09:00:00Z"}
    assert out["status"] == "scheduled"


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_the_calls_that_stop_mail_are_retried(action: str) -> None:
    transport = StubTransport(boom(), json_response(200, {"id": "brd_x"}))
    getattr(client(transport).broadcasts, action)("brd_x")

    assert len(transport.requests) == 2
    assert transport.requests[0].method == "POST"
    assert transport.requests[0].path.endswith(f"/v1/broadcasts/brd_x/{action}")


def test_resume_starts_mail_and_is_not_retried() -> None:
    transport = StubTransport(boom(), json_response(200, {"id": "brd_x"}))
    with pytest.raises(PosthasteError):
        client(transport).broadcasts.resume("brd_x")
    assert len(transport.requests) == 1
    assert transport.requests[0].path.endswith("/v1/broadcasts/brd_x/resume")


def test_a_quality_pause_arrives_as_a_conflict_with_its_type() -> None:
    transport = StubTransport(
        json_response(409, {"error": {"type": "quality_pause", "message": "too many bounces"}})
    )
    with pytest.raises(ConflictError) as caught:
        client(transport).broadcasts.resume("brd_x")
    assert caught.value.type == "quality_pause"


def test_get_returns_progress_and_delivery() -> None:
    body = {
        "id": "brd_x",
        "status": "sending",
        "progress": {"targeted": 3, "accepted": 2, "suppressed": 1, "failed": 0, "failures": {}},
        "delivery": {
            "inFlight": 1,
            "delivered": 1,
            "bounced": 0,
            "complained": 0,
            "failed": 0,
            "unsubscribed": 0,
        },
    }
    transport = StubTransport(json_response(200, body))
    out = client(transport).broadcasts.get("brd_x")

    assert transport.last.path.endswith("/v1/broadcasts/brd_x")
    assert out["progress"]["accepted"] == 2
    assert out["delivery"]["inFlight"] == 1


def test_broadcasts_list_all_pages_newest_first() -> None:
    transport = StubTransport(
        json_response(200, paged([{"id": "brd_b"}], "brd_b", True)),
        json_response(200, paged([{"id": "brd_a"}], None, False)),
    )
    out = client(transport).broadcasts.list_all(limit=1)

    assert [b["id"] for b in out] == ["brd_b", "brd_a"]
    assert transport.requests[1].query == "limit=1&before=brd_b"


def test_update_and_delete_a_draft() -> None:
    transport = StubTransport(json_response(200, {"id": "brd_x"}), empty_response())
    posthaste = client(transport)
    posthaste.broadcasts.update("brd_x", template="outage-notice")
    posthaste.broadcasts.delete("brd_x")

    assert transport.requests[0].method == "PATCH"
    assert transport.requests[0].json == {"template": "outage-notice"}
    assert transport.requests[1].method == "DELETE"


def test_test_send_goes_once_to_the_named_teammates() -> None:
    transport = StubTransport(
        boom(), json_response(200, {"data": [{"to": "you@example.com", "id": "msg_t"}]})
    )
    with pytest.raises(PosthasteError):
        client(transport).broadcasts.test("brd_x", ["you@example.com"])

    # Each call mails the test again, so it is never repeated for you.
    assert len(transport.requests) == 1
    assert transport.requests[0].path.endswith("/v1/broadcasts/brd_x/test")
    assert transport.requests[0].json == {"to": ["you@example.com"]}
