"""DigitalOcean API v2 client for Droplets and the preflight reads, with an injectable transport.

The bulk delete-by-tag endpoint is deliberately absent: deletion is by ID only,
after the caller has listed and filtered on both tags.
"""

import ipaddress
import os
import threading
import time
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .provider import (
    CreateAction,
    DestroyAction,
    Droplet,
    DropletSpec,
    Firewall,
    InboundRule,
    ProviderError,
    Size,
)
from .settings import TOKEN_VARIABLE

API_URL = "https://api.digitalocean.com/v2"
PAGE_SIZE = 200
MAX_PAGES = 50
MAX_ATTEMPTS = 5
# The limit on each phase of an attempt. httpx applies it separately to connecting, to each
# write and each read, and to waiting for a pooled connection.
REQUEST_SECONDS = 30


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
            base_url=API_URL, transport=transport, timeout=REQUEST_SECONDS, trust_env=False,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        self.sleep = sleep
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds

    def __repr__(self):
        return "DigitalOceanClient()"

    def create_droplet(self, spec: DropletSpec) -> Droplet:
        body = {"name": spec.name, "region": spec.region, "size": spec.size,
                "image": spec.image, "tags": list(spec.tags), "ssh_keys": [spec.ssh_key],
                "user_data": spec.user_data}
        # A 5xx or dropped connection may follow a successful create, so only a
        # 429, which the API rejects before acting, is retried here.
        response = self.request("POST", "/droplets", json=body, retry_server_errors=False)
        return parse_droplet(expect(response, 202).get("droplet"))

    def get_droplet(self, droplet_id, *, time_left=None):
        response = self.request("GET", f"/droplets/{int(droplet_id)}", time_left=time_left)
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
        return [CreateAction(resource_id=a["resource_id"], started_at=a["started_at"],
                             status=a["status"])
                for a in self.droplet_actions("create") if a["started_at"] >= since]

    def list_destroy_actions(self, *, time_left=None):
        return [DestroyAction(id=whole_number(a["id"]), resource_id=a["resource_id"],
                              started_at=a["started_at"], status=a["status"])
                for a in self.droplet_actions("destroy", time_left)]

    def droplet_actions(self, kind, time_left=None):
        # The API documents no order for the action log, so every page is read.
        actions = [parse_action(item)
                   for item in self.read_all("/actions", "actions", {}, time_left)]
        return [a for a in actions if a["type"] == kind and a["resource_type"] == "droplet"]

    def ssh_key_registered(self, fingerprint):
        response = self.request("GET", f"/account/keys/{quote(fingerprint, safe=':')}")
        if response.status_code == 404:
            return False
        key = expect(response, 200).get("ssh_key")
        if not isinstance(key, dict) or not isinstance(key.get("fingerprint"), str):
            raise ProviderError("MALFORMED_RESPONSE")
        return key["fingerprint"] == fingerprint

    def get_firewall(self, firewall_id):
        response = self.request("GET", f"/firewalls/{quote(firewall_id, safe='')}")
        if response.status_code == 404:
            return None
        return parse_firewall(expect(response, 200).get("firewall"))

    def list_sizes(self):
        return [parse_size(item) for item in self.read_all("/sizes", "sizes", {})]

    def read_all(self, path, key, filters, time_left=None):
        """Every item across pages; anything short of a complete, well-formed read raises.
        With `time_left`, every page is a budgeted request, so the budget bounds the whole."""
        items = []
        for page in range(1, MAX_PAGES + 1):
            params = filters | {"page": page, "per_page": PAGE_SIZE}
            body = expect(self.request("GET", path, params=params, time_left=time_left), 200)
            batch, more, total = page_parts(body, key)
            items.extend(batch)
            if not more:
                if len(items) != total:
                    raise ProviderError("INCOMPLETE_LISTING")
                return items
        raise ProviderError("TOO_MANY_PAGES")

    def delete_droplet(self, droplet_id):
        """True when the API acknowledged our DELETE; a 404 is not an acknowledgment."""
        response = self.request("DELETE", f"/droplets/{int(droplet_id)}")
        if response.status_code == 404:
            return False
        if not 200 <= response.status_code < 300:
            raise ProviderError(f"HTTP_{response.status_code}")
        return True

    def request(self, method, path, *, retry_server_errors=True, time_left=None, **kwargs):
        """Send with the retries `should_retry` allows. With `time_left`, which returns the
        seconds left, no attempt starts or is waited on once none is left, and the limit on
        each phase of an attempt and each wait before a retry are cut to fit."""
        for attempt in range(MAX_ATTEMPTS):
            if time_left is not None:
                left = time_left()
                if left <= 0:
                    raise ProviderError("NO_TIME_LEFT")
                kwargs["timeout"] = min(REQUEST_SECONDS, left)
            try:
                response = self.send(method, path, kwargs, time_left)
            except httpx.TransportError:
                if not retry_server_errors or attempt == MAX_ATTEMPTS - 1:
                    raise ProviderError("TRANSPORT_ERROR") from None
                self.wait(self.delay(attempt, None), time_left)
                continue
            if not self.should_retry(response, retry_server_errors, attempt):
                return response
            self.wait(self.delay(attempt, response), time_left)
        raise AssertionError("unreachable")

    def send(self, method, path, kwargs, time_left):
        """Make one attempt. Without `time_left` it runs here, as cleanup's by-ID and tag reads do.
        With it, since httpx limits each phase of a request but not the whole, it runs on a
        daemon thread: once `time_left` finds no time left while it still runs, it is
        abandoned and NO_TIME_LEFT raised. An abandoned attempt uses only the HTTP client,
        which threads may share, and its result, if one comes, is dropped."""
        if time_left is None:
            return self._client.request(method, path, **kwargs)
        outcome = {}

        def attempt():
            try:
                outcome["response"] = self._client.request(method, path, **kwargs)
            except BaseException as error:
                outcome["error"] = error

        worker = threading.Thread(target=attempt, daemon=True)
        worker.start()
        while worker.is_alive():
            left = time_left()
            if left <= 0:
                raise ProviderError("NO_TIME_LEFT")
            worker.join(left)
        if "error" in outcome:
            raise outcome["error"]
        return outcome["response"]

    def wait(self, seconds, time_left):
        """Sleep before a retry. A wait longer than the time left is cut to it. The next
        attempt then finds no time left, so no retry is sent before its full wait, a
        Retry-After hint included."""
        if time_left is not None:
            seconds = min(seconds, max(time_left(), 0))
        self.sleep(seconds)

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
                   tags=tuple(tags), created_at=parse_time(created),
                   public_ipv4=public_ipv4(item.get("networks")))


