"""Preview or persist owner-private provider credentials without provider calls."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from balatro_horizons.config import ROOT

ALLOWED = frozenset({"OPENAI_API_KEY", "ANTHROPIC_API_KEY"})
_ERROR_CODES = frozenset(
    {
        "INVALID_CREDENTIAL_FILE",
        "ONLY_PROVIDER_CREDENTIALS_ALLOWED",
        "INVALID_CREDENTIAL_VALUE",
        "PROVIDER_CREDENTIAL_MISSING",
        "SYMLINK_DESTINATION_FORBIDDEN",
        "NATIVE_LINUX_PATH_REQUIRED",
        "CREDENTIAL_SOURCE_UNREADABLE",
        "CREDENTIAL_DESTINATION_UNSAFE",
        "UNSUPPORTED_ENVIRONMENT_PATH",
    }
)


def credential_names(data):
    try:
        lines = data.decode("utf-8").splitlines()
    except (AttributeError, UnicodeError):
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


def _native(path):
    if path.is_relative_to("/mnt"):
        raise ValueError("NATIVE_LINUX_PATH_REQUIRED")
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        raise ValueError("CREDENTIAL_DESTINATION_UNSAFE") from None
    if resolved.is_relative_to("/mnt"):
        raise ValueError("NATIVE_LINUX_PATH_REQUIRED")
    return resolved


def atomic_private(path, data):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("SYMLINK_DESTINATION_FORBIDDEN")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except OSError:
        raise ValueError("CREDENTIAL_DESTINATION_UNSAFE") from None


def configure(root, unit_dir, source=None, *, apply=False):
    root, unit_dir = _native(root), _native(unit_dir)
    target = root / "private/providers.env"
    dropin = unit_dir / "balatro-horizons.service.d/20-provider-environment.conf"
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("SYMLINK_DESTINATION_FORBIDDEN")
    if any(character in str(target) for character in ('"', "\\", "%")) or any(
        character.isspace() for character in str(target)
    ):
        raise ValueError("UNSUPPORTED_ENVIRONMENT_PATH")
    if source is not None:
        source = _native(source)
        try:
            data = source.read_bytes()
        except OSError:
            raise ValueError("CREDENTIAL_SOURCE_UNREADABLE") from None
    elif target.exists():
        try:
            data = target.read_bytes()
        except OSError:
            raise ValueError("CREDENTIAL_SOURCE_UNREADABLE") from None
    else:
        data = b"# Provider credentials for the local backend. Never commit this file.\n"
    names = credential_names(data)
    if source is not None and not names:
        raise ValueError("PROVIDER_CREDENTIAL_MISSING")
    settings = f"[Service]\nEnvironmentFile=\nEnvironmentFile={target}\n".encode()
    if apply:
        atomic_private(target, data)
        atomic_private(dropin, settings)
    return {
        "applied": apply,
        "credential_names": names,
        "credential_count": len(names),
        "drop_in_written": apply,
        "service_restart_required": apply,
        "provider_calls": 0,
    }


def configure_parser(parser):
    parser.description = __doc__
    parser.add_argument(
        "--source", type=Path, help="Linux environment file; values are never echoed"
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--unit-dir",
        type=Path,
        default=Path.home() / ".config/systemd/user",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--apply", action="store_true", help="Write owner-private credentials")
    parser.set_defaults(operation_handler=run, operation_parser=parser)


def _safe_error(error):
    code = str(error)
    return code if code in _ERROR_CODES else type(error).__name__


def run(args):
    try:
        result = configure(args.root, args.unit_dir, args.source, apply=args.apply)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": _safe_error(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0
