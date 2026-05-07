"""
scraper/accession_lookup.py

Look up data repository accession numbers (GEO, SRA, ArrayExpress) for a
published paper. Uses two sources in order:

  1. Europe PMC  — searches by DOI or title, then calls the databaseLinks
                   endpoint which extracts accession numbers from full paper
                   text. Free, no key required.

  2. NCBI EUtils — fallback via pubmed→gds elink. Requires 0.34s between
                   calls to stay under the 3 req/s unauthenticated limit.

Returns a comma-separated string of accession numbers, or "" if none found.
"""

import time
import requests

_EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_EPMC_LINKS  = "https://www.ebi.ac.uk/europepmc/webservices/rest/{src}/{id}/databaseLinks/{db}/1/json"
_NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_NCBI_ELINK   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
_NCBI_ESUM    = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

_TARGET_DBS = ("GEO", "SRA", "ArrayExpress")


def get_accessions(title: str, doi: str = "") -> str:
    """
    Return a comma-separated string of accession numbers for the paper,
    or '' if none are found.
    """
    pmid, src = _epmc_find(title, doi)

    accessions = []
    if pmid:
        for db in _TARGET_DBS:
            accessions.extend(_epmc_links(src, pmid, db))

    if not accessions:
        accessions = _ncbi_geo(doi or title)

    return ", ".join(dict.fromkeys(accessions))   # deduplicate, preserve order


# ── Europe PMC ────────────────────────────────────────────────────────────────

def _epmc_find(title: str, doi: str = "") -> tuple[str, str]:
    """Search Europe PMC, return (id, source) or ('', '')."""
    query = f'DOI:"{doi}"' if doi else f'TITLE:"{title[:120]}"'
    try:
        r = requests.get(
            _EPMC_SEARCH,
            params={"query": query, "resultType": "lite", "format": "json", "pageSize": 1},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("resultList", {}).get("result", [])
        if results:
            paper = results[0]
            return paper.get("id", ""), paper.get("source", "MED")
    except Exception:
        pass
    return "", ""


def _epmc_links(src: str, pmid: str, db: str) -> list[str]:
    """Fetch accession IDs via the Europe PMC databaseLinks endpoint."""
    url = _EPMC_LINKS.format(src=src, id=pmid, db=db)
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        links = (
            r.json()
            .get("dbCrossReferenceList", {})
            .get("dbCrossReference", [])
        )
        return [lnk.get("dbId", "") for lnk in links if lnk.get("dbId")]
    except Exception:
        return []


# ── NCBI EUtils fallback ──────────────────────────────────────────────────────

def _ncbi_geo(doi_or_title: str) -> list[str]:
    """
    DOI (preferred) or title → PubMed ID → GEO dataset UIDs → GSE accessions.
    Returns list of GSE/GDS accession strings.
    """
    try:
        # Step 1: resolve to PMID
        if doi_or_title.startswith("10."):
            term = f"{doi_or_title}[doi]"
        else:
            term = f"{doi_or_title[:120]}[title]"

        r = requests.get(
            _NCBI_ESEARCH,
            params={"db": "pubmed", "term": term, "retmode": "json"},
            timeout=10,
        )
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        pmid = ids[0]
        time.sleep(0.4)

        # Step 2: PMID → GEO dataset internal UIDs
        r = requests.get(
            _NCBI_ELINK,
            params={"dbfrom": "pubmed", "db": "gds", "id": pmid, "retmode": "json"},
            timeout=10,
        )
        r.raise_for_status()
        gds_ids = []
        for ls in r.json().get("linksets", []):
            for ld in ls.get("linksetdbs", []):
                if ld.get("dbto") == "gds":
                    gds_ids.extend(str(g) for g in ld.get("links", []))
        if not gds_ids:
            return []
        time.sleep(0.4)

        # Step 3: GEO UIDs → GSE accession strings
        r = requests.get(
            _NCBI_ESUM,
            params={"db": "gds", "id": ",".join(gds_ids), "retmode": "json"},
            timeout=10,
        )
        r.raise_for_status()
        docsum = r.json().get("result", {})
        return [
            docsum[uid].get("accession", "")
            for uid in gds_ids
            if docsum.get(uid, {}).get("accession")
        ]
    except Exception:
        return []
