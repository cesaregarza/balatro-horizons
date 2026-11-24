"""DigitalOcean API v2 client for Droplets, with an injectable HTTP transport.

The bulk delete-by-tag endpoint is deliberately absent: deletion is by ID only,
after the caller has listed and filtered on both tags.
"""

import os
import time
from datetime import UTC, datetime

import httpx

from .provider import Droplet, DropletSpec, ProviderError
from .settings import TOKEN_VARIABLE

API_URL = "https://api.digitalocean.com/v2"
PAGE_SIZE = 200
MAX_PAGES = 50
MAX_ATTEMPTS = 5


def retryable(status):
    return status == 429 or 500 <= status < 600


class DigitalOceanClient:
    def __init__(self, token, *, transport=None, sleep=time.sleep, base_seconds=1.0,
                 max_seconds=30.0):
        if not token:
            raise ValueError("DIGITALOCEAN_TOKEN_MISSING")
        self._client = httpx.Client(
            base_url=API_URL, transport=transport, timeout=30, trust_env=False,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        self.sleep = sleep
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds

    def __repr__(self):
        return "DigitalOceanClient()"

    def create_droplet(self, spec: DropletSpec) -> Droplet:
        body = {"name": spec.name, "region": spec.region, "size": spec.size,
                "image": spec.image, "tags": list(spec.tags)}
        # A 5xx or dropped connection may follow a successful create, so only a
        # 429, which the API rejects before acting, is retried here.
        response = self.request("POST", "/droplets", json=body, retry_server_errors=False)
        return parse_droplet(expect(response, 202)["droplet"])

    def get_droplet(self, droplet_id):
        response = self.request("GET", f"/droplets/{int(droplet_id)}")
        if response.status_code == 404:
            return None
        return parse_droplet(expect(response, 200)["droplet"])

    def list_droplets(self, tag):
        droplets = []
        for page in range(1, MAX_PAGES + 1):
            params = {"tag_name": tag, "page": page, "per_page": PAGE_SIZE}
            body = expect(self.request("GET", "/droplets", params=params), 200)
            droplets.extend(parse_droplet(item) for item in body.get("droplets", []))
            if not body.get("links", {}).get("pages", {}).get("next"):
                return droplets
        raise ProviderError("TOO_MANY_PAGES")

    def delete_droplet(self, droplet_id):
        response = self.request("DELETE", f"/droplets/{int(droplet_id)}")
        if response.status_code not in (204, 404):
            raise ProviderError(f"HTTP_{response.status_code}")

    def request(self, method, path, *, retry_server_errors=True, **kwargs):
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TransportError:
                if not retry_server_errors or attempt == MAX_ATTEMPTS - 1:
                    raise ProviderError("TRANSPORT_ERROR") from None
                self.sleep(self.delay(attempt, None))
                continue
            if not self.should_retry(response, retry_server_errors, attempt):
                return response
            self.sleep(self.delay(attempt, response))
        raise AssertionError("unreachable")

    def should_retry(self, response, retry_server_errors, attempt):
        if attempt == MAX_ATTEMPTS - 1:
            return False
        if response.status_code == 429:
            return True
        return retry_server_errors and retryable(response.status_code)

    def delay(self, attempt, response):
        hinted = response.headers.get("retry-after") if response is not None else None
        if hinted and hinted.isdigit():
            return min(float(hinted), self.max_seconds)
        return min(self.base_seconds * 2**attempt, self.max_seconds)


def expect(response, status):
    if response.status_code != status:
        raise ProviderError(f"HTTP_{response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise ProviderError("MALFORMED_RESPONSE") from None


def parse_droplet(item):
    try:
        return Droplet(id=int(item["id"]), name=str(item["name"]), status=str(item["status"]),
                       tags=tuple(item.get("tags") or ()),
                       created_at=parse_time(item.get("created_at")))
    except (KeyError, TypeError, ValueError):
        raise ProviderError("MALFORMED_RESPONSE") from None


def parse_time(value):
    """An aware UTC time, or None: a bad timestamp must not block a teardown."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def client_from_environment(**kwargs):
    """Read the token at call time; it is held only inside the HTTP client."""
    return DigitalOceanClient(os.environ.get(TOKEN_VARIABLE, ""), **kwargs)
