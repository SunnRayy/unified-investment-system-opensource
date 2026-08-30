"""Gold / Insurance hooks placeholder (Program OSR WS-2 mechanical split).

Gold and Insurance readers use the config engine's normal declarative path —
column mapping, `id_field_maps`, and identity templates driven entirely by
YAML (config/readers/gold.yaml, config/readers/insurance.yaml) plus the
ID_FIELD_MAP_SEEDS vocabulary (src.database.mapping_seeds) — with no custom
post-transactions hook function. There was nothing to extract from the
pre-split src/sources/reader_hooks.py for either source.

This module exists so the hooks/ package's naming matches the reader
lineup 1:1 and so a future gold- or insurance-specific hook has an obvious
home, without forcing one into existence prematurely (see AGENTS.md's
guidance against speculative abstractions).

IMPORT CONSTRAINT (mirrors src.sources.registry, shared by every module in
this package): stdlib + pandas only at module level. Lazy imports inside a
function body are allowed.
"""
from __future__ import annotations
