"""The DigitalOcean client against a fake HTTP transport; no request leaves the process."""

import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cloud_support import FINGERPRINT, FIREWALL_ID, Cloud, DigitalOceanStub, FakeClock

from balatro_horizons.cli import main
from balatro_horizons.cloud.digitalocean import (
    MAX_PAGES,
    PAGE_SIZE,
    DigitalOceanClient,
    client_from_environment,
)
from balatro_horizons.cloud.provider import DropletSpec, ProviderError

TOKEN = "dop_v1_test-token-value-that-must-never-leak"
DROPLET = {"id": 7, "name": "bh-r1", "status": "new", "tags": ["bh-owner:c", "bh-run:r1"]}
USER_DATA = "#cloud-config\n{}\n"


def client_for(handler, sleeps=None):
    pauses = [] if sleeps is None else sleeps
    return DigitalOceanClient(TOKEN, transport=httpx.MockTransport(handler),
                              sleep=pauses.append)


def replies(*responses):
    queue = list(responses)
    seen = []

    def handler(request):
        seen.append(request)
        return queue.pop(0)

    return handler, seen


def droplet_spec():
    """A spec with the registered key and the user data that every create carries."""
    assert {"ssh_key", "user_data"} <= set(DropletSpec.__dataclass_fields__)
    return DropletSpec(name="bh-r1", region="nyc3", size="s-2vcpu-8gb-amd",
                       image="ubuntu-24-04-x64", tags=("bh-owner:c", "bh-run:r1"),
                       ssh_key=FINGERPRINT, user_data=USER_DATA)


def test_create_sends_the_spec_with_both_tags_and_bearer_auth():
    handler, seen = replies(httpx.Response(202, json={"droplet": DROPLET}))
    droplet = client_for(handler).create_droplet(droplet_spec())
    assert (droplet.id, droplet.tags) == (7, ("bh-owner:c", "bh-run:r1"))
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "https://api.digitalocean.com/v2/droplets")
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {
        "name": "bh-r1", "region": "nyc3", "size": "s-2vcpu-8gb-amd",
        "image": "ubuntu-24-04-x64", "tags": ["bh-owner:c", "bh-run:r1"],
        "ssh_keys": [FINGERPRINT], "user_data": USER_DATA,
    }


@pytest.mark.parametrize("networks,expected", [
    ({"v4": [{"ip_address": "192.0.2.200", "type": "private"},
             {"ip_address": "198.51.100.7", "type": "public"}], "v6": []}, "198.51.100.7"),
    ({"v4": [], "v6": [{"ip_address": "2001:db8::7", "type": "public"}]}, None),
    ({"v4": [{"ip_address": "198.51.100.7/24", "type": "public"}]}, None),
    ({"v4": "198.51.100.7"}, None),
    (None, None),
])
def test_the_public_ipv4_address_is_read_or_left_unknown(networks, expected):
    handler, _ = replies(httpx.Response(200, json={"droplet": DROPLET | {"networks": networks}}))
    assert getattr(client_for(handler).get_droplet(7), "public_ipv4", "not read") == expected


def test_list_by_tag_follows_pagination():
    next_link = {"pages": {"next": "https://api.digitalocean.com/v2/droplets?page=2"}}
    handler, seen = replies(
        httpx.Response(200, json={"droplets": [DROPLET], "links": next_link,
                                  "meta": {"total": 2}}),
        httpx.Response(200, json={"droplets": [DROPLET | {"id": 8}], "links": {},
                                  "meta": {"total": 2}}),
    )
    droplets = client_for(handler).list_droplets("bh-run:r1")
    assert [d.id for d in droplets] == [7, 8]
    assert [dict(r.url.params) for r in seen] == [
        {"tag_name": "bh-run:r1", "page": "1", "per_page": "200"},
        {"tag_name": "bh-run:r1", "page": "2", "per_page": "200"},
    ]


