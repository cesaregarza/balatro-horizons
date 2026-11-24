"""DigitalOcean API v2 client for Droplets, with an injectable HTTP transport.

The bulk delete-by-tag endpoint is deliberately absent: deletion is by ID only,
after the caller has listed and filtered on both tags.
"""

import os
import time
from datetime import UTC, datetime

import httpx

from .provider import CreateAction, Droplet, DropletSpec, ProviderError
from .settings import TOKEN_VARIABLE

API_URL = "https://api.digitalocean.com/v2"
PAGE_SIZE = 200
MAX_PAGES = 50
MAX_ATTEMPTS = 5


# The statuses the API documents for an action.
ACTION_STATUSES = ("in-progress", "completed", "errored")

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
        return parse_droplet(expect(response, 202).get("droplet"))

    def get_droplet(self, droplet_id):
        response = self.request("GET", f"/droplets/{int(droplet_id)}")
        if response.status_code == 404:
            return None
        return parse_droplet(expect(response, 200).get("droplet"))

    def list_droplets(self, tag):
        return [parse_droplet(item) for item in self.read_all("/droplets", "droplets",
                                                              {"tag_name": tag})]

    def find_droplets_by_name(self, name):
        # The API matches names exactly, ignoring case, and refuses name with tag_name.
        return [parse_droplet(item) for item in self.read_all("/droplets", "droplets",
                                                              {"name": name})]

    def list_create_actions(self, since):
        # The API documents no order for the action log, so every page is read.
        actions = [parse_action(item) for item in self.read_all("/actions", "actions", {})]
        return [CreateAction(resource_id=a["resource_id"], started_at=a["started_at"],
                             status=a["status"])
                for a in actions
                if a["type"] == "create" and a["resource_type"] == "droplet"
                and a["started_at"] >= since]

    def read_all(self, path, key, filters):
        """Every item across pages; anything short of a complete, well-formed read raises."""
        items = []
        for page in range(1, MAX_PAGES + 1):
            params = filters | {"page": page, "per_page": PAGE_SIZE}
            body = expect(self.request("GET", path, params=params), 200)
            batch, more, total = page_parts(body, key)
            items.extend(batch)
            if not more:
                if len(items) != total:
                    raise ProviderError("INCOMPLETE_LISTING")
                return items
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
        body = response.json()
    except ValueError:
        raise ProviderError("MALFORMED_RESPONSE") from None
    if not isinstance(body, dict):
        raise ProviderError("MALFORMED_RESPONSE")
    return body


def page_parts(body, key):
    """The items, whether another page follows, and the reported total."""
    items = body.get(key) if isinstance(body, dict) else None
    links = body.get("links", {}) if isinstance(body, dict) else None
    meta = body.get("meta") if isinstance(body, dict) else None
    pages = links.get("pages", {}) if isinstance(links, dict) else None
    following = pages.get("next") if isinstance(pages, dict) else None
    total = meta.get("total") if isinstance(meta, dict) else None
    if not isinstance(items, list) or not isinstance(total, int) or isinstance(total, bool):
        raise ProviderError("MALFORMED_RESPONSE")
    if following is not None and not isinstance(following, str):
        raise ProviderError("MALFORMED_RESPONSE")
    return items, bool(following), total


def whole_number(value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProviderError("MALFORMED_RESPONSE")
    return value


def parse_droplet(item):
    if not isinstance(item, dict):
        raise ProviderError("MALFORMED_RESPONSE")
    tags, created = item.get("tags"), item.get("created_at")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ProviderError("MALFORMED_RESPONSE")
    if created is not None and not isinstance(created, str):
        raise ProviderError("MALFORMED_RESPONSE")
    name, status = item.get("name"), item.get("status")
    if not isinstance(name, str) or not isinstance(status, str):
        raise ProviderError("MALFORMED_RESPONSE")
    return Droplet(id=whole_number(item.get("id")), name=name, status=status,
                   tags=tuple(tags), created_at=parse_time(created))


def parse_action(item):
    if not isinstance(item, dict):
        raise ProviderError("MALFORMED_RESPONSE")
    started = parse_time(item.get("started_at"))
    kind, resource_type = item.get("type"), item.get("resource_type")
    resource_id, status = item.get("resource_id"), item.get("status")
    if started is None or not isinstance(kind, str) or not isinstance(resource_type, str):
        raise ProviderError("MALFORMED_RESPONSE")
    if status not in ACTION_STATUSES:
        raise ProviderError("MALFORMED_RESPONSE")
    if resource_id is not None:
        whole_number(resource_id)
    return {"type": kind, "resource_type": resource_type, "resource_id": resource_id,
            "started_at": started, "status": status}


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
