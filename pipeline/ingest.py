"""
Budget ingestion pipeline — pipeline/ingest.py

Steps:
  1. Fetch document list from Riksdagen API          ← implemented here
  2. Filter to budget motions and government proposal ← implemented here
  3. Download PDFs                                    (TODO)
  4. Extract text with pdfplumber                    (TODO)
  5. Send to Claude API for structured extraction    (TODO)
  6. Validate output                                  (TODO)
  7. Write to data/ JSON and SQLite                  (TODO)
"""

import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import requests

# ── Constants ────────────────────────────────────────────────────────────────

RIKSDAGEN_BASE = "https://data.riksdagen.se"
DOC_LIST_URL = RIKSDAGEN_BASE + "/dokumentlista/"

OPPOSITION_PARTIES: Set[str] = {"S", "V", "MP", "C"}

# These parties file one motion per utgiftsområde (27 areas expected).
# C files a single consolidated motion, so it is excluded from area coverage checks.
PER_AREA_PARTIES: Set[str] = {"S", "V", "MP"}
BUDGET_AREA_COUNT = 27

# Budget motions (budgetmotioner) are filed in early October each year in
# response to the government's budget proposition (prop. YYYY/YY:1).
# Searching the full riksmöte returns thousands of unrelated motions; using
# a narrow date window around the filing deadline keeps page counts low.
BUDGET_MOTION_WINDOW_START = "10-01"  # MM-DD, relative to riksmöte start year
BUDGET_MOTION_WINDOW_END = "10-31"

# Per-area budget motions from S, V, MP are titled "Utgiftsområde N <name>".
# Main party budget motions include "budgetmotion" in the title.
# NOTE: C files topical sub-motions (e.g. "Arbetsmarknad") rather than
# per-area ones — their consolidated figures are in the main budget motion.
BUDGET_TITLE_PREFIXES = ("utgiftsområde",)
BUDGET_TITLE_KEYWORDS = ("budgetmotion",)

REQUEST_TIMEOUT = 30  # seconds
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0  # seconds

# ── Data model ───────────────────────────────────────────────────────────────


@dataclass
class BudgetDocument:
    dok_id: str
    title: str
    party: Optional[str]    # None for government documents
    doc_type: str           # "mot" or "prop"
    riksmote: str           # e.g. "2024/25"
    pdf_url: Optional[str]


# ── HTTP helpers ─────────────────────────────────────────────────────────────