def test_delete_is_by_id_and_only_a_2xx_is_an_acknowledgment():
    handler, seen = replies(httpx.Response(204), httpx.Response(404, json={"id": "not_found"}))
    client = client_for(handler)
    assert client.delete_droplet(7) is True
    # A 404 on DELETE is not an acknowledgment of our DELETE.
    assert client.delete_droplet(7) is False
    assert [(r.method, r.url.path, dict(r.url.params)) for r in seen] == [
        ("DELETE", "/v2/droplets/7", {}), ("DELETE", "/v2/droplets/7", {}),
    ]
    assert not [name for name in dir(client) if "tag" in name and "delete" in name]


@pytest.mark.parametrize("stamp,expected", [
    ("2026-11-24T10:30:00Z", datetime(2026, 11, 24, 10, 30, tzinfo=UTC)),
    ("yesterday", None), (None, None), ("2026-11-24T10:30:00", None),
])
def test_droplet_creation_time_is_parsed_or_left_unknown(stamp, expected):
    handler, _ = replies(httpx.Response(200, json={"droplet": DROPLET | {"created_at": stamp}}))
    assert client_for(handler).get_droplet(7).created_at == expected


def listing(droplets, **extra):
    return httpx.Response(200, json={"droplets": droplets, "links": {},
                                     "meta": {"total": len(droplets)}} | extra)


MALFORMED_LISTINGS = {
    "missing tags": listing([{k: v for k, v in DROPLET.items() if k != "tags"}]),
    "tags not a list": listing([DROPLET | {"tags": "bh-run:r1"}]),
    "boolean id": listing([DROPLET | {"id": True}]),
    "numeric created_at": listing([DROPLET | {"created_at": 1764000000}]),
    "droplets not a list": listing({}),
    "non-string next link": (listing([DROPLET], links={"pages": {"next": 2}}), listing([])),
    "missing meta": httpx.Response(200, json={"droplets": [DROPLET], "links": {}}),
    "total disagrees": listing([DROPLET], meta={"total": 3}),
}


@pytest.mark.parametrize("case", MALFORMED_LISTINGS)
def test_malformed_listing_is_a_failed_listing_never_an_empty_one(case):
    responses = MALFORMED_LISTINGS[case]
    handler, _ = replies(*(responses if isinstance(responses, tuple) else (responses,)))
    with pytest.raises(ProviderError):
        client_for(handler).list_droplets("bh-run:r1")


def test_name_filter_lists_exact_names_without_a_tag_filter():
    handler, seen = replies(listing([DROPLET]))
    method = getattr(client_for(handler), "find_droplets_by_name", None)
    assert method is not None
    assert [d.id for d in method("bh-r1")] == [7]
    assert dict(seen[0].url.params) == {"name": "bh-r1", "page": "1", "per_page": "200"}


def test_action_log_keeps_droplet_creates_since():
    since = datetime(2026, 11, 24, 12, tzinfo=UTC)
    # Newest first, as the live log is.
    actions = [
        {"id": 4, "type": "create", "resource_type": "droplet", "resource_id": None,
         "status": "in-progress", "started_at": "2026-11-24T12:07:00Z"},
        {"id": 2, "type": "destroy", "resource_type": "droplet", "resource_id": 71,
         "status": "completed", "started_at": "2026-11-24T12:06:00Z"},
        {"id": 1, "type": "create", "resource_type": "droplet", "resource_id": 70,
         "status": "completed", "started_at": "2026-11-24T12:05:00Z"},
        {"id": 3, "type": "create", "resource_type": "droplet", "resource_id": 72,
         "status": "completed", "started_at": "2026-11-24T11:00:00Z"},
    ]
    next_link = {"pages": {"next": "https://api.digitalocean.com/v2/actions?page=2"}}
    handler, seen = replies(
        httpx.Response(200, json={"actions": actions[:2], "links": next_link,
                                  "meta": {"total": 4}}),
        httpx.Response(200, json={"actions": actions[2:], "links": {}, "meta": {"total": 4}}),
    )
    method = getattr(client_for(handler), "list_create_actions", None)
    assert method is not None
    # A create with no resource ID yet is kept: it may be the one still being made.
    assert [(a.resource_id, getattr(a, "status", None)) for a in method(since)] == [
        (None, "in-progress"), (70, "completed")]
    assert [r.url.path for r in seen] == ["/v2/actions", "/v2/actions"]


