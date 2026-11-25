"""The values operations owns: the controller's SSH key, the firewall and the bundle.

They live in a git-ignored file, never in the repository. Only `bh cloud launch` loads
them, so status, cancel and reconcile, and with them every teardown, work without it.
"""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from .settings import ACCESS_PATH


class AccessError(ValueError):
    """The access file is missing or invalid; the message is the refusal code."""


def absolute(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("must be an absolute path")
    return path


AbsolutePath = Annotated[Path, AfterValidator(absolute)]


class Bundle(BaseModel):
    """The game bundle on the controller host, and the descriptor it must match."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: AbsolutePath
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AccessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ssh_key_path: AbsolutePath
    # As the provider records it: colon-separated MD5 hex.
    ssh_key_fingerprint: str = Field(pattern=r"^[0-9a-f]{2}(:[0-9a-f]{2}){15}$")
    firewall_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    bundle: Bundle


def load_access(path: Path | None = None) -> AccessSettings:
    """The access settings, or a named refusal: a bad file is never a traceback."""
    try:
        text = Path(path or ACCESS_PATH).read_text()
    except FileNotFoundError:
        raise AccessError("CLOUD_ACCESS_MISSING") from None
    except OSError:
        raise AccessError("CLOUD_ACCESS_INVALID") from None
    try:
        # Every value is read as text, so an unquoted fingerprint or digest that happens
        # to look like a number is never misread as one.
        return AccessSettings.model_validate(yaml.load(text, Loader=yaml.BaseLoader))
    except (yaml.YAMLError, ValidationError):
        raise AccessError("CLOUD_ACCESS_INVALID") from None
