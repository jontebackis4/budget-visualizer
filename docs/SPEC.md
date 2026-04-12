# Swedish Budget Comparison Tool — v1 Specification

## Overview

A static web app that lets users compare Sweden's annual central government budget
proposal (budgetpropositionen) against opposition party motions (budgetmotioner)
across the 27 fixed expenditure areas (utgiftsområden). The primary view is a
**delta chart**: how much each party proposes to spend relative to the government.

**Budget years covered:** 3–5 most recent years (backfilled on initial setup).
**Parties:** Any opposition parties that file budget motions (configured per year in the source manifest).
**Language:** Swedish throughout.
**ESV outcomes:** not in v1.

---

## Architecture Summary

```
GitHub Actions (annual, Oct)
    |
    v
Ingestion script (Python)
  ├─ Load source manifest (PDF URLs + page ranges from YAML)
  ├─ Download PDFs, extract specified pages with pdfplumber
  ├─ Normalize tables via Claude API (structured JSON output)
  ├─ Write data/ JSON to a draft PR branch
  └─ Post extracted figures as PR comment for human review
         |
         v (human merges PR)
SvelteKit build step
  ├─ Reads SQLite (populated from approved JSON)
  └─ Exports static JSON files
         |
         v
Static site on Vercel / Cloudflare Pages
```

---

## 1. Data Pipeline

### 1.1 Sources

| Source | Format | How obtained |
|---|---|---|
| Government proposal | PDF (one summary document) | URL listed manually in `pipeline/sources/{riksmöte_year}.yaml` |
| Party motions | PDF (one per party, covers all utgiftsområden) | URLs listed manually in `pipeline/sources/{riksmöte_year}.yaml` |

All source URLs are curated manually each year into the source manifest. This is
intentional: the government summary document and each party's main budget motion
have unpredictable titles and locations that are not reliably auto-discoverable via
the API. Which parties are in opposition varies by year and is captured entirely in
the source manifest.

#### Source manifest format

Before running the pipeline for a new year, create
`pipeline/sources/{riksmöte_year}.yaml` (e.g. `pipeline/sources/2023_24.yaml`):

```yaml
government:
  url: https://data.riksdagen.se/...
  pages: "45-52"       # pages containing the summary spending table

parties:
  S:
    url: https://data.riksdagen.se/...
    pages: "142-155"
  V:
    url: https://data.riksdagen.se/...
    pages: "98-110"
  MP:
    url: https://data.riksdagen.se/...
    pages: "77-89"
  C:
    url: https://data.riksdagen.se/...
    pages: "101-114"
```

The `pages` field specifies the page range (1-indexed, inclusive) sent to Claude.
Open the PDF manually to locate the summary table and note the page numbers before
running the pipeline. The `pages` field may be omitted to send the full document,
though this risks exceeding the model's input token limit for large documents.

Party abbreviations are free-form strings (e.g. `SD`, `KD`, `L`) — add or remove
entries as the opposition composition changes. The pipeline aborts with a clear
error if the manifest for the requested year is missing.

### 1.2 Ingestion Script (`pipeline/ingest.py`)

**Steps per budget year:**

1. **Load source manifest** from `pipeline/sources/{riksmöte_year}.yaml`. Abort if
   file is missing or malformed.
2. **Download PDFs** — government summary + one per party — from the URLs in the
   manifest.
3. **Extract text** using `pdfplumber`, slicing to the `pages` range specified in the
   manifest for each source.
4. **Send to Claude API** for structured extraction (see §1.3).
5. **Validate** output (see §1.4).
6. **Write approved data** to `data/budget_{year}.json` and to SQLite.

**Failure policy:** If the Claude API call fails (rate limit, timeout, malformed JSON),
the script aborts immediately, writes a detailed error log to `pipeline/logs/`, and
exits with a non-zero code so GitHub Actions surfaces the failure as a workflow error.
No partial writes to SQLite.

### 1.3 Claude API Extraction

**Model:** `claude-opus-4-6` (most capable; ingestion runs once per year so cost is
acceptable).

**Input:** The text extracted from the pages specified in the source manifest for
that document (pre-sliced by pdfplumber). The operator identifies the correct pages
manually before running the pipeline.

**Prompt strategy:** Request structured JSON output with an explicit schema:

```
You are extracting budget data from a Swedish government or party document.

Return a JSON object with this exact schema:
{
  "source": "government" | string,  // party abbreviation as given in the source manifest
  "budget_year": integer,
  "rows": [
    { "area_id": integer (1–27), "area_name": string, "amount_ksek": integer }
  ],
  "total_ksek": integer,
  "extraction_notes": string  // any uncertainty or anomalies
}

Rules:
- area_id must be 1–27 (the official utgiftsområde number)
- amount_ksek is in thousands of SEK (KSEK), rounded to the nearest thousand
- If a row is absent or illegible, omit it from rows[] and note it in extraction_notes
- Do not invent figures — if unsure, omit and note it

Text to extract from:
<text>
{extracted_page_text}
</text>
```