@pytest.mark.parametrize("status", [None, 3, "pending"])
def test_action_with_an_unknown_status_is_a_failed_read(status):
    bad = {"id": 1, "type": "create", "resource_type": "droplet", "resource_id": 70,
           "status": status, "started_at": "2026-11-24T12:05:00Z"}
    handler, _ = replies(httpx.Response(200, json={"actions": [bad], "links": {},
                                                   "meta": {"total": 1}}))
    with pytest.raises(ProviderError):
        client_for(handler).list_create_actions(datetime(2026, 11, 24, tzinfo=UTC))


def test_action_log_with_a_malformed_entry_is_a_failed_read():
    bad = {"id": 1, "type": "create", "resource_type": "droplet", "resource_id": "70",
           "started_at": "soon"}
    handler, _ = replies(httpx.Response(200, json={"actions": [bad], "links": {},
                                                   "meta": {"total": 1}}))
    method = getattr(client_for(handler), "list_create_actions", None)
    assert method is not None
    with pytest.raises(ProviderError):
        method(datetime(2026, 11, 24, tzinfo=UTC))


def client_method(handler, name):
    method = getattr(client_for(handler), name, None)
    assert method is not None, f"the client has {name}"
    return method


def test_a_key_is_registered_only_when_the_account_holds_its_fingerprint():
    other = "ff:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:00"
    handler, seen = replies(
        httpx.Response(200, json={"ssh_key": {"id": 512, "fingerprint": FINGERPRINT}}),
        httpx.Response(404, json={"id": "not_found"}),
        httpx.Response(200, json={"ssh_key": {"id": 512, "fingerprint": other}}),
        httpx.Response(200, json={"ssh_key": {"id": 512}}),
    )
    registered = client_method(handler, "ssh_key_registered")
    assert [registered(FINGERPRINT) for _ in range(3)] == [True, False, False]
    with pytest.raises(ProviderError):
        registered(FINGERPRINT)
    assert {r.url.path for r in seen} == {f"/v2/account/keys/{FINGERPRINT}"}


FIREWALL = {"id": FIREWALL_ID, "name": "workers", "tags": ["bh-owner:c"],
            "inbound_rules": [{"protocol": "tcp", "ports": "22",
                               "sources": {"addresses": ["203.0.113.7/32"], "tags": ["web"]}}],
            "outbound_rules": []}


def test_a_firewall_is_read_by_id_with_every_source_it_admits():
    handler, seen = replies(httpx.Response(200, json={"firewall": FIREWALL}),
                            httpx.Response(404, json={"id": "not_found"}))
    get_firewall = client_method(handler, "get_firewall")
    firewall = get_firewall(FIREWALL_ID)
    assert firewall.tags == ("bh-owner:c",)
    assert [(rule.protocol, rule.ports, rule.sources) for rule in firewall.inbound_rules] == [
        ("tcp", "22", (("addresses", "203.0.113.7/32"), ("tags", "web")))]
    assert get_firewall(FIREWALL_ID) is None
    assert {r.url.path for r in seen} == {f"/v2/firewalls/{FIREWALL_ID}"}


@pytest.mark.parametrize("firewall", [
    {"tags": ["bh-owner:c"]},
    FIREWALL | {"tags": "bh-owner:c"},
    FIREWALL | {"inbound_rules": [{"protocol": "tcp", "ports": "22"}]},
    FIREWALL | {"inbound_rules": [{"protocol": "tcp", "ports": 22, "sources": {}}]},
    FIREWALL | {"inbound_rules": [{"protocol": "tcp", "sources": {"addresses": "0.0.0.0/0"}}]},
])
def test_a_malformed_firewall_is_a_failed_read(firewall):
    handler, _ = replies(httpx.Response(200, json={"firewall": firewall}))
    with pytest.raises(ProviderError):
        client_method(handler, "get_firewall")(FIREWALL_ID)


SIZE = {"slug": "s-2vcpu-8gb-amd", "available": True, "price_hourly": 0.0625,
        "regions": ["nyc3", "sfo3"]}


