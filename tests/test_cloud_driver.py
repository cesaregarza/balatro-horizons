"""The scripted driver against the in-process local API: no socket, no game and no wait.

The fake API answers each request the way the mod does, and the clock only moves when
the driver sleeps, so every bound is exact.
"""

from datetime import UTC, datetime, timedelta

import pytest
from local_api_support import HAND, FakeLocalApi

from balatro_horizons.cloud.worker import driver

TOKEN = "a" * 64
INSTANCE = "b" * 32
URL = "http://127.0.0.1:12346/"
SEED = "TESTSEED"
START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
# One round: a blind, two discards while the pair is weak, the hand, the cash-out, the shop.
ROUND = [("select", None), ("bh_action", "discard"), ("bh_action", "discard"),
         ("bh_action", "play_hand"), ("cash_out", None), ("next_round", None)]


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def api(**options):
    return FakeLocalApi(TOKEN, INSTANCE, **{"end_after": 5} | options)


def play(local_api, *, cap=driver.ACTION_CAP, running=lambda: True):
    """Play one game: how it ended, or why it failed, the game, and each count reported."""
    clock = Clock()
    counts = []
    game = driver.Game(driver.Rpc(local_api, URL, TOKEN), running=running,
                       committed=counts.append, clock=clock, sleep=clock.advance, cap=cap)
    try:
        outcome = game.play(SEED)
    except driver.PlayFailed as failure:
        outcome = f"failed: {failure}"
    return outcome, game, counts


def asked_about(local_api):
    """The request IDs the driver asked the status of, in order."""
    return [body["params"]["request_id"] for body in local_api.bodies_for("bh_request_status")]


def test_a_game_starts_from_its_seed_and_plays_to_a_game_over():
    local_api = api()
    outcome, game, counts = play(local_api)
    assert outcome == "game_over"
    assert local_api.sent() == [("start", None), *ROUND[:4]]
    start = local_api.bodies_for("start")[0]["params"]
    assert start == {"deck": "RED", "seed": SEED, "stake": "WHITE", "_bh_token": TOKEN}
    assert counts == [1, 2, 3, 4, 5]
    assert [move["state"] for move in game.moves] == [
        "BLIND_SELECT", "SELECTING_HAND", "SELECTING_HAND", "SELECTING_HAND", "GAME_OVER"]
    assert (game.ante, game.round) == (1, 1)


def test_every_request_carries_the_token_and_a_fresh_id_of_its_own():
    local_api = api()
    local_api.faults = {("bh_action", 1): "drop"}
    assert play(local_api)[0] == "game_over"
    methods = {body["method"] for body in local_api.bodies}
    assert {"bh_inspect", "bh_request_status", "start", "bh_action"} <= methods
    for body in local_api.bodies:
        assert (body["jsonrpc"], body["params"]["_bh_token"]) == ("2.0", TOKEN)
    ids = [body["id"] for body in local_api.bodies]
    assert len(ids) == len(set(ids))
    assert not [request_id for request_id in ids if TOKEN in request_id or INSTANCE in request_id]


@pytest.mark.parametrize("fault", ["drop", "stray"])
def test_a_lost_answer_is_resolved_by_the_move_id_and_the_move_is_never_resent(fault):
    local_api = api()
    local_api.faults = {("bh_action", 1): fault}
    outcome, game, counts = play(local_api)
    assert outcome == "game_over"
    # The discard committed once, so the hand still plays after a second discard.
    assert local_api.sent() == [("start", None), *ROUND[:4]]
    assert asked_about(local_api) == [local_api.bodies_for("bh_action")[0]["id"]]
    assert counts == [1, 2, 3, 4, 5]
    assert game.moves[2] == {"method": "bh_action", "action": "discard",
                             "state": "SELECTING_HAND"}


@pytest.mark.parametrize("fault", ["lose", "reject"])
def test_a_move_that_did_not_commit_ends_play_without_a_resend(fault):
    local_api = api()
    local_api.faults = {("select", 1): fault}
    outcome, game, counts = play(local_api)
    assert outcome == "failed: uncertain move: select"
    assert local_api.sent() == [("start", None), ("select", None)]
    assert asked_about(local_api) == [local_api.bodies_for("select")[0]["id"]]
    assert counts == [1]


