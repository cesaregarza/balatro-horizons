# Dashboard architecture

The browser is a thin operator client. `web/src/api/client.ts` owns the
typed HTTP boundary, `screens/` owns top-level screens, and `workbench/` owns
the opt-in staged review surface. `App.tsx` only coordinates navigation,
tokens, polling, and screen state.

The Python gateway is assembled from `api/app.py`, shared middleware, and
route modules. Review and intervention routes are registered only when
`Config.workbench_enabled` is true. The factory defaults that flag to false;
the `bh review` dashboard command explicitly opts into the workbench. A
default-off factory therefore returns 404 for review, branch, annotation,
batch, and settings mutation routes while retaining bootstrap, run-library,
run, and operator-status routes.

Decision exports are projected on the server. The browser requests JSON or
JSONL from `/api/review/export/{format}` and never assembles an export from
detail records. The shared action descriptor table at
`web/src/actionDescriptors.json` feeds both the Python summary and TypeScript
presentation labels.

DevTrace is reached from Decision Explorer's development toggle. Budget
continuation is an operator-only workbench action and creates an immutable
assisted child after the parent journal, checkpoint, spending ledger, and
implementation fingerprint have been revalidated.

All native game and harness modules remain outside the gateway's route
projection. The loopback bind guard must be explicitly overridden before an
operator can bind remotely; a public tailnet origin still requires HTTPS and a
`.ts.net` hostname.