def _get_json(url: str, params: dict) -> dict:
    """GET with retries; raises RuntimeError on persistent failure."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < RETRY_ATTEMPTS:
                logging.warning(
                    "Request failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt,
                    RETRY_ATTEMPTS,
                    exc,
                    RETRY_BACKOFF,
                )
                time.sleep(RETRY_BACKOFF)
    raise RuntimeError(
        f"Riksdagen API unreachable after {RETRY_ATTEMPTS} attempts: {last_exc}"
    )


def _paginate(base_params: dict) -> List[dict]:
    """
    Fetch all pages for *base_params*, returning deduplicated documents.

    The API page size is fixed at 20 regardless of the ``antal`` parameter.
    Documents for multi-signatory motions appear once per signatory, so we
    deduplicate by ``dok_id`` before returning.
    """
    seen: Dict[str, dict] = {}
    page = 1
    total_pages = 1

    while page <= total_pages:
        data = _get_json(DOC_LIST_URL, {**base_params, "sida": page})
        doc_list = data.get("dokumentlista", {})

        raw = doc_list.get("dokument") or []
        if isinstance(raw, dict):
            raw = [raw]

        for doc in raw:
            dk = doc.get("dok_id")
            if dk and dk not in seen:
                seen[dk] = doc

        total_pages = int(doc_list.get("@sidor") or 1)
        logging.debug("  page %d/%d — %d unique so far", page, total_pages, len(seen))
        page += 1

    return list(seen.values())


# ── Step 1: Fetch document list ───────────────────────────────────────────────


def _riksmote_start_year(riksmote: str) -> int:
    """Extract the start calendar year from a riksmöte string, e.g. '2024/25' → 2024."""
    return int(riksmote.split("/")[0])


def fetch_budget_motions(riksmote: str) -> List[dict]:
    """
    Fetch all opposition-party budget motions for *riksmote* from the
    Riksdagen API.

    Budget motions are filed in early October each year. To avoid paginating
    through thousands of unrelated motions, we query per party with a date
    window around the filing deadline (Oct 1–15).

    Args:
        riksmote: Parliamentary session, e.g. "2024/25".

    Returns:
        List of raw document dicts, deduplicated by dok_id.
    """
    year = _riksmote_start_year(riksmote)
    date_from = f"{year}-{BUDGET_MOTION_WINDOW_START}"
    date_to = f"{year}-{BUDGET_MOTION_WINDOW_END}"

    logging.info(
        "Fetching budget motions for riksmöte %s (window %s–%s) …",
        riksmote,
        date_from,
        date_to,
    )

    all_docs: Dict[str, dict] = {}

    for party in sorted(OPPOSITION_PARTIES):
        params = {
            "doktyp": "mot",
            "rm": riksmote,
            "parti": party,
            "from": date_from,
            "tom": date_to,
            "utformat": "json",
            "antal": 20,
        }
        docs = _paginate(params)
        logging.info("  %s: %d documents in window", party, len(docs))
        for doc in docs:
            dk = doc.get("dok_id")
            if dk:
                all_docs[dk] = doc

    logging.info("Fetched %d unique motions across all parties.", len(all_docs))
    return list(all_docs.values())


def _fetch_proposal_parts(parent_dok_id: str) -> List[dict]:
    """
    The budget proposition is split into one document per utgiftsområde, each
    identified as ``{parent_dok_id}d{N}`` (Riksdagen "del" numbering, where
    N starts at 2 — d1 is the introduction volume, d2 is area 1, etc.).

    This enumerates N=2..30 and returns status-doc dicts for any that exist,
    supplementing the search results that the dokumentlista API may truncate.
    """
    parts: List[dict] = []
    for n in range(2, 31):
        dok_id = f"{parent_dok_id}d{n}"
        try:
            resp = requests.get(
                f"{RIKSDAGEN_BASE}/dokumentstatus/{dok_id}.json",
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200 or not resp.text.strip():
                continue
            doc = resp.json().get("dokumentstatus", {}).get("dokument", {})
            if doc.get("dok_id"):
                # Normalise to the same shape as dokumentlista items
                parts.append({
                    "dok_id": doc.get("dok_id"),
                    "titel": doc.get("titel"),
                    "rm": doc.get("rm"),
                    "doktyp": doc.get("doktyp"),
                    "filbilaga": None,  # PDF URL resolved separately
                })
        except requests.RequestException:
            continue
    logging.info(
        "Enumerated %d sub-volume parts for %s.", len(parts), parent_dok_id
    )
    return parts


def fetch_government_proposals(riksmote: str) -> List[dict]:
    """
    Fetch all government budget proposal documents (prop. YYYY/YY:1) for
    *riksmote* from the Riksdagen API.

    The Riksdagen search API caps results at ~20 unique documents, which can
    miss some of the 27 utgiftsområde volumes.  To fill the gaps, we also
    enumerate the parent proposition's sub-volumes directly via the
    ``dokumentstatus`` endpoint.

    Args:
        riksmote: Parliamentary session, e.g. "2024/25".

    Returns:
        List of raw document dicts, deduplicated by dok_id.
    """
    logging.info("Fetching government proposals for riksmöte %s …", riksmote)
    params = {
        "doktyp": "prop",
        "rm": riksmote,
        "nr": "1",          # budget proposition is always nr=1
        "utformat": "json",
        "antal": 20,
    }
    docs_by_id: Dict[str, dict] = {
        d["dok_id"]: d for d in _paginate(params) if d.get("dok_id")
    }

    # Derive the parent dok_id by stripping the "d{N}" suffix from any sub-volume.
    # Sub-volume IDs follow the pattern "{parent}d{N}", e.g. "HC031d2" → parent "HC031".
    parent_id: Optional[str] = None
    for dk in docs_by_id:
        m = re.match(r"^(.+?)d\d+$", dk)
        if m:
            parent_id = m.group(1)
            break

    if parent_id:
        logging.info("Fetching sub-volumes for parent proposal %s …", parent_id)
        for part in _fetch_proposal_parts(parent_id):
            dk = part.get("dok_id")
            if dk and dk not in docs_by_id:
                docs_by_id[dk] = part
                logging.info("  added missing volume %s: %s", dk, part.get("titel", "")[:50])

    logging.info("Fetched %d government proposal documents total.", len(docs_by_id))
    return list(docs_by_id.values())


# ── Step 2: Filter to budget documents ───────────────────────────────────────


def _doc_parties(doc: dict) -> Set[str]:
    """Return the set of party codes for a document's signatories."""
    intressenter = (doc.get("dokintressent") or {}).get("intressent", [])
    if isinstance(intressenter, dict):
        intressenter = [intressenter]
    return {
        i.get("partibet", "").upper().strip()
        for i in intressenter
        if i.get("partibet")
    }


def _pdf_url(doc: dict) -> Optional[str]:
    """Return the PDF download URL for *doc*, preferring the filbilaga list."""
    filbilaga = doc.get("filbilaga") or {}
    fil = filbilaga.get("fil")
    if isinstance(fil, list):
        fil = next((f for f in fil if isinstance(f, dict) and f.get("typ") == "pdf"), None)
    if isinstance(fil, dict):
        url = fil.get("url", "")
        if url:
            return url

    # Fallback: construct from dok_id (always works for riksdagen.se)
    dok_id = doc.get("dok_id")
    if dok_id:
        return f"{RIKSDAGEN_BASE}/dokument/{dok_id}.pdf"
    return None


