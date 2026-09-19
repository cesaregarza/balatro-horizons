#!/usr/bin/env python3
"""Prepare persistent, owner-only provider credentials for the local user service.

Preview by default. --apply writes files; it never restarts a service or calls a
provider. Without --source it prepares an empty file only if one does not exist.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

ALLOWED = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
ROOT = Path(__file__).resolve().parents[1]


def credential_names(data):
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError:
        raise ValueError("INVALID_CREDENTIAL_FILE") from None
    names = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if key not in ALLOWED or not separator or key in names:
            raise ValueError("ONLY_PROVIDER_CREDENTIALS_ALLOWED")
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("INVALID_CREDENTIAL_VALUE")
            value = value[1:-1]
        if not value or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("INVALID_CREDENTIAL_VALUE")
        names.append(key)
    return sorted(names)


def atomic_private(path, data):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("SYMLINK_DESTINATION_FORBIDDEN")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def configure(root, unit_dir, source=None, *, apply=False):
    root, unit_dir = root.resolve(), unit_dir.resolve()
    if any(p.is_relative_to("/mnt") for p in (root, unit_dir)):
        raise ValueError("NATIVE_LINUX_PATH_REQUIRED")
    target = root / "private/providers.env"
    dropin = unit_dir / "balatro-horizons.service.d/20-provider-environment.conf"
    if source is not None:
        if source.resolve().is_relative_to("/mnt"):
            raise ValueError("NATIVE_LINUX_PATH_REQUIRED")
        data = source.read_bytes()
    elif target.exists():
        data = target.read_bytes()
    else:
        data = b"# Provider credentials for the local backend. Never commit this file.\n"
    names = credential_names(data)
    if source is not None and not names:
        raise ValueError("PROVIDER_CREDENTIAL_MISSING")
    if any(c in str(target) for c in ('"', "\\", "%")) or any(c.isspace() for c in str(target)):
        raise ValueError("UNSUPPORTED_ENVIRONMENT_PATH")
    settings = f'[Service]\nEnvironmentFile=\nEnvironmentFile={target}\n'.encode()
    if apply:
        atomic_private(target, data)
        atomic_private(dropin, settings)
    return {"applied": apply, "credential_names": names, "environment_file": str(target),
            "drop_in": str(dropin), "service_restart_required": apply,
            "provider_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Linux environment file containing provider keys; never echoed")
    parser.add_argument("--apply", action="store_true", help="Write private files; default is a read-only preview")
    args = parser.parse_args(argv)
    try:
        result = configure(ROOT, Path.home() / ".config/systemd/user", args.source, apply=args.apply)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error) if str(error).isupper() else type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
