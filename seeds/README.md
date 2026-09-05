# Huinsight seed packs (Program OSR WS-3a)

**Status: additive scaffolding, not wired up.** `src/database/seed_loader.py`
reads this directory and returns data in memory; nothing here is inserted
into a database by any code path yet. Re-pointing the V75-V82 migrations at
an active pack is WS-3b, a separate reviewed phase (see the impact analysis
at `docs/plans/2026-08-16-ws1-swap-impact.md` §5 for the production-safety
constraint that phase must satisfy).

## Why YAML

The three things a seed pack has to express (reader_mappings vocabularies,
memo/data-fix/unforced-error rows, valuation PE bands) are already edited by
hand today, in Python dict literals (`src/database/mapping_seeds.py`) and
inline SQL (`schema.sql`, `connector.py`). YAML keeps that "edit a file,
read the diff in review" workflow while being language-agnostic (a
contributor adding a new seed profile shouldn't need to write Python) and
matching the convention already used for `config/readers/*.yaml` — one file
per reader, so a change to (say) the Schwab vocabulary touches exactly the
file named after it.

## Layout

```
seeds/<profile>/
  reader_mappings/
    financial_summary.yaml   # fs_column (mapped + ignored) + ie_column
    gold.yaml                # id_field_map
    insurance.yaml           # id_field_map
    rsu.yaml                 # id_field_map
    schwab.yaml               # known_etf, symbol_norm, action_map
    cn_fund.yaml               # type_map
  memos.yaml                  # memo_registry + memo_asset_map
  data_fixes.yaml
  unforced_errors.yaml
  valuation_reference.yaml
```

Every file is optional — a missing file behaves exactly like a present file
with an empty top-level list/mapping. `seeds/empty/` is every file present
but empty, for clarity; a profile could equally be an empty directory.

## `reader_mappings/*.yaml` — schema per mapping_kind

These map 1:1 onto `src.services.reader_mappings._DEFAULTS`'s 7
`(reader_key, mapping_kind)` entries today. The loader (`load_seed_pack`)
decodes each into the exact same Python shape `_DEFAULTS` currently holds,
so WS-3b can substitute one for the other without touching any hook or
transformer.

**`fs_column`** (`financial_summary.yaml`) — the 資産負債 balance-sheet
column → asset melt. `mapped` entries produce a holdings row when the
column exists in the sheet; `ignored` entries are FS's own informational
copy of a value another reader already owns (melting them would double
count) — kept as a separate list rather than a `status` field on `mapped`
because an ignored column carries no `asset_id`/`asset_name`/`currency` at
all (mirrors `FS_IGNORED_COLUMNS_SEED`, which seeds an *empty* map_value).

```yaml
fs_column:
  mapped:
    - excel_col: "RMB现金现金"
      asset_id: "CASH_Cash_CNY"
      asset_name: "现金 (CNY)"
      currency: "CNY"
  ignored:
    - excel_col: "投资资产_黄金_纸黄金(元)"
      reason: "Gold_Excel reader is authoritative"
```

**`ie_column`** (`financial_summary.yaml`) — 月度收支 column semantics
(`src.database.mapping_seeds.IEColumn`: role/bucket/currency/group/validates).
`bucket`, `group`, and `validates` are optional (omit or `null`).

```yaml
ie_column:
  - excel_col: "收入_主动收入_工资"
    role: "income"
    bucket: null
    currency: "CNY"
    group: "active_income"
  - excel_col: "总收入合计"
    role: "computed"
    bucket: null
    currency: "CNY"
    validates:
      groups: ["active_income", "passive_income"]
```

**`id_field_map`** (`gold.yaml` / `insurance.yaml` / `rsu.yaml`) — raw
label → canonical code, per reader. The loader reconstructs the
`"field:label"` flat map_key `load_reader_mappings` expects.

```yaml
id_field_map:
  - field: "account"
    label: "招行"
    code: "CMB"
```

**`known_etf`** (`schwab.yaml`) — a bare ticker list (public tickers, not
owner-specific — reused verbatim across every profile so far).

```yaml
known_etf: ["QQQ", "SPY", "VOO"]
```

**`symbol_norm`** (`schwab.yaml`) — compound-ticker normalization.

```yaml
symbol_norm:
  - from: "BRK/B"
    to: "BRK-B"
```

**`action_map`** (`schwab.yaml`) / **`type_map`** (`cn_fund.yaml`) — raw
broker/platform label → Huinsight transaction_type. Same shape, different file
per reader.

```yaml
action_map:
  - raw: "Buy"
    type: "buy"
```

## `memos.yaml`, `data_fixes.yaml`, `unforced_errors.yaml`, `valuation_reference.yaml`

These have no `_DEFAULTS`-style runtime merge today — they're seeded once
into their tables directly (`schema.sql` INSERTs, connector.py's V15 block)
with no code-side fallback. The loader returns them as plain lists of dicts
matching each table's insert columns; WS-3b decides how (or whether) to
turn them into idempotent per-row migrations against an active profile.
Field names match the INSERT statements in `schema.sql`/`connector.py`
directly (e.g. `data_fixes.due_days` becomes the `due_at` interval offset
from `opened_at` at seed time, mirroring `CURRENT_TIMESTAMP + INTERVAL n DAY`
in the current SQL).

## The loader

`src.database.seed_loader.load_seed_pack(profile=None)`:
- `profile` argument > `$UIS_SEED_PROFILE` env var > `"example"` default.
- Raises `SeedProfileNotFoundError` (a `FileNotFoundError` subclass) if
  `seeds/<profile>/` doesn't exist.
- Returns a `SeedPack` dataclass: `reader_mappings` (keyed exactly like
  `_DEFAULTS`), `fs_ignored_columns`, `memo_registry`, `memo_asset_map`,
  `data_fixes`, `unforced_errors`, `valuation_reference`.

Not imported by `src/database/connector.py`, `src/database/mapping_seeds.py`,
or `src/services/reader_mappings.py` yet — see the top of `seed_loader.py`.

## Profiles

- **`example`** — derived from `tools/demo_data/persona.yaml` (Program
  OSR's synthetic household). `reader_mappings/financial_summary.yaml`'s
  keys match the persona Financial Summary's column names exactly — that
  lock-step is the whole point: swap in `example` and the demo data's FS
  workbook melts fully, with zero owner-specific literals anywhere in the
  pack.
- **`empty`** — every file present, every collection empty. Loads cleanly;
  a sync against this profile produces zero reader_mappings, zero memos,
  zero PE bands — a genuinely blank slate for a from-scratch deployment.
- **`private-ray`** (future, WS-4) — the project owner's real mapping
  vocabulary, gitignored, never committed to the public-bound tree. Not
  built in this phase.
