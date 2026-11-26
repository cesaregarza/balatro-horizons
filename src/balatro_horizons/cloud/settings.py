"""Cloud runner settings, loaded from their own file rather than the run config."""

from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from balatro_horizons.config import ROOT

DEFAULT_PATH = ROOT / "configs/cloud.yaml"
# Operations' private values; the directory is git-ignored.
ACCESS_PATH = ROOT / "private/cloud-access.yaml"
# The token is read at the moment a client is built and is never stored.
TOKEN_VARIABLE = "DIGITALOCEAN_TOKEN"


class CloudSettings(BaseModel):
    # Budget and price feed sums and comparisons, so an infinite or NaN value is invalid.
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    # Part of the owner tag: the controller only ever deletes resources carrying it.
    controller_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    region: str = "nyc3"
    size: str = "s-2vcpu-8gb-amd"
    image: str = "ubuntu-24-04-x64"
    max_run_hours: float = Field(default=4, gt=0, le=72)
    hourly_price_usd: float = Field(default=0.063, gt=0)
    # Parallel runs are a later slice; one controller supervises one worker.
    max_concurrent_workers: int = Field(default=1, ge=1, le=1)
    infrastructure_budget_usd: float = Field(gt=0)
    # From the create until the worker reports the game ready.
    boot_timeout_seconds: int = Field(default=1200, gt=0)
    heartbeat_grace_seconds: int = Field(default=300, gt=0)
    cleanup_verify_seconds: int = Field(default=600, gt=0)
    retry_base_seconds: float = Field(default=2, gt=0)
    retry_max_seconds: float = Field(default=60, gt=0)
    poll_seconds: float = Field(default=15, gt=0)

    @property
    def owner_tag(self):
        return f"bh-owner:{self.controller_id}"

    @property
    def worst_case_usd(self):
        return self.max_run_hours * self.hourly_price_usd


def utc_now():
    return datetime.now(UTC)


def droplet_name(run_id):
    return f"bh-{run_id}"


def run_tag(run_id):
    return f"bh-run:{run_id}"


def run_id_from_tags(tags):
    prefix = run_tag("")
    found = [tag[len(prefix):] for tag in tags if tag.startswith(prefix)]
    return found[0] if len(found) == 1 else None


def load_settings(path: Path | None = None) -> CloudSettings:
    return CloudSettings.model_validate(yaml.safe_load(Path(path or DEFAULT_PATH).read_text()))
