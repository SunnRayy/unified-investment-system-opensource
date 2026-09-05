"""Env manager — reads and writes individual keys in the project .env file.

Uses python-dotenv's set_key/get_key which preserve comments and ordering
for all untouched lines.
"""

from __future__ import annotations

from pathlib import Path

ENV_PATH = Path(__file__).parents[2] / ".env"


def get_key_status(env_var: str) -> str:
    """Return 'configured' if the env var has a non-empty value in .env, else 'missing'."""
    try:
        from dotenv import get_key  # noqa: PLC0415
        val = get_key(str(ENV_PATH), env_var)
        return "configured" if val else "missing"
    except Exception:
        return "missing"


def update_key(env_var: str, value: str) -> None:
    """Write or update a single key in .env. Preserves all other lines and comments."""
    from dotenv import set_key  # noqa: PLC0415
    set_key(str(ENV_PATH), env_var, value)
