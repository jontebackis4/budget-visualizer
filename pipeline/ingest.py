"""
Budget ingestion pipeline — pipeline/ingest.py

Steps:
  1. Load source manifest from pipeline/sources/{fiscal_year}.yaml
  2. Download PDFs (government + parties)
  3. Extract text from specified pages using pdfplumber
  4. Send to Claude API for structured extraction
  5. Validate output
  6. Write to data/ JSON and SQLite
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import requests
import yaml
from dotenv import load_dotenv

# ── Paths ─────────────────────────────────────────────────────────────────────

PIPELINE_DIR = Path(__file__).parent

load_dotenv(PIPELINE_DIR.parent / ".env")

SOURCES_DIR = PIPELINE_DIR / "sources"
LOGS_DIR = PIPELINE_DIR / "logs"
CACHE_DIR = PIPELINE_DIR / "cache"
DATA_DIR = PIPELINE_DIR.parent / "data"

# ── Constants ─────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 30  # seconds
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0  # seconds
CLAUDE_MODEL = "claude-opus-4-6"

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class SourceDocument:
    label: str                    # "government" or party abbreviation
    url: str
    pages: Optional[str] = None   # e.g. "45-52", or None for full document
    local_path: Optional[Path] = field(default=None)


# ── Riksmöte helpers ──────────────────────────────────────────────────────────


def riksmote_str(fiscal_year: int) -> str:
    """Convert a fiscal year to the Riksdagen riksmöte string, e.g. 2025 → '2024/25'."""
    short = str(fiscal_year)[-2:]
    return f"{fiscal_year - 1}/{short}"


def current_budget_year() -> int:
    """Return the current fiscal year (next calendar year if Oct or later, else current year)."""
    now = datetime.now()
    return now.year + 1 if now.month >= 10 else now.year


# ── Step 1: Load source manifest ──────────────────────────────────────────────


def load_manifest(year: int) -> List[SourceDocument]:
    """
    Load the source manifest for *year* from pipeline/sources/{year_str}.yaml.

    Returns a list of SourceDocument — government first, then parties.
    Aborts with a clear error if the file is missing or malformed.
    """
    path = SOURCES_DIR / f"{year}.yaml"
    if not path.exists():
        sys.exit(
            f"ERROR: Source manifest not found: {path}\n"
            f"Create it from pipeline/sources/TEMPLATE.yaml and fill in the URLs."
        )
    with path.open() as f:
        data = yaml.safe_load(f)

    gov = data.get("government")
    if not gov or not gov.get("url"):
        sys.exit(f"ERROR: No 'government' entry with a URL found in {path}.")

    parties = data.get("parties") or {}
    if not parties:
        sys.exit(f"ERROR: No parties found in {path}. Check the manifest format.")

    docs: List[SourceDocument] = [
        SourceDocument(label="government", url=gov["url"], pages=gov.get("pages")),
    ]
    for party, entry in parties.items():
        if not entry or not entry.get("url"):
            sys.exit(f"ERROR: Party '{party}' in {path} is missing a URL.")
        docs.append(SourceDocument(label=party, url=entry["url"], pages=entry.get("pages")))

    logging.info(
        "Loaded manifest for %s: government + %d parties (%s)",
        riksmote_str(year), len(parties), ", ".join(sorted(parties)),
    )
    return docs


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def download_file(url: str, dest: Path) -> None:
    """Download *url* to *dest*, with retries. Skip if dest already exists."""
    if dest.exists():
        logging.debug("  already cached: %s", dest.name)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, stream=True)
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            logging.debug("  downloaded %s → %s", url, dest.name)
            return
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < RETRY_ATTEMPTS:
                logging.warning(
                    "Download failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt, RETRY_ATTEMPTS, exc, RETRY_BACKOFF,
                )
                time.sleep(RETRY_BACKOFF)
    raise RuntimeError(
        f"Failed to download {url} after {RETRY_ATTEMPTS} attempts: {last_exc}"
    )


# ── Step 2: Download PDFs ─────────────────────────────────────────────────────


def download_pdfs(docs: List[SourceDocument], year: int) -> List[SourceDocument]:
    """Download each source PDF and set local_path on the document."""
    cache_dir = CACHE_DIR / str(year)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        dest = cache_dir / f"{doc.label}.pdf"
        logging.info("Downloading %s: %s", doc.label, doc.url)
        download_file(doc.url, dest)
        doc.local_path = dest
    logging.info("Downloaded %d PDFs.", len(docs))
    return docs


# ── Step 3: Extract text ───────────────────────────────────────────────────────


def _parse_pages(pages_str: str) -> Tuple[int, int]:
    """
    Parse a page range string into a 0-indexed (start, end) tuple for pdfplumber.

    "45-52" → (44, 51)
    "45"    → (44, 44)
    """
    parts = pages_str.strip().split("-")
    start = int(parts[0]) - 1
    end = int(parts[1]) - 1 if len(parts) == 2 else start
    return start, end


def extract_pdf_text(pdf_path: Path, pages: Optional[str] = None) -> str:
    """
    Extract text from *pdf_path* using pdfplumber.

    If *pages* is given (e.g. "45-52"), only those pages are extracted.
    Otherwise the full document is used.
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        if pages:
            start, end = _parse_pages(pages)
            page_objs = pdf.pages[start:end + 1]
        else:
            page_objs = pdf.pages
        texts = [p.extract_text() or "" for p in page_objs]

    return "\n\n".join(t for t in texts if t.strip())


