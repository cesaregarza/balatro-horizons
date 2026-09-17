# Dashboard status and card modifier visibility

The operator view previously verified every saved run's entire journal on each
status poll. Its two-second browser interval could enqueue another request while
the previous request was still running. The optional event stream also performed
that synchronous work directly on the server event loop.

`review/operator_status.py` now caches compact per-episode aggregates. It verifies
a journal again when its device, inode, size, modification time or change time
changes; concurrent callers share the cache. Reads of changing journals use the
writer lock. The cache retains no observations or provider bodies. Terminal
results come from verified journal records even before SQLite is updated, and
pending provider reservations remain in reported costs. Unchanged polls do not
append duplicate exposure records; new event boundaries and outcomes still do.
Prospective review continues to use its existing independent reveal controls.

Browser status and human-control polling wait for completion before scheduling
the next request, ignore responses from a disabled watcher, and pause in hidden
tabs. Event-stream status work runs in the thread pool. The first status request
after a server restart still verifies existing journals once.

## Card presentation

Jokers, owned consumables, shop offers and hand cards show labeled modifier
badges. Polychrome names have a rainbow treatment on dark panels; edition borders
and badges also distinguish Foil, Holographic and Negative. Eternal, Perishable
(including a recorded remaining-round count), Rental and Debuffed have separate
badges. Playing cards show named enhancements and Red, Blue, Gold or Purple seal
badges. Wider card tiles keep labels readable on mobile.

Only explicit public modifier fields are interpreted. Ordinary Joker ability
strings are not treated as playing-card enhancements. Unknown edition text is
escaped and never inserted into a CSS class. Concealed cards suppress modifier
classes, badges, identity, counters, suit colors and effect tooltips. Missing
historical modifiers are not reconstructed or guessed. Original journals and
exports remain unchanged.

## Verification and deployment

- 328 offline Python tests passed; Ruff and the production TypeScript/Vite build
  passed. Cache tests cover concurrent reuse, exposure, updated costs, terminal
  indexing races, metadata-changing journal corruption, and operator access.
- All 18 browser tests passed on an isolated port-8766 fixture server. After
  widening the tiles, the three affected phone/mobile checks passed again.
  Screenshots were inspected for readable labels and concealed-card leakage.
- The native implementation fingerprint remains unchanged. No Balatro process
  was started or restarted and no paid provider call was made.
- The tested frontend was published atomically, retaining previous assets. The
  idle Linux dashboard service was restarted once while both worker locks were
  held. The latest run retained its verified game-loss result.
- Local observations on 2026-09-17: the old status request took 2,609.51 ms;
  the new first request took 2,608.34 ms and a cached request took 77.24 ms
  (both include bootstrap). The deployed homepage returned HTTP 200 in 9.17 ms.
  These are individual measurements, not a sustained load benchmark.

Use `scripts/workbench_status.py --timing` for subsequent compact latency checks.
This release does not include the separately staged model-facing cost summary.