def test_sizes_are_read_across_every_page():
    next_link = {"pages": {"next": "https://api.digitalocean.com/v2/sizes?page=2"}}
    handler, seen = replies(
        httpx.Response(200, json={"sizes": [SIZE], "links": next_link, "meta": {"total": 2}}),
        httpx.Response(200, json={"sizes": [SIZE | {"slug": "s-1vcpu-1gb", "price_hourly": 1}],
                                  "links": {}, "meta": {"total": 2}}),
    )
    sizes = client_method(handler, "list_sizes")()
    assert [(s.slug, s.available, s.price_hourly, s.regions) for s in sizes] == [
        ("s-2vcpu-8gb-amd", True, 0.0625, ("nyc3", "sfo3")),
        ("s-1vcpu-1gb", True, 1.0, ("nyc3", "sfo3"))]
    assert [r.url.path for r in seen] == ["/v2/sizes", "/v2/sizes"]


@pytest.mark.parametrize("change", [{"price_hourly": "0.0625"}, {"price_hourly": True},
                                    {"available": "yes"}, {"regions": "nyc3"}, {"slug": None}])
def test_a_malformed_size_is_a_failed_read(change):
    handler, _ = replies(httpx.Response(200, json={"sizes": [SIZE | change], "links": {},
                                                   "meta": {"total": 1}}))
    with pytest.raises(ProviderError):
        client_method(handler, "list_sizes")()


def test_only_droplet_destroys_are_kept():
    def action(action_id, kind="destroy", resource="droplet", status="completed", hour=12):
        return {"id": action_id, "type": kind, "resource_type": resource, "resource_id": 70,
                "status": status, "started_at": f"2026-11-24T{hour}:05:00Z"}

    actions = [action(1), action(2, kind="create"), action(3, resource="volume"),
               action(4, status="errored", hour=11)]
    next_link = {"pages": {"next": "https://api.digitalocean.com/v2/actions?page=2"}}
    handler, seen = replies(
        httpx.Response(200, json={"actions": actions[:2], "links": next_link,
                                  "meta": {"total": 4}}),
        httpx.Response(200, json={"actions": actions[2:], "links": {}, "meta": {"total": 4}}),
        httpx.Response(200, json={"actions": [action("1")], "links": {}, "meta": {"total": 1}}),
    )
    destroys = client_method(handler, "list_destroy_actions")
    since = datetime(2026, 11, 24, 11, tzinfo=UTC)
    assert [(a.id, a.resource_id, a.status, a.started_at) for a in destroys(since)] == [
        (1, 70, "completed", datetime(2026, 11, 24, 12, 5, tzinfo=UTC)),
        (4, 70, "errored", datetime(2026, 11, 24, 11, 5, tzinfo=UTC))]
    assert [r.url.path for r in seen] == ["/v2/actions", "/v2/actions"]
    with pytest.raises(ProviderError):
        destroys(since)


# A made-up action log, newest first: the entry at each position started ten seconds
# before the one above it.
NEWEST = datetime(2026, 11, 24, 12, tzinfo=UTC)


def at(position):
    return NEWEST - timedelta(seconds=10 * position)


def logged(position, kind="power_on", status="completed", started=None, **change):
    """The log's entry at `position`, counted from the newest."""
    return {"id": 900000 - position, "type": kind, "resource_type": "droplet",
            "resource_id": 7000 + position, "status": status,
            "started_at": (started or at(position)).strftime("%Y-%m-%dT%H:%M:%SZ")} | change


def log_of(count):
    """A log of `count` entries, none of them a create or a destroy."""
    return [logged(position) for position in range(count)]


class ActionLog:
    """Serves `entries` in pages of the size each request asks for, and notes each page."""

    def __init__(self, entries):
        self.entries, self.pages = entries, []

    def __call__(self, request):
        page, size = int(request.url.params["page"]), int(request.url.params["per_page"])
        self.pages.append(page)
        following = {"next": f"https://api.digitalocean.com/v2/actions?page={page + 1}"}
        return httpx.Response(200, json={
            "actions": self.entries[(page - 1) * size:page * size],
            "links": {"pages": following} if page * size < len(self.entries) else {},
            "meta": {"total": len(self.entries)}})


