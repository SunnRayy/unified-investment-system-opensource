# Adding a data source

Huinsight ships with 7 built-in readers (Schwab, IBKR, CN Fund, Gold, Insurance, RSU,
Financial Summary). If your broker or asset type isn't one of those, you don't
need to fork the reader engine or send a PR to get your own data in — you can
add a source entirely from **outside** `src/sources/`, with two files.

This page is both a walkthrough and a pointer at a complete, working example:
[`examples/adding-a-source/`](../examples/adding-a-source/). Everything below
is exercised end-to-end by
[`tests/sources/test_plugin_hook_end_to_end.py`](../tests/sources/test_plugin_hook_end_to_end.py) —
it's not aspirational documentation, it's a test that runs the real sync
orchestrator against exactly this example and asserts the rows land correctly.

## The two files

### 1. A reader YAML — what to read and how

```yaml
# examples/adding-a-source/reader.yaml
identity:
  source_key: demo_broker
  source_system: Demo_Broker_CSV
  display_label: "Demo Broker (worked example)"
  display_name: "Demo Broker"
  account_name: "Demo Broker"
  asset_prefixes:
    - DEMO_
  allowed_extensions:
    - .csv
  category: reader
  validator: null

parsing:
  format: csv
  snapshot_date:
    strategy: file_mtime
  sheets:
    - name: holdings
      target: holdings
      file_glob: "*.csv"
      select: latest
      skiprows: 0
  holdings_from_sheet_hook: demo_broker_holdings_from_csv
```

`identity` is what the rest of the app needs to know about the source — its
display name, which asset-ID prefixes belong to it, what file types it
accepts. `parsing` is the declarative pipeline: what format the file is in,
how to find the newest one, and — the only line that needs code — which
**hook** turns the raw rows into Huinsight holdings.

The full schema (`excel` vs `csv` vs `flex_csv` formats, `rename`/`value_maps`
for simple column-mapping sources that need no hook at all, `id_template` for
composite asset IDs) is documented in
[`src/sources/reader_config.py`](../src/sources/reader_config.py)'s docstrings
— every built-in reader's YAML under `config/readers/` is a worked example of
some corner of it.

### 2. A hook — the one function that needs code

```python
# examples/adding-a-source/plugin_hook.py
from src.sources.hooks import register_hook
import pandas as pd

def demo_broker_holdings_from_csv(sheet_df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    """Turn Demo Broker's raw CSV rows (Symbol, Quantity, Price) into Huinsight
    holdings rows."""
    if sheet_df is None or sheet_df.empty:
        return pd.DataFrame()

    snapshot_date = metadata.get("snapshot_date")
    rows = []
    for _, row in sheet_df.iterrows():
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol:
            continue
        quantity = float(row.get("Quantity", 0) or 0)
        price = float(row.get("Price", 0) or 0)
        rows.append({
            "asset_id": f"DEMO_{symbol}",
            "asset_name": symbol,
            "quantity": quantity,
            "market_price_unit": price,
            "market_value": quantity * price,
            "currency": "CNY",
            "snapshot_date": snapshot_date,
            "source_system": "Demo_Broker_CSV",
        })
    return pd.DataFrame(rows)

register_hook("demo_broker_holdings_from_csv", demo_broker_holdings_from_csv)
```

The hook signature is always `(sheet_df, metadata) -> pd.DataFrame` — you get
whatever the sheet config selected (here, the latest matching CSV, read raw)
and a metadata dict (snapshot date, plus anything the engine resolved for
you), and you return a DataFrame shaped like a holdings row. For a fuller
real-world example — FX conversion, a separate cash row, symbol
normalization — read [`src/sources/hooks/schwab.py`](../src/sources/hooks/schwab.py).
There's also `holdings_hook` (derives holdings from *transactions* instead of
a raw sheet — see RSU) and `transactions_from_sheet_hook` (mirrors
`holdings_from_sheet_hook` for the transactions side).

`register_hook(name, fn)` is the entire registration API. It's additive —
it never touches the 11 built-in hooks — and it's safe to shadow a built-in
name if you deliberately want to (you'll get a warning in the logs, not an
error).

## Wiring it into a real install

The example above is loaded explicitly by its own test, pointed at
`examples/adding-a-source/` directly. To use it for real:

1. Copy `examples/adding-a-source/reader.yaml` to `config/readers/demo_broker.yaml`.
2. Copy `examples/adding-a-source/plugin_hook.py` to `plugins/hooks/demo_broker.py`
   (that directory is gitignored — it's *your* extension, not part of the
   shared repo; see [`plugins/README.md`](../plugins/README.md)).
3. Add an entry to `config/settings.yaml`'s `source_registry`:
   ```yaml
   source_registry:
     demo_broker:
       enabled: true
       data_dir: /path/to/your/demo-broker-exports/
   ```
4. Drop your real export (same columns as `holdings.csv`) in that directory
   and run `python main.py --sync-v3`.

No other wiring is required. The orchestrator's reader dispatch
(`src/sync/orchestrator.py`'s `_dispatch_phase2_readers`, ADR-018) walks every
reader the registry knows about; a key with no specialized function
(Schwab, IBKR, and the other 5 built-ins each have one, for behavior that
predates the config-driven engine) runs through the same generic path your
new source uses. Your hook is discovered automatically — `get_hook()` scans
`plugins/hooks/*.py` the first time it sees a name it doesn't already know,
so dropping the file in is enough.

## What you get for free

Once wired in, `demo_broker` participates in everything every other reader
does: the integrity gate, the shadow/staleness pipeline, cost-basis
tracking, the freshness panel, the Settings page's data-source management UI.
It is not a second-class source — the built-in 7 and a plugin source go
through the identical downstream pipeline from P2 onward.

## What's still code-only

A few things are deliberately harder to reach without a code change, so
they're worth knowing about before you plan a source around them:

- **Non-generic normalization** — the `_normalize_holdings_df` /
  `_normalize_transactions_df` step in `src/sync/phases/_ingest.py` has a
  handful of `if source_system == "Schwab_CSV"` branches for legacy
  column-renaming quirks specific to those readers. A new source doesn't need
  any of these (emit the contract column names directly, the way this
  example and the IBKR reader do), but if your source's raw export needs
  reader-specific massaging beyond what a hook can do inline, that's the
  file to look at. See `docs/design-retrospective.md` for why this shape is
  on the list of things worth rebuilding.
- **FX defaults** — `src/data_manager/currency_converter.get_default_usd_cny_rate()`
  is the one settings-driven USD→CNY fallback; a source denominated in a
  currency other than USD/CNY needs its own conversion logic in the hook (see
  the `wizard_holdings_from_sheet` hook for an example that takes an explicit
  `fx_rate` from metadata).
