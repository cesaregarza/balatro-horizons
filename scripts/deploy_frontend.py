#!/usr/bin/env python3
"""Publish a built frontend without restarting the backend or deleting old assets."""

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

from install_candidate import child, digest, native


def deploy(build, dist, backup):
    build, dist, backup = (native(path) for path in (build, dist, backup))
    index = child(build, "index.html")
    references = re.findall(r'(?:src|href)="(/assets/[^"?#]+)"', index.read_text())
    if not references or not (build / "assets").is_dir():
        raise ValueError("INVALID_FRONTEND_BUILD")
    for reference in references:
        if not child(build, reference.lstrip("/")).is_file():
            raise ValueError("MISSING_FRONTEND_ASSET")
    # Preflight before writing anything. Retained assets keep open tabs usable.
    sources = []
    for path in sorted((build / "assets").rglob("*")):
        if not path.is_file():
            continue
        name = str(path.relative_to(build))
        source, target = child(build, name), child(dist, name)
        if target.exists() and digest(target) != digest(source):
            raise ValueError("EXISTING_ASSET_CONTENT_CHANGED")
        sources.append((source, target))
    old_index = child(dist, "index.html")
    if backup.exists():
        raise ValueError("BACKUP_ALREADY_EXISTS")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if old_index.exists():
        shutil.copy2(old_index, backup)
    for source, target in sources:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        if digest(source) != digest(target):
            raise ValueError("ASSET_COPY_MISMATCH")
    dist.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dist, prefix=".index-", delete=False) as stream:
        staged = Path(stream.name)
    try:
        shutil.copy2(index, staged)
        if digest(index) != digest(staged):
            raise ValueError("INDEX_COPY_MISMATCH")
        staged.replace(old_index)
    finally:
        staged.unlink(missing_ok=True)
    return {"deployed": True, "index_sha256": digest(old_index), "assets": len(sources),
            "previous_index": str(backup) if backup.exists() else None, "backend_restarted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True, help="Completed Vite build directory")
    parser.add_argument("--dist", type=Path, required=True, help="Existing served web/dist directory")
    parser.add_argument("--backup", type=Path, required=True, help="New file for the previous index")
    args = parser.parse_args()
    print(json.dumps(deploy(args.build, args.dist, args.backup), sort_keys=True))


if __name__ == "__main__":
    main()
