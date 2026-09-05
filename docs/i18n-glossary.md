# Huinsight Bilingual Glossary — English ↔ 简体中文

The canonical term list for the Huinsight interface. Roughly 2,500 user-visible strings are being
translated by several people and several agent sessions working in parallel; without one
authority, "cost basis" becomes 成本价 on one page, 持仓成本 on another and 成本基础 on a
third, and the product stops sounding like one product.

**If a term is in this file, use exactly what is in this file.** If it is not, add it here in
the same commit that first uses it.

Program BIL · see `docs/decisions/ADR-028-bilingual-ui-i18n-foundation.md` for the decisions
behind this vocabulary.

---

## The governing principle

> **UI vocabulary must match the vocabulary in the owner's own workbook and data — not a
> dictionary translation.**

Huinsight was built for a Chinese household whose Financial Summary workbook is already in Chinese,
whose funds are Chinese and whose banks are Chinese. The data model keys on that workbook's
column headers. So when a term already exists somewhere in the product — a tier name, a
taxonomy class, a spreadsheet column, a prompt — **that spelling wins over a better-sounding
translation.**

Two owner rulings show what this means in practice:

- **Tier → 梯队**, not the more natural-sounding 层级, because `asset_tiers.name` already reads
  `第一梯队 (底仓/价值型)`. A UI saying 层级 while the data says 梯队 is two vocabularies for
  one concept.
- **Illiquid / non-rebalanceable → 固定资产**, because that is the owner's word for property
  and insurance — and it is literally in his data (`固定资产_房产_阳光花园`). "非流动资产" is more
  precise and would still be wrong.

When in doubt, grep the product before reaching for a dictionary:
`src/database/schema.sql`, `src/services/ai_advisor/prompts.py`, `src/database/mapping_seeds.py`,
`ux-command-center/pages/Valuation.tsx`, and the `asset_tiers` / `risk_profiles` /
`taxonomy_classes` tables.

---

## ⚠ Never translate: source-language passthrough

A source system's own text is that system's record, not this app's copy. Reader-supplied
description, memo and reason fields are rendered **verbatim in whatever language the source
wrote them**, on every locale.

The case that keeps getting reported as an i18n bug: the Decision Hub timeline shows
`Reason: 季度分红发放` on a CN-fund dividend entry while the surrounding UI is English, and the
IBKR trades above it read `IBKR trade IEFA`. Both are correct — each is the language its source
used. The string is a `交易原因` column value from the CN-fund transaction sheet (see
`_CN_FUND_MEMO_POOL` in `tools/demo_data/generate.py` for the demo equivalents), and routing it
through i18n would translate someone else's bank statement.

The test for whether a Chinese string on an English screen is a defect: **did this application
generate it?** App-generated labels, statuses and headings must be localised. Values that arrived
from a reader must not be.

## ⚠ Never translate: data-matching keys

`src/database/mapping_seeds.py`, `config/reference_sheet.yaml` and `seeds/*/reader_mappings/*`
contain Chinese strings such as:

```
投资资产_股票基金_美股基金_IBKR
收入_主动收入_RSU
固定资产_房产_阳光花园
非必要开支_旅行出游
```

**These are not display strings. They are match keys against the real column headers in the
user's Excel workbook.** Editing one — even to "fix" a typo like `Schawab`, which is
deliberately preserved in `投资理财_股票基金_Schawab` — silently breaks ingestion for that
column. The reader stops matching, the value drops out of the pipeline, and nothing errors.

The same applies in reverse: these strings must never be *added to* a translation catalog.
They are data. If one of them needs to appear in the UI, map it to a catalog key at the
display layer and leave the seed untouched.

Related: reader mappings are DB-backed and UI-managed (ADR-023). Never add a mapping in code.

---

## Conventions

### 你, not 您

**Use 你 throughout.** Justification is precedent, not preference: the AI advisor's persona
prompts, which the owner has been reading for months, already address him as 你
(`你将扮演用户的私人投资策略官`, `你的核心身份是一位资深的价值投资策略师`). Huinsight is a
self-hosted single-user dashboard — 您 would make the tool address its own owner like a bank
form. Switching register mid-product is worse than either choice.

Best of all, address no one. `保存` beats `你可以保存`. Most UI strings are labels, not
sentences, and Chinese UI convention drops the pronoun entirely.

### English that stays English

Do **not** translate:

| Category | Examples |
|---|---|
| Product & module names | `Huinsight`, `WealthOS`, `Compass`, `North Star`, `Decision Hub` |
| Brokers, banks, vendors | `Schwab`, `IBKR`, `GuruFocus`, `FactSet`, `DuckDB` |
| Tickers & currency codes | `GOOGL`, `SGOV`, `3033.HK`, `CNY`, `USD` |
| Established finance acronyms | `TWR`, `XIRR`, `FIFO`, `RSU`, `ETF`, `PE`, `PE-TTM`, `QDII` |
| Reader / source keys | `schwab`, `ibkr_flex`, `cn_fund`, `financial_summary` |

Gloss an acronym on **first use per page**, then use it bare: `XIRR（资金加权收益率）` →
thereafter `XIRR`. Do not gloss it in a table header — there is no room, and the column is
already labelled by its neighbours.

`P&L` is the exception: it is translated (盈亏), because 盈亏 is the ordinary Chinese word and
"P&L" is not ordinary English.

### Punctuation

- Chinese prose takes **full-width** punctuation: `，。、；：？！（）「」` — and **no space**
  around it. `信号基于研究参考阈值（GuruFocus / FactSet / 东吴证券）。`
- Latin runs, code, tickers, file paths and numbers keep **half-width** punctuation:
  `3033.HK`, `data/unified.duckdb`, `(17-21×)`.
- A separator between two Latin items stays half-width even inside Chinese prose:
  `GuruFocus / FactSet`.
- Use `·` (U+00B7) as the inline separator Huinsight already uses everywhere:
  `无持仓数据 · 点击「刷新数据」加载`.
- Quote UI affordances with `「」`, matching existing copy (`点击「刷新数据」`).
- **Exception, and it is data not prose**: `asset_tiers.name` uses half-width parens with a
  leading space — `第一梯队 (底仓/价值型)`. Render it exactly as stored. Do not "fix" it.

No space between Chinese and adjacent Latin/digits — Chinese typography does not use one, and
inserting it makes catalog values diff-noisy against the data they mirror.

### Numbers, currency and units

- Always Arabic numerals, half-width. Never 一百二十.
- Currency symbol prefixes the number with no space: `¥1,234`, `$1,234`.
- **Compact magnitudes are locale-derived, not translated.** `zh-CN` gets 万/亿, `en` gets
  K/M/B, from the same `Intl.NumberFormat(locale, {notation:'compact'})` call in
  `ux-command-center/src/utils/formatMoney.ts`. Never hand-write `万` into a catalog value,
  and never hand-roll a divide-by-1e4.
- Percentages: half-width `%`, no space — `+5.5%`, `−2.3%`.
- Dates go through `formatDate()` in the same module. Never pin a locale at a call site.
- Counts and units follow the number: `3 项失败`, `12 个资产`, `30 天`.

### Length

Chinese runs roughly 30% shorter than English. The failure mode is therefore **English
overflowing a container sized while reviewing Chinese** — check both directions when you
review a page, not just the Chinese one.

---

## Core financial terms

All five open terminology questions were resolved by the owner on 2026-08-22 and are now binding.
Any NEW term you are unsure of: add the row, mark it **⚠ needs owner ruling**, use it consistently, and
report it. Terms so marked are proposals — they are open and
the owner's answer overrides.