def test_a_status_request_that_fails_ends_play_as_uncertain():
    local_api = api()
    local_api.faults = {("select", 1): "drop", ("bh_request_status", 1): "lose"}
    outcome, _, counts = play(local_api)
    assert outcome == "failed: uncertain move: select"
    assert local_api.sent() == [("start", None), ("select", None)]
    assert asked_about(local_api) == [local_api.bodies_for("select")[0]["id"]]
    assert counts == [1]


def test_a_refused_move_ends_play_with_its_name_and_no_retry():
    local_api = api()
    local_api.faults = {("select", 1): "refuse"}
    outcome, _, counts = play(local_api)
    assert outcome == "failed: move refused: select"
    assert local_api.sent() == [("start", None), ("select", None)]
    assert local_api.bodies_for("bh_request_status") == [] and counts == [1]


def test_the_action_cap_stops_play_at_exactly_the_cap():
    local_api = api(end_after=None)
    outcome, game, counts = play(local_api, cap=7)
    assert outcome == "action_cap"
    assert len(game.moves) == len(local_api.sent()) == 7 and counts == list(range(1, 8))
    assert local_api.sent() == [("start", None), *ROUND]
    assert driver.ACTION_CAP == 200


@pytest.mark.parametrize(("ending", "outcome"), [("GAME_OVER", "game_over"), ("won", "won")])
def test_a_game_over_or_a_win_ends_play_with_its_outcome(ending, outcome):
    local_api = api(end_after=8, ending=ending)
    assert play(local_api)[0] == outcome
    assert len(local_api.sent()) == 8


def test_play_waits_between_moves_while_the_game_is_busy():
    local_api = api()
    local_api.busy_after_move = 3
    outcome, game, _ = play(local_api)
    assert outcome == "game_over"
    # After each move the driver inspects through three busy answers to a ready one.
    assert len(local_api.bodies_for("bh_inspect")) == 4 * len(game.moves) == 20
    assert game.clock() - START == timedelta(seconds=0.75 * len(game.moves))


def test_a_game_that_never_settles_ends_play_at_the_bound():
    local_api = api()
    local_api.busy_after_move = 10**6
    outcome, game, _ = play(local_api)
    assert outcome == "failed: not ready between moves"
    # An inspect every quarter second for fifteen seconds, then no more.
    assert (driver.SETTLE_POLL_SECONDS, driver.SETTLE_SECONDS) == (0.25, 15)
    assert len(local_api.bodies_for("bh_inspect")) == 60
    assert game.clock() - START == timedelta(seconds=15)
    for body in local_api.bodies_for("bh_inspect"):
        assert body["params"] == {"_bh_token": TOKEN}


def test_an_inspect_that_fails_counts_as_not_ready():
    local_api = api()
    local_api.faults = {("bh_inspect", turn): "lose" for turn in range(1, 4)}
    assert play(local_api)[0] == "game_over"
    never = api()
    never.faults = {("bh_inspect", turn): "lose" for turn in range(1, 1000)}
    assert play(never)[0] == "failed: not ready between moves"
    assert never.sent() == [("start", None)]


def test_the_game_exiting_during_play_ends_it():
    local_api = api(end_after=None)
    outcome, _, counts = play(local_api, running=lambda: local_api.committed < 2)
    assert outcome == "failed: game exited"
    assert counts == [1, 2]


def lost(body):
    raise OSError("timed out")


@pytest.mark.parametrize(("reply", "problem"), [
    (lambda body: {"jsonrpc": "2.0", "id": "another", "result": {}}, "reply id mismatch"),
    (lambda body: {"jsonrpc": "2.0", "id": body["id"]}, "no result"),
    (lambda body: {"jsonrpc": "2.0", "id": body["id"], "result": "MENU"}, "no result"),
    (lambda body: "not an object", "reply id mismatch"),
    (lost, "timed out"),
])
def test_a_reply_that_does_not_answer_the_call_is_no_answer(reply, problem):
    rpc = driver.Rpc(lambda url, body, timeout: reply(body), URL, TOKEN)
    with pytest.raises(driver.NoAnswer, match=f"^{problem}$"):
        rpc.call("bh_inspect", {}, driver.INSPECT_SECONDS)


def test_an_error_reply_is_a_refusal_whatever_else_it_carries():
    rpc = driver.Rpc(lambda url, body, timeout: {
        "jsonrpc": "2.0", "id": body["id"], "error": {"code": -32000}, "result": {}}, URL, TOKEN)
    with pytest.raises(driver.Refused):
        rpc.call("select", {}, driver.MOVE_SECONDS)


