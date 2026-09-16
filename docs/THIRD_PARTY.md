# Third-party components

The licensed Balatro executable and assets are copied locally from the owner's installation. They, native save files, and extracted licensed rules are not committed or included in public exports.

- BalatroBot 1.5.2, pinned commit `e7c6db8a9ad88318f6e4128eefd6e61aafc94885`: MIT, copyright Coder. Its license is retained in the isolated mod directory. The optional preview guard is a local patch; the Horizons instrumentation is in `native/patches/`.
- Steamodded 26.829.0, pinned commit `39182f0cc7b1af86d3d3d6afc5422661a07b4312`: GNU GPL version 3 license text retained in the local source and runtime copy. The upstream source is kept in the ignored Linux `vendor/smods` directory.
- Lovely 0.9.0: downloaded from the pinned upstream release with a fixed archive checksum. It is installed only in the dedicated runtime, not the owner's Steam installation.
- BlindDeck `8015c448d0a97e4f9d180ae6f1192412cc308871`: inspected as an API behavior reference. It is not installed or bundled.
- Python and browser dependencies are identified by `uv.lock` and `web/package-lock.json`; their upstream license metadata remains in installed packages.

This repository is a local application and does not redistribute the game or a packaged mod runtime. Preserve upstream license files and source provenance if packaging the open-source components later.
