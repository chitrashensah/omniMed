# Literature search + citation verification across biomedical databases.
#
# Four sources, chosen for coverage rather than count:
#
#   Europe PMC  — primary search. A superset of PubMed that also indexes
#                 preprints and open-access full text, in one keyless JSON call.
#   PubMed      — the canonical index, via NCBI E-utilities. Carries MeSH terms
#                 and PMIDs, which is what a reviewer expects to see cited.
#   Crossref    — DOI resolution. The authority for "is this DOI real".
#   ClinicalTrials.gov — has a candidate actually reached human trials. For a
#                 project about *therapeutic* miRNAs this is the translational
#                 evidence no literature index provides.
#
# All four are free and need no API key. NCBI asks callers to identify
# themselves (NCBI_TOOL / NCBI_EMAIL) and rate-limits to 3 req/s without a key,
# 10 with one — both are handled below.
#
# Every function is best-effort: a source that is slow, down, or rate-limited
# yields no results rather than failing the whole search.

import os
import re
import time
import asyncio
import hashlib
import threading
from dataclasses import dataclass, field, asdict

import httpx

TIMEOUT = 25.0
MAX_RESULTS_DEFAULT = 8

EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF = "https://api.crossref.org"
DATACITE = "https://api.datacite.org"
CTGOV = "https://clinicaltrials.gov/api/v2"

# NCBI asks for a tool name and contact address on every request so they can
# reach you if a script misbehaves. Not authentication — just courtesy, and
# they throttle harder without it.
NCBI_TOOL = os.getenv("NCBI_TOOL", "OmniMed")
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "")
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")

SOURCES = ("europepmc", "pubmed", "trials")


# ── Rate limiting ──────────────────────────────────────────────────────────
class _RateLimiter:
    """NCBI caps at 3 requests/second (10 with an API key). Exceeding it gets
    the whole project's IP throttled, so this is a hard serialisation point
    rather than a best-effort delay.

    Each caller reserves the next free slot under a threading lock and then
    sleeps until it. It used an asyncio.Lock before, but every Flask request
    runs its own event loop (asyncio.run), and an asyncio.Lock binds to the
    first loop that waits on it: from the second request on, any concurrent
    lookup raised "bound to a different event loop", which surfaced as
    "Lookup failed" on perfectly findable citations. A threading lock is shared
    by every loop and thread, and is never held across an await.
    """

    def __init__(self, per_second: float):
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    async def wait(self):
        with self._lock:
            slot = max(time.monotonic(), self._next)
            self._next = slot + self._interval
        delay = slot - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)


_ncbi_limiter = _RateLimiter(9.0 if NCBI_API_KEY else 2.5)


# ── Cache ──────────────────────────────────────────────────────────────────
# Identical searches are common (same question re-run across models, retries,
# a reviewer reproducing a result). Caching keeps latency and load down, and
# means a rerun returns the same papers rather than a drifting live index.
_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 60 * 60 * 6  # 6 hours


def _cache_key(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def _cache_get(key: str):
    hit = _cache.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    return val


def _cache_put(key: str, val):
    if len(_cache) > 500:          # crude bound; this is a process-local cache
        _cache.clear()
    _cache[key] = (time.time(), val)


# ── Unified result shape ───────────────────────────────────────────────────
@dataclass
class Paper:
    """One retrieved record, normalised across sources so downstream code
    (claim extraction, citation verification) never branches on provenance."""
    source: str
    title: str = ""
    abstract: str = ""
    journal: str = ""
    year: str = ""
    authors: list = field(default_factory=list)
    pmid: str = ""
    doi: str = ""
    url: str = ""
    is_open_access: bool = False
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def citation(self) -> str:
        who = self.authors[0] + " et al." if self.authors else "Unknown"
        bits = [b for b in (who, self.title, self.journal, self.year) if b]
        tail = f" PMID:{self.pmid}" if self.pmid else (f" DOI:{self.doi}" if self.doi else "")
        return " ".join(bits) + tail


def _clean(text: str, limit: int = 2000) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)       # strip inline markup from abstracts
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# ── Europe PMC ─────────────────────────────────────────────────────────────
async def search_europepmc(client: httpx.AsyncClient, query: str,
                           limit: int = MAX_RESULTS_DEFAULT,
                           year_from: int | None = None,
                           year_to: int | None = None) -> list[Paper]:
    q = query
    if year_from or year_to:
        q += f" AND (FIRST_PDATE:[{year_from or 1900} TO {year_to or 3000}])"
    # Errors propagate: search() records the source as unavailable, which an
    # empty list would hide ("no papers exist" when the database was unreachable).
    r = await client.get(f"{EUROPEPMC}/search", params={
        "query": q, "format": "json", "pageSize": min(limit, 25),
        "resultType": "core",
    })
    r.raise_for_status()
    results = r.json().get("resultList", {}).get("result", []) or []

    out = []
    for it in results[:limit]:
        authors = [a.get("lastName", "") for a in
                   (it.get("authorList", {}) or {}).get("author", [])[:6] if a.get("lastName")]
        doi = it.get("doi", "") or ""
        pmid = it.get("pmid", "") or ""
        out.append(Paper(
            source="europepmc",
            title=_clean(it.get("title", ""), 400),
            abstract=_clean(it.get("abstractText", "")),
            journal=_clean((it.get("journalInfo", {}) or {}).get("journal", {}).get("title", ""), 200),
            year=str(it.get("pubYear", "") or ""),
            authors=authors,
            pmid=pmid, doi=doi,
            url=(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid
                 else f"https://doi.org/{doi}" if doi else ""),
            is_open_access=(it.get("isOpenAccess") == "Y"),
            extra={"cited_by": it.get("citedByCount", 0)},
        ))
    return out


# ── PubMed (NCBI E-utilities) ──────────────────────────────────────────────
def _ncbi_params(**kw) -> dict:
    p = {"tool": NCBI_TOOL, **kw}
    if NCBI_EMAIL:
        p["email"] = NCBI_EMAIL
    if NCBI_API_KEY:
        p["api_key"] = NCBI_API_KEY
    return p


async def search_pubmed(client: httpx.AsyncClient, query: str,
                        limit: int = MAX_RESULTS_DEFAULT,
                        year_from: int | None = None,
                        year_to: int | None = None) -> list[Paper]:
    """esearch for PMIDs, then efetch for the records. Two calls by design —
    NCBI has no combined endpoint. Errors propagate, as in search_europepmc."""
    term = query
    if year_from or year_to:
        term += f" AND ({year_from or 1800}:{year_to or 3000}[dp])"
    await _ncbi_limiter.wait()
    r = await client.get(f"{EUTILS}/esearch.fcgi", params=_ncbi_params(
        db="pubmed", term=term, retmode="json", retmax=min(limit, 25), sort="relevance"))
    r.raise_for_status()
    ids = r.json().get("esearchresult", {}).get("idlist", []) or []
    if not ids:
        return []

    await _ncbi_limiter.wait()
    r2 = await client.get(f"{EUTILS}/efetch.fcgi", params=_ncbi_params(
        db="pubmed", id=",".join(ids), retmode="xml"))
    r2.raise_for_status()
    return _parse_pubmed_xml(r2.text)[:limit]


