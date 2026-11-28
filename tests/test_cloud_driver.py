"""The scripted driver against the in-process local API: no socket, no game and no wait.

The fake API answers each request the way the mod does, and the clock only moves when
the driver sleeps, so every bound is exact.
"""

from datetime import UTC, datetime, timedelta

import pytest
from local_api_support import HAND, FakeLocalApi, refusal

from balatro_horizons.cloud.worker import driver

TOKEN = "a" * 64
INSTANCE = "b" * 32
URL = "http://127.0.0.1:12346/"
SEED = "TESTSEED"
START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
# One round: a blind, two discards while the pair is weak, the hand, the cash-out, the shop.
ROUND = [("select", None), ("bh_action", "discard"), ("bh_action", "discard"),
         ("bh_action", "play_hand"), ("cash_out", None), ("next_round", None)]
# The mod's own refusals put prose like this in the message, and their category in the
# data's name.
PROSE = "Method not available in the current state"


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def api(**options):
    return FakeLocalApi(TOKEN, INSTANCE, **{"end_after": 5} | options)


def play(local_api, *, cap=driver.ACTION_CAP, running=None, post=None):
    """Play one game: how it ended, or why it failed, the game, and each count reported.

    The local API keeps the game's clock. The game runs until the API's exit fault unless
    `running` says otherwise, and `post` may stand in front of the API.
    """
    clock = Clock()
    local_api.clock = clock
    counts = []
    game = driver.Game(driver.Rpc(post or local_api, URL, TOKEN),
                       running=running or local_api.running, committed=counts.append,
                       clock=clock, sleep=clock.advance, cap=cap)
    try:
        outcome = game.play(SEED)
    except driver.PlayFailed as failure:
        outcome = f"failed: {failure}"
    return outcome, game, counts


def asked_about(local_api):
    """The request IDs the driver asked the status of, in order."""
    return [body["params"]["request_id"] for body in local_api.bodies_for("bh_request_status")]


def asked_after_the_send(local_api):
    """Seconds from the first bh_action move's send to each status ask, in order."""
    sent = local_api.times_for("bh_action")[0]
    return [(at - sent).total_seconds() for at in local_api.times_for("bh_request_status")]


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


@pytest.mark.parametrize(("fault", "status"), [("lose", "unseen"), ("reject", "rejected")])
def test_a_move_that_did_not_commit_ends_play_without_a_resend(fault, status):
    local_api = api()
    local_api.faults = {("select", 1): fault}
    outcome, game, counts = play(local_api)
    assert outcome == (
        f"failed: uncertain move: select; answer TimeoutError after 0 s; status {status}")
    assert local_api.sent() == [("start", None), ("select", None)]
    assert asked_about(local_api) == [local_api.bodies_for("select")[0]["id"]]
    assert counts == [1]


def test_a_status_request_that_fails_ends_play_as_uncertain():
    local_api = api()
    local_api.faults = {("select", 1): "drop", ("bh_request_status", 1): "lose"}
    outcome, _, counts = play(local_api)
    assert outcome == ("failed: uncertain move: select; answer TimeoutError after 0 s; "
                       "status lookup TimeoutError")
    assert local_api.sent() == [("start", None), ("select", None)]
    assert asked_about(local_api) == [local_api.bodies_for("select")[0]["id"]]
    assert counts == [1]


def test_a_refused_move_ends_play_with_its_name_and_no_retry():
    local_api = api()
    local_api.faults = {("select", 1): "refuse"}
    outcome, _, counts = play(local_api)
    assert outcome == "failed: move refused: select: BUSY"
    assert local_api.sent() == [("start", None), ("select", None)]
    assert local_api.bodies_for("bh_request_status") == [] and counts == [1]


def test_an_answer_lost_while_the_game_settles_the_move_is_asked_about_until_it_commits():
    # The answer is lost four seconds after the send. The move commits on the fourth ask.
    local_api, timeouts = api(), set()
    local_api.faults = {("bh_action", 1): "pending"}
    local_api.lost_after, local_api.pending_asks = 4, 3

    def post(url, body, timeout):
        timeouts.add((body["method"], timeout))
        return local_api(url, body, timeout)

    outcome, game, counts = play(local_api, post=post)
    assert outcome == "game_over"
    assert timeouts == {("start", 85), ("select", 85), ("bh_action", 85),
                        ("bh_request_status", 12), ("bh_inspect", 10)}
    move = local_api.bodies_for("bh_action")[0]["id"]
    assert [body["id"] for body in local_api.bodies].count(move) == 1
    assert asked_about(local_api) == [move] * 4
    assert asked_after_the_send(local_api) == [4, 6, 8, 10]
    assert local_api.sent() == [("start", None), *ROUND[:4]] and counts == [1, 2, 3, 4, 5]
    assert game.moves[2] == {"method": "bh_action", "action": "discard",
                             "state": "SELECTING_HAND"}


