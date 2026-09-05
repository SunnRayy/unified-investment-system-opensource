# plugins/

Drop-in extension point for adding a data source without editing any file
under `src/sources/` (Program OSR WS-2). This directory is scanned by the
hook registry at `src/sources/hooks/__init__.py` — see that module's
docstring for the full API (`register_hook`, `discover_plugin_hooks`).

## plugins/hooks/

Every `.py` file here (except ones starting with `_`) is imported once,
automatically, the first time the sync engine looks up a hook name it
doesn't already know. Your file's top level should call `register_hook`:

```python
# plugins/hooks/my_broker.py
from src.sources.hooks import register_hook
import pandas as pd

def my_broker_holdings_from_csv(sheet_df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    ...  # return a holdings DataFrame

register_hook("my_broker_holdings_from_csv", my_broker_holdings_from_csv)
```

Then declare it in your reader's YAML (`config/readers/my_broker.yaml`):

```yaml
parsing:
  ...
  holdings_from_sheet_hook: my_broker_holdings_from_csv
```

No other wiring is required — no import to add anywhere, no registry file
to edit. A complete worked example (CSV fixture + reader YAML + plugin hook
+ an end-to-end integration test that syncs it through the orchestrator's
auto-dispatch) lives at `examples/adding-a-source/`; see
`docs/adding-a-source.md` for the full walkthrough.

## Why this directory is (mostly) empty in the public repo

`plugins/hooks/*.py` is gitignored — this is *your* extension point, not
part of the shared codebase. Only this README and the placeholder
`hooks/README.md` are tracked. A broken file here is logged and skipped; it
never breaks the built-in readers.