def _parse_pubmed_xml(xml: str) -> list[Paper]:
    """Minimal XML parse. efetch returns a fixed schema, and a dependency-free
    parse keeps this importable without adding a library."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml)
    except Exception:
        return []

    out = []
    for art in root.findall(".//PubmedArticle"):
        def txt(path, default=""):
            el = art.find(path)
            return "".join(el.itertext()).strip() if el is not None else default

        # Abstracts are split into labelled sections (BACKGROUND, METHODS…);
        # join them so the full text is searchable as one block.
        abstract = " ".join(
            "".join(node.itertext()).strip()
            for node in art.findall(".//Abstract/AbstractText")
        )
        pmid = txt(".//PMID")
        doi = ""
        for aid in art.findall(".//ArticleId"):
            if aid.get("IdType") == "doi":
                doi = (aid.text or "").strip()

        authors = []
        for a in art.findall(".//Author")[:6]:
            ln = a.find("LastName")
            if ln is not None and ln.text:
                authors.append(ln.text.strip())

        mesh = [m.text for m in art.findall(".//MeshHeading/DescriptorName") if m.text]

        # A citation to a retracted paper passes every other check — the ID
        # resolves, the paper is on topic — while being exactly the kind of
        # source a researcher must not build on. PubMed marks it as a
        # publication type, so it costs nothing to carry through.
        pubtypes = [p.text for p in art.findall(".//PublicationType") if p.text]
        retracted = any("retracted publication" in (t or "").lower() for t in pubtypes)

        out.append(Paper(
            source="pubmed",
            title=_clean(txt(".//ArticleTitle"), 400),
            abstract=_clean(abstract),
            journal=_clean(txt(".//Journal/Title"), 200),
            year=txt(".//PubDate/Year") or txt(".//PubDate/MedlineDate")[:4],
            authors=authors, pmid=pmid, doi=doi,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            extra={"mesh": mesh[:12], "retracted": retracted},
        ))
    return out


# ── ClinicalTrials.gov ─────────────────────────────────────────────────────
async def search_trials(client: httpx.AsyncClient, query: str,
                        limit: int = 5) -> list[Paper]:
    """Registered human trials. Literature indexes say what was studied in a
    dish; this says what reached patients — the difference between an
    interesting target and a therapeutic one."""
    # ClinicalTrials.gov ANDs every word and indexes ~500k studies, not 37M
    # papers. A literature-style query ("microRNA-21 cardiac fibrosis") matched
    # nothing at all, which made this source look broken when it was simply
    # being asked a question no trial could satisfy. Widen it: OR the molecule
    # names, and keep only a couple of context words.
    ents = _rna_entities(query)
    terms = []
    for e in sorted(ents)[:3]:
        if e.startswith(("mir-", "let-")):
            num = e.split("-", 1)[1]
            terms.append(f"miR-{num} OR microRNA-{num}")
        else:
            terms.append(e)
    if not terms:
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", query)
                 if w.lower() not in _STOPWORDS][:3]
        terms = [" OR ".join(words)] if words else [query]

    r = await client.get(f"{CTGOV}/studies", params={       # errors propagate, as above
        "query.term": " OR ".join(f"({t})" for t in terms),
        "pageSize": min(limit, 20),
    })
    r.raise_for_status()
    studies = r.json().get("studies", []) or []

    out = []
    for st in studies[:limit]:
        proto = st.get("protocolSection", {}) or {}
        ident = proto.get("identificationModule", {}) or {}
        status = proto.get("statusModule", {}) or {}
        desc = proto.get("descriptionModule", {}) or {}
        design = proto.get("designModule", {}) or {}
        nct = ident.get("nctId", "")
        out.append(Paper(
            source="trials",
            title=_clean(ident.get("briefTitle", ""), 400),
            abstract=_clean(desc.get("briefSummary", "")),
            year=str((status.get("startDateStruct", {}) or {}).get("date", ""))[:4],
            url=f"https://clinicaltrials.gov/study/{nct}" if nct else "",
            extra={
                "nct_id": nct,
                "status": status.get("overallStatus", ""),
                "phases": design.get("phases", []),
            },
        ))
    return out


# ── Unified search ─────────────────────────────────────────────────────────
async def search(query: str, sources=SOURCES, limit: int = MAX_RESULTS_DEFAULT,
                 year_from: int | None = None, year_to: int | None = None) -> dict:
    """Query several databases concurrently and return one merged, deduplicated
    result set. A failing source contributes nothing rather than raising, and
    is listed in `unavailable`; such a result is not cached, so an outage is
    not remembered as "no papers"."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "papers": [], "by_source": {}, "unavailable": [], "cached": False}

    key = _cache_key("search", query, tuple(sources), limit, year_from, year_to)
    hit = _cache_get(key)
    if hit is not None:
        return {**hit, "cached": True}

    async with httpx.AsyncClient(timeout=TIMEOUT,
                                 headers={"User-Agent": f"{NCBI_TOOL} (biomedical research)"}) as client:
        jobs, names = [], []
        if "europepmc" in sources:
            jobs.append(search_europepmc(client, query, limit, year_from, year_to)); names.append("europepmc")
        if "pubmed" in sources:
            jobs.append(search_pubmed(client, query, limit, year_from, year_to)); names.append("pubmed")
        if "trials" in sources:
            jobs.append(search_trials(client, query, min(limit, 5))); names.append("trials")

        settled = await asyncio.gather(*jobs, return_exceptions=True)

    by_source, merged, seen, unavailable = {}, [], set(), []
    for name, res in zip(names, settled):
        if not isinstance(res, list):
            unavailable.append(name)
        papers = res if isinstance(res, list) else []
        by_source[name] = len(papers)
        for p in papers:
            # Europe PMC and PubMed overlap heavily; dedupe on PMID/DOI so the
            # same paper is not counted twice as independent support.
            ident = p.pmid or p.doi or p.title.lower()[:80]
            if ident and ident in seen:
                continue
            seen.add(ident)
            merged.append(p)

    # PubMed's [dp] filter matches the electronic publication date while the
    # year shown here is the print year, so a 2005-2010 search returned papers
    # labelled 2011 and looked broken. Trials carry a start date, not a
    # publication date, so they are exempt rather than silently dropped.
    if year_from or year_to:
        merged = [p for p in merged
                  if p.source == "trials" or not p.year
                  or ((not year_from or int(p.year) >= year_from)
                      and (not year_to or int(p.year) <= year_to))]

    result = {"query": query, "papers": [p.to_dict() for p in merged],
              "by_source": by_source, "count": len(merged), "unavailable": unavailable}
    if not unavailable:
        _cache_put(key, result)
    return {**result, "cached": False}


# ── Citation verification ──────────────────────────────────────────────────
# Models write citations in markdown, so the label and the number are often
# separated by formatting: "**PMID:** 20683889", "PMID: [12345678]",
# "PubMed ID - 12345678". Matching only bare "PMID: 12345678" silently found
# zero citations in real answers, which would have made this whole layer
# look like every model cites nothing.
PMID_RE = re.compile(r"\b(?:PMIDs?|PubMed\s*ID)\b[\s:*_\-\[\]()]{0,8}(\d{6,9})\b", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\)\]\},;\"'*<>]+)", re.I)