def _is_budget_motion_title(title: str) -> bool:
    t = title.lower()
    return t.startswith(BUDGET_TITLE_PREFIXES) or any(kw in t for kw in BUDGET_TITLE_KEYWORDS)


def filter_budget_motions(documents: List[dict]) -> List[BudgetDocument]:
    """
    Filter raw documents down to the per-utgiftsområde and main budget motions
    filed by the four opposition parties (S, V, MP, C).

    Args:
        documents: Raw dicts from :func:`fetch_budget_motions`.

    Returns:
        Deduplicated list of :class:`BudgetDocument` instances.
    """
    matched: List[BudgetDocument] = []

    for doc in documents:
        parties = _doc_parties(doc) & OPPOSITION_PARTIES
        if not parties:
            continue

        title = doc.get("titel") or ""
        if not _is_budget_motion_title(title):
            continue

        # Attribute the document to a single party. Budget motions are filed
        # by one party, so all signatories share the same partibet.
        party = sorted(parties)[0] if len(parties) == 1 else sorted(parties)[0]

        matched.append(
            BudgetDocument(
                dok_id=doc.get("dok_id", ""),
                title=title,
                party=party,
                doc_type=doc.get("doktyp", "mot"),
                riksmote=doc.get("rm", ""),
                pdf_url=_pdf_url(doc),
            )
        )

    by_party = {p: sum(1 for d in matched if d.party == p) for p in sorted(OPPOSITION_PARTIES)}
    logging.info(
        "Matched %d budget motions — %s",
        len(matched),
        ", ".join(f"{p}: {n}" for p, n in by_party.items()),
    )
    return matched


def filter_government_proposals(documents: List[dict]) -> List[BudgetDocument]:
    """
    Filter government proposal documents down to per-utgiftsområde PDFs.

    The government proposal titles follow the format:
    "Budgetpropositionen för YYYY - Utgiftsområde N <name>"
    so we match on "utgiftsområde" anywhere in the title.

    Args:
        documents: Raw dicts from :func:`fetch_government_proposals`.

    Returns:
        List of :class:`BudgetDocument` instances.
    """
    matched: List[BudgetDocument] = []

    for doc in documents:
        title = doc.get("titel") or ""
        if "utgiftsområde" not in title.lower():
            continue

        matched.append(
            BudgetDocument(
                dok_id=doc.get("dok_id", ""),
                title=title,
                party=None,
                doc_type=doc.get("doktyp", "prop"),
                riksmote=doc.get("rm", ""),
                pdf_url=_pdf_url(doc),
            )
        )

    logging.info("Matched %d government proposal documents.", len(matched))
    return matched


# ── Step 2b: Validate coverage ────────────────────────────────────────────────


def _extract_area_number(title: str) -> Optional[int]:
    """Return the utgiftsområde number from a title, e.g. 'Utgiftsområde 3 Skatt' → 3."""
    m = re.search(r"utgiftsområde\s+(\d+)", title.lower())
    return int(m.group(1)) if m else None


def validate_coverage(motions: List[BudgetDocument]) -> bool:
    """
    Verify that each per-area opposition party has all 27 utgiftsområde motions.

    Prints a WARNING to stderr for each party with missing areas.
    C is excluded — it intentionally files a single consolidated motion.

    Returns:
        True if all per-area parties are complete, False otherwise.
    """
    all_ok = True
    for party in sorted(PER_AREA_PARTIES):
        areas_found = {
            _extract_area_number(d.title)
            for d in motions
            if d.party == party and _extract_area_number(d.title) is not None
        }
        missing = set(range(1, BUDGET_AREA_COUNT + 1)) - areas_found
        if missing:
            print(
                f"WARNING: {party} is missing utgiftsområde(n): "
                + ", ".join(str(n) for n in sorted(missing)),
                file=sys.stderr,
            )
            all_ok = False
        else:
            logging.info("%s: all %d budget areas present.", party, BUDGET_AREA_COUNT)
    return all_ok


# ── CLI entry point ───────────────────────────────────────────────────────────


def main(riksmote: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Step 1: fetch
    raw_motions = fetch_budget_motions(riksmote)
    raw_proposals = fetch_government_proposals(riksmote)

    # Step 2: filter
    motions = filter_budget_motions(raw_motions)
    proposals = filter_government_proposals(raw_proposals)

    # Validate that per-area parties have all 27 areas
    coverage_ok = validate_coverage(motions)

    # Print summary
    all_docs = motions + proposals
    print(f"\nFound {len(all_docs)} budget documents for riksmöte {riksmote}:\n")
    for doc in sorted(all_docs, key=lambda d: (d.party or "", d.title)):
        party_label = doc.party or "Govt"
        print(f"  [{party_label:4s}] {doc.title[:75]}")
        print(f"         dok_id={doc.dok_id}  pdf={doc.pdf_url}")

    if not coverage_ok:
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python ingest.py <riksmote>  # e.g. 2024/25", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
