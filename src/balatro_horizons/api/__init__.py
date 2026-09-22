"""Public API factory compatibility surface."""

from balatro_horizons.config import ROOT

from .app import create_app as _create_app


def create_app(data_dir=None, config=None, *, public_origin=None, **kwargs):
    return _create_app(
        data_dir,
        config,
        public_origin=public_origin,
        output_root=ROOT,
        **kwargs,
    )


__all__ = ["ROOT", "create_app"]