# Models write "miR‑21" with a non-breaking hyphen (U+2011), or an en dash, as
# often as with "-". Every regex here matches only ASCII "-", so such an answer
# looked like it named no candidates and cited nothing checkable (observed with
# GPT-OSS, 2026-10-21). Normalised on the way in; display text is untouched.
# GPT-OSS also writes full-width punctuation ("PMID：38612441"), which the
# identifier patterns did not match, so its citations went uncounted.
_HYPHENS = str.maketrans({**{c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2212\ufe63\uff0d"},
                          "\uff1a": ":", "\uff3b": "[", "\uff3d": "]", "\uff08": "(", "\uff09": ")"})


def normalize_text(text: str) -> str:
    return (text or "").translate(_HYPHENS)


def extract_citations(text: str) -> dict:
    """Pull PMIDs and DOIs out of free-text model output."""
    pmids = sorted(set(PMID_RE.findall(text or "")))
    dois = sorted({d.rstrip(".,;)").lower() for d in DOI_RE.findall(text or "")})
    return {"pmids": pmids, "dois": dois}


async def verify_pmid(client: httpx.AsyncClient, pmid: str) -> dict:
    key = _cache_key("pmid", pmid)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    # efetch rather than esummary: esummary has no abstract, and without one
    # the support check has only a title to reason from and abstains. This is
    # the fallback path, so a batch failure would otherwise silently degrade
    # every support verdict in the answer.
    papers, failed = [], False
    for attempt in range(2):        # one retry: NCBI throttling is often transient
        try:
            await _ncbi_limiter.wait()
            r = await client.get(f"{EUTILS}/efetch.fcgi",
                                 params=_ncbi_params(db="pubmed", id=pmid, retmode="xml"))
            r.raise_for_status()
            papers = _parse_pubmed_xml(r.text)
            failed = False
            break
        except Exception:
            failed = True
            if attempt == 0:
                await asyncio.sleep(0.8)

    if failed:
        # Unreachable is not the same as fabricated — never assert absence on
        # a network failure, or a flaky connection becomes a hallucination.
        out = {"id": pmid, "type": "pmid", "exists": None, "error": "LOOKUP_FAILED"}
    elif not papers:
        out = {"id": pmid, "type": "pmid", "exists": False}
    else:
        rec = papers[0].to_dict()
        out = {"id": pmid, "type": "pmid", "exists": True,
               "title": rec["title"], "journal": rec["journal"],
               "year": rec["year"], "abstract": rec["abstract"],
               "mesh": (rec.get("extra") or {}).get("mesh") or [],
               "retracted": bool((rec.get("extra") or {}).get("retracted")),
               "url": rec["url"] or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"}
    _cache_put(key, out)
    return out


async def _lookup_datacite(client: httpx.AsyncClient, doi: str) -> dict | None:
    """DataCite registers DOIs that Crossref does not: datasets, software,
    Zenodo records, and some preprints. Checking Crossref alone reports every
    one of those as fabricated — including this project's own Zenodo DOI."""
    try:
        r = await client.get(f"{DATACITE}/dois/{doi}",
                             headers={"Accept": "application/vnd.api+json"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        attrs = (r.json().get("data", {}) or {}).get("attributes", {}) or {}
        titles = attrs.get("titles") or [{}]
        return {"id": doi, "type": "doi", "exists": True,
                "title": _clean(titles[0].get("title", ""), 300),
                "journal": attrs.get("publisher", "") or "",
                "year": str(attrs.get("publicationYear", "") or ""),
                "registry": "datacite",
                "url": f"https://doi.org/{doi}"}
    except Exception:
        return None


async def verify_doi(client: httpx.AsyncClient, doi: str) -> dict:
    key = _cache_key("doi", doi)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    crossref_404 = False
    out = None
    try:
        r = await client.get(f"{CROSSREF}/works/{doi}")
        if r.status_code == 404:
            crossref_404 = True
        else:
            r.raise_for_status()
            msg = r.json().get("message", {}) or {}
            title = (msg.get("title") or [""])[0]
            issued = ((msg.get("issued", {}) or {}).get("date-parts") or [[""]])[0]
            out = {"id": doi, "type": "doi", "exists": True,
                   "title": _clean(title, 300),
                   "journal": (msg.get("container-title") or [""])[0],
                   "year": str(issued[0]) if issued else "",
                   "registry": "crossref",
                   "url": f"https://doi.org/{doi}"}
    except Exception:
        out = None

    if out is None:
        # Absent from Crossref is not the same as non-existent. Only declare a
        # DOI fabricated once the other major registry has also been asked.
        out = await _lookup_datacite(client, doi)
        if out is None:
            out = ({"id": doi, "type": "doi", "exists": False} if crossref_404
                   else {"id": doi, "type": "doi", "exists": None,
                         "error": "LOOKUP_FAILED"})

    _cache_put(key, out)
    return out


# Entities worth checking a citation against. A claim is usually "<RNA> does
# <something> in <disease>", so the RNA's name is the sharpest, least ambiguous
# token to match on.
# MIRN### is how MeSH writes them ("MIRN208 microRNA, mouse"); without it the
# curated headings never matched the prose form the models use.
MIRNA_RE = re.compile(r"\b(?:miR|microRNA|miRNA|MIRN|let)[-\s]?(\d+[a-z]?(?:-\d+[a-z]?p?)?)\b", re.I)

# Long non-coding RNAs have arbitrary names rather than a numbering scheme, so
# they cannot be matched by pattern. This covers the ones that actually recur
# in cardiovascular work; anything outside it falls back to miRNA matching and
# reports "unknown" rather than guessing.
LNCRNA_NAMES = {
    "braveheart", "bvht", "malat1", "neat1", "h19", "anril", "miat", "fendrr",
    "chaer", "wisper", "chast", "mhrt", "carl", "uca1", "hotair", "meg3",
    "kcnq1ot1", "linc00299", "tug1", "gas5", "xist", "lipcar", "sencr", "smilr",
}
LNC_RE = re.compile(r"\b(" + "|".join(sorted(LNCRNA_NAMES, key=len, reverse=True)) + r")\b", re.I)

# A claim often names the downstream target rather than only the RNA — "miR-21
# represses PTEN", "CDR132L raises SERCA2a". Matching on the RNA alone reported
# "unknown" for every such claim, which is the single commonest shape of a
# mechanistic statement in this field, so targets are matched too.
#
# Gene symbols are matched by shape (2-8 characters, upper-case with digits or
# a known suffix) rather than from a list, because no fixed list survives
# contact with a new paper. The stop-set below removes the acronyms that share
# that shape but are not genes — without it "RNA", "PCR" and "LV" matched.
# The trailing [a-z]? matters: SERCA2a, NFkB and Kir2.1 are written with a
# lower-case tail, and requiring all-caps silently skipped every one of them.
GENE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,7}[a-z]?(?:-[A-Z0-9]{1,3})?)\b")
NOT_GENES = {
    "RNA", "DNA", "MRNA", "MIRNA", "LNCRNA", "SIRNA", "PCR", "QPCR", "RTPCR",
    "CDNA", "UTR", "SNP", "KO", "WT", "LV", "RV", "EF", "LVEF", "HF", "HFREF",
    "HFPEF", "MI", "AMI", "TAC", "IR", "ROS", "ATP", "ADP", "CAD", "CVD", "ECG",
    "EKG", "MRI", "PET", "CT", "US", "FDA", "NIH", "PMID", "DOI", "AND", "OR",
    "NOT", "THE", "IN", "OF", "A", "I", "II", "III", "IV", "V", "VI", "NA", "KCL",
    "PBS", "ELISA", "WB", "IHC", "FACS", "SEM", "SD", "CI", "ANOVA", "NS", "BP",
    "HR", "AAV", "AAV9", "SHAM", "PI", "USD", "AI", "LLM", "GPT",
    # Journal, database and agency abbreviations, read as gene symbols before:
    # a claim "concerns BMJ" was compared with a paper that "concerns CINAHL".
    "BMJ", "JAMA", "NEJM", "PLOS", "PNAS", "JACC", "EHJ", "CINAHL", "EMBASE",
    "MEDLINE", "EPOC", "GRADE", "PRISMA", "WHO", "EU", "UK", "USA", "AHA", "ACC",
    "ESC", "AASLD", "NCBI", "URL", "HTML", "PDF", "PMCID",
    # Disease, drug, cell-type and cell-line abbreviations. "DIC" (doxorubicin-
    # induced cardiotoxicity) read as a gene made a correct citation look like
    # one about "DIC" checked against a paper about "AKT, PTEN" (2026-10-23).
    "DIC", "DOX", "DOXO", "DCM", "HCM", "ICM", "ACM", "AF", "PAH", "IRI", "MIRI",
    "CM", "CMS", "NRCM", "NRCMS", "NMCM", "NMCMS", "AMVC", "AMVCS", "IPSC",
    "IPSCS", "HIPSC", "HIPSCS", "MSC", "MSCS", "BMSC", "BMSCS", "BM", "EV", "EVS",
    "EXO", "EXOS", "AC16", "H9C2", "HL-1", "HUVEC", "HUVECS", "HEK293", "NTA",
    "TEM",
}


def _targets(text: str) -> set[str]:
    """Gene and protein symbols named in a piece of text."""
    return {g.upper() for g in GENE_RE.findall(text or "") if g.upper() not in NOT_GENES}


def _mirnas(text: str) -> set[str]:
    """Normalise miR-21, microRNA-21, miR 21 → '21' so they compare equal.

    The prefix is kept, because let-7a and miR-7a are different molecules and
    folding both to '7a' made a let-7 paper look like support for a miR-7 claim.
    """
    out = set()
    for m in MIRNA_RE.finditer(text or ""):
        prefix = "let" if m.group(0).lower().lstrip("([ ").startswith("let") else "mir"
        out.add(f"{prefix}-{m.group(1).lower()}")
    return out


def _mir_family(name: str) -> str:
    """The family a miRNA belongs to: mir-21-5p → mir-21, mir-29b → mir-29.

    Authors write the precise form in a claim ("miR-21-5p", "miR-29b") while
    the paper's title carries the bare family name ("microRNA-21", "miR-29").
    Compared literally those never match, which reported five of eight
    realistic citations as unsupported — the arm suffix and the family letter
    are the single biggest source of false accusations here.
    """
    name = re.sub(r"-[35]p$", "", name)          # drop the arm: -5p / -3p
    return re.sub(r"^(mir|let)-(\d+)[a-z]?(?:-\d+)?$", r"\1-\2", name)


def _rna_entities(text: str) -> set[str]:
    """Every RNA named in a piece of text, miRNA or lncRNA, normalised."""
    ents = set(_mirnas(text))
    ents |= {m.lower() for m in LNC_RE.findall(text or "")}
    return ents


HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s+.+|\*\*[^*\n]+\*\*:?)\s*$")


def _section_heading(text: str, pos: int) -> str:
    """The nearest heading line before pos: a markdown '#' heading or a line
    that is bold on its own ("**1. miR-9-5p (miRNA)**"). Empty if none."""
    for line in reversed(text[:max(pos, 0)].splitlines()):
        if HEADING_RE.match(line):
            return line.strip()
    return ""


def _claim_context(text: str, marker: str, window: int = 400) -> str:
    """The claim a citation is attached to.

    Answers are usually a list, one candidate per bullet, so the claim is the
    bullet the citation sits in. Prefer that block — a fixed character window
    bleeds into the *previous* bullet and mixes up which miRNA was being cited.
    """
    i = text.find(marker)
    if i < 0:
        return ""

    # Walk back to the start of this bullet/paragraph, not just N characters.
    start = max(text.rfind("\n\n", 0, i), text.rfind("\n", 0, i))
    if start < 0 or i - start > window:
        start = max(0, i - window)
    end = text.find("\n", i)
    if end < 0:
        end = len(text)
    block = text[start:end].strip()

    if _rna_entities(block):
        return block

    # No RNA on this line. If it is a wrapped continuation of the bullet above
    # ("  *Thum et al., 2008.* PMID: …"), widen to pick up the claim it belongs
    # to. If it starts its own list item, it is a separate statement — widening
    # would borrow the previous bullet's miRNA and report a mismatch against a
    # claim the citation was never attached to. Better to report nothing.
    if re.match(r"^(?:[-*•]|\d+[.)])\s", block):
        # ...unless the list sits under a heading naming the candidate
        # ("#### miR-495-3p" then "- **Cell lines studied**: … [PMID]"). The
        # heading scopes every bullet below it, so it is the claim's subject;
        # without it the bullet was matched on stray acronyms instead.
        heading = _section_heading(text, start)
        if _rna_entities(heading):
            return f"{heading}\n{block}"
        return block
    return text[max(0, i - window): end]


def assess_citation_support(claim_context: str, paper: dict) -> dict:
    """Does the cited paper plausibly support the claim it was attached to?

    Existence is not support. A model can cite a real PMID that has nothing to
    do with its claim — observed in testing: three real PMIDs (a liver enzyme
    paper, a prion paper, an ion-channel paper) attached to microRNA claims.
    Existence-checking alone scored that as a clean 0% hallucination rate.

    This compares the miRNA named in the claim against the miRNAs named in the
    paper's own title and abstract. Deterministic and conservative: it reports
    'unknown' rather than guessing whenever there is nothing concrete to match.
    """
    claimed = _rna_entities(claim_context)
    haystack = f"{paper.get('title','')} {paper.get('abstract','')}"
    mesh = " ".join(paper.get("mesh") or [])

    if not haystack.strip():
        return {"support": "unknown", "reason": "no title/abstract retrieved"}

    # No RNA in the claim does not mean nothing to check. Most mechanistic
    # statements name a downstream target ("represses PTEN", "raises SERCA2a"),
    # and MeSH headings describe what the paper is indexed as being about.
    # Falling straight to "unknown" here abstained on a large share of perfectly
    # checkable claims.
    if not claimed:
        claim_targets = _targets(claim_context)
        if claim_targets:
            paper_targets = _targets(f"{haystack} {mesh}")
            hit = claim_targets & paper_targets
            if hit:
                return {"support": "supported",
                        "reason": f"paper discusses {', '.join(sorted(hit))}",
                        "matched": sorted(hit), "match_level": "target"}
            if paper_targets:
                return {"support": "mismatched",
                        "reason": (f"claim concerns {', '.join(sorted(claim_targets)[:4])} but the "
                                   f"paper concerns {', '.join(sorted(paper_targets)[:4])}"),
                        "claimed": sorted(claim_targets)[:6],
                        "paper_about": sorted(paper_targets)[:6]}
        return {"support": "unknown", "reason": "no RNA or gene target named in the claim"}

    # MeSH headings are curated by indexers and name the molecule even when the
    # abstract only alludes to it, so they are searched alongside the text.
    found = _rna_entities(f"{haystack} {mesh}")
    overlap = claimed & found
    if overlap:
        return {"support": "supported",
                "reason": f"paper mentions {', '.join(sorted(overlap))}",
                "matched": sorted(overlap)}

    # Nothing matched exactly. Before calling it a miscitation, compare at the
    # family level: a claim about miR-21-5p cited to a paper titled
    # "MicroRNA-21 …" is correctly cited, and calling that a mismatch is a
    # false accusation. The relation is named in the reason so a reader can
    # see it was a family match rather than an exact one.
    fam_claimed = {_mir_family(c) for c in claimed}
    fam_found = {_mir_family(f) for f in found}
    fam_overlap = fam_claimed & fam_found
    if fam_overlap:
        return {"support": "supported",
                "reason": (f"paper covers the {', '.join(sorted(fam_overlap))} family "
                           f"({', '.join(sorted(found))}); claim is about "
                           f"{', '.join(sorted(claimed))}"),
                "matched": sorted(fam_overlap), "match_level": "family"}

    if found:
        return {"support": "mismatched",
                "reason": (f"claim is about {', '.join(sorted(claimed))} but the paper "
                           f"is about {', '.join(sorted(found))}"),
                "claimed": sorted(claimed), "paper_about": sorted(found)}

    # Nothing matched and the paper names no RNA at all. With an abstract in
    # hand that is a real finding. With only a title it is not — plenty of
    # papers never name their molecule in the title — so abstain rather than
    # accuse, since this verdict feeds both the miscitation rate and the
    # judge's evidence score.
    if not paper.get("abstract"):
        return {"support": "unknown",
                "reason": "no abstract available to check the claim against"}
    return {"support": "unrelated",
            "reason": (f"claim is about {', '.join(sorted(claimed))}; the cited paper "
                       f"does not mention it")}


# ── Full-text second look ──────────────────────────────────────────────────
# The support check reads the title and abstract, which is all PubMed gives.
# A review's abstract rarely names every molecule it covers: "MicroRNAs and
# Cardiovascular Disease Risk" (PMID 38206553) discusses miR-146a and miR-155
# in its body but names neither in its abstract, and was reported as "does not
# mention it". For a flagged citation, the open-access full text (Europe PMC)
# is checked before the verdict stands.
FULLTEXT_CHECKS = 8                    # flagged citations re-checked per answer

# Bumped whenever a change would give a different verdict on the same answer,
# so clients holding a saved result from an older checker re-run it instead of
# showing a stale verdict. 2 = full-text second look, topic-checked Crossref
# fallback, author-name fixes; 3 = NCBI PubMed Central as a second source;
# 4 = candidate heading as claim context, disease/cell-line acronyms not genes.
CHECKER_VERSION = 4
FULLTEXT_MAX_CHARS = 600_000


# What a full-text source reports: the article XML, None when it has no open
# full text for this paper, or _FAILED when the lookup itself failed (worth
# retrying later, so not cached).
_FAILED = object()


async def _europepmc_fulltext(client: httpx.AsyncClient, pmid: str):
    try:
        r = await client.get(f"{EUROPEPMC}/search", params={
            "query": f"EXT_ID:{pmid} AND SRC:MED", "format": "json", "resultType": "lite"})
        r.raise_for_status()
        items = (r.json().get("resultList", {}) or {}).get("result", []) or []
        it = items[0] if items else {}
        if not it.get("pmcid") or it.get("isOpenAccess") != "Y":
            return None
        x = await client.get(f"{EUROPEPMC}/{it['pmcid']}/fullTextXML")
        x.raise_for_status()
        return x.text
    except Exception:
        return _FAILED


async def _ncbi_fulltext(client: httpx.AsyncClient, pmid: str):
    """The same PubMed Central text, from NCBI: PMID → PMC id → article XML.

    The backup when Europe PMC is down or does not return the paper. Some
    PubMed Central articles come back without their body (the publisher does
    not allow full-text download); a title page is not a full text, so that
    counts as none.
    """
    try:
        await _ncbi_limiter.wait()
        r = await client.get(f"{EUTILS}/elink.fcgi", params=_ncbi_params(
            dbfrom="pubmed", db="pmc", id=pmid, retmode="json"))
        r.raise_for_status()
        pmc = [link for ls in r.json().get("linksets", [])
               for db in ls.get("linksetdbs", []) if db.get("linkname") == "pubmed_pmc"
               for link in db.get("links", [])]
        if not pmc:
            return None
        await _ncbi_limiter.wait()
        x = await client.get(f"{EUTILS}/efetch.fcgi", params=_ncbi_params(db="pmc", id=pmc[0]))
        x.raise_for_status()
        return x.text if "<body" in x.text else None
    except Exception:
        return _FAILED


async def _fulltext_terms(client: httpx.AsyncClient, pmid: str) -> str | None:
    """The RNA and gene names in a paper's open-access full text, as one
    string, or None when there is no open full text (or the lookups failed).

    Europe PMC first, NCBI's PubMed Central second: both serve the same
    open-access archive, so the second matters when the first is down or
    misses a paper. No AI is involved — free public APIs and text matching.
    Only the names are kept, not the text: a review runs to hundreds of KB,
    and the support check needs nothing else from it.
    """
    key = _cache_key("fulltext", pmid)
    hit = _cache_get(key)
    if hit is not None:
        return hit or None
    outcomes = []
    for source in (_europepmc_fulltext, _ncbi_fulltext):
        xml = await source(client, pmid)
        if isinstance(xml, str):
            body = normalize_text(re.sub(r"<[^>]+>", " ", xml[:FULLTEXT_MAX_CHARS]))
            terms = " ".join(sorted(_rna_entities(body)) + sorted(_targets(body)))
            _cache_put(key, terms)
            return terms
        outcomes.append(xml)
    if all(o is None for o in outcomes):
        _cache_put(key, "")              # both say: no open full text
    return None


async def _second_look(client: httpx.AsyncClient, cite: dict, claim_context: str) -> None:
    """Re-check a citation flagged from its abstract, in place."""
    pmid = cite.get("pmid") or (cite["id"] if cite.get("type") == "pmid" else "")
    terms = await _fulltext_terms(client, pmid) if pmid else None
    if terms:
        res = assess_citation_support(claim_context, {"title": cite.get("title", ""),
                                                      "abstract": terms})
        if res["support"] == "supported":
            claimed = {_mir_family(e) for e in _rna_entities(claim_context)}
            present = {_mir_family(e) for e in _rna_entities(terms)}
            missing = sorted(claimed - present)
            reason = f"the full text discusses {', '.join(res.get('matched') or [])}"
            if missing:
                reason += f"; it does not mention {', '.join(missing)}"
            cite["support"] = {**res, "reason": reason, "source": "full text"}
        else:
            cite["support"] = {**cite["support"], "source": "full text",
                               "reason": cite["support"]["reason"] + " (full text checked too)"}
        return
    # No open full text. A paper on the claim's subject may well discuss the
    # molecule in its body, so an absence from the abstract alone is not a
    # miscitation. A paper on another subject (an ADHD study cited for miR-21)
    # still is.
    shared, _ = _topical_overlap(claim_context, cite)
    if shared >= 2:
        cite["support"] = {"support": "unknown", "source": "abstract",
                           "reason": "not named in the title or abstract, and the full "
                                     "text is not openly available to check"}


# Narrative citations: "Klattenhoff et al., Cell, 2013". Models cite this way
# constantly, and checking only PMIDs/DOIs gave those answers a free pass —
# a model that cites in prose looked like it cited nothing, while one that
# supplied PMIDs got scrutinised. That biases the comparison, so these are
# resolved too.
# The optional particle keeps "van Rooij" whole. Captured as "Rooij", the
# PubMed author search missed the paper and the citation came back unfound.
# Initials after the surname ("Boon RA et al.", "van Rooij E, et al.") are
# skipped: without that, "RA" and "AJ" were taken as surnames, and "AJ 2010"
# resolved to an unrelated paper on neighbourhood safety.
# In "Yang B, Lin H, et al." the first author is the one PubMed indexes the
# paper under, so co-authors listed before "et al." are matched but skipped.
_SURNAME = (r"(?:(?:[Vv]an|[Vv]on|[Dd]e|[Dd]er|[Dd]en|[Dd]i|[Dd]a|[Dd]u|[Ll]e|[Ll]a|[Dd]el|[Dd]os)\s+){0,2}"
            r"[A-Z][a-zA-ZÀ-ɏ'\-]{1,24}")
_INITIALS = r"(?:\s+[A-Z]{1,3}\.?){0,2}"
NARRATIVE_RE = re.compile(
    rf"\b({_SURNAME}){_INITIALS}(?:,\s*{_SURNAME}{_INITIALS}){{0,3}},?\s*(?:et\s+al\.?|and\s+colleagues)"
    r"[^.\n;]{0,90}?\b((?:19|20)\d{2})\b")


def extract_narrative_citations(text: str) -> list[dict]:
    """Author-year citations, with the journal name if one is nearby."""
    out, seen = [], set()
    for m in NARRATIVE_RE.finditer(text or ""):
        author, year = m.group(1), m.group(2)
        key = (author.lower(), year)
        if key in seen:
            continue
        seen.add(key)
        middle = text[m.end(1):m.start(2)]
        journal = re.sub(r"(?:et al\.?|and colleagues)|[,.;*_()\[\]]", " ", middle)
        out.append({
            "author": author, "year": year,
            "journal": re.sub(r"\s+", " ", journal).strip()[:60],
            "raw": m.group(0).strip(),
        })
    return out


def _plausible_journal(s: str) -> str:
    """The text between "et al." and the year is usually the journal, but not
    always — "Thum et al. showed miR-21 drives fibrosis in 2008" puts a clause
    there. Feeding that to PubMed's [ta] field silently returns nothing, so
    anything that doesn't look like a journal name is dropped."""
    s = (s or "").strip(" ,.;:-")
    if not s or len(s) > 40 or len(s.split()) > 5 or re.search(r"\d", s):
        return ""
    return s


# Progressively weaker PubMed queries. Each adds a constraint the one below it
# lacks, so the first hit is also the best-evidenced one.
_NARR_TRIES = ("author+year+journal+topic", "author+year+topic",
               "author+year+journal", "author+year")


async def _pubmed_narrative(client: httpx.AsyncClient, cite: dict,
                            entities: set) -> tuple[dict | None, str]:
    """Find the PMID behind an author-year citation.

    PubMed rather than Crossref, because Crossref's relevance ranking is
    subject-agnostic: asked for "Thum Nature 2008 miR-21" it returns other
    people's miR-21 papers, and the one Thum 2008 record it does surface is a
    different paper in a different journal — which an author+year rule would
    have accepted. A fielded PubMed query pins the right record instead.
    """
    author = re.sub(r"[^A-Za-zÀ-ɏ'\- ]", "", cite["author"]).strip()
    year, journal = cite["year"], _plausible_journal(cite.get("journal", ""))
    topic = " OR ".join(sorted(entities)[:4])
    base = f'{author}[au] AND {year}[dp]'

    queries = {
        "author+year+journal+topic": f'{base} AND "{journal}"[ta] AND ({topic})' if (journal and topic) else None,
        "author+year+topic": f'{base} AND ({topic})' if topic else None,
        "author+year+journal": f'{base} AND "{journal}"[ta]' if journal else None,
        "author+year": base,
    }

    async def _get(path: str, **params):
        """One E-utilities call, retried once. NCBI drops a request now and
        then under sustained use; without a retry that surfaces as "lookup
        failed" on a citation that is perfectly findable a second later."""
        for attempt in (0, 1):
            try:
                await _ncbi_limiter.wait()
                r = await client.get(f"{EUTILS}/{path}", params=_ncbi_params(**params))
                r.raise_for_status()
                return r
            except Exception:
                if attempt:
                    return None
                await asyncio.sleep(0.8)
        return None

    async def esearch(term: str) -> list | None:
        """None means the lookup failed; [] means PubMed genuinely has nothing."""
        r = await _get("esearch.fcgi", db="pubmed", term=term,
                       retmode="json", retmax=5, sort="relevance")
        if r is None:
            return None
        try:
            return r.json().get("esearchresult", {}).get("idlist", []) or []
        except Exception:
            return None

    # Two passes over the ladder. Under load NCBI answers 200 with an empty
    # idlist instead of an error, which reads identically to "no such paper" —
    # left alone that turns a throttle into a fabrication claim, and the same
    # citation verified twice gives two different verdicts. A second pass after
    # a backoff is only paid on the all-empty path, which is exactly the case
    # about to be reported as unfindable.
    for attempt in (0, 1):
        if attempt:
            await asyncio.sleep(0.8)
        for level in _NARR_TRIES:
            term = queries.get(level)
            if not term:
                continue
            ids = await esearch(term)
            if ids is None:
                return None, "LOOKUP_FAILED"

            # Author + year alone is too blunt to pick between candidates —
            # that is exactly the Thum case above — so it only counts when it
            # is unambiguous.
            if not ids or (level == "author+year" and len(ids) != 1):
                continue

            r = await _get("efetch.fcgi", db="pubmed", id=ids[0], retmode="xml")
            if r is None:
                return None, "LOOKUP_FAILED"
            papers = _parse_pubmed_xml(r.text)
            if papers:
                return papers[0].to_dict(), level

    return None, ""


async def _crossref_narrative(client: httpx.AsyncClient, cite: dict,
                              entities: set, claim_context: str = "") -> dict | None:
    """Fallback for venues PubMed does not index — books, society abstracts,
    non-biomedical journals. Scoped by author so the year check means something.
    """
    bib = " ".join(filter(None, [cite.get("journal", ""), cite["year"],
                                 " ".join(sorted(entities)[:4])]))
    try:
        r = await client.get(f"{CROSSREF}/works", params={
            "query.author": cite["author"], "query.bibliographic": bib, "rows": 5,
            "select": "title,author,container-title,issued,DOI,type",
        })
        r.raise_for_status()
        items = (r.json().get("message", {}) or {}).get("items", []) or []
    except Exception:
        return None

    journal = _plausible_journal(cite.get("journal", "")).lower()
    claim_targets = _targets(claim_context)
    best = None
    for it in items:
        first = ((it.get("author") or [{}])[0].get("family") or "").lower()
        parts = ((it.get("issued", {}) or {}).get("date-parts") or [[None]])[0] or [None]
        if first != cite["author"].lower() or str(parts[0] or "") != cite["year"]:
            continue
        title = _clean((it.get("title") or [""])[0], 300)
        # Same surname and year is not the same paper. Accepted on that alone,
        # "Thum 2008" resolved to "Cardiac Dissonance Without Conductors" and
        # "Boon 2013" to a paper on pipe-laying ships, both shown as Real. The
        # title must name the molecule or a target from the claim; otherwise
        # the citation stays unconfirmed for a human to check.
        title_rnas = {_mir_family(e) for e in _rna_entities(title)}
        on_topic = (title_rnas & {_mir_family(e) for e in entities}) or \
                   (claim_targets and claim_targets & _targets(title))
        if not on_topic:
            continue
        container = (it.get("container-title") or [""])[0]
        # A conference abstract and the full paper it became often share author
        # and year; prefer the paper, and prefer the journal the model named.
        score = 0
        if journal and journal in container.lower():
            score += 2
        if not title.lower().startswith("abstract"):
            score += 1
        cand = {"title": title, "journal": container, "year": cite["year"],
                "doi": it.get("DOI", ""),
                "url": f"https://doi.org/{it['DOI']}" if it.get("DOI") else ""}
        if best is None or score > best[0]:
            best = (score, cand)
    return best[1] if best else None


async def resolve_narrative(client: httpx.AsyncClient, cite: dict,
                            claim_context: str = "") -> dict:
    """Resolve an author-year citation to a real paper.

    Confidence is lower than an exact identifier lookup by nature, so the
    match level is reported alongside the result rather than hidden, and
    anything unresolved is reported as unconfirmed rather than guessed at —
    the cost of a false accusation here is much higher than a missed detection.
    Resolving to a PMID means these get the same support check as an explicit
    identifier, so citing in prose is no longer a way to dodge scrutiny.
    """
    entities = _rna_entities(claim_context)
    key = _cache_key("narr2", cite["author"], cite["year"],
                     cite.get("journal", ""), " ".join(sorted(entities)))
    hit = _cache_get(key)
    if hit is not None:
        return hit

    out = {"type": "narrative", "id": cite["raw"],
           "author": cite["author"], "year": cite["year"]}

    paper, level = await _pubmed_narrative(client, cite, entities)
    if level == "LOOKUP_FAILED":
        out.update({"exists": None, "error": "LOOKUP_FAILED"})
        return out                                   # transient — do not cache

    if paper:
        out.update({"exists": True, "title": paper["title"],
                    "journal": paper["journal"], "year": paper["year"] or cite["year"],
                    "abstract": paper["abstract"], "pmid": paper["pmid"],
                    "mesh": (paper.get("extra") or {}).get("mesh") or [],
                    "retracted": bool((paper.get("extra") or {}).get("retracted")),
                    "doi": paper["doi"], "url": paper["url"], "match": level})
        _cache_put(key, out)
        return out

    cross = await _crossref_narrative(client, cite, entities, claim_context)
    if cross:
        out.update({"exists": True, **cross, "match": "crossref author+year"})
        _cache_put(key, out)
        return out

    # Neither index has a record by this author in this year. Suggestive, but
    # not proof of fabrication — coverage is incomplete, so this is flagged for
    # a human rather than counted against the model.
    out.update({"exists": None, "error": "UNCONFIRMED",
                "reason": "no PubMed or Crossref record by this author in this year"})
    _cache_put(key, out)
    return out


# ── Unsourced claims ───────────────────────────────────────────────────────
#
# Verifying citations only measures the claims a model chose to cite. A model
# that asserts "miR-29 reverses fibrosis" with no identifier at all scores a
# perfect citation record, because there is nothing to check — which rewards
# saying less. These functions ask the opposite question: for a claim with no
# citation, does supporting literature exist at all?
#
# This is evidence lookup, not citation verification, and the two are reported
# separately. Finding no paper is a prompt to look closer, not proof the claim
# is wrong: PubMed phrasing differs from prose, and absence here is weak.

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "been",
    "are", "was", "were", "which", "their", "its", "can", "may", "also", "both",
    "more", "most", "such", "than", "then", "these", "those", "into", "via",
    "role", "roles", "shown", "show", "shows", "study", "studies", "data",
    "evidence", "suggest", "suggests", "potential", "promising", "important",
    "key", "novel", "target", "targets", "therapeutic", "treatment", "patients",
    "human", "mice", "mouse", "model", "models", "levels", "expression",
    # Verbs and intensifiers: they carry the claim's grammar, not its subject,
    # and searching for them returned nothing for claims that are well covered.
    "drives", "drive", "reverses", "reverse", "induces", "induce", "causes",
    "cause", "regulates", "regulate", "promotes", "promote", "inhibits",
    "inhibit", "reduces", "reduce", "increases", "increase", "improves",
    "improve", "completely", "significantly", "strongly", "directly",
    "through", "within", "across", "between", "during", "after", "before",
    "established", "represents", "remains", "appears", "contributes",
}
MAX_UNSOURCED_CHECKS = 6          # each costs two NCBI calls; keep it bounded


