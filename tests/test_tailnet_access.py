import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app


def test_exact_tailnet_origin_through_loopback_proxy(store, config):
    origin = "https://workbench.example.ts.net:8443"
    app = create_app(store.root, config, public_origin=origin)
    with TestClient(app, base_url="http://workbench.example.ts.net:8443") as client:
        boot = client.get("/api/bootstrap")
        assert boot.status_code == 200
        op = {"X-BH-Operator": boot.json()["operator_token"], "Origin": origin}
        assert client.get("/api/episodes", headers=op).status_code == 200
        assert client.post("/api/reviews", headers=op, json={}).status_code == 422
        assert (
            client.get("/api/bootstrap", headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
        assert (
            client.get(
                "/api/bootstrap", headers={"Host": "workbench.example.ts.net:9443"}
            ).status_code
            == 403
        )
        assert (
            client.get("/api/bootstrap", headers={"Host": "other.example.ts.net"}).status_code
            == 400
        )
        assert (
            client.get(
                "/api/bootstrap",
                headers={"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"},
            ).status_code
            == 200
        )


def test_tailnet_host_requires_explicit_configuration(store, config):
    with TestClient(create_app(store.root, config)) as client:
        assert (
            client.get(
                "/api/bootstrap", headers={"Host": "workbench.example.ts.net:8443"}
            ).status_code
            == 400
        )


@pytest.mark.parametrize(
    "origin",
    [
        "http://workbench.example.ts.net",
        "https://example.com",
        "https://user:password@workbench.example.ts.net",
        "https://workbench.example.ts.net/path",
        "https://workbench.example.ts.net?x=1",
    ],
)
def test_invalid_external_origin_rejected(store, config, origin):
    with pytest.raises(ValueError, match="INVALID_TAILNET_ORIGIN"):
        create_app(store.root, config, public_origin=origin)
