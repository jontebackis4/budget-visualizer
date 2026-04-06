# Swedish Budget Comparison Tool — Architecture & Tech Stack

## Project Overview

A web app for comparing the Swedish central government's annual budget
(budgetpropositionen) against opposition party budget motions
(budgetmotioner) and actual outcomes (utfall). Data is sourced from
Riksdagen's Open Data API, party PDF documents, and ESV (Ekonomistyrningsverket).

---

## Data Sources

### 1. Government Budget (Budgetpropositionen)
- **Source:** Riksdagen Open Data API + Regeringen.se
- **Format:** PDFs, one per utgiftsområde (27 total)
- **API endpoint:** `https://data.riksdagen.se/dokumentlista/?doktyp=prop&rm={year}&nr=1&utformat=json`
- **Document content:** `https://data.riksdagen.se/dokumentstatus/{dok_id}.json`
- **Published:** Late September each year

### 2. Party Budget Motions (Budgetmotioner)
- **Source:** Riksdagen Open Data API
- **Parties:** S, V, MP, C (opposition parties; governing parties don't file motions)
- **Format:** PDFs — one main motion + one per utgiftsområde per party
- **API endpoint:** `https://data.riksdagen.se/dokumentlista/?doktyp=mot&rm={year}&utformat=json`
  - Filter results by title/party to isolate budget motions
- **Published:** Early October each year (deadline ~7 October)
- **Structure:** All parties use the same 27 utgiftsområden as the government.
  Each party proposes a spending amount per area — these are directly comparable.

### 3. Budget Outcomes (Utfall)
- **Source:** ESV (Ekonomistyrningsverket) — esv.se/statistik-och-data
- **Format:** Downloadable Excel/CSV files (no PDF parsing needed)
- **Data:** Monthly actuals per utgiftsområde; annual summary in Årsredovisning för staten
- **Note:** ESV is a separate system from Riksdagen — fetched independently

---

## Data Structure

The budget is divided into **27 fixed utgiftsområden (expenditure areas)**,
numbered 1–27. All parties and the government use these exact same categories —
they are legally mandated. This makes top-level comparison straightforward.

Within each utgiftsområde there are individual **anslag** (appropriations/line items).
At this level, parties may present figures differently. A first version of the tool
should focus on the utgiftsområde level; anslag-level detail can be added later.

### Database Schema (SQLite)

```sql
-- The 27 fixed expenditure areas (static, rarely changes)
CREATE TABLE expenditure_areas (
    id      INTEGER PRIMARY KEY,  -- Official number 1–27
    name    TEXT NOT NULL         -- e.g. "Hälsovård, sjukvård och social omsorg"
);

-- Who produced each budget figure
CREATE TABLE sources (
    id      INTEGER PRIMARY KEY,
    type    TEXT NOT NULL,  -- 'government', 'party', 'outcome'
    party   TEXT            -- 'S', 'V', 'MP', 'C' — null for government/outcome
);

-- Core data: one row per year × source × expenditure area
CREATE TABLE budget_values (
    id           INTEGER PRIMARY KEY,
    budget_year  INTEGER NOT NULL,
    source_id    INTEGER REFERENCES sources(id),
    area_id      INTEGER REFERENCES expenditure_areas(id),
    amount_msek  DECIMAL(12,0),  -- Amount in millions SEK
    doc_id       TEXT,           -- Riksdagen document ID for traceability
    UNIQUE (budget_year, source_id, area_id)
);
```

**Note on outcomes:** ESV publishes monthly actuals throughout the year.
Consider adding a `month` column to `budget_values` (null for proposals,
1–12 for ESV monthly data) if in-year tracking is desired.

**Scale:** Even with 10 years × 8 sources × 27 areas, this is ~2,000 rows
at the utgiftsområde level. SQLite is more than sufficient.

---

## Data Pipeline

The pipeline runs **once per year** (October–November) when new documents
are published. It is implemented as a Python script and triggered via
GitHub Actions.

```
Riksdagen API              ESV website
     |                          |
     v                          v
Fetch document list        Download CSV/Excel
Filter budget docs         (outcomes data)
     |                          |
     v                          |
Download PDFs                   |
     |                          |
     v                          |
Parse PDFs                      |
(pdfplumber / PyMuPDF)          |
Extract utgiftsområde tables    |
     |                          |
     +----------+---------------+
                |
                v
         SQLite database
                |
                v
        SvelteKit build step
        (reads DB, exports JSON)
                |
                v
     Static JSON data files
     bundled with frontend
```

### PDF Parsing Notes
- Government and party PDFs contain real embedded text (not scans) — standard
  PDF text extraction works reliably.
- Each party formats their motion slightly differently. Expect some
  per-party parsing logic, especially for the summary spending tables.
- An LLM-based extraction step (e.g. calling Claude API) can help normalize
  inconsistent table formats across parties.
- After each annual run, do a manual spot-check that parsed figures match
  the source PDFs before deploying.

---

## Tech Stack

### Frontend
| Concern | Choice | Reason |
|---|---|---|
| Framework | **SvelteKit** | Compiles to lean JS, excellent static site generation, fast by default |
| Charts | **Observable Plot** | Purpose-built for tabular/statistical data, great grouped bar charts |
| Client-side filtering | Plain JS array filtering | Data is small enough; no search library needed |

### Data at Runtime
- All budget data is **pre-built into static JSON files** at build time.
- The browser loads JSON directly — no database queries, no server round-trips.
- This is the primary reason the app feels instant.

### Backend / Hosting
| Concern | Choice | Reason |
|---|---|---|
| Hosting | **Vercel** or **Cloudflare Pages** | Edge CDN, generous free tier, automatic deploys from Git |
| Database | **SQLite** (build-time only) | Not exposed to web; used only during ingestion and build |
| Ingestion | **Python script** | PDF parsing with pdfplumber/PyMuPDF, ESV CSV handling |
| Automation | **GitHub Actions** | Trigger annual ingestion + rebuild on schedule or manually |

### Key Libraries (Python ingestion)
- `pdfplumber` or `PyMuPDF` — PDF text and table extraction
- `requests` — Riksdagen API calls
- `pandas` — ESV CSV/Excel processing
- `sqlite3` — Standard library, no ORM needed at this scale

---

## Architecture Principle: Static-First

Because budget data updates only once per year, the app uses a
**static site generation** pattern rather than a traditional dynamic server.

At build time, SvelteKit reads from SQLite and bakes all data into
pre-computed JSON. The deployed site is entirely static files served
from a CDN — no database, no server, no latency on data queries.

If user accounts, saved comparisons, or comments are needed in the future,
a backend (e.g. a small serverless function + database) can be added then.
For a read-only comparison tool, this is unnecessary.

---

## Annual Update Workflow

1. **~September 22:** Government publishes budgetpropositionen
2. **~October 7:** Opposition parties publish budgetmotioner
3. **GitHub Actions** triggers ingestion script
4. Script fetches new PDFs from Riksdagen API, parses tables, writes to SQLite
5. ESV outcomes from previous year are fetched and added
6. SvelteKit build runs, generates updated static JSON
7. New version deployed to Vercel/Cloudflare Pages
8. Manual spot-check of parsed figures against source documents

---

## Future Considerations

- **Anslag-level detail:** The 27-area structure is a clean first layer.
  Drilling into individual anslag within each area is a natural v2 feature.
- **Spring fiscal policy bill (Vårpropositionen):** Published in April,
  contains updated forecasts — could be a third data point between
  proposal and outcome.
- **Historical backfill:** Riksdagen API has documents going back to 1975/76.
  Parsing older PDFs may require extra handling for scanned documents.