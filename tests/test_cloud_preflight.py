"""The preflight: the key, the firewall, the size and its price, and the bundle, all read
before a run is recorded. A refusal leaves no record, no pin and no Droplet."""

import pytest
from cloud_support import BUNDLE_BYTES, FINGERPRINT, FIREWALL_ID, OPERATOR_SOURCE, Cloud


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def assert_refused(cloud, *reasons):
    assert cloud.controller().launch("r1") == {
        "outcome": "refused", "run_id": "r1", "reasons": list(reasons)}
    assert cloud.ledger.get("r1") is None and cloud.creates() == []
    assert not (cloud.root / "known_hosts").exists()


def assert_admitted(cloud):
    assert cloud.controller().launch("r1")["outcome"] == "launched"
    assert {call[0] for call in cloud.provider.calls} >= {"ssh_key", "firewall", "sizes"}


def inbound(cloud, *rules):
    cloud.provider.firewalls[FIREWALL_ID]["inbound_rules"] = list(rules)


def ssh_from(*addresses, **other_sources):
    return {"protocol": "tcp", "ports": "22",
            "sources": {"addresses": list(addresses)} | other_sources}


def test_an_admitted_launch_reads_the_key_firewall_and_sizes_before_the_create(cloud):
    assert cloud.controller().launch("r1")["outcome"] == "launched"
    steps = [call[:2] for call in cloud.provider.calls
             if call[0] in ("ssh_key", "firewall", "sizes", "create")]
    assert steps == [("ssh_key", FINGERPRINT), ("firewall", FIREWALL_ID), ("sizes",),
                     ("create", "bh-r1")]


def test_an_unregistered_key_is_refused(cloud):
    cloud.provider.ssh_keys = set()
    assert_refused(cloud, "SSH_KEY_NOT_REGISTERED")


FIREWALLS = {
    "missing": None,
    "not on the owner tag": {"tags": ["web"]},
    "udp": {"inbound_rules": [ssh_from(OPERATOR_SOURCE) | {"protocol": "udp"}]},
    "another port": {"inbound_rules": [ssh_from(OPERATOR_SOURCE) | {"ports": "80"}]},
    "a port range": {"inbound_rules": [ssh_from(OPERATOR_SOURCE) | {"ports": "1-65535"}]},
    "a second rule": {"inbound_rules": [ssh_from(OPERATOR_SOURCE),
                                        ssh_from(OPERATOR_SOURCE) | {"ports": "8080"}]},
    "every address": {"inbound_rules": [ssh_from("0.0.0.0/0")]},
    "a network": {"inbound_rules": [ssh_from("203.0.113.0/24")]},
    "an IPv6 address": {"inbound_rules": [ssh_from("2001:db8::7/128")]},
    "every IPv6 address": {"inbound_rules": [ssh_from(OPERATOR_SOURCE, "::/0")]},
    "a malformed address": {"inbound_rules": [ssh_from("203.0.113.7/24")]},
    "a tag": {"inbound_rules": [ssh_from(OPERATOR_SOURCE, tags=["web"])]},
    "a Droplet": {"inbound_rules": [ssh_from(OPERATOR_SOURCE, droplet_ids=[8])]},
    "a load balancer": {"inbound_rules": [ssh_from(
        OPERATOR_SOURCE, load_balancer_uids=["made-up-load-balancer"])]},
}


@pytest.mark.parametrize("case", FIREWALLS)
def test_a_firewall_that_admits_more_than_ssh_from_single_addresses_is_refused(cloud, case):
    if FIREWALLS[case] is None:
        cloud.provider.firewalls = {}
    else:
        cloud.provider.firewalls[FIREWALL_ID].update(FIREWALLS[case])
    assert_refused(cloud, "FIREWALL_NOT_RESTRICTED")


@pytest.mark.parametrize("rules", [[], [ssh_from()], [ssh_from(OPERATOR_SOURCE), ssh_from()]],
                         ids=["no inbound rule", "a rule with no sources",
                              "a second rule with no sources"])
def test_a_firewall_with_no_rule_or_a_rule_without_a_source_is_refused(cloud, rules):
    # Such a rule admits nothing, so the run could only wait out its boot timeout.
    inbound(cloud, *rules)
    assert_refused(cloud, "FIREWALL_NOT_RESTRICTED")


def test_single_addresses_with_or_without_a_prefix_are_admitted(cloud):
    inbound(cloud, ssh_from("203.0.113.7", "198.51.100.9/32"))
    assert_admitted(cloud)


@pytest.mark.parametrize("change", [
    {"available": False},
    {"regions": ["sfo3", "ams3"]},
    {"slug": "s-4vcpu-8gb"},
])
def test_a_size_not_offered_in_the_region_is_refused(cloud, change):
    cloud.provider.sizes[-1].update(change)
    assert_refused(cloud, "SIZE_NOT_OFFERED")


def test_a_price_above_the_configured_one_is_refused(cloud):
    cloud.provider.sizes[-1]["price_hourly"] = cloud.settings.hourly_price_usd + 0.001
    assert_refused(cloud, "PRICE_UNDERSTATED")


def test_a_price_at_or_below_the_configured_one_is_admitted(cloud):
    cloud.provider.sizes[-1]["price_hourly"] = cloud.settings.hourly_price_usd - 0.01
    assert_admitted(cloud)


@pytest.mark.parametrize("contents", [BUNDLE_BYTES + b"more", BUNDLE_BYTES[::-1], None])
def test_a_bundle_that_differs_from_its_descriptor_is_refused(cloud, contents):
    if contents is None:
        cloud.bundle_path.unlink()
    else:
        cloud.bundle_path.write_bytes(contents)
    assert_refused(cloud, "BUNDLE_MISMATCH")


def test_a_bundle_whose_descriptor_names_another_file_is_refused(cloud, monkeypatch):
    access = cloud.access_file()
    access["bundle"]["name"] = "another-bundle.tar"
    monkeypatch.setattr(cloud, "access_file", lambda: access)
    assert_refused(cloud, "BUNDLE_MISMATCH")


def test_every_failed_check_is_reported_in_order(cloud):
    cloud.provider.ssh_keys = set()
    cloud.provider.firewalls = {}
    cloud.provider.sizes[-1]["price_hourly"] = 1.0
    cloud.bundle_path.write_bytes(b"other")
    assert_refused(cloud, "SSH_KEY_NOT_REGISTERED", "FIREWALL_NOT_RESTRICTED",
                   "PRICE_UNDERSTATED", "BUNDLE_MISMATCH")


def test_a_failed_read_refuses_as_unverified(cloud):
    cloud.provider.preflight_failures = 1
    assert_refused(cloud, "PREFLIGHT_UNVERIFIED")


def test_the_launch_gate_refuses_before_the_preflight_reads_anything(cloud):
    cloud.launched()
    calls = len(cloud.provider.calls)
    assert [call[0] for call in cloud.provider.calls].count("ssh_key") == 1
    assert cloud.controller().launch("r2")["reasons"] == ["CONCURRENCY_LIMIT"]
    assert not [call for call in cloud.provider.calls[calls:]
                if call[0] in ("ssh_key", "firewall", "sizes")]


def test_a_refused_preflight_leaves_nothing_that_blocks_a_later_launch(cloud):
    cloud.provider.ssh_keys = set()
    assert_refused(cloud, "SSH_KEY_NOT_REGISTERED")
    cloud.provider.ssh_keys = {FINGERPRINT}
    assert_admitted(cloud)
    assert cloud.active_alerts() == []