def extract_unsourced_claims(text: str) -> list[dict]:
    """Lines that make a molecular claim but carry no citation of any kind."""
    cited_spans = []
    for rx in (PMID_RE, DOI_RE, NARRATIVE_RE):
        cited_spans += [m.group(0) for m in rx.finditer(text or "")]

    out = []
    for raw_line in (text or "").splitlines():
        # Markdown would otherwise reach both the PubMed query and the screen
        # ("Regulatory axis:** miR-21 → PTEN↓").
        line = re.sub(r"\*\*|__|`|^#+\s*", "", raw_line).strip(" -*•\t")
        if len(line) < 25:
            continue
        if any(span in raw_line for span in cited_spans):
            continue                      # this line already cites something
        ents = _rna_entities(line)
        if not ents:
            continue                      # no molecule named — nothing to look up
        out.append({"claim": line[:300], "entities": sorted(ents)})
        if len(out) >= MAX_UNSOURCED_CHECKS:
            break
    return out


def _claim_query(claim: dict) -> str:
    """A PubMed query from the molecule plus the claim's distinctive words."""
    ents = []
    for e in claim["entities"][:3]:
        if e.startswith(("mir-", "let-")):
            num = e.split("-", 1)[1]
            ents.append(f'("miR-{num}" OR "microRNA-{num}")')
        else:
            ents.append(f'"{e}"')

    words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", claim["claim"].lower())
             if w not in _STOPWORDS and not w.startswith(("mir", "microrna"))]
    # The first few content words of a claim are reliably the ones carrying its
    # meaning; dict.fromkeys preserves that order while dropping repeats.
    # Broad on the query, strict on the result. ANDing the topic words missed
    # claims that are well covered under different phrasing; ORing them
    # returned a MALAT1 immunology paper for a MALAT1 endothelial claim. So the
    # query casts wide and _topical_overlap below decides what actually counts.
    topic = " OR ".join(f'"{w}"' for w in list(dict.fromkeys(words))[:4])
    q = " AND ".join(filter(None, [" OR ".join(ents), f"({topic})" if topic else ""]))
    # NOT is its own operator in PubMed, not a modifier on AND. Writing
    # "AND NOT retracted publication[pt]" parses as a bare NOT and silently cut
    # 1,034 matches to 11; the phrase has to be quoted too.
    return q + ' NOT "retracted publication"[pt]'