**Validation after extraction:**
- Sum `rows[].amount_ksek` and compare against `total_ksek`.
- If the discrepancy exceeds 500,000 KSEK (500 MSEK), flag it in the PR comment but do not abort
  (rounding across 27 areas can accumulate).
- If `rows` contains fewer than 20 entries, flag as likely incomplete.
- If `area_id` values are not a subset of 1–27, abort.

### 1.4 Human Review Gate

After ingestion, the pipeline opens a **draft PR** on GitHub with:
- `data/budget_{year}.json` committed (the extracted figures)
- `data/review/budget_{year}.sqlite` (an updated SQLite with the new year)

**Validation:** Total govt 1 234 567 000 KSEK (source PDF: 1 234 890 000 KSEK, Δ 323 000 KSEK ✓)

⚠️ **Flagged rows:** Area 22 — {party} total absent (extraction_notes: "table cut off at page break")
```

**Reviewer:** Spot-check flagged rows against the source PDF. If figures look correct,
merge the PR. Merging triggers the SvelteKit build and deployment.

### 1.5 GitHub Actions Workflow

**Trigger:** Scheduled (~Oct 1 and Oct 10 each year) + manual `workflow_dispatch`.

**Jobs:**

```
ingest → opens draft PR with extracted data + posts review comment
             ↓ (human merges PR)
build  → triggered on push to main matching data/budget_*.json
       → runs SvelteKit build, exports static JSON, deploys to Vercel/Cloudflare
```

**Secrets required:** `ANTHROPIC_API_KEY`, Vercel/Cloudflare deploy token.

---

## 2. Database Schema

```sql
CREATE TABLE expenditure_areas (
    id    INTEGER PRIMARY KEY,  -- 1–27
    name  TEXT NOT NULL         -- Swedish name, e.g. "Hälsovård, sjukvård och social omsorg"
);

-- Government proposal amounts (one row per year × area)
CREATE TABLE budget_values (
    id           INTEGER PRIMARY KEY,
    budget_year  INTEGER NOT NULL,
    area_id      INTEGER REFERENCES expenditure_areas(id),
    amount_ksek  INTEGER,   -- NULL = no data extracted
    doc_url      TEXT,      -- source PDF URL
    UNIQUE (budget_year, area_id)
);

-- Opposition party deviations from the government proposal
CREATE TABLE counter_deviations (
    id           INTEGER PRIMARY KEY,
    budget_year  INTEGER NOT NULL,
    party        TEXT NOT NULL,   -- abbreviation from source manifest, e.g. "S", "V"
    area_id      INTEGER REFERENCES expenditure_areas(id),
    delta_ksek   INTEGER,         -- NULL = party filed no figure for this area
    doc_url      TEXT,
    UNIQUE (budget_year, party, area_id)
);

-- Convenience view: effective party amounts (govt + delta)
CREATE VIEW counter_effective_amounts AS
    SELECT cd.budget_year, cd.party, cd.area_id, ea.name AS area_name,
           bv.amount_ksek, cd.delta_ksek,
           bv.amount_ksek + cd.delta_ksek AS effective_amount_ksek
    FROM counter_deviations cd
    JOIN budget_values bv ON bv.budget_year = cd.budget_year AND bv.area_id = cd.area_id
    JOIN expenditure_areas ea ON ea.id = cd.area_id;
```

`delta_ksek = NULL` means the party filed no figure for that area (shown as "Ingen uppgift"
in the UI). This is distinct from filing zero (no change from government).

---

## 3. Static JSON Export

Run `python pipeline/export_json.py` (manually, or as part of the GitHub Actions build
job) to read the SQLite and write static JSON files consumed by the frontend:

```
frontend/src/lib/data/
  expenditure_areas.json     -- [{id, name}] — static, rarely changes
  available_years.json       -- [2025, 2026, ...] — sorted ascending
  budget_{year}.json         -- per year:
    {
      "government": [{"area_id": int, "amount_ksek": int}],
      "parties": {
        "S": [{"area_id": int, "delta_ksek": int | null}],
        ...
      }
    }
  index.ts                   -- re-exports all budget_{year}.json as a typed map
                             --   (generated; import { budgetByYear } from '$lib/data')
```

All JSON files are imported directly into SvelteKit components — no runtime
database access, no server routes needed for data. The frontend displays values
in MSEK (divides KSEK by 1000) for readability.

---

## 4. Frontend

### 4.1 Stack

| Concern | Choice |
|---|---|
| Framework | SvelteKit (static adapter) |
| Charts | Observable Plot |
| Styling | Plain CSS (no framework) |
| Deployment | Vercel or Cloudflare Pages |

### 4.2 Routes

| Route | Description |
|---|---|
| `/` | Main delta view — defaults to most recent year |
| `/om` | About page: data sources, methodology, caveats |

No other routes in v1.

### 4.3 Main Delta View

**Layout (desktop):**

```
┌─────────────────────────────────────────────────────────┐
│  [Year selector ▼]  [Jämför med föregående år ☐]        │
│  [MSEK / % toggle]                                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Delta chart (Observable Plot)                          │
│  • Y-axis: utgiftsområden 1–27 (fixed order)            │
│  • X-axis: delta in MSEK (or %) from baseline           │
│  • One bar per party per area                           │
│  • Bars to the right = more spending than baseline      │
│  • Bars to the left = less spending                     │
│  • Null bars shown in grey labeled "Ingen uppgift"      │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Mobile:** Chart scrolls horizontally. Utgiftsområde names truncate with full name
visible on tap/hover tooltip.