def test_each_call_has_its_own_timeout():
    seen = []

    def post(url, body, timeout):
        seen.append((body["method"], timeout))
        return {"jsonrpc": "2.0", "id": body["id"], "result": {"status": "committed"}}

    rpc = driver.Rpc(post, URL, TOKEN)
    rpc.inspect()
    rpc.call("bh_request_status", {"request_id": "x"}, driver.STATUS_SECONDS)
    rpc.call("select", {}, driver.MOVE_SECONDS)
    assert seen == [("bh_inspect", 10), ("bh_request_status", 12), ("select", 85)]


def test_a_seed_is_eight_characters_from_the_run_alphabet():
    seeds = {driver.new_seed() for _ in range(50)}
    assert len(seeds) > 1
    for seed in seeds:
        assert len(seed) == 8 and set(seed) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def hand(discards_left, cards=HAND):
    return {"state": "SELECTING_HAND", "hand": {"cards": cards, "highlighted_limit": 5},
            "round": {"discards_left": discards_left}}


def test_the_policy_discards_while_the_hand_is_weak_then_plays_the_best_hand():
    policy = driver.Policy()
    assert policy.choose(hand(3)) == ("bh_action", {"action": "discard", "cards": [2, 3, 4]})
    assert policy.choose(hand(2)) == ("bh_action", {"action": "discard", "cards": [2, 3, 4]})
    assert policy.choose(hand(1)) == ("bh_action", {"action": "play_hand", "cards": [0, 1]})
    assert driver.Policy().choose(hand(0)) == (
        "bh_action", {"action": "play_hand", "cards": [0, 1]})


def test_the_policy_never_reads_the_game_private_fields():
    public = hand(3)
    private = public | {"seed": "PRIVATE3", "cards": {"cards": HAND[:1]},
                        "bh": {"ready": True, "state": "SHOP"}}
    assert driver.Policy().choose(private) == driver.Policy().choose(public)


def card(kind, buy, **fields):
    return {"set": kind, "cost": {"buy": buy}} | fields


def shop(money, cards=(), packs=(), reroll_cost=5, jokers=None):
    return {"state": "SHOP", "money": money, "shop": {"cards": list(cards)},
            "packs": {"cards": list(packs)}, "round": {"reroll_cost": reroll_cost},
            "jokers": jokers or {"count": 0, "limit": 5}}


def test_the_shop_buys_a_joker_there_is_room_for():
    full = {"count": 5, "limit": 5}
    joker = card("Joker", 4)
    assert driver.Policy().choose(shop(4, [joker])) == (
        "bh_action", {"action": "buy", "area": "shop", "index": 0})
    assert driver.Policy().choose(shop(4, [joker], jokers=full)) == ("next_round", {})
    assert driver.Policy().choose(shop(3, [joker])) == ("next_round", {})


def test_the_shop_uses_one_planet_opens_one_pack_and_rerolls_once():
    policy = driver.Policy()
    planet = card("Planet", 3, usable=True)
    pack = card("Booster", 4)
    assert policy.choose(shop(20, [planet], [pack])) == (
        "bh_action", {"action": "buy", "area": "shop", "index": 0, "mode": "buy_and_use"})
    assert policy.choose(shop(20, [planet], [pack])) == (
        "bh_action", {"action": "buy", "area": "packs", "index": 0})
    assert policy.choose(shop(20, [planet], [pack])) == ("reroll", {})
    assert policy.choose(shop(20, [planet], [pack])) == ("next_round", {})
    assert driver.Policy().choose(shop(9)) == ("next_round", {})
    assert driver.Policy().choose(shop(10)) == ("reroll", {})


def test_an_open_pack_takes_the_first_card_it_can_use_or_is_skipped():
    state = hand(0) | {"state": "TAROT_PACK", "pack": {"count": 2, "cards": [
        card("Tarot", 0, usable=False), card("Tarot", 0, usable=True, min_targets=2)]}}
    assert driver.Policy().choose(state) == (
        "bh_action", {"action": "choose_pack", "area": "pack", "index": 1, "targets": [0, 1]})
    state["pack"]["cards"] = [card("Spectral", 0, usable=True, min_targets=6)]
    assert driver.Policy().choose(state) == ("bh_action", {"action": "skip_pack"})


def test_an_unhandled_state_ends_play_with_its_name():
    with pytest.raises(driver.PlayFailed, match="^unhandled state: SPLASH$"):
        driver.Policy().choose({"state": "SPLASH"})
