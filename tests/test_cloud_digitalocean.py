"""The DigitalOcean client against a fake HTTP transport; no request leaves the process."""

import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
from cloud_support import Cloud, DigitalOceanStub

from balatro_horizons.cli import main
from balatro_horizons.cloud.digitalocean import DigitalOceanClient, client_from_environment
from balatro_horizons.cloud.provider import DropletSpec, ProviderError

TOKEN = "dop_v1_test-token-value-that-must-never-leak"
DROPLET = {"id": 7, "name": "bh-r1", "status": "new", "tags": ["bh-owner:c", "bh-run:r1"]}


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


def test_create_sends_the_spec_with_both_tags_and_bearer_auth():
    handler, seen = replies(httpx.Response(202, json={"droplet": DROPLET}))
    spec = DropletSpec(name="bh-r1", region="nyc3", size="s-2vcpu-8gb-amd",
                       image="ubuntu-24-04-x64", tags=("bh-owner:c", "bh-run:r1"))
    droplet = client_for(handler).create_droplet(spec)
    assert (droplet.id, droplet.tags) == (7, ("bh-owner:c", "bh-run:r1"))
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "https://api.digitalocean.com/v2/droplets")
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {
        "name": "bh-r1", "region": "nyc3", "size": "s-2vcpu-8gb-amd",
        "image": "ubuntu-24-04-x64", "tags": ["bh-owner:c", "bh-run:r1"],
    }


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


def test_action_log_is_read_completely_and_keeps_droplet_creates_since():
    since = datetime(2026, 11, 24, 12, tzinfo=UTC)
    actions = [
        {"id": 1, "type": "create", "resource_type": "droplet", "resource_id": 70,
         "status": "completed", "started_at": "2026-11-24T12:05:00Z"},
        {"id": 2, "type": "destroy", "resource_type": "droplet", "resource_id": 71,
         "status": "completed", "started_at": "2026-11-24T12:06:00Z"},
        {"id": 3, "type": "create", "resource_type": "droplet", "resource_id": 72,
         "status": "completed", "started_at": "2026-11-24T11:00:00Z"},
        {"id": 4, "type": "create", "resource_type": "droplet", "resource_id": None,
         "status": "in-progress", "started_at": "2026-11-24T12:07:00Z"},
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
        (70, "completed"), (None, "in-progress")]
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
        client_for(handler).create_droplet(
            DropletSpec(name="n", region="r", size="s", image="i", tags=()))
    assert len(seen) == 2


def test_create_transport_error_is_not_retried():
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ConnectError("reset")

    with pytest.raises(ProviderError, match="TRANSPORT_ERROR"):
        client_for(handler).create_droplet(
            DropletSpec(name="n", region="r", size="s", image="i", tags=()))
    assert len(seen) == 1


def test_persistent_server_errors_exhaust_retries():
    handler, seen = replies(*[httpx.Response(502)] * 5)
    with pytest.raises(ProviderError, match="HTTP_502"):
        client_for(handler).delete_droplet(7)
    assert len(seen) == 5


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
    cloud.channel.finish("r1", cloud.clock())
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
