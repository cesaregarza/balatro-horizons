# Architecture

Balatro Horizons has three runtime layers with a narrow typed boundary:

```text
native game / evaluator
        │ EvaluatorSession + public observations
        ▼
game/ contract ── harness/ tools, providers, budgets
        │ public API schemas and privacy projection
        ▼
api/ + web/ ── exploration, reports, annotations, optional workbench
        │
        ├── evidence/ ── certification, provenance, private artifacts
        └── storage/ ── append-only journals, manifests, rebuildable indexes
```

## Game layer

`src/balatro_horizons/game/` owns transport, native state conversion, action
validation, lifecycle, and session ownership. `GameSession` exposes observation,
apply, terminal, checkpoint, restore, and close. `EvaluatorSession` adds the
operator-only start, fixture, raw-inspection, replay, and calibration seams.
The public contract exposes observations and enumerated actions; raw endpoint
text, seeds, saves, and engine state remain private. Native failures are
infrastructure failures; enumerated rejected actions are agent-visible reasons.

## Harness layer

The harness turns one public observation into bounded helper calls or one action.
The current prompt, guide snapshot, skills, provider capability, memory, costs,
and request ledger are frozen per episode. `ActionEnvelope` carries an
observation ID, typed action, optional memory replacement, and optional decision
note. Providers share the same action/helper boundary. Limits and spending
reservations are resolved from one configuration source and recorded before
calls; unknown usage is retained and caps fail closed.

## Dashboard layer

The browser uses typed HTTP clients and screen components. The Python gateway
projects public data and never exposes private run directories. Run library,
exploration, settings, batches, reports, and append-only retrospective
annotations are ordinary dashboard surfaces. Staged reveal, branches,
comparison, human takeover, and verification are the opt-in workbench and
return 404 when disabled; see [explicit budget continuation](docs/dashboard.md#explicit-budget-continuation).
The loopback bind guard and trusted-origin middleware protect every route.

## Evidence and storage

Evidence records bind source, runtime, environment, deck, stake, and restoration
scope. Certification compares public state and private continuation fingerprints
across fresh processes; a save call alone is not proof. Reuse requires an
immutable parent certificate, unchanged native bytes, the same environment lock,
and a matching offline report. Public manifests and event journals are
hash-chained and append-only. Annotation revisions and review records are also
append-only; SQLite indexes are rebuildable and never the source of truth.

Execution ownership stays in `service.py`: admission, scheduling, and policy
selection remain there, while `service_execution.py` owns the prepared plan and
locked game lifecycle. `evaluation/reports.py` owns report files and rendering;
`evaluation/export.py` owns the ordered public episode snapshot assembled for
those reports. Both helpers preserve the service/report public entry points.

## Trust boundary

The native process, provider credentials, raw observations, seeds, saves,
private manifests, spending details, and divergence payloads are operator-only.
The public boundary contains schema-selected observations, action outcomes,
sanitized reports, and reviewer-independent annotation revisions. Every export
is projected and privacy-scanned server-side. A browser cannot request raw
engine state or assemble a private export from detail records.

Contracts are the seam: `contracts.py` defines public observations, actions,
action envelopes, and annotation inputs; `game/contract.py` defines sessions;
API schemas define route payloads; and the web client mirrors those types. A
new helper, export, flag, or seam names its consumer in the same change.