def public_ipv4(networks):
    """The first public IPv4 address, or None: an odd shape must not block a teardown."""
    entries = networks.get("v4") if isinstance(networks, dict) else None
    for entry in entries if isinstance(entries, list) else ():
        if isinstance(entry, dict) and entry.get("type") == "public":
            address = entry.get("ip_address")
            try:
                return str(ipaddress.IPv4Address(address)) if isinstance(address, str) else None
            except ValueError:
                return None
    return None


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
    return {"id": item.get("id"), "type": kind, "resource_type": resource_type,
            "resource_id": resource_id, "started_at": started, "status": status}


def parse_firewall(item):
    if not isinstance(item, dict) or not isinstance(item.get("inbound_rules"), list):
        raise ProviderError("MALFORMED_RESPONSE")
    return Firewall(tags=strings(item.get("tags")),
                    inbound_rules=tuple(parse_rule(rule) for rule in item["inbound_rules"]))


def parse_rule(rule):
    """Every source of every kind is kept, so the preflight can refuse what it does not expect."""
    if not isinstance(rule, dict) or not isinstance(rule.get("sources"), dict):
        raise ProviderError("MALFORMED_RESPONSE")
    protocol, ports = rule.get("protocol"), rule.get("ports", "")
    if not isinstance(protocol, str) or not isinstance(ports, str):
        raise ProviderError("MALFORMED_RESPONSE")
    sources = []
    for kind, values in rule["sources"].items():
        if not isinstance(values, list):
            raise ProviderError("MALFORMED_RESPONSE")
        sources.extend((kind, str(value)) for value in values)
    return InboundRule(protocol=protocol, ports=ports, sources=tuple(sources))


def parse_size(item):
    if not isinstance(item, dict):
        raise ProviderError("MALFORMED_RESPONSE")
    slug, available, price = item.get("slug"), item.get("available"), item.get("price_hourly")
    if not isinstance(slug, str) or not isinstance(available, bool):
        raise ProviderError("MALFORMED_RESPONSE")
    if not isinstance(price, int | float) or isinstance(price, bool):
        raise ProviderError("MALFORMED_RESPONSE")
    return Size(slug=slug, available=available, price_hourly=float(price),
                regions=strings(item.get("regions")))


def strings(value):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProviderError("MALFORMED_RESPONSE")
    return tuple(value)


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