| English | 简体中文 | Notes |
|---|---|---|
| Holdings | 持仓 | The set of positions. Evidenced in `Valuation.tsx` (`无持仓数据`) and in `prompts.py` (`持仓分析与风险预警`). |
| Position | 持仓 / 仓位 | 持仓 for the thing held; **仓位** when the meaning is size or weight (`prompts.py`: `仓位和执行质量`). Do not use 仓位 for a row in the holdings table. |
| Cost basis | 持仓成本 | Short form 成本 in narrow columns. Huinsight computes **FIFO remaining** cost, not total-cost-of-all-buys — see the PIS `Cost_Price_Unit` trap in `AGENTS.md`. |
| Market value | 市值 | All stored values are CNY; the ¥/$ shown is a display conversion. |
| Unrealized P&L | 浮动盈亏 | **Owner ruling 2026-08-22** — the broker idiom, not 未实现盈亏. |
| Realized P&L | 已实现盈亏 | Partner of 浮动盈亏 above. Derived from the owner's ruling on the pair; conventional counterpart in the same idiom. |
| Net worth | 净资产 | Nets liabilities (the Balance Sheet report is the authority). Not 总资产, which is assets before liabilities. |
| Total assets | 总资产 | |
| Liabilities | 负债 | |
| Allocation | 资产配置 | Short form 配置. Owner ruling: **Compass Report → 资产配置报告**, and the **English is renamed to "Allocation Report"** to match (2026-08-22). |
| Target allocation | 目标配置 | |
| Drift | 偏离 | `prompts.py`: `明确识别资产偏离`. |
| Rebalance | 再平衡 | `prompts.py`: `再平衡纪律`, `再平衡需求`. |
| Non-rebalanceable / illiquid | 固定资产 | **Owner ruling.** His term for property + insurance; it is in his data (`固定资产_房产_阳光花园`). Not 非流动资产. |
| Drawdown | 回撤 | Max drawdown → 最大回撤. |
| Volatility | 波动率 | |
| Attribution | 业绩归因 | **Owner ruling 2026-08-22.** Monthly Attribution → 月度归因. |
| Contribution (attribution) | 贡献度 | A share of the total return. **Never 投入.** |
| Contribution (cash in) | 资金投入 | Money moved into the portfolio (Investment Contributions, ADR-025). **Never 贡献** — English overloads this word and Chinese must not. |
| Redemption | 赎回 | Fund/理财 money coming back out. The test for whether a ledger entry is a redemption: was this money ever in a 投资理财 column. |
| Liquidation | 清仓 | A position taken fully to zero. |
| Deposit | 存款 | 活期存款 / 定期存款 per the taxonomy. |
| Buy / Sell / Hold | 买入 / 卖出 / 持有 | Exactly as in `prompts.py`. |
| Transaction | 交易 | A trade row. Cash movements are 资金流水. |
| TWR | 时间加权收益率（TWR） | Gloss once per page, then `TWR`. |
| XIRR | XIRR | Gloss once as `XIRR（资金加权收益率）`. Keep the acronym — Chinese finance writing does. |
| Return | 收益率 | A rate. A money amount is 收益, not 收益率. |
| Cost | 成本 | |
| Benchmark | 基准 | |
| Concentration | 集中度 | `prompts.py`: `集中度控制`. |
| Margin of safety | 安全边际 | From the persona prompt — do not re-translate. |
| Circle of competence | 能力圈 | Same. |
| Watchlist | 观察列表 | Evidenced in `Valuation.tsx`. |
| Valuation | 估值 | Current valuation → 当前估值. |
| Valuation percentile | 历史百分位 | **历史%位** in narrow table headers — that is the existing column label in `Valuation.tsx`. A specific bucket reads `第N分位`. |
| Signal | 信号 | |
| Fair-value band | 合理区 | `Valuation.tsx`: `合理区 (17-21×)`. |
| Accumulating (insufficient history) | 积累中 | Existing copy: `积累中 · 3d`. |
| Tracked indexes | 跟踪指数 | |

---

## System & data-model terms

These describe Huinsight's own machinery. Several are internal jargon that leaks into Operations and
audit pages; they need Chinese that is *explanatory*, because a literal rendering means nothing
to a reader who has not read the architecture docs.

| English | 简体中文 | Notes |
|---|---|---|
| Sync | 同步 | Full sync → 完整同步. Market-only → 行情同步. |
| Snapshot | 快照 | 持仓快照 where the object needs naming. |
| Snapshot date | 快照日期 | |
| Reader | 数据源 | **Owner ruling 2026-08-22** — reader and source collapse to one Chinese term, 数据源. The English inconsistency is not reproduced in Chinese. |
| Source | 数据源 | The file or feed itself (Schwab CSV, IBKR Flex, …). |
| Authority (reader-first authority model) | 权威来源 | The source of truth for a value. Co-authority (Schwab + IBKR) → 双权威来源. |
| Shadow row | 影子记录 | **Owner ruling 2026-08-22.** `is_shadow=TRUE` means **superseded** — an older snapshot kept for point-in-time history — **not invalid**. Never write Chinese around this term that implies bad or discardable data. |
| Stale | 陈旧 | A reader that has stopped reporting. |
| Integrity check | 数据完整性检查 | 16 checks; 5 are blocking. Blocking → 阻断性, advisory → 提示性. |
| Data source health | 数据源健康度 | |
| Freshness | 数据新鲜度 | |
| Reconciliation | 对账 | |
| Classification | 分类 | Taxonomy → 分类体系. |
| Taxonomy class | 资产类别 | |
| Tier | 梯队 | **Owner ruling.** Not 层级. Mirrors `asset_tiers.name`. |
| Risk Profiles | 风险偏好配置 | **Owner ruling.** |
| Income & Expense | 收支 | **Owner ruling**, overruling 月度收支. |
| Switch language / currency | 切换语言 / 切换币种 | **Owner ruling** — generic, not "switch to Chinese". |
| Refresh data | 刷新数据 | Existing copy in `Valuation.tsx`. |
| Import | 导入 | |
| Audit | 审计 | |
| Verification | 核验 | |

