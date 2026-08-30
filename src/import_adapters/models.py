from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FileReadResult:
    headers: list[str]
    preview_rows: list[dict[str, Any]]
    total_rows: int
    full_rows: list[dict[str, Any]] | None = None
