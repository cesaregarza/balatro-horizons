"""Executable identity with exact, one-way historical module migrations.

AST hashes ignore comments and formatting, not executable statements or methods.
Native files are compared byte-for-byte by the separate native manifest.
Historical code is parsed as data, never imported or executed.
"""

import ast

from balatro_horizons.evidence.identity_migrations import upgrade_historical_manifest
from balatro_horizons.evidence.provenance import (
    IMPLEMENTATION_DIRECTORIES,
    IMPLEMENTATION_FILES,
    fingerprint_sources,
    native_component_manifest,
)
from balatro_horizons.storage.journal import digest

# These define the comparison itself, not the historical execution contract.
# The receipt separately binds their exact bytes via the full current source hash.
POLICY_FILES = frozenset({
    "evidence/compatibility.py", "evidence/execution_identity.py",
    "evidence/identity_migrations.py",
})


def execution_manifest(sources, *, historical=False):
    """Cover every fingerprinted execution file outside the native/policy layers."""
    prefix = "src/balatro_horizons/"
    native = native_component_manifest(sources)
    result = {}
    for name, content in sources.items():
        path = name.removeprefix(prefix)
        if (not name.startswith(prefix) or name in native
                or path in POLICY_FILES or path == "game/fake.py"):
            continue
        if path in IMPLEMENTATION_FILES or path.split("/")[0] in IMPLEMENTATION_DIRECTORIES:
            result[name] = digest(ast.dump(ast.parse(content)))
    if historical:
        return upgrade_historical_manifest(result, fingerprint_sources(sources))
    return result
