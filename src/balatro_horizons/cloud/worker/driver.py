"""The scripted driver: one game through the game's local API, with no model.

The worker loads this file by its path and calls it once the game is ready, in the
worker's own process, with the run's token and the worker's transport. It uses only the
standard library and imports nothing from the controller's package. Every request is a
JSON-RPC 2.0 call that carries the token and a fresh ID.

A move whose answer is lost is never sent again: the driver asks by the move's own ID
whether it committed, again while the game is still settling it, and play goes on only if
it did. Play ends at a game over, a win or the action cap. Anything else ends it as a
failure with a named reason, and a failed move's reason holds only closed values and
numbers.

The policy follows the pilot's scripted policy: the same choices from the same public
observation fields. It exercises the game; it does not try to play it well.
"""

import re
import secrets
import uuid
from collections import Counter
from datetime import timedelta
from itertools import combinations

# Committed moves per game. A live check needs 50 or a normal game over; the cap bounds
# a game that never ends.
ACTION_CAP = 200
DECK = "RED"
STAKE = "WHITE"
# The alphabet the run API draws its seeds from: no 0, 1, I or O.
SEED_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SEED_LENGTH = 8
# Each call's timeout, in seconds, bounds a single wait for the reply, not the whole call.
INSPECT_SECONDS = 10
STATUS_SECONDS = 12
MOVE_SECONDS = 85
# A move still pending is asked about again this often, within its own window.
STATUS_POLL_SECONDS = 2
# Between moves the game must settle: ready, and not busy.
SETTLE_POLL_SECONDS = 0.25
SETTLE_SECONDS = 15
# Each poker hand's chips and mult before any level, for a hand the observation omits.
BASE_SCORES = {
    "High Card": (5, 1), "Pair": (10, 2), "Two Pair": (20, 2), "Three of a Kind": (30, 3),
    "Straight": (30, 4), "Flush": (35, 4), "Full House": (40, 4), "Four of a Kind": (60, 7),
    "Straight Flush": (100, 8),
}
# A card's rank as the game names it, long or short. An Ace ranks high.
RANKS = {str(number): number for number in range(2, 11)} | {
    "Jack": 11, "Queen": 12, "King": 13, "Ace": 14, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}
ACE = 14
WHEEL = [2, 3, 4, 5, ACE]
# A price the policy never meets, for an offer that shows none.
UNPRICED = 10**9
# What a failure's reason may carry from a move, an error reply and a lost answer. Anything
# else reads `other`, so every reason fits in 200 characters.
ACTION = re.compile(r"[a-z_]{1,20}")
CODE = re.compile(r"[A-Z_]{1,40}")
KIND = re.compile(r"[A-Za-z][A-Za-z0-9 ]{0,39}")
# The names the pinned mod gives its own refusals, whose message is prose. The dispatch
# patch's refusals carry their code in the message.
MOD_ERROR_NAMES = ("INTERNAL_ERROR", "BAD_REQUEST", "INVALID_STATE", "NOT_ALLOWED")


class PlayFailed(Exception):
    """Ends play; the message is the reason the worker reports."""


class RpcFailed(Exception):
    """A call that brought back no result."""


class NoAnswer(RpcFailed):
    """No reply came back that answers the call, so what the call did is unknown. The message
    names how the answer was lost."""


class Refused(RpcFailed):
    """The game answered the call with an error, which `code` names."""

    def __init__(self, code):
        super().__init__("error reply")
        self.code = code


def new_seed():
    return "".join(secrets.choice(SEED_ALPHABET) for _ in range(SEED_LENGTH))


def new_request_id():
    """A fresh random ID, which owes nothing to the token or the game's instance."""
    return uuid.uuid4().hex


class Rpc:
    """JSON-RPC 2.0 calls to the game's local API, each with the run's token.

    `post(url, body, timeout)` returns the decoded reply, and raises OSError when no reply
    came back that could be read.
    """

    def __init__(self, post, url, token):
        self.post = post
        self.url = url
        self.token = token

    def call(self, method, params, timeout, request_id=None):
        """The call's result. NoAnswer when no reply answers it; Refused for an error reply."""
        request_id = request_id or new_request_id()
        body = {"jsonrpc": "2.0", "id": request_id, "method": method,
                "params": params | {"_bh_token": self.token}}
        try:
            reply = self.post(self.url, body, timeout)
        except OSError as error:
            raise NoAnswer(kind_of(error)) from None
        if not isinstance(reply, dict) or reply.get("id") != request_id:
            raise NoAnswer("reply id mismatch")
        if reply.get("error") is not None:
            raise Refused(code_of(reply["error"]))
        if not isinstance(reply.get("result"), dict):
            raise NoAnswer("no result")
        return reply["result"]

    def inspect(self):
        return self.call("bh_inspect", {}, INSPECT_SECONDS)