# ── Step 4: Claude API extraction ─────────────────────────────────────────────

_MAIN_EXTRACTION_PROMPT = """\
You are extracting budget data from a Swedish government budget document.

Return a JSON object with this exact schema:
{{
  "source": "{source}",
  "budget_year": {budget_year},
  "budget_type": "main",
  "rows": [
    {{ "area_id": integer (1-27), "area_name": string, "amount_ksek": integer }}
  ],
  "total_ksek": integer,
  "extraction_notes": string
}}

Rules:
- area_id must be 1-27 (the official utgiftsområde number)
- amount_ksek is the amount in thousands of SEK (tkr) exactly as stated in the document — do not round or convert
- If a row is absent or illegible, omit it from rows[] and note it in extraction_notes
- Do not invent figures — if unsure, omit and note it

Text to extract from:
<text>
{text}
</text>"""

_COUNTER_EXTRACTION_PROMPT = """\
You are extracting budget deviation data from a Swedish party counter-budget document.

The document lists each utgiftsområde (expenditure area) alongside the government's proposed
amount and the party's suggested deviation (avvikelse från regeringen). Extract the deviation
exactly as stated — do not add it to the government amount.

Return a JSON object with this exact schema:
{{
  "source": "{source}",
  "budget_year": {budget_year},
  "budget_type": "counter",
  "rows": [
    {{ "area_id": integer (1-27), "area_name": string, "delta_ksek": integer }}
  ],
  "total_delta_ksek": integer,
  "extraction_notes": string
}}

Rules:
- area_id must be 1-27 (the official utgiftsområde number)
- delta_ksek is the signed deviation in thousands of SEK (tkr) exactly as stated — positive means more than the government proposal, negative means less
- If the deviation is ±0 or blank, use 0
- total_delta_ksek is the sum of all row deviations (or the stated total deviation if explicitly given)
- Do not add the government amount to the delta — extract only the deviation figure
- If a row is absent or illegible, omit it and note it in extraction_notes
- Do not invent figures — if unsure, omit and note it

Text to extract from:
<text>
{text}
</text>"""


def extract_with_claude(
    text: str, source_label: str, budget_year: int, budget_type: str,
    log_responses: bool = False,
) -> dict:
    """
    Call the Claude API to extract structured budget data from *text*.

    Raises RuntimeError (with details written to logs/) on API failure.
    If *log_responses* is True, the raw API response is written to logs/.
    """
    import anthropic

    client = anthropic.Anthropic()
    template = _MAIN_EXTRACTION_PROMPT if budget_type == "main" else _COUNTER_EXTRACTION_PROMPT
    prompt = template.format(
        source=source_label,
        budget_year=budget_year,
        text=text,
    )

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        _write_error_log(source_label, budget_year, str(exc))
        raise RuntimeError(
            f"Claude API call failed for {source_label}: {exc}"
        ) from exc

    raw = message.content[0].text if message.content else ""

    if log_responses:
        _write_response_log(source_label, budget_year, raw)

    # Extract JSON from a ```json ... ``` block anywhere in the response,
    # then fall back to the outermost { ... } if no fence is found.
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _write_error_log(
            source_label, budget_year,
            f"JSON parse error: {exc}\nRaw response:\n{raw}",
        )
        raise RuntimeError(
            f"Claude returned invalid JSON for {source_label}: {exc}"
        ) from exc


