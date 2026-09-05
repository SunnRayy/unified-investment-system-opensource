"""Shared helper: taxonomy_classes English-name -> name_cn lookup.

Program BIL / WS-9. `taxonomy_classes.name` is a stable KEY (joins, filters,
grouping) and is never translated. `name_cn` is the ONLY source of truth for
the Chinese display name — it is user-editable via the Taxonomy page, so
this helper reads it fresh on every call rather than caching a copy anywhere
(the table is <30 rows; a second source of truth for these strings is
exactly the class of bug this project has been bitten by repeatedly — see
docs/decisions and the "two-sources-for-one-value" pattern).

Endpoints that render a class or sub-class name should look it up here and
return an additive `*_cn` companion field alongside the existing English
field. Never remove or rename the English field — it is the key consumers
join/filter/group on.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_class_name_cn_map(db) -> dict[str, str]:
    """Return {taxonomy_classes.name: name_cn} for rows with a non-empty name_cn.

    Names with no name_cn set are omitted (not included as None) so callers
    can use a plain dict .get(name) and treat a miss the same as "no
    translation available" — the frontend resolver falls back to the
    English name in both cases.

    Defensive: some call sites (and a number of narrow-schema unit test
    fixtures that build a minimal in-memory DB without taxonomy_classes) run
    against a DB that doesn't have this table. This is an additive display
    enrichment, not a required dependency, so a missing table degrades to
    "no translations available" (empty map) rather than breaking the caller.
    """
    try:
        rows = db.execute(
            "SELECT name, name_cn FROM taxonomy_classes WHERE name_cn IS NOT NULL AND name_cn != ''"
        ).fetchall()
    except Exception:
        logger.debug("get_class_name_cn_map: taxonomy_classes unavailable, returning {}", exc_info=True)
        return {}
    return {name: name_cn for name, name_cn in rows}