class Game:
    """One scripted game, from its start to its end.

    `running()` says whether the game's process is still up, and `committed(count)` hears
    of each committed move. `clock` and `sleep` are the worker's, so tests never wait.
    """

    def __init__(self, rpc, *, running, committed, clock, sleep, cap=ACTION_CAP):
        self.rpc = rpc
        self.running = running
        self.committed = committed
        self.clock = clock
        self.sleep = sleep
        self.cap = cap
        # Each committed move: its method, its action if it has one, and the state it left.
        self.moves = []
        # Where the latest observation found the game.
        self.ante = self.round = None

    def play(self, seed):
        """Start a game from `seed`, then move until it ends; how it ended."""
        policy = Policy()
        self.move("start", {"deck": DECK, "seed": seed, "stake": STAKE})
        while True:
            observation = self.settled()
            self.ante = whole(observation.get("ante_num"))
            self.round = whole(observation.get("round_num"))
            if observation.get("won"):
                return "won"
            if observation.get("state") == "GAME_OVER":
                return "game_over"
            if len(self.moves) >= self.cap:
                return "action_cap"
            self.move(*policy.choose(observation))

    def move(self, method, params):
        """Send one move and record it once it has committed."""
        request_id = new_request_id()
        move = move_name(method, params)
        sent_at = self.clock()
        try:
            result = self.rpc.call(method, params, MOVE_SECONDS, request_id)
        except Refused as refusal:
            raise PlayFailed(f"move refused: {move}: {refusal.code}") from None
        except NoAnswer as lost:
            answer = f"answer {lost} after {whole_seconds(self.clock() - sent_at)} s"
            result = self.resolve(move, request_id, sent_at, answer)
        self.moves.append({"method": method, "action": params.get("action"),
                           "state": state_name(result)})
        self.committed(len(self.moves))

    def resolve(self, move, request_id, sent_at, answer):
        """Ask by the move's own ID whether it committed; its response if it did. The move is
        never sent again. The first ask is always made, however late the answer was lost; the
        window, counted from the send, limits only the repeats of a `pending` status."""
        window_ends = sent_at + timedelta(seconds=MOVE_SECONDS)
        while True:
            try:
                status = self.rpc.call("bh_request_status", {"request_id": request_id},
                                       STATUS_SECONDS)
            except RpcFailed as failure:
                lookup = f"refused {failure.code}" if isinstance(failure, Refused) else failure
                raise self.uncertain(move, answer, f"status lookup {lookup}") from None
            value = status_of(status)
            if value == "committed":
                return status.get("response")
            # The game settles every move it admits within its own bound of at most 60 s,
            # as committed, rejected or unknown, so pending is not yet an answer.
            if value != "pending":
                raise self.uncertain(move, answer, f"status {value}")
            self.sleep(STATUS_POLL_SECONDS)
            if self.clock() >= window_ends:
                raise self.uncertain(move, answer, "status pending")

    def uncertain(self, move, answer, status):
        """A move whose outcome is unknown ends play, named for the game if it has exited."""
        named = f"uncertain move: {move}" if self.running() else f"game exited during {move}"
        return PlayFailed(f"{named}; {answer}; {status}")

    def settled(self):
        """The observation once the game is ready for a move, inspected every quarter second
        for at most 15 seconds. An inspect that fails counts as not ready."""
        give_up = self.clock() + timedelta(seconds=SETTLE_SECONDS)
        while self.clock() < give_up:
            if not self.running():
                raise PlayFailed("game exited")
            try:
                observation = self.rpc.inspect()
            except RpcFailed:
                observation = {}
            if ready_for_a_move(observation):
                return observation
            self.sleep(SETTLE_POLL_SECONDS)
        raise PlayFailed("not ready between moves")