def _write_error_log(source_label: str, budget_year: int, detail: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"error_{budget_year}_{source_label}_{ts}.log"
    log_path.write_text(detail)
    logging.error("Error log written to %s", log_path)


def _write_response_log(source_label: str, budget_year: int, raw: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"response_{budget_year}_{source_label}_{ts}.log"
    log_path.write_text(raw)
    logging.debug("Response log written to %s", log_path)


# ── Step 5: Validate extraction ────────────────────────────────────────────────


def validate_extraction(result: dict) -> Tuple[bool, List[str]]:
    """
    Validate a Claude extraction result.

    Returns:
        (abort, warnings) — abort=True means invalid area_id values were found
        and the pipeline should stop. warnings is a list of human-readable issues.
    """
    warnings: List[str] = []
    abort = False
    budget_type = result.get("budget_type", "main")
    rows = result.get("rows", [])

    invalid_ids = [
        r.get("area_id") for r in rows
        if r.get("area_id") is None or not (1 <= r.get("area_id", 0) <= 27)
    ]
    if invalid_ids:
        warnings.append(f"Invalid area_id values (must be 1–27): {invalid_ids}")
        abort = True

    if len(rows) < 20:
        warnings.append(
            f"Only {len(rows)} rows extracted (expected ≥20) — likely incomplete."
        )

    if budget_type == "main":
        total_ksek = result.get("total_ksek")
        if total_ksek is not None and rows:
            computed = sum(r.get("amount_ksek", 0) or 0 for r in rows)
            discrepancy = abs(computed - total_ksek)
            if discrepancy != 0:
                warnings.append(
                    f"Sum of rows ({computed:,} ksek) differs from total_ksek "
                    f"({total_ksek:,} ksek) by {discrepancy:,} ksek."
                )
    else:
        total_delta = result.get("total_delta_ksek")
        if total_delta is not None and rows:
            computed = sum(r.get("delta_ksek", 0) or 0 for r in rows)
            discrepancy = abs(computed - total_delta)
            if discrepancy != 0:
                warnings.append(
                    f"Sum of deltas ({computed:,} ksek) differs from total_delta_ksek "
                    f"({total_delta:,} ksek) by {discrepancy:,} ksek."
                )

    return abort, warnings


# ── Step 6: Write output ───────────────────────────────────────────────────────


def write_json(extractions: List[dict], year: int) -> Path:
    """Write all extractions to data/budget_{year}.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"budget_{year}.json"
    path.write_text(json.dumps(extractions, ensure_ascii=False, indent=2))
    logging.info("Wrote %s", path)
    return path


def write_sqlite(extractions: List[dict], year: int) -> Path:
    """Write extracted data to data/review/budget_{year}.sqlite."""
    review_dir = DATA_DIR / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    db_path = review_dir / f"budget_{year}.sqlite"

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS expenditure_areas (
            id    INTEGER PRIMARY KEY,
            name  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS budget_values (
            id           INTEGER PRIMARY KEY,
            budget_year  INTEGER NOT NULL,
            area_id      INTEGER REFERENCES expenditure_areas(id),
            amount_ksek  INTEGER,
            doc_url      TEXT,
            UNIQUE (budget_year, area_id)
        );
        CREATE TABLE IF NOT EXISTS counter_deviations (
            id           INTEGER PRIMARY KEY,
            budget_year  INTEGER NOT NULL,
            party        TEXT NOT NULL,
            area_id      INTEGER REFERENCES expenditure_areas(id),
            delta_ksek   INTEGER,
            doc_url      TEXT,
            UNIQUE (budget_year, party, area_id)
        );
        CREATE VIEW IF NOT EXISTS counter_effective_amounts AS
            SELECT
                cd.budget_year,
                cd.party,
                cd.area_id,
                ea.name                            AS area_name,
                bv.amount_ksek,
                cd.delta_ksek,
                bv.amount_ksek + cd.delta_ksek     AS effective_amount_ksek
            FROM counter_deviations cd
            JOIN budget_values bv
              ON bv.budget_year = cd.budget_year AND bv.area_id = cd.area_id
            JOIN expenditure_areas ea ON ea.id = cd.area_id;
    """)

    for extraction in extractions:
        budget_type = extraction.get("budget_type", "main")
        source_label = extraction.get("source", "unknown")
        doc_url = extraction.get("doc_url")

        for r in extraction.get("rows", []):
            area_id = r.get("area_id")
            area_name = r.get("area_name", "")
            cur.execute(
                "INSERT OR IGNORE INTO expenditure_areas (id, name) VALUES (?, ?)",
                (area_id, area_name),
            )
            if budget_type == "main":
                cur.execute(
                    """INSERT OR REPLACE INTO budget_values
                       (budget_year, area_id, amount_ksek, doc_url)
                       VALUES (?, ?, ?, ?)""",
                    (year, area_id, r.get("amount_ksek"), doc_url),
                )
            else:
                cur.execute(
                    """INSERT OR REPLACE INTO counter_deviations
                       (budget_year, party, area_id, delta_ksek, doc_url)
                       VALUES (?, ?, ?, ?, ?)""",
                    (year, source_label, area_id, r.get("delta_ksek"), doc_url),
                )

    conn.commit()
    conn.close()
    logging.info("Wrote %s", db_path)
    return db_path


# ── CLI entry point ───────────────────────────────────────────────────────────


def main(year: int, log_responses: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    rmote = riksmote_str(year)
    logging.info("Starting ingestion for riksmöte %s (budget year %d)", rmote, year)

    # Step 1: Load source manifest
    docs = load_manifest(year)

    # Step 2: Download all PDFs
    docs = download_pdfs(docs, year)

    all_extractions: List[dict] = []
    all_warnings: List[Tuple[str, List[str]]] = []

    # Steps 3–5: Extract, send to Claude, validate
    for doc in docs:
        if not doc.local_path or not doc.local_path.exists():
            logging.warning("Missing local PDF for %s — skipping.", doc.label)
            continue
        budget_type = "main" if doc.label == "government" else "counter"
        page_info = f" (pages {doc.pages})" if doc.pages else ""
        logging.info("Extracting text from %s%s …", doc.label, page_info)
        text = extract_pdf_text(doc.local_path, doc.pages)
        logging.info("Sending %s to Claude …", doc.label)
        result = extract_with_claude(
            text, doc.label, year, budget_type, log_responses=log_responses
        )
        result["doc_url"] = doc.url
        abort, warnings = validate_extraction(result)
        if warnings:
            all_warnings.append((doc.label, warnings))
        if abort:
            sys.exit(f"Aborting: {doc.label} extraction has invalid area_id values.")
        all_extractions.append(result)

    # Step 6: Write output
    json_path = write_json(all_extractions, year)
    sqlite_path = write_sqlite(all_extractions, year)

    print(f"\nIngestion complete for riksmöte {rmote}:")
    print(f"  JSON:   {json_path}")
    print(f"  SQLite: {sqlite_path}")

    if all_warnings:
        print("\nWarnings (review before merging PR):")
        for source, ws in all_warnings:
            for w in ws:
                print(f"  [{source}] {w}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest Swedish budget data for a given fiscal year.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=current_budget_year(),
        help=(
            "Fiscal year the budget governs (e.g. 2025 for the budget submitted as prop. 2024/25:1). "
            f"Defaults to current fiscal year ({current_budget_year()})."
        ),
    )
    parser.add_argument(
        "--log-responses",
        action="store_true",
        help="Save raw Claude API responses to pipeline/logs/response_*.log files.",
    )
    args = parser.parse_args()
    main(args.year, log_responses=args.log_responses)
