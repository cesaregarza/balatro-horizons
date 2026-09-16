# Live decision explorer

The retrospective decision explorer now refreshes every two seconds until the run records a terminal outcome. It preserves the selected decision, search/filter settings, scroll position, and before/after board selection while rows arrive. Use **Jump to latest decision** to move deliberately; **Update live** pauses/resumes polling. A hidden browser tab sends no polling requests. Failed refreshes retain the last snapshot and retry after five seconds. Completion stops polling.

The existing retrospective endpoints still own access and exposure recording. Prospective tokens cannot enumerate or seek decisions. The browser does not fetch the operator status feed to drive this feature. Each new journal head refreshes the selected detail as well as the ledger. Detail reads now use the journal writer lock to avoid observing a partial append.

There is a valid live interval between a durable action commit and its subsequent settled observation. During that interval the ledger retains the pending row with “Action committed · waiting for settled state”; it does not count a finished transition or invent effects. A completed journal missing its settled observation still fails validation.

Validation: 11 backend explorer/report tests passed; the final reader-lock change also passed 10 explorer/branch tests. Four browser tests passed, covering desktop navigation, phone navigation, appended rows, retained selection/board, pause/resume, transient errors, stop-on-completion, and CLI validation. These tests use synthetic fixtures and mocked incremental responses; no native game or paid provider calls are launched by them.