def move_name(method, params):
    """The method, and for bh_action its action, as a reason names the move."""
    if method != "bh_action":
        return method
    action = params.get("action")
    named = isinstance(action, str) and ACTION.fullmatch(action)
    return f"bh_action {action if named else 'other'}"


def kind_of(error):
    """How the transport says an answer was lost: its name, kept only when it fits KIND."""
    kind = str(error) or type(error).__name__
    return kind if KIND.fullmatch(kind) else "other"


def code_of(error):
    """An error reply's code: its message when that fits CODE, or else its data's name when
    that is one of MOD_ERROR_NAMES. Anything else reads `other`."""
    if not isinstance(error, dict):
        return "other"
    message, data = error.get("message"), error.get("data")
    name = data.get("name") if isinstance(data, dict) else None
    if isinstance(message, str) and CODE.fullmatch(message):
        return message
    return name if name in MOD_ERROR_NAMES else "other"


def status_of(status):
    """What the game's record says of a move. An unknown record with an intent is a move the
    game admitted and gave up on; one without is a move it never saw."""
    value = status.get("status")
    if value == "unknown":
        return "unknown" if status.get("intent") is not None else "unseen"
    return value if value in ("committed", "pending", "rejected") else "invalid"


def whole_seconds(elapsed):
    return max(int(elapsed.total_seconds()), 0)


def ready_for_a_move(observation):
    bh = part(observation, "bh")
    return bool(bh.get("ready")) and not bh.get("busy")


def state_name(result):
    """The state a move left the game in, as its result names it, if it does."""
    state = result.get("state") if isinstance(result, dict) else None
    return state[:40] if isinstance(state, str) else None


