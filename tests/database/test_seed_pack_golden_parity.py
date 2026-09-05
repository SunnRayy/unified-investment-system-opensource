"""Golden parity test: seeds/private-ray/ vs the legacy hardcoded defaults
(Program OSR WS-3b).

seeds/private-ray/ is the project owner's real reader-mapping vocabulary,
gitignored (never committed — see .gitignore, seeds/README.md). This test
proves that IF it exists, loading it produces byte-identical output to
src.services.reader_mappings._legacy_defaults() (the historical hardcoded
dict, still what every deployment without $UIS_SEED_PROFILE set uses today).
That's the precondition WS-3b's activation step depends on: the env var can
only safely get set once this test is green.

On CI and any fresh clone, seeds/private-ray/ does not exist — this test
SKIPS (not fails, not silently vanishes) with an explicit, greppable reason.
"""
from __future__ import annotations

import pytest

from src.database.seed_loader import SEEDS_ROOT, load_seed_pack
from src.services.reader_mappings import _legacy_defaults

PRIVATE_PROFILE = "private-ray"
_SKIP_REASON = "seed pack 'private-ray' not present (gitignored, private-only — expected on CI/fresh clone)"


def _private_pack_present() -> bool:
    return (SEEDS_ROOT / PRIVATE_PROFILE).is_dir()


@pytest.mark.skipif(not _private_pack_present(), reason=_SKIP_REASON)
class TestPrivateRayGoldenParity:
    def test_reader_mappings_byte_identical_to_legacy_defaults(self):
        pack = load_seed_pack(PRIVATE_PROFILE)
        assert pack.reader_mappings == _legacy_defaults(), (
            "seeds/private-ray/ must reproduce _legacy_defaults() exactly — "
            "any divergence means the private pack has drifted from "
            "src/database/mapping_seeds.py and activating UIS_SEED_PROFILE="
            "private-ray would silently change production behavior."
        )

    def test_all_nine_reader_mapping_keys_present(self):
        pack = load_seed_pack(PRIVATE_PROFILE)
        expected_keys = {
            ("financial_summary", "fs_column"), ("financial_summary", "ie_column"),
            ("gold", "id_field_map"), ("insurance", "id_field_map"), ("rsu", "id_field_map"),
            ("schwab", "known_etf"), ("schwab", "symbol_norm"), ("schwab", "action_map"),
            ("cn_fund", "type_map"),
        }
        assert set(pack.reader_mappings.keys()) == expected_keys
