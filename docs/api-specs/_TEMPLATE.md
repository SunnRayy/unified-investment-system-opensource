# API Spec: <Feature Name>

> Feature: <One-line description of what this feature does>
> Status: Draft | Ready | Implemented
> Last Updated: YYYY-MM-DD

---

## Section A: API Contract

### Endpoint

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/<path>` | <What this endpoint returns> |

### Request Body (if POST/PUT)

```typescript
interface RequestBody {
  // Remove this section if GET
}
```

### Response Type

```typescript
interface <FeatureName>Response {
  field_name: string;       // Description
  numeric_field: number;    // Description, units (e.g., CNY)
  array_field: Item[];      // Description
}

interface Item {
  // If response contains arrays of objects
}
```

### Example Response

```json
{
  "field_name": "realistic example value",
  "numeric_field": 12345.67,
  "array_field": [
    {"nested": "example"}
  ]
}
```

---

## Section B: Data Binding Map

| UI Element | Location in Mockup | API Field | Format |
|------------|-------------------|-----------|--------|
| <Card title value> | <Header area> | `field_name` | text |
| <Main number> | <Center of card> | `numeric_field` | currency:CNY |
| <List item name> | <Table row> | `array_field[].nested` | text |

---

## Section C: Demo Data Markers

### Placeholder Values (MUST be replaced)

| Demo Value in Mockup | Replace With | Format |
|---------------------|--------------|--------|
| `示例文本` | `field_name` | text |
| `¥123,456` | `numeric_field` | currency:CNY |
| `12.34%` | `percent_field` | percent:2 |

### Static Values (DO NOT replace)

| Value | Reason |
|-------|--------|
| "Feature Title" | UI label, not data |
| Column headers | UI labels |
| Button text | UI labels |

---

## Section D: Component Reference

### Main Component Structure

```
┌─────────────────────────────────────┐
│ <Title Label>                       │  ← STATIC
│                                     │
│ <Main Value>                        │  ← BIND: field_name
│ <Secondary Info>                    │  ← BIND: other_field
│                                     │
└─────────────────────────────────────┘
```

### Styling Notes

- Main value: Large font, bold
- Positive numbers: Green (#22c55e)
- Negative numbers: Red (#ef4444)
- Badges: Rounded, colored by type

---

## Section E: Data Quality Requirements

### Language

- [ ] Asset names: `Chinese` / `English` (pick one)
- [ ] Asset classes: `Chinese` / `English` (pick one)
- [ ] UI labels: `Chinese` / `English` (pick one)

### Currency

- [ ] Display currency: `CNY` / `USD` / `Original`
- [ ] Symbol: `¥` / `$` / `HK$`
- [ ] Decimal places: `0` / `2`

### Number Formatting

| Field | Type | Precision | Sign Display |
|-------|------|-----------|--------------|
| `numeric_field` | currency | 2 | negative_only |
| `percent_field` | percent | 2 | always |
| `count_field` | integer | 0 | never |

---

## Section F: Data Model Reference

> **CRITICAL**: This section prevents data correctness issues. The Architect must understand
> the database schema before writing the spec. Backend agents must verify this before implementation.

### Required Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `table_name` | What data it holds | `id`, `relevant_field` |

### Required JOINs

```sql
-- Describe the JOIN path from source to output
-- Example: holdings → asset_registry → asset_taxonomy (for class hierarchy)
FROM primary_table p
JOIN related_table r ON p.key = r.key
JOIN another_table a ON r.other_key = a.key
```

### Data Derivation Logic

| Output Field | Source | Calculation |
|--------------|--------|-------------|
| `response_field` | `table.column` | Direct value / SUM / weighted average / etc. |

### Known Data Model Gotchas

- **Gotcha 1**: Describe any non-obvious relationships
- **Gotcha 2**: Describe any tables that look similar but serve different purposes

### Pre-Implementation Verification Query

```sql
-- Backend agent: Run this BEFORE implementing to verify you understand the data model
-- Expected: Should return representative sample matching your understanding
SELECT
  key_field,
  derived_field
FROM relevant_tables
WHERE <filter for small sample>
LIMIT 5;
```

---

## Validation Checklist

### Backend Validation

- [ ] Endpoint returns 200
- [ ] Response matches TypeScript interface
- [ ] All fields present
- [ ] Field types correct (numbers are numbers)
- [ ] Language correct per Section E

**Data Model Verification (Section F):**
- [ ] Ran pre-implementation verification query
- [ ] Query results match expected data model
- [ ] All required JOINs implemented in actual query
- [ ] No hardcoded mappings used (use table JOINs instead)

**Actual Response:**
```json
// Backend agent: paste curl output here
```

**Data Sanity Check:**
Pick 2-3 specific records and trace them through:

| Record ID | Source Table Value | API Output Value | Match? |
|-----------|-------------------|------------------|--------|
| `<id>` | `<raw value from DB>` | `<value in response>` | Yes/No |

If any mismatch, investigate before proceeding.

---

### Annotation Validation

- [ ] All Section C placeholder values marked with BIND
- [ ] Static values NOT marked
- [ ] Component structure matches Section D
- [ ] Loop structures identified for arrays

**Binding Count:**
- Expected from Section B: ___
- Actual BIND comments: ___

---

### Frontend Implementation Validation

- [ ] API called correctly (verify in Network tab)
- [ ] Real data displayed (not demo values)
- [ ] Formatting matches Section B
- [ ] Empty state handled
- [ ] Error state handled

**Data Quality Check:**
- [ ] Language consistent
- [ ] Currency symbols correct
- [ ] Number formats correct (%, decimals)
- [ ] Signed numbers show +/-

**Screenshot Evidence:**

| Element | Spec Example | Actual Displayed |
|---------|--------------|------------------|
| field_name | example | _____________ |
| numeric_field | ¥12,345.67 | _____________ |

---

## Sign-off

- [ ] Backend validated by: _______________
- [ ] Annotation validated by: _______________
- [ ] Frontend validated by: _______________
- [ ] Architect sign-off: _______________

**Date:** _______________
**Ready for merge:** Yes / No