def whole(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def part(value, key):
    """The object under `key`, or an empty one when `value` has none there."""
    found = value.get(key) if isinstance(value, dict) else None
    return found if isinstance(found, dict) else {}


class Policy:
    """The pilot's scripted choices. A short game still exercises a discard, a pack, a planet
    and a reroll: each is made once when the game allows it, and the discard again while
    the hand is weak."""

    def __init__(self):
        self.discarded = self.opened_pack = self.used_planet = self.rerolled = False

    def choose(self, observation):
        """The next move as (method, params), from the public observation only."""
        # Never the private random state, the seed or the undealt deck.
        state = {key: value for key, value in observation.items()
                 if key not in ("bh", "seed", "cards")}
        phase = state.get("state")
        if phase == "BLIND_SELECT":
            return "select", {}
        if phase == "ROUND_EVAL":
            return "cash_out", {}
        if phase == "SELECTING_HAND":
            return self.hand_move(state)
        if phase == "SHOP":
            return self.shop_move(state)
        if part(state, "pack").get("count", 0) and phase not in ("MENU", "GAME_OVER"):
            return pack_move(state)
        raise PlayFailed(f"unhandled state: {phase}")

    def hand_move(self, state):
        """Play the best hand. Discard the other cards instead the first time, and again
        while the best is a High Card or a Pair with two or more discards left."""
        selected, name = best_hand(state)
        others = [index for index, card in enumerate(hand_cards(state))
                  if index not in selected and not card.get("forced_selection")]
        discards = part(state, "round").get("discards_left", 0)
        weak = name in ("High Card", "Pair") and discards > 1
        if others and discards > 0 and (not self.discarded or weak):
            self.discarded = True
            return "bh_action", {"action": "discard", "cards": others[:5]}
        return "bh_action", {"action": "play_hand", "cards": selected}

    def shop_move(self, state):
        """Buy the first offer that fits: a joker there is room for or, once a game, a usable
        planet, used at once. Failing that, open the first affordable pack once a game,
        reroll once if money allows, and leave."""
        money = state.get("money", 0)
        for index, card in enumerate(part(state, "shop").get("cards", [])):
            affordable = part(card, "cost").get("buy", UNPRICED) <= money
            if card.get("set") in ("JOKER", "Joker"):
                if (affordable and card.get("acquire_allowed", True)
                        and has_room(part(state, "jokers"))
                        and not part(card, "modifier").get("rental")):
                    return "bh_action", {"action": "buy", "area": "shop", "index": index}
            elif (str(card.get("set", "")).upper() == "PLANET" and affordable
                    and card.get("usable") and not self.used_planet):
                self.used_planet = True
                return "bh_action", {"action": "buy", "area": "shop", "index": index,
                                     "mode": "buy_and_use"}
        if not self.opened_pack:
            for index, card in enumerate(part(state, "packs").get("cards", [])):
                if part(card, "cost").get("buy", UNPRICED) <= money:
                    self.opened_pack = True
                    return "bh_action", {"action": "buy", "area": "packs", "index": index}
        if not self.rerolled and money >= part(state, "round").get("reroll_cost", 5) + 5:
            self.rerolled = True
            return "reroll", {}
        return "next_round", {}


def pack_move(state):
    """Take the first card in the open pack that can be used now, with its fewest targets,
    or skip the pack."""
    hand = hand_cards(state)
    for index, card in enumerate(part(state, "pack").get("cards", [])):
        targets = card.get("min_targets", 0)
        kind = str(card.get("set", "")).upper()
        if targets > len(hand):
            continue
        if kind == "JOKER" and not has_room(part(state, "jokers")):
            continue
        if kind in ("TAROT", "PLANET", "SPECTRAL") and not card.get("usable"):
            continue
        return "bh_action", {"action": "choose_pack", "area": "pack", "index": index,
                             "targets": list(range(targets))}
    return "bh_action", {"action": "skip_pack"}


def has_room(jokers):
    return jokers.get("count", 0) < jokers.get("limit", 5)


def hand_cards(state):
    return part(state, "hand").get("cards", [])


def best_hand(state):
    """The cards to play, as hand positions, and the poker hand they make.

    Every selection of one to five cards that keeps the forced cards is scored. The best
    score wins, then the fewer cards, then the later positions.
    """
    cards = hand_cards(state)
    forced = {index for index, card in enumerate(cards) if card.get("forced_selection")}
    limit = min(5, part(state, "hand").get("highlighted_limit", 5), len(cards))
    choices = []
    for size in range(1, limit + 1):
        for chosen in combinations(range(len(cards)), size):
            if forced <= set(chosen):
                points, name = score([cards[index] for index in chosen], part(state, "hands"))
                choices.append((points, -size, chosen, name))
    if not choices:
        raise PlayFailed("no legal hand")
    _, _, chosen, name = max(choices)
    return list(chosen), name


def score(cards, hands):
    """The score the policy expects for playing these cards, and the poker hand they make.

    The hand's chips and mult are its level in `hands`, or else its base values. Each
    scoring card that is not debuffed adds its chips: an Ace 11, a face card 10.
    """
    ranks = [rank(card) for card in cards]
    name, scoring = poker_hand(cards, ranks)
    base_chips, base_mult = BASE_SCORES[name]
    level = part(hands, name)
    chips = float(level.get("chips", base_chips))
    mult = float(level.get("mult", base_mult))
    for card, value in zip(cards, ranks, strict=True):
        if value in scoring and not part(card, "state").get("debuff"):
            chips += 11 if value == ACE else min(value, 10)
    return chips * mult, name


def poker_hand(cards, ranks):
    """The poker hand the cards make, and the ranks that score in it."""
    counts = Counter(value for value in ranks if value)
    # The ranks by how many cards share each, then by rank, highest first.
    groups = sorted(counts, key=lambda value: (counts[value], value), reverse=True)
    sizes = sorted(counts.values(), reverse=True)
    suits = [part(card, "value").get("suit") for card in cards]
    flush = len(cards) == 5 and all(ranks) and all(suits) and len(set(suits)) == 1
    distinct = sorted(set(ranks))
    straight = (len(distinct) == 5 and 0 not in distinct
                and (distinct[-1] - distinct[0] == 4 or distinct == WHEEL))
    if straight and flush:
        return "Straight Flush", ranks
    if sizes[:1] == [4]:
        return "Four of a Kind", groups[:1]
    if sizes == [3, 2]:
        return "Full House", ranks
    if flush:
        return "Flush", ranks
    if straight:
        return "Straight", ranks
    if sizes[:1] == [3]:
        return "Three of a Kind", groups[:1]
    if sizes[:2] == [2, 2]:
        return "Two Pair", groups[:2]
    if sizes[:1] == [2]:
        return "Pair", groups[:1]
    return "High Card", [max(ranks, default=0)]


def rank(card):
    """The card's rank, or 0 for a face-down card or a rank the policy cannot read."""
    if part(card, "state").get("hidden"):
        return 0
    return RANKS.get(str(part(card, "value").get("rank")), 0)