def _topical_overlap(claim_text: str, paper: dict) -> tuple[int, list]:
    """How many of the claim's subject words the paper actually discusses."""
    words = {w for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", claim_text.lower())
             if w not in _STOPWORDS and not w.startswith(("mir", "microrna"))}
    hay = (f"{paper.get('title','')} {paper.get('abstract','')} "
           f"{' '.join(paper.get('mesh') or [])}").lower()
    hit = sorted(w for w in words if w in hay)
    return len(hit), hit[:5]


async def check_claim_evidence(client: httpx.AsyncClient, claim: dict) -> dict:
    """Does PubMed hold a paper that backs this uncited claim?"""
    key = _cache_key("unsourced", claim["claim"][:160])
    hit = _cache_get(key)
    if hit is not None:
        return hit

    out = {"claim": claim["claim"], "entities": claim["entities"]}
    try:
        await _ncbi_limiter.wait()
        r = await client.get(f"{EUTILS}/esearch.fcgi", params=_ncbi_params(
            db="pubmed", term=_claim_query(claim), retmode="json",
            retmax=3, sort="relevance"))
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", []) or []
    except Exception:
        out.update({"status": "lookup_failed"})
        return out                                   # transient — do not cache

    if not ids:
        out.update({"status": "none_found"})
        _cache_put(key, out)
        return out

    try:
        await _ncbi_limiter.wait()
        r = await client.get(f"{EUTILS}/efetch.fcgi", params=_ncbi_params(
            db="pubmed", id=",".join(ids[:3]), retmode="xml"))
        r.raise_for_status()
        papers = _parse_pubmed_xml(r.text)
    except Exception:
        out.update({"status": "lookup_failed"})
        return out

    # Take the first retrieved paper that actually concerns the claim, so a
    # keyword hit on an unrelated paper is not offered as support.
    for p in papers:
        d = p.to_dict()
        d["mesh"] = (d.get("extra") or {}).get("mesh") or []
        if (d.get("extra") or {}).get("retracted"):
            continue
        n_overlap, matched = _topical_overlap(claim["claim"], d)
        if (assess_citation_support(claim["claim"], d)["support"] == "supported"
                and n_overlap >= 2):
            out.update({"status": "related_found", "matched_terms": matched,
                        "pmid": p.pmid,
                        "title": p.title, "journal": p.journal, "year": p.year,
                        "url": p.url})
            _cache_put(key, out)
            return out

    out.update({"status": "none_found"})
    _cache_put(key, out)
    return out