def long_log():
    """A log longer than a full read can cover, with a run's create and destroy on page 1."""
    log = log_of(MAX_PAGES * PAGE_SIZE + 1)
    log[3], log[90] = logged(3, kind="destroy"), logged(90, kind="create")
    return log


def test_a_log_too_long_to_read_in_full_shows_the_runs_destroy_from_page_one_alone():
    log = ActionLog(long_log())
    destroys = client_for(log).list_destroy_actions(at(120))
    assert ([(a.id, a.resource_id, a.status) for a in destroys], log.pages) == (
        [(900000 - 3, 7003, "completed")], [1])


def test_the_create_path_reads_the_same_long_log_from_page_one_alone():
    log = ActionLog(long_log())
    creates = client_for(log).list_create_actions(at(120))
    assert ([(a.resource_id, a.status) for a in creates], log.pages) == (
        [(7090, "completed")], [1])


def test_a_window_reaching_past_page_one_reads_exactly_two_pages():
    log = ActionLog(log_of(3 * PAGE_SIZE))
    log.entries[250] = logged(250, kind="destroy")
    # Every entry on page 1 started after `since`.
    destroys = client_for(log).list_destroy_actions(at(300))
    assert ([a.resource_id for a in destroys], log.pages) == ([7250], [1, 2])


def test_an_action_started_at_since_counts_and_one_a_second_earlier_does_not():
    last = PAGE_SIZE - 1
    since = at(last)
    log = ActionLog(log_of(3 * PAGE_SIZE))
    # Page 1 ends with a destroy started at `since`, and page 2 starts with one a second
    # earlier, so page 2 is the last read.
    log.entries[last] = logged(last, kind="destroy")
    log.entries[last + 1] = logged(last + 1, kind="destroy", started=since - timedelta(seconds=1))
    destroys = client_for(log).list_destroy_actions(since)
    assert ([a.resource_id for a in destroys], log.pages) == ([7000 + last], [1, 2])


@pytest.mark.parametrize("position", [50, PAGE_SIZE], ids=["within_a_page", "across_pages"])
def test_an_entry_that_started_after_the_one_before_it_fails_the_read(position):
    log = ActionLog(log_of(3 * PAGE_SIZE))
    log.entries[position] = logged(position, started=at(position - 1) + timedelta(seconds=1))
    with pytest.raises(ProviderError, match="ACTION_LOG_ORDER"):
        client_for(log).list_destroy_actions(at(2 * PAGE_SIZE))


def test_a_page_shifted_by_newer_actions_repeats_entries_and_misses_none():
    log = ActionLog(log_of(3 * PAGE_SIZE))
    # A run's destroy is still in progress near the end of page 1.
    tail = PAGE_SIZE - 2
    log.entries[5], log.entries[250] = logged(5, kind="destroy"), logged(250, kind="destroy")
    log.entries[tail] = logged(tail, kind="destroy", status="in-progress")

    def shifted(request):
        page = log(request)
        if log.pages == [1]:
            # Three newer actions arrive and the destroy completes before page 2 is read,
            # so page 2 starts with the last three entries of page 1.
            log.entries = [logged(-3), logged(-2), logged(-1)] + log.entries
            log.entries[3 + tail] = logged(tail, kind="destroy")
        return page

    destroys = client_for(shifted).list_destroy_actions(at(300))
    assert ([(a.resource_id, a.status) for a in destroys], log.pages) == (
        [(7005, "completed"), (7000 + tail, "completed"), (7250, "completed")], [1, 2])


def test_a_window_not_reached_within_the_page_bound_fails_the_read():
    log = ActionLog(long_log())
    with pytest.raises(ProviderError, match="TOO_MANY_PAGES"):
        client_for(log).list_destroy_actions(at(MAX_PAGES * PAGE_SIZE + 1))
    assert log.pages == list(range(1, MAX_PAGES + 1))