### ⚠ reader vs source — one open inconsistency

The English UI uses "reader" and "source" more or less interchangeably (Settings says
*sources*; the architecture says *7 readers*). English tolerates the slippage; Chinese will
expose it, because 数据读取器 and 数据源 are visibly different words. Two options for the
owner:

1. Collapse to 数据源 everywhere in the UI, and keep 读取器 for docs and logs only.
2. Keep both, and fix the English to match (rename Settings' "sources" to "readers").

Option 1 is proposed as the default: the user configures files, not components.

---

## Values that come from the database

These render from data, not from the catalog. **Display them verbatim** — do not add a
translation, and do not "improve" the spacing.

**Tiers** (`asset_tiers.name`): `第一梯队 (底仓/价值型)` · `第二梯队 (辅助/分散)` ·
`第三梯队 (交易/择时)`

**Risk profiles** (`risk_profiles.name`): `保守型` · `均衡型` · `成长型` · `进取型`

**Taxonomy classes** (`taxonomy_classes.name` → `name_cn`):

| EN | 中文 | | EN | 中文 |
|---|---|---|---|---|
| Equity | 股票 | | Gold | 黄金 |
| US Equity | 美股 | | Commodity | 商品 |
| CN Equity | A股 | | Energy | 能源 |
| HK ETF | 港股 | | Crypto | 加密货币 |
| Fixed Income | 固定收益 | | Alternative | 另类投资 |
| CN Bonds | 国债 | | Insurance | 保险 |
| US Bonds | 美债 | | Property | 住宅地产 |
| Cash | 现金 | | Real Estate | 房地产 |
| Cash Checking | 活期存款 | | SMB | 创业投资 |
| Cash Deposit | 定期存款 | | Money Market | 货币市场 |
| Bank Wealth | 银行理财 | | | |

Note the known asymmetry, recorded and deliberately not fixed here:
`taxonomy_classes.name_cn` is English-primary with a Chinese translation column, while
`asset_tiers.name_en` and `risk_profiles.name_en` are Chinese-primary with an (empty) English
column. Reconciling them is a later ADR, not part of Program BIL.

---

## Asset-type labels

From `Valuation.tsx`, already in the product:

| Code | 中文 |
|---|---|
| `US_STOCK` | 美股个股 |
| `US_INDEX` | 美股指数 ETF |
| `HK_INDEX` | 港股指数 ETF |
| `CN_INDEX` | A股宽基指数 |
| `CN_MARKET` | A股市场板块 |

---

## AI advisor

The advisor's Chinese is **already written** — `src/services/ai_advisor/prompts.py` has been in
production. Reuse its wording rather than inventing a second vocabulary for the same ideas.

Section labels are resolved from the frontend catalog against **stable ASCII IDs**, never taken
from the model's output. WS-5 owns `aiAdvisor.json`; these are the canonical display strings.

**Brief sections** — `宏观形势` · `持仓分析与风险预警` · `风险预警汇总` · `操作建议` ·
`明日关注`

**Review sections** — `交易汇总` · `建议准确性` · `组合表现` · `经验沉淀` · `准则更新建议`

**Accuracy tiers** — `high` → 高准确度 · `medium` → 中准确度 · `low` → 低准确度. These are an
enum now; the Chinese is a display label, never a matched value.

**Investment-philosophy vocabulary**, verbatim from the persona prompt: 安全边际 · 能力圈 ·
逆向思维 · 再平衡纪律 · 集中度控制 · 价值投资 · 防御缺口 · 决策质量 · 行为偏差.

---

## Adding a term

1. Grep the product first — `schema.sql`, `prompts.py`, `Valuation.tsx`, the seeds, the DB
   tables. An existing spelling beats a new one, even a worse existing spelling.
2. Add the row here, in the right table, in the same commit as the string that needs it.
3. If two English words map to one Chinese word (or the reverse), say so in Notes — that is
   exactly where drift starts. See the two `Contribution` rows.
4. Unsure? Add the row, mark it **⚠ needs owner ruling**, and use it consistently in the
   meantime. A consistent wrong term is one `sed` away from right; an inconsistent one is not.

---

## Open rulings

| # | Term | Question |
|---|---|---|
| 5 | Compass Report | **RESOLVED 2026-08-22** — English becomes "Allocation Report", Chinese 资产配置报告. |
