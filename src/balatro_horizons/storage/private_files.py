"""Safe atomic writes for owner-private files."""

import os
import tempfile
from pathlib import Path


class _WriteFailure(ValueError):
    """A private write failed, with whether replacement already happened."""

    def __init__(self, replaced):
        super().__init__("CREDENTIAL_DESTINATION_UNSAFE")
        self.replaced = replaced


def _check_destination(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("SYMLINK_DESTINATION_FORBIDDEN")
    parent = path.parent
    while True:
        if parent.is_symlink():
            raise ValueError("SYMLINK_DESTINATION_FORBIDDEN")
        if parent.exists():
            if not parent.is_dir():
                raise ValueError("CREDENTIAL_DESTINATION_UNSAFE")
        next_parent = parent.parent
        if next_parent == parent:
            break
        parent = next_parent
    if path.exists() and not path.is_file():
        raise ValueError("CREDENTIAL_DESTINATION_UNSAFE")


def atomic_private(path, data):
    path = Path(path)
    _check_destination(path)
    replaced = False
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
            replaced = True
            path.chmod(0o600)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except OSError:
        raise _WriteFailure(replaced) from None
