"""Small fail-closed validation seam shared by evidence operations."""


def require(condition: bool, code: str):
    """Raise a stable machine-readable failure instead of guessing."""
    if not condition:
        raise ValueError(code)
