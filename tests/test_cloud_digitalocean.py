"""The DigitalOcean client against a fake HTTP transport; no request leaves the process."""

import json
import logging

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
        httpx.Response(200, json={"droplets": [DROPLET], "links": next_link}),
        httpx.Response(200, json={"droplets": [DROPLET | {"id": 8}], "links": {}}),
    )
    droplets = client_for(handler).list_droplets("bh-run:r1")
    assert [d.id for d in droplets] == [7, 8]
    assert [dict(r.url.params) for r in seen] == [
        {"tag_name": "bh-run:r1", "page": "1", "per_page": "200"},
        {"tag_name": "bh-run:r1", "page": "2", "per_page": "200"},
    ]


def test_delete_is_by_id_and_404_means_gone():
    handler, seen = replies(httpx.Response(204), httpx.Response(404, json={"id": "not_found"}))
    client = client_for(handler)
    client.delete_droplet(7)
    client.delete_droplet(7)
    assert [(r.method, r.url.path, dict(r.url.params)) for r in seen] == [
        ("DELETE", "/v2/droplets/7", {}), ("DELETE", "/v2/droplets/7", {}),
    ]
    assert not [name for name in dir(client) if "tag" in name and "delete" in name]


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