@pytest.mark.parametrize("value", ["900000", None, True, 1.5])
def test_an_entry_whose_id_is_not_a_whole_number_fails_the_read(value):
    log = ActionLog(log_of(3) + [logged(3, kind="create")])
    log.entries[1] = logged(1, id=value)
    with pytest.raises(ProviderError, match="MALFORMED_RESPONSE"):
        client_for(log).list_create_actions(at(10))


def test_get_reads_404_as_gone():
    handler, _ = replies(httpx.Response(404, json={"id": "not_found"}))
    assert client_for(handler).get_droplet(7) is None


def test_429_and_5xx_are_retried_honoring_retry_after():
    handler, seen = replies(
        httpx.Response(429, headers={"retry-after": "3"}),
        httpx.Response(503),
        httpx.Response(200, json={"droplet": DROPLET}),
    )
    sleeps = []
    assert client_for(handler, sleeps).get_droplet(7).id == 7
    assert (len(seen), sleeps) == (3, [3.0, 2.0])


def test_create_retries_429_but_never_a_5xx_that_may_have_created():
    handler, seen = replies(httpx.Response(429), httpx.Response(500))
    with pytest.raises(ProviderError, match="HTTP_500"):
        client_for(handler).create_droplet(droplet_spec())
    assert len(seen) == 2


def test_create_transport_error_is_not_retried():
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ConnectError("reset")

    with pytest.raises(ProviderError, match="TRANSPORT_ERROR"):
        client_for(handler).create_droplet(droplet_spec())
    assert len(seen) == 1


def test_persistent_server_errors_exhaust_retries():
    handler, seen = replies(*[httpx.Response(502)] * 5)
    with pytest.raises(ProviderError, match="HTTP_502"):
        client_for(handler).delete_droplet(7)
    assert len(seen) == 5


