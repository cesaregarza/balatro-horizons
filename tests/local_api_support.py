"""The game's local API, in process, for the driver and worker tests: no socket and no game.

It answers JSON-RPC bodies the way the mod does. A move that commits leaves a record that
`bh_request_status` returns by the move's own ID. Tests script faults by method and by
the call's turn for that method.
"""

from collections import Counter

# The policy plays this hand as a pair of aces, after discarding the other three cards.
HAND = [{"value": {"rank": rank, "suit": suit}} for rank, suit in (
    ("Ace", "Spades"), ("Ace", "Hearts"), ("King", "Clubs"), ("7", "Diamonds"), ("4", "Clubs"))]
# The phase each move leaves the game in. Any other move leaves it where it was.
NEXT_PHASE = {"start": "BLIND_SELECT", "select": "SELECTING_HAND", "play_hand": "ROUND_EVAL",
              "cash_out": "SHOP", "next_round": "BLIND_SELECT"}
# The faults a call can meet, by name:
#   lose    no answer comes back, and nothing commits;
#   drop    the move commits, then its answer is lost;
#   stray   the move commits, and its answer carries another ID;
#   refuse  the game answers with an error, and nothing commits;
#   reject  the game rejects the move, then the answer is lost.
FAULTS = ("lose", "drop", "stray", "refuse", "reject")


class FakeLocalApi:
    """One game behind its API. It ends once `end_after` moves have committed, in a game
    over or a win as `ending` says; with no `end_after` it plays on."""

    def __init__(self, token, instance_id, *, end_after=None, ending="GAME_OVER"):
        self.token = token
        self.instance_id = instance_id
        self.end_after = end_after
        self.ending = ending
        # (method, turn) -> fault, where a method's first call is turn 1.
        self.faults = {}
        # How many inspects find the game busy after each committed move.
        self.busy_after_move = 0
        self.bodies = []
        self.records = {}
        self.turns = Counter()
        self.phase = "MENU"
        self.round = 0
        self.discards = 0
        self.committed = 0
        self.busy = 0

    def __call__(self, url, body, timeout):
        """One request, as the worker's transport sends it: the decoded reply, or OSError."""
        self.bodies.append(body)
        method, params = body["method"], body["params"]
        self.turns[method] += 1
        fault = self.faults.get((method, self.turns[method]))
        if fault == "lose":
            raise OSError("timed out")
        if params.get("_bh_token") != self.token:
            return refusal(body, "unauthorized")
        if method == "bh_inspect":
            return answer(body, self.inspect())
        if method == "bh_request_status":
            return answer(body, self.records.get(params.get("request_id"), {"status": "unknown"}))
        if fault == "refuse":
            return refusal(body, "refused")
        if fault == "reject":
            self.records[body["id"]] = {"status": "rejected", "intent": method}
            raise OSError("timed out")
        result = self.apply(method, params)
        self.records[body["id"]] = {"status": "committed", "response": result, "intent": method}
        if fault == "drop":
            raise OSError("timed out")
        return answer({"id": "another"} if fault == "stray" else body, result)

    def apply(self, method, params):
        move = params.get("action") if method == "bh_action" else method
        if move == "start":
            self.round = 1
        if move == "select":
            self.discards = 3
        if move == "discard":
            self.discards -= 1
        if move == "next_round":
            self.round += 1
        self.phase = NEXT_PHASE.get(move, self.phase)
        self.committed += 1
        self.busy = self.busy_after_move
        return self.view()

    def inspect(self):
        busy, self.busy = self.busy > 0, max(self.busy - 1, 0)
        return self.view(busy)

    def view(self, busy=False):
        over = self.end_after is not None and self.committed >= self.end_after
        phase = "GAME_OVER" if over and self.ending == "GAME_OVER" else self.phase
        view = {"state": phase, "ante_num": 1, "round_num": self.round, "money": 4,
                # The game's own: the policy never reads them.
                "seed": "PRIVATE2", "cards": {"count": 44},
                "bh": {"ready": phase != "MENU", "busy": busy, "instance_id": self.instance_id,
                       "game_version": "9.9.9-test"}}
        if over and self.ending == "won":
            view["won"] = True
        if phase == "SELECTING_HAND":
            view |= {"hand": {"cards": HAND, "highlighted_limit": 5},
                     "round": {"discards_left": self.discards}}
        if phase == "SHOP":
            view |= {"shop": {"cards": []}, "packs": {"cards": []}, "round": {"reroll_cost": 5}}
        return view

    def sent(self):
        """The moves sent, in order, as (method, action) pairs."""
        return [(body["method"], body["params"].get("action")) for body in self.bodies
                if body["method"] not in ("bh_inspect", "bh_request_status")]

    def bodies_for(self, method):
        return [body for body in self.bodies if body["method"] == method]


def answer(body, result):
    return {"jsonrpc": "2.0", "id": body["id"], "result": result}


def refusal(body, message):
    return {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32000, "message": message}}