def test_a_move_still_pending_when_its_window_ends_names_its_cause():
    # The answer is lost five seconds after the send, and the move never settles. An ask
    # starts every two seconds while less than 85 seconds have passed since the send.
    local_api = api()
    local_api.faults = {("bh_action", 1): "pending"}
    local_api.lost_after = 5
    outcome, _, counts = play(local_api)
    assert outcome == ("failed: uncertain move: bh_action discard; answer TimeoutError after "
                       "5 s; status pending")
    assert asked_after_the_send(local_api) == list(range(5, 85, 2))
    assert local_api.sent() == [("start", None), *ROUND[:2]] and counts == [1, 2]


@pytest.mark.parametrize("lost_after", [85, 120])
def test_an_answer_lost_after_the_whole_window_is_asked_about_once(lost_after):
    local_api = api()
    local_api.faults = {("bh_action", 1): "pending"}
    local_api.lost_after = lost_after
    outcome, _, _ = play(local_api)
    assert outcome == ("failed: uncertain move: bh_action discard; answer TimeoutError after "
                       f"{lost_after} s; status pending")
    assert asked_after_the_send(local_api) == [lost_after]


@pytest.mark.parametrize("failed_ask", [1, 2])
def test_a_status_call_that_fails_ends_the_asking_at_once(failed_ask):
    local_api = api()
    local_api.faults = {("bh_action", 1): "pending", ("bh_request_status", failed_ask): "lose"}
    local_api.lost_after = 3
    outcome, _, _ = play(local_api)
    assert outcome == ("failed: uncertain move: bh_action discard; answer TimeoutError after "
                       "3 s; status lookup TimeoutError")
    assert asked_after_the_send(local_api) == [3, 5][:failed_ask]


@pytest.mark.parametrize(("message", "data", "code"), [
    ("NOT_READY", {"name": "INFRASTRUCTURE"}, "NOT_READY"),
    (PROSE, {"name": "INVALID_STATE"}, "INVALID_STATE"),
], ids=["patch-code", "prose-invalid-state"])
def test_a_refused_status_call_names_its_code(message, data, code):
    local_api = api()
    local_api.faults = {("bh_action", 1): "pending", ("bh_request_status", 1): "refuse"}
    local_api.lost_after = 3
    local_api.refusal, local_api.refusal_data = message, data
    outcome, _, _ = play(local_api)
    assert outcome == ("failed: uncertain move: bh_action discard; answer TimeoutError after "
                       f"3 s; status lookup refused {code}")
    assert asked_after_the_send(local_api) == [3]


@pytest.mark.parametrize(("fault", "status"), [("expire", "unknown"), ("lose", "unseen")])
def test_an_unknown_status_tells_a_move_the_game_gave_up_on_from_one_it_never_saw(fault,
                                                                                   status):
    # The game keeps an intent for every move it admitted; a move it never saw has none.
    local_api = api()
    local_api.faults = {("bh_action", 1): fault}
    outcome, _, counts = play(local_api)
    assert outcome == ("failed: uncertain move: bh_action discard; answer TimeoutError after "
                       f"0 s; status {status}")
    assert counts == [1, 2]


@pytest.mark.parametrize("status", [{"status": "settling"}, {"status": 3}, {}])
def test_a_status_the_driver_cannot_read_is_invalid(status):
    local_api = api()
    local_api.faults = {("select", 1): "drop"}
    asks = []

    def post(url, body, timeout):
        if body["method"] != "bh_request_status":
            return local_api(url, body, timeout)
        asks.append(body)
        return {"jsonrpc": "2.0", "id": body["id"], "result": status}

    outcome, _, _ = play(local_api, post=post)
    assert outcome == ("failed: uncertain move: select; answer TimeoutError after 0 s; "
                       "status invalid")
    assert len(asks) == 1


def test_a_game_that_exits_during_a_move_is_named_for_it():
    # The game stops seven seconds into the discard, so the status call meets a refused
    # connection.
    local_api = api()
    local_api.faults = {("bh_action", 1): "exit"}
    local_api.lost_after = 7
    outcome, _, counts = play(local_api)
    assert outcome == ("failed: game exited during bh_action discard; answer RemoteDisconnected "
                       "after 7 s; status lookup URLError")
    assert asked_after_the_send(local_api) == [7]
    assert local_api.sent() == [("start", None), *ROUND[:2]] and counts == [1, 2]


