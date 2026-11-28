"""The game's local API, in process, for the driver and worker tests: no socket and no game.

It answers JSON-RPC bodies the way the mod does. A move that commits leaves a record that
`bh_request_status` returns by the move's own ID. A refusal comes back as the mod sends one,
over HTTP 200: an error reply with the request's own ID. Tests script faults by method and
by the call's turn for that method. Given a clock, it keeps when each request came, and a
move's lost answer takes `lost_after` seconds on that clock.
"""

from collections import Counter

# The policy plays this hand as a pair of aces, after discarding the other three cards.
HAND = [{"value": {"rank": rank, "suit": suit}} for rank, suit in (
    ("Ace", "Spades"), ("Ace", "Hearts"), ("King", "Clubs"), ("7", "Diamonds"), ("4", "Clubs"))]
# The phase each move leaves the game in. Any other move leaves it where it was.
NEXT_PHASE = {"start": "BLIND_SELECT", "select": "SELECTING_HAND", "play_hand": "ROUND_EVAL",
              "cash_out": "SHOP", "next_round": "BLIND_SELECT"}
# The calls that only read. Every other call is a move.
READS = ("bh_inspect", "bh_request_status")
# The faults a call can meet, by name:
#   lose     no answer comes back, and nothing commits;
#   drop     the move commits, then its answer is lost;
#   stray    the move commits, and its answer carries another ID;
#   refuse   the game answers the call with an error, and nothing commits;
#   reject   the game rejects the move, then the answer is lost;
#   pending  the game admits the move and its answer is lost while the game settles it:
#            the status reads pending for `pending_asks` asks and the move then commits,
#            or with no count it stays pending;
#   expire   the game admits the move, its answer is lost, and the game gives up on it:
#            the status reads unknown, with the move's intent;
#   exit     the game stops during the move: its answer is lost, every later call meets
#            a refused connection, and the game no longer runs.
FAULTS = ("lose", "drop", "stray", "refuse", "reject", "pending", "expire", "exit")
# How the worker's transport names each way an answer is lost.
TIMED_OUT = "TimeoutError"
CLOSED = "RemoteDisconnected"
CONNECTION_REFUSED = "URLError"
# A driver that never stops calling fails its test instead of hanging it.
REQUEST_LIMIT = 100_000


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
        # The message and the data of the error a refused call gets. The patch puts its code
        # in the message and names a BUSY refusal INFRASTRUCTURE. With no data, the error
        # carries none.
        self.refusal = "BUSY"
        self.refusal_data = {"name": "INFRASTRUCTURE"}
        # How many asks find a pending move still pending; None keeps it pending.
        self.pending_asks = None
        self.lost_after = 0
        # Something with __call__() for the time and advance(seconds), or None.
        self.clock = None
        self.bodies = []
        self.times = []
        self.records = {}
        self.pending = {}
        self.turns = Counter()
        self.exited = False
        self.phase = "MENU"
        self.round = 0
        self.discards = 0
        self.committed = 0
        self.busy = 0

    def __call__(self, url, body, timeout):
        """One request, as the worker's transport sends it: the decoded reply, or OSError."""
        assert len(self.bodies) < REQUEST_LIMIT, "the driver never stopped calling"
        self.bodies.append(body)
        self.times.append(self.clock() if self.clock else None)
        method, params = body["method"], body["params"]
        self.turns[method] += 1
        fault = self.faults.get((method, self.turns[method]))
        if self.exited:
            raise OSError(CONNECTION_REFUSED)
        if fault == "lose":
            raise self.lost(method)
        if params.get("_bh_token") != self.token:
            return refusal(body, "UNAUTHORIZED", {"name": "HARNESS_FAULT"})
        if fault == "refuse":
            return refusal(body, self.refusal, self.refusal_data)
        if method == "bh_inspect":
            return answer(body, self.inspect())
        if method == "bh_request_status":
            return answer(body, self.status(params.get("request_id")))
        if fault == "exit":
            self.exited = True
            raise self.lost(method, CLOSED)
        if fault in ("reject", "pending", "expire"):
            status = {"reject": "rejected", "pending": "pending", "expire": "unknown"}[fault]
            self.records[body["id"]] = {"status": status, "intent": method}
            self.pending[body["id"]] = (method, params)
            raise self.lost(method)
        result = self.commit(body["id"], method, params)["response"]
        if fault == "drop":
            raise self.lost(method)
        return answer({"id": "another"} if fault == "stray" else body, result)

    def lost(self, method, kind=TIMED_OUT):
        """A lost answer as the transport raises it; a move's comes `lost_after` seconds after
        it was sent."""
        if self.clock is not None and method not in READS:
            self.clock.advance(self.lost_after)
        return OSError(kind)

    def status(self, request_id):
        """The game's record of a move, or unknown with no intent for a move it never saw."""
        record = self.records.get(request_id, {"status": "unknown"})
        if record["status"] != "pending" or self.pending_asks is None:
            return record
        if self.pending_asks > 0:
            self.pending_asks -= 1
            return record
        return self.commit(request_id, *self.pending[request_id])

    def commit(self, request_id, method, params):
        result = self.apply(method, params)
        self.records[request_id] = {"status": "committed", "response": result, "intent": method}
        return self.records[request_id]

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

    def running(self):
        """Whether the game is still up: it stops at an `exit` fault."""
        return not self.exited

    def sent(self):
        """The moves sent, in order, as (method, action) pairs."""
        return [(body["method"], body["params"].get("action")) for body in self.bodies
                if body["method"] not in READS]

    def bodies_for(self, method):
        return [body for body in self.bodies if body["method"] == method]

    def times_for(self, method):
        """When each request of `method` came, by the clock."""
        return [at for body, at in zip(self.bodies, self.times, strict=True)
                if body["method"] == method]


def answer(body, result):
    return {"jsonrpc": "2.0", "id": body["id"], "result": result}


def refusal(body, message, data):
    """An error reply in the mod's envelope: a code, a message and, unless `data` is None,
    the data. The mod's own refusals put prose in the message and their category in the
    data's name."""
    error = {"code": -32000, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "error": error, "id": body["id"]}
