# Dashboard architecture

The browser is a thin operator client. `web/src/api/client.ts` owns the
typed HTTP boundary, `screens/` owns top-level screens, and `workbench/` owns
the opt-in staged review surface. `App.tsx` only coordinates navigation,
tokens, polling, and screen state.

The Python gateway is assembled from `api/app.py`, shared middleware, and
route modules. Review and intervention routes are registered only when
`Config.workbench_enabled` is true. The factory defaults that flag to false;
the `bh review` command opts into the staged workbench only with
`--workbench`. The flag gates the workbench route module: staged review,
review navigation, branches, verification, human control, and workbench
annotations. The default-off factory still mounts the dashboard's explorer,
append-only annotation, batch, export, and settings routes; only the
workbench-only routes return 404.

Decision exports are projected on the server. The browser requests JSON or
JSONL with GET from `/api/explore/export/{format}` (or the staged
`/api/review/export/{format}`) and never assembles an export from detail
records. The shared action descriptor table at
`web/src/actionDescriptors.json` feeds both the Python summary and TypeScript
presentation labels.

DevTrace is reached from Decision Explorer's development toggle.

All native game and harness modules remain outside the gateway's route
projection. The review command accepts loopback hosts only; a public tailnet
origin still requires HTTPS and a `.ts.net` hostname.
