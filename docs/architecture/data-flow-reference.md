# Data Flow Reference: Decision Loop and Sync Authority

## 1. Two Data Worlds (Sync Pipeline vs Decision Ledger)

Huinsight runs two connected, but distinct, data worlds:

- Sync Pipeline (reader-first authority): file readers populate authoritative `holdings`, `transactions`, and market context tables. Portfolio views, performance, balance sheet, and WealthOS consume this world.
- Decision Ledger: `trade_logs`, memo/insight linkage, and decision analytics tables track why a trade happened and how it performed as a decision.

These worlds solve different questions:

- Sync world: "What is the portfolio position and valuation?"
- Decision world: "What decision was made, why, and what happened after?"

## 2. The Bridge: `linked_transaction_id`, Bidirectional Linking, and `verification_status`

The bridge between the worlds is explicit:

- `trade_logs.linked_transaction_id` links a decision trade record to an authoritative `transactions.id`.
- `link_trade_logs_to_transactions()` matches pending decision logs to imported transactions.
- `backfill_trade_logs_from_transactions()` creates missing decision logs from imported buy/sell transactions.

`verification_status` lifecycle:

- `pending`: decision trade was recorded, but no authoritative transaction match yet.
- `verified`: authoritative transaction was matched/linked.
- `unmatched`: still unmatched after 15 days and at least one successful full sync opportunity after creation.

## 3. Portfolio Positions Are Reader-First

Manual trade logging does not write `holdings`. Portfolio positions are updated only by reader sync pipelines and authority resolution. This avoids mixing intent logs with valuation authority.

## 4. Decision Analytics Count Attributed Trades Only

Decision Hub surfaces split by purpose:

- Timeline and scorecard: show all meaningful decision ledger trades (manual, imported, linked, or not yet attributed).
- Funnel, leaderboard, and intelligence: count only trades with confirmed memo/insight attribution.

Entry method (manual vs imported) is metadata. Attribution to memo/insight is the deciding fact for AI analytics.

## 5. Closed Decision Loop

```text
memo / insight
    -> trade execution
    -> transaction import or manual record
    -> trade_log
    -> verification against authoritative transactions
    -> attribution-aware Decision Hub + AI statistics
```