@pytest.mark.parametrize(("faults", "lost_after", "asks", "status"), [
    ({("bh_action", 1): "pending", ("bh_request_status", 2): "lose"}, 3, 2,
     "status lookup TimeoutError"),
    ({("bh_action", 1): "reject"}, 0, 1, "status rejected"),
    ({("bh_action", 1): "pending"}, 5, 2, "status pending"),
], ids=["lookup-fails", "rejected", "window-ends"])
def test_a_game_that_exits_while_its_move_is_asked_about_is_named_for_it(faults, lost_after,
                                                                        asks, status):
    # The game runs when the answer is lost, and has exited once `asks` status asks are made.
    local_api = api()
    local_api.faults, local_api.lost_after = faults, lost_after
    outcome, _, _ = play(
        local_api, running=lambda: len(local_api.bodies_for("bh_request_status")) < asks)
    assert outcome == ("failed: game exited during bh_action discard; answer TimeoutError "
                       f"after {lost_after} s; {status}")


@pytest.mark.parametrize(("message", "code"), [
    ("UNAFFORDABLE", "UNAFFORDABLE"), ("A" * 40, "A" * 40), ("A" * 41, "other"),
    ("unaffordable", "other"), ("NOT ALLOWED", "other"), ("CODE_2", "other"), ("", "other"),
    (None, "other"),
], ids=["code", "longest", "too-long", "lowercase", "space", "digit", "empty", "none"])
def test_a_refused_move_is_named_with_its_code(message, code):
    local_api = api()
    local_api.faults = {("bh_action", 1): "refuse"}
    local_api.refusal = message
    outcome, _, counts = play(local_api)
    assert outcome == f"failed: move refused: bh_action discard: {code}"
    assert local_api.bodies_for("bh_request_status") == [] and counts == [1, 2]


@pytest.mark.parametrize(("message", "data", "code"), [
    (PROSE, {"name": "INVALID_STATE"}, "INVALID_STATE"),
    ("Not enough money", {"name": "NOT_ALLOWED"}, "NOT_ALLOWED"),
    ("Internal error", {"name": "INTERNAL_ERROR"}, "INTERNAL_ERROR"),
    ("Invalid request", {"name": "BAD_REQUEST"}, "BAD_REQUEST"),
    ("UNAFFORDABLE", {"name": "NOT_ALLOWED"}, "UNAFFORDABLE"),
    (PROSE, {"name": "INFRASTRUCTURE"}, "other"),
    (PROSE, None, "other"),
    (PROSE, "INVALID_STATE", "other"),
    (PROSE, {"name": ["INVALID_STATE"]}, "other"),
], ids=["prose-invalid-state", "prose-not-allowed", "prose-internal-error", "prose-bad-request",
        "message-wins", "name-outside-the-four", "no-data", "data-not-an-object",
        "name-not-a-string"])
def test_a_refusal_in_prose_is_named_by_its_category(message, data, code):
    local_api = api()
    local_api.faults = {("bh_action", 1): "refuse"}
    local_api.refusal, local_api.refusal_data = message, data
    outcome, _, counts = play(local_api)
    assert outcome == f"failed: move refused: bh_action discard: {code}"
    assert local_api.bodies_for("bh_request_status") == [] and counts == [1, 2]


def lost_move(action, kind, refused_with=None):
    """Why a bh_action move fails when its answer, a million seconds after the send, is lost
    with `kind`, and the game has stopped. Its status call is lost the same way, or refused
    with `refused_with` as the error's message when that is given."""
    clock = Clock()

    def post(url, body, timeout):
        if body["method"] == "bh_action":
            clock.advance(10**6)
        if refused_with is not None and body["method"] == "bh_request_status":
            return refusal(body, refused_with, {"name": "INFRASTRUCTURE"})
        raise OSError(kind)

    game = driver.Game(driver.Rpc(post, URL, TOKEN), running=lambda: False,
                       committed=lambda count: None, clock=clock, sleep=clock.advance)
    with pytest.raises(driver.PlayFailed) as failed:
        game.move("bh_action", {"action": action})
    return str(failed.value)


def test_the_longest_reason_fits_in_200_characters():
    # The longest move is an action of 20 letters, and the longest kind and the longest code
    # have 40 characters each. A refused status call gives the longest form.
    action, kind, code = "a" * 20, "K" * 40, "C" * 40
    reason = lost_move(action, kind, refused_with=code)
    assert reason == (f"game exited during bh_action {action}; answer {kind} after 1000000 s; "
                      f"status lookup refused {code}")
    assert len(reason) <= 200


@pytest.mark.parametrize(("action", "kind", "named"), [
    ("a" * 21, "K" * 41, ("other", "other")),
    ("Play_Hand", "timed out!", ("other", "other")),
    (None, "", ("other", "OSError")),
    ("play_hand", "HTTPError 500", ("play_hand", "HTTPError 500")),
], ids=["too-long", "outside-pattern", "missing", "named"])
def test_an_action_or_a_kind_the_reason_cannot_name_reads_other(action, kind, named):
    move, answer = named
    assert lost_move(action, kind) == (f"game exited during bh_action {move}; answer {answer} "
                                       f"after 1000000 s; status lookup {answer}")


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
