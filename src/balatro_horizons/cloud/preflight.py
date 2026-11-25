"""Preflight: what must already hold before a run is recorded and a Droplet created.

Operations sets up the SSH key and the firewall once; nothing here creates either. The
preflight only reads, and it runs before the run is persisted, so a refused preflight
leaves no record. It is the one exception to persisting a run before any provider call.
"""

import hashlib
import ipaddress

CHUNK_BYTES = 1 << 20


def preflight_refusals(provider, settings, access):
    """Each failed check's refusal code, in a fixed order; an empty list admits the launch."""
    reasons = provider_refusals(provider, settings, access)
    if not bundle_matches(access.bundle):
        reasons.append("BUNDLE_MISMATCH")
    return reasons


def provider_refusals(provider, settings, access):
    try:
        registered = provider.ssh_key_registered(access.ssh_key_fingerprint)
        firewall = provider.get_firewall(access.firewall_id)
        size = next((s for s in provider.list_sizes() if s.slug == settings.size), None)
    except Exception:
        # A failed read proves nothing either way, so it refuses under its own code.
        return ["PREFLIGHT_UNVERIFIED"]
    checks = {
        "SSH_KEY_NOT_REGISTERED": registered,
        "FIREWALL_NOT_RESTRICTED": restricted(firewall, settings.owner_tag),
        "SIZE_NOT_OFFERED": offered(size, settings.region),
        # The budget reserves at the configured price, so a higher real price breaks it.
        "PRICE_UNDERSTATED": size is None or size.price_hourly <= settings.hourly_price_usd,
    }
    return [code for code, passed in checks.items() if not passed]


def restricted(firewall, owner_tag):
    """It covers the owner tag and admits only TCP 22, only from single IPv4 addresses.

    Outbound rules are not checked: the worker needs HTTPS and DNS out.
    """
    if firewall is None or owner_tag not in firewall.tags:
        return False
    return all(rule.protocol == "tcp" and rule.ports == "22"
               and all(single_ipv4(kind, value) for kind, value in rule.sources)
               for rule in firewall.inbound_rules)


def single_ipv4(kind, value):
    """Sources by tag, Droplet, load balancer or cluster can widen without a rule change."""
    if kind != "addresses":
        return False
    try:
        network = ipaddress.ip_network(value)
    except ValueError:
        return False
    return network.version == 4 and network.num_addresses == 1


def offered(size, region):
    return size is not None and size.available and region in size.regions


def bundle_matches(bundle):
    """Name, then size, then the SHA256 read in chunks, so the cheap checks refuse first."""
    path = bundle.path
    try:
        if path.name != bundle.name or path.stat().st_size != bundle.bytes:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == bundle.sha256