async def check_unsourced_claims(text: str) -> dict:
    """Look for literature behind every uncited molecular claim in an answer."""
    claims = extract_unsourced_claims(normalize_text(text))
    if not claims:
        return {"checked": 0, "related_found": 0, "none_found": 0, "claims": []}

    async with httpx.AsyncClient(timeout=TIMEOUT,
                                 headers={"User-Agent": f"{NCBI_TOOL} (biomedical research)"}) as client:
        results = await asyncio.gather(
            *[check_claim_evidence(client, c) for c in claims], return_exceptions=True)

    out = [r for r in results if isinstance(r, dict)]
    return {
        "checked": len(out),
        "related_found": sum(1 for r in out if r.get("status") == "related_found"),
        "none_found": sum(1 for r in out if r.get("status") == "none_found"),
        "claims": out,
    }


# ── Candidate profiling ────────────────────────────────────────────────────
#
# The synthesis used to read the models' answers and nothing else, so a
# candidate was only as good as the prose describing it, and one named by a
# single model was easy to lose. This builds an independent evidence profile
# for every candidate any model proposed — literature volume, whether it has
# reached a trial, and how recent the work is — so the council weighs the
# candidates against the record rather than against each other's wording.

def extract_candidates(answers: dict) -> dict:
    """Every RNA any model named, with the set of models that named it.

    A union, deliberately. Ranking by how many models agreed would drop the
    candidate only one model thought of — which is exactly the candidate worth
    a second look, and exactly what the old synthesis kept losing.
    """
    found: dict[str, set] = {}
    for model, text in (answers or {}).items():
        for ent in _rna_entities(normalize_text(text)):
            found.setdefault(ent, set()).add(model)
    return {k: sorted(v) for k, v in found.items()}