# A read is answered in steps, each a pause in seconds and then the bytes it sends: the
# headers come after the first pause, and the body after the others, in parts. Each pause
# fits in the limit the attempt carries on one phase of a request, which is the whole
# budget, but all of them together take longer than the budget. The budget runs on the real
# clock: a blocked attempt is waited on until `time_left` finds no time left, so blocking
# one against a fake clock that never advances would wait forever.
BUDGET, EARLY, LATE = 1.2, 0.05, 0.5
BODY = json.dumps({"droplet": DROPLET}).encode()
TWO_WAITS = 1.0, [(1.0, BODY)]
DRIP = 0, [(0.4, BODY[len(BODY) * i // 6:len(BODY) * (i + 1) // 6]) for i in range(6)]


class Parts(httpx.SyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    def __iter__(self):
        for pause, part in self.parts:
            time.sleep(pause)
            yield part


def paced(steps):
    """A transport that answers in `steps`, noting for each request the thread that sent it
    and the limit it carries on one phase."""
    first, parts = steps
    seen = []

    def handler(request):
        seen.append((threading.get_ident(), request.extensions["timeout"]["read"]))
        time.sleep(first)
        return httpx.Response(200, headers={"content-length": str(len(BODY))},
                              stream=Parts(parts))

    return httpx.MockTransport(handler), seen


@pytest.mark.parametrize("steps", [TWO_WAITS, DRIP], ids=["two_waits", "drip"])
def test_a_budgeted_read_ends_at_its_budget_though_each_phase_is_in_time(steps):
    transport, seen = paced(steps)
    client = DigitalOceanClient(TOKEN, transport=transport)
    started = time.monotonic()
    try:
        outcome = client.get_droplet(7, time_left=lambda: started + BUDGET - time.monotonic())
    except ProviderError as error:
        outcome = str(error)
    elapsed = time.monotonic() - started
    pauses = [steps[0], *(pause for pause, _ in steps[1])]
    assert (outcome, BUDGET - EARLY <= elapsed < BUDGET + LATE, max(pauses) < seen[0][1]) == (
        "NO_TIME_LEFT", True, True)


def test_a_cleanup_read_waits_for_the_whole_answer_on_the_calling_thread():
    # The Droplet arrives only after both pauses.
    transport, seen = paced(TWO_WAITS)
    try:
        outcome = DigitalOceanClient(TOKEN, transport=transport).get_droplet(7).id
    except ProviderError as error:
        outcome = str(error)
    assert (outcome, [thread for thread, _ in seen]) == (7, [threading.get_ident()])


def destroy_listing(pages, budget, refused=()):
    """List the destroy actions from a log of `pages` empty pages, each answered 20 seconds
    on a fake clock, within `budget` seconds, or with no budget when it is None. With no
    entries to reach back past `since`, every page is read. The first request for a page in
    `refused` is answered 429 with a 30 second retry-after. Returns the outcome, each
    request's page with the limit it carried on one phase, and the waits."""
    clock, seen, waits = FakeClock(), [], []
    started = clock()

    def handler(request):
        page = int(request.url.params["page"])
        seen.append((page, request.extensions["timeout"]["read"]))
        if page in refused and [p for p, _ in seen].count(page) == 1:
            return httpx.Response(429, headers={"retry-after": "30"})
        clock.advance(20)
        following = {"next": f"https://api.digitalocean.com/v2/actions?page={page + 1}"}
        return httpx.Response(200, json={"actions": [], "meta": {"total": 0},
                                         "links": {"pages": following} if page < pages else {}})

    def wait(seconds):
        waits.append(seconds)
        clock.advance(seconds)

    def time_left():
        return budget - (clock() - started).total_seconds()

    read = DigitalOceanClient(TOKEN, transport=httpx.MockTransport(handler),
                              sleep=wait).list_destroy_actions
    try:
        outcome = len(read(started) if budget is None else read(started, time_left=time_left))
    except ProviderError as error:
        outcome = str(error)
    return outcome, seen, waits


@pytest.mark.parametrize("pages,budget,refused,seen,waits", [
    # Each attempt's limit is cut to the time left, and no page starts once none is.
    (4, 50, (), [(1, 30), (2, 30), (3, 10)], []),
    # A wait before a retry is cut to the time left too.
    (3, 45, (2,), [(1, 30), (2, 25)], [25]),
    (1, 0, (), [], []),
], ids=["attempts", "waits", "none_left"])
def test_a_budgeted_destroy_listing_fits_every_page_in_the_time_left(pages, budget, refused,
                                                                      seen, waits):
    assert destroy_listing(pages, budget, refused) == ("NO_TIME_LEFT", seen, waits)


def test_an_unbudgeted_destroy_listing_keeps_its_full_retry_policy():
    assert destroy_listing(3, None, refused=(2,)) == (
        0, [(1, 30), (2, 30), (2, 30), (3, 30)], [30])


def test_missing_token_is_refused_before_any_request(monkeypatch):
    monkeypatch.delenv("DIGITALOCEAN_TOKEN", raising=False)
    with pytest.raises(ValueError, match="DIGITALOCEAN_TOKEN_MISSING"):
        client_from_environment()


def test_token_never_reaches_ledger_logs_user_data_or_status(
    tmp_path, monkeypatch, caplog, capsys,
):
    monkeypatch.setenv("DIGITALOCEAN_TOKEN", TOKEN)
    caplog.set_level(logging.DEBUG)
    stub = DigitalOceanStub()
    cloud = Cloud(tmp_path / "data/cloud")
    cloud.provider = client_from_environment(transport=httpx.MockTransport(stub),
                                             sleep=cloud.clock.advance)
    controller = cloud.launched()
    cloud.channel.finish("r1")
    controller.tick()
    assert cloud.ledger.get("r1").state == "torn_down"
    config = tmp_path / "cloud.yaml"
    config.write_text(json.dumps(cloud.settings.model_dump()))
    main(["cloud", "status", "--data-dir", str(tmp_path / "data"), "--cloud-config", str(config)])
    written = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    bodies = "".join(r.content.decode() for r in stub.requests)
    assert stub.requests and all(r.headers["authorization"] == f"Bearer {TOKEN}"
                                 for r in stub.requests)
    for surface in (written, caplog.text, capsys.readouterr().out, bodies, repr(cloud.provider)):
        assert TOKEN not in surface