### 4.4 Year Comparison Mode

Activated by the "Jämför med föregående år" toggle (shown only when ≥2 years available).

When active, two sets of bars appear per area — one for each year. Both years'
deltas are relative to **the earlier year's government proposal** (fixed baseline).
This surfaces absolute spending growth across years, not just within-year disagreement.

Example: If year selector is set to 2024 and toggle is on:
- "2024 bars" = party 2024 proposal − govt 2023 proposal
- "2023 bars" = party 2023 proposal − govt 2023 proposal

The X-axis label updates to reflect the baseline year: "Delta från regeringen {year-1}
(MSEK)".

### 4.5 Chart Specification (Observable Plot)

```js
Plot.plot({
  marginLeft: 200,  // room for utgiftsområde names
  x: { label: "Delta från regeringen (MSEK)", grid: true },
  y: { label: null },
  color: { legend: true },
  marks: [
    Plot.barX(data, {
      x: "delta_msek",        // delta_ksek / 1000, computed from SQLite delta_ksek
      y: "area_name",
      fill: "party",
      title: d => `${d.party}: ${d.delta_msek > 0 ? "+" : ""}${d.delta_msek.toLocaleString("sv-SE")} MSEK`,
    }),
    Plot.ruleX([0]),  // zero baseline
  ]
})
```

**Party colors:** Use official Swedish party colors. Colors are defined in
`src/lib/partyColors.ts` as a map from party abbreviation to hex — add an entry
whenever a new party appears in the source manifest. Fall back to a neutral grey
(`#888888`) for any party not in the map.

Known colors:

| Party | Color |
|---|---|
| S (Socialdemokraterna) | `#E8112d` |
| V (Vänsterpartiet) | `#AF1E2D` |
| MP (Miljöpartiet) | `#83C441` |
| C (Centerpartiet) | `#009933` |
| SD (Sverigedemokraterna) | `#DDDD00` |
| KD (Kristdemokraterna) | `#231977` |
| L (Liberalerna) | `#6BB7EC` |
| M (Moderaterna) | `#52BDEC` |

Null (no data) bars: `#CCCCCC`, rendered at x=0 with a distinct pattern or opacity.

### 4.6 MSEK / % Toggle

Default: MSEK.

When % is selected, delta is computed as:
```
delta_pct = delta_ksek / amount_ksek × 100
```

If `amount_ksek` is 0 or null for an area, the % value is undefined — show
"Ingen uppgift" for that cell.

The X-axis label, tooltips, and any summary figures update accordingly.

### 4.7 Responsive Design

- **≥1024px:** Full chart visible without scroll. Utgiftsområde names displayed in full.
- **768–1023px:** Chart may require horizontal scroll on X-axis. Names abbreviated
  to "UO {id}" with full name in tooltip.
- **<768px (mobile):** Horizontal scroll on chart container. Pinch-to-zoom enabled
  via standard browser behavior (no custom zoom). Summary totals above the chart
  remain readable without scroll.

---

## 5. Data Seeding (Initial Setup)

For the initial deployment, historical data (3–5 years) must be backfilled manually:

1. Run `python pipeline/ingest.py --year {year}` for each historical year.
2. Each run opens a separate draft PR — review and merge in chronological order.
3. After all years are merged, run a single SvelteKit build.

The ingestion script accepts `--year` as a CLI argument. Without it, it defaults to
the current riksmöte year (derived from current date: budget year = calendar year if
month ≥ October, else calendar year − 1).

---

## 6. Out of Scope for v1

- ESV outcomes / utfall data
- Anslag-level detail (only utgiftsområde totals)
- Vårpropositionen
- User accounts, saved comparisons, sharing links
- English language option
- Search or text filtering of utgiftsområden
- Sorting by delta magnitude (fixed 1–27 order only)
- Historical data before ~2020 (older PDFs may be scanned; not handled)

---

## 7. Open Questions

- **Which hosting:** Vercel or Cloudflare Pages? (Either works; decide based on
  existing account preferences.)
- **Riksmöte year format:** Riksdagen API uses "rm" in the format `2023/24`. The
  ingestion script must map calendar year → riksmöte string correctly.
- **Rounding convention:** Parties may round to nearest 10 MSEK or 100 MSEK.
  Store as-reported; document rounding in the `/om` page.