def _display_name(ent: str) -> str:
    if ent.startswith("mir-"):
        return "miR-" + ent[4:]
    if ent.startswith("let-"):
        return "let-" + ent[4:]
    return ent.upper()


def _entity_query(ent: str) -> str:
    if ent.startswith(("mir-", "let-")):
        prefix, num = ent.split("-", 1)
        stem = "miR" if prefix == "mir" else "let"
        return f'("{stem}-{num}" OR "microRNA-{num}")' if prefix == "mir" else f'"{stem}-{num}"'
    return f'"{ent}"'


async def profile_candidate(client: httpx.AsyncClient, ent: str,
                            context: str = "") -> dict:
    """Independent evidence for one candidate: how much, how recent, in trials."""
    ctx_words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", (context or "").lower())
                 if w not in _STOPWORDS][:3]
    ctx = " OR ".join(f'"{w}"' for w in dict.fromkeys(ctx_words)) if ctx_words else ""

    key = _cache_key("profile", ent, ctx)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    out = {"entity": ent, "name": _display_name(ent)}
    term = _entity_query(ent) + (f" AND ({ctx})" if ctx else "")
    term += ' NOT "retracted publication"[pt]'

    try:
        await _ncbi_limiter.wait()
        r = await client.get(f"{EUTILS}/esearch.fcgi", params=_ncbi_params(
            db="pubmed", term=term, retmode="json", retmax=1, sort="relevance"))
        r.raise_for_status()
        res = r.json().get("esearchresult", {}) or {}
        out["papers"] = int(res.get("count") or 0)
    except Exception:
        out["papers"] = None            # unknown, not zero — never score a
                                        # candidate down for a network failure

    # Recent work separates a live research programme from a dormant one.
    try:
        await _ncbi_limiter.wait()
        r = await client.get(f"{EUTILS}/esearch.fcgi", params=_ncbi_params(
            db="pubmed", term=f"{term} AND 2021:3000[dp]", retmode="json", retmax=1))
        r.raise_for_status()
        out["papers_recent"] = int((r.json().get("esearchresult", {}) or {}).get("count") or 0)
    except Exception:
        out["papers_recent"] = None

    # A registered trial is the sharpest signal that a target is therapeutic
    # rather than merely interesting — no literature index carries it.
    #
    # query.intr, not query.term: free-text search returns anything that
    # mentions the molecule anywhere, which gave miR-132 a concussion study and
    # matched the lncRNA Wisper to an unrelated drug trial that happens to be
    # called WISPer. Searching the intervention field asks the question actually
    # being posed — is this molecule being given to patients — and returns the
    # two real CDR132L trials for miR-132.
    try:
        r = await client.get(f"{CTGOV}/studies", params={
            "query.intr": _display_name(ent), "pageSize": 3, "countTotal": "true"})
        r.raise_for_status()
        data = r.json()
        studies = data.get("studies", []) or []
        # pageSize only limits the examples returned; the count comes from
        # totalCount. Counting `studies` capped every candidate at 3 trials.
        out["trials"] = data.get("totalCount", len(studies))
        if studies:
            proto = studies[0].get("protocolSection", {}) or {}
            out["trial_example"] = _clean(
                (proto.get("identificationModule", {}) or {}).get("briefTitle", ""), 160)
    except Exception:
        out["trials"] = None

    _cache_put(key, out)
    return out


MAX_CANDIDATES = 12


async def profile_candidates(answers: dict, context: str = "") -> list[dict]:
    """Evidence profile for every candidate named across all the answers."""
    named = extract_candidates(answers)
    if not named:
        return []

    # Most-named first only to decide what fits in the budget; the council is
    # still shown the single-model candidates and told not to rank by consensus.
    order = sorted(named, key=lambda k: (-len(named[k]), k))[:MAX_CANDIDATES]

    async with httpx.AsyncClient(timeout=TIMEOUT,
                                 headers={"User-Agent": f"{NCBI_TOOL} (biomedical research)"}) as client:
        profiles = await asyncio.gather(
            *[profile_candidate(client, e, context) for e in order],
            return_exceptions=True)

    out = []
    for ent, p in zip(order, profiles):
        if not isinstance(p, dict):
            p = {"entity": ent, "name": _display_name(ent), "papers": None}
        p["proposed_by"] = named[ent]
        p["n_models"] = len(named[ent])
        out.append(p)
    return out


# Per answer. Real answers cite well under 20 papers; the cap bounds the
# Crossref/PubMed calls one request can trigger (DOIs are checked concurrently).
MAX_CITATIONS = 40
MAX_NARRATIVE_CITATIONS = 15


async def verify_citations(text: str) -> dict:
    """Check every citation in a model's answer against PubMed and Crossref.

    This is the objective half of the scoring loop: it needs no AI judgement
    and no wet-lab work, so it produces a per-model accuracy signal on day one.
    `exists: None` means the lookup failed — deliberately distinct from False,
    so a network problem is never recorded as a fabricated citation.
    """
    text = normalize_text(text)
    found = extract_citations(text)
    narrative = extract_narrative_citations(text)
    truncated = (len(found["pmids"]) > MAX_CITATIONS or len(found["dois"]) > MAX_CITATIONS
                 or len(narrative) > MAX_NARRATIVE_CITATIONS)
    found = {"pmids": found["pmids"][:MAX_CITATIONS], "dois": found["dois"][:MAX_CITATIONS]}
    narrative = narrative[:MAX_NARRATIVE_CITATIONS]
    ids = found["pmids"] + found["dois"]
    if not ids and not narrative:
        return {"checked": 0, "verified": 0, "fabricated": 0, "unresolved": 0,
                "mismatched": 0, "hallucination_rate": None,
                "miscitation_rate": None, "citations": [],
                "checker_version": CHECKER_VERSION}

    async with httpx.AsyncClient(timeout=TIMEOUT,
                                 headers={"User-Agent": f"{NCBI_TOOL} (biomedical research)"}) as client:
        # One efetch for every PMID rather than one per citation: fewer calls
        # against the NCBI rate limit, and it returns abstracts, which esummary
        # does not. The abstract is what makes support-checking possible.
        pmid_records = {}
        if found["pmids"]:
            try:
                await _ncbi_limiter.wait()
                r = await client.get(f"{EUTILS}/efetch.fcgi", params=_ncbi_params(
                    db="pubmed", id=",".join(found["pmids"]), retmode="xml"))
                r.raise_for_status()
                for paper in _parse_pubmed_xml(r.text):
                    if paper.pmid:
                        pmid_records[paper.pmid] = paper.to_dict()
            except Exception:
                pmid_records = {}

        cites = []
        for pmid in found["pmids"]:
            rec = pmid_records.get(pmid)
            if rec:
                cites.append({"id": pmid, "type": "pmid", "exists": True,
                              "title": rec["title"], "journal": rec["journal"],
                              "year": rec["year"], "abstract": rec["abstract"],
                              "mesh": (rec.get("extra") or {}).get("mesh") or [],
                              "retracted": bool((rec.get("extra") or {}).get("retracted")),
                              "url": rec["url"]})
            elif pmid_records or not found["pmids"]:
                # The batch call succeeded but this ID was absent → fabricated.
                cites.append({"id": pmid, "type": "pmid", "exists": False})
            else:
                # The batch call itself failed. Unreachable is not the same as
                # fabricated — never assert absence on a network failure.
                cites.append(await verify_pmid(client, pmid))

        # A paper is routinely cited with both identifiers ("PMID: 24602777,
        # DOI: 10.1161/…"). Those are one citation, not two — counting both
        # inflated `checked` above the number of papers actually cited.
        cited_dois = {(rec.get("doi") or "").lower() for rec in pmid_records.values()} - {""}
        fresh_dois = [d for d in found["dois"] if d.lower() not in cited_dois]

        doi_results = await asyncio.gather(
            *[verify_doi(client, d) for d in fresh_dois], return_exceptions=True)
        for r in doi_results:
            if not isinstance(r, dict):
                continue
            # The same paper can also surface as a DOI whose record matches a
            # PMID title — catch that too, since efetch does not always carry
            # the DOI for older records.
            if any((r.get("title") or "").lower()[:60] == (c.get("title") or "").lower()[:60]
                   for c in cites if c.get("title")):
                continue
            cites.append(r)

        # Author-year citations. Dropped when they resolve to a paper already
        # cited by identifier, so "miR-21 (Thum et al. 2008, PMID:19043405)"
        # counts once rather than twice.
        seen = {v for c in cites for v in
                (c.get("id", "").lower(), (c.get("title") or "").lower()[:60]) if v}
        narr_results = await asyncio.gather(
            *[resolve_narrative(client, n, _claim_context(text, n["raw"]))
              for n in narrative], return_exceptions=True)
        for nr in narr_results:
            if not isinstance(nr, dict):
                continue
            keys = {(nr.get("title") or "").lower()[:60], (nr.get("pmid") or "").lower(),
                    (nr.get("doi") or "").lower()} - {""}
            if keys & seen:
                continue
            seen |= keys
            cites.append(nr)

    # Second tier: for citations that do exist, does the paper actually concern
    # the claim it was attached to?
    flagged = []
    for c in cites:
        if c.get("exists") is True:
            ctx = _claim_context(text, c["id"])
            c["support"] = assess_citation_support(ctx, c)
            if c["support"]["support"] in ("mismatched", "unrelated"):
                flagged.append((c, ctx))
    if flagged:
        async with httpx.AsyncClient(timeout=TIMEOUT,
                                     headers={"User-Agent": f"{NCBI_TOOL} (biomedical research)"}) as client:
            await asyncio.gather(*[_second_look(client, c, ctx)
                                   for c, ctx in flagged[:FULLTEXT_CHECKS]],
                                 return_exceptions=True)
    for c in cites:
        c.pop("abstract", None)          # keep the payload small for the client
        c.pop("mesh", None)

    verified = sum(1 for c in cites if c.get("exists") is True)
    fabricated = sum(1 for c in cites if c.get("exists") is False)
    unresolved = sum(1 for c in cites if c.get("exists") is None)
    mismatched = sum(1 for c in cites
                     if (c.get("support") or {}).get("support") in ("mismatched", "unrelated"))
    # Counted separately: a retracted paper is real and usually on topic, so it
    # is neither a hallucination nor a miscitation — but it is the one source a
    # researcher most needs flagged.
    retracted = sum(1 for c in cites if c.get("retracted"))

    decided = verified + fabricated
    # Two distinct failure modes, deliberately reported separately:
    #   hallucination — the identifier does not exist at all
    #   miscitation   — the identifier is real but the paper does not support
    #                   the claim. This is the harder failure: it passes a
    #                   naive existence check, so a model can look perfectly
    #                   sourced while citing unrelated work.
    judged = sum(1 for c in cites
                 if (c.get("support") or {}).get("support") in
                 ("supported", "mismatched", "unrelated"))
    return {
        "checked": len(cites),
        "verified": verified,
        "fabricated": fabricated,
        "unresolved": unresolved,
        "mismatched": mismatched,
        "retracted": retracted,
        "hallucination_rate": round(fabricated / decided, 3) if decided else None,
        "miscitation_rate": round(mismatched / judged, 3) if judged else None,
        "truncated": truncated,
        "citations": cites,
        "checker_version": CHECKER_VERSION,
    }
