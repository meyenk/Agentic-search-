"""
sources/registry.py — Every free, no-key data source, exposed as a callable
tool with a description. The Search node's LLM picks which one(s) to call
based on the profile (region, track) and what's worked so far — we don't
hardcode "if region == UK use Arbeitnow", the model reads the descriptions
and decides.

Each function returns a list of raw dicts (not yet Candidate-shaped) —
normalization happens in search.py right after the call.
"""

import re
import time
import logging
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

REQUEST_HEADERS = {"User-Agent": "outreach-pipeline/1.0 (personal research tool)"}

DESCRIPTION_CHAR_BUDGET = 900

# Heading text (not body text) that conventionally marks a requirements/
# qualifications section across the ATS platforms Arbeitnow aggregates from
# (Greenhouse, SmartRecruiters, Team Tailor, Recruitee, Comeet, Join.com) —
# used only to find WHERE that section is, never to search for a specific
# requirement like "PhD". EN + DE since Arbeitnow skews Germany-heavy.
REQUIREMENTS_HEADING_KEYWORDS = [
    "qualifikation", "anforderungen", "dein profil", "ihr profil", "voraussetzungen",
    "das bringst du mit", "requirements", "qualifications", "what you bring",
    "what you'll bring", "about you", "who you are", "your profile",
    "skills and experience", "what we're looking for", "what you need",
]


def _extract_relevant_description(html: str, budget: int = DESCRIPTION_CHAR_BUDGET) -> str:
    """Prefers the posting's own requirements/qualifications section (found by
    its heading text) over a flat prefix. Arbeitnow passes through raw,
    ATS-sourced HTML that's usually broken into heading-delimited sections in
    whatever order the poster chose — a flat [:budget] slice from byte 0
    reliably misses whatever comes after the intro/tasks blurb, which is
    exactly what let a PhD-level role read as a good match once. Falls back
    to the old flat-slice behavior (now with tags stripped) whenever no
    heading looks like a requirements section, or there are no headings at
    all — not every source posting is this well-structured."""
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    headings = soup.find_all(["h1", "h2", "h3", "h4"])

    requirements_text = ""
    for h in headings:
        label = h.get_text(" ", strip=True).lower()
        if not any(kw in label for kw in REQUIREMENTS_HEADING_KEYWORDS):
            continue
        parts = []
        for sib in h.find_next_siblings():
            if sib.name in ("h1", "h2", "h3", "h4"):
                break
            parts.append(sib.get_text(" ", strip=True))
        requirements_text = " ".join(p for p in parts if p).strip()
        if requirements_text:
            break

    plain_text = soup.get_text(" ", strip=True)

    if not requirements_text:
        return plain_text[:budget]

    requirements_text = requirements_text[: budget - 40]
    prefix = f"[Qualifications/Requirements] {requirements_text}  "
    return (prefix + plain_text[: max(0, budget - len(prefix))]).strip()[:budget]


def _normalize_key(value) -> str:
    return re.sub(r"[\s_-]+", " ", str(value).strip().lower())


def _location_allowed(job_location_text: str, requested_location: str) -> bool:
    """Soft client-side location filter for sources whose API takes a
    `location` param in their docstring/signature but has no real server-side
    location filter to send it to (Arbeitnow, The Muse) — without this, the
    planner-supplied location is silently discarded and results skew toward
    wherever the source's underlying data happens to concentrate. Same
    fail-open philosophy as onsite_location_mismatch (defined below): only
    excludes a listing when both sides name a specific, disagreeing country;
    remote/unclear/unlabeled listings always pass through."""
    if not requested_location:
        return True
    return not onsite_location_mismatch(job_location_text, requested_location)


def _normalize_enum(value, mapping: dict, label: str) -> str | None:
    """Maps a planner-supplied value onto a source's real documented enum
    (case/spacing-insensitive), or returns None and logs a warning if it
    doesn't match anything — so a malformed or hallucinated filter value is
    silently dropped rather than sent to the API and either erroring or (worse)
    being silently ignored server-side with no signal that it did nothing."""
    if value is None or value == "":
        return None
    key = _normalize_key(value)
    normalized = mapping.get(key)
    if normalized is None:
        log.warning(f"  Unrecognized {label} value '{value}' — ignoring this filter.")
    return normalized


# ── JOB SOURCES ──────────────────────────────────────────────

def _search_arbeitnow_impl(base_url: str, source_tag: str, default_location: str,
                            query: str, location: str, visa_sponsorship) -> list[dict]:
    """Shared implementation for the two Arbeitnow boards (main .com and the
    UK-specific .co.uk one added in 2026) — same API shape, different data."""
    try:
        params = {}
        if visa_sponsorship is not None:
            if isinstance(visa_sponsorship, str):
                visa_sponsorship = visa_sponsorship.strip().lower() in ("true", "yes", "1")
            params["visa_sponsorship"] = "true" if visa_sponsorship else "false"

        r = requests.get(base_url, params=params, timeout=10, headers=REQUEST_HEADERS)
        data = r.json().get("data", [])
        out = []
        for job in data:
            text = f"{job.get('title','')} {job.get('description','')}".lower()
            if query.lower() not in text and not any(w in text for w in query.lower().split()):
                continue
            job_location = job.get("location", "")
            if not _location_allowed(job_location, location):
                continue
            out.append({
                "name": job.get("title", ""),
                "org": job.get("company_name", ""),
                "location": ", ".join(job_location.split(",")[:2]) if job_location else default_location,
                "url": job.get("url", ""),
                "description": _extract_relevant_description(job.get("description", "") or ""),
                "posted_date": job.get("created_at", ""),
                "source": source_tag,
            })
        return out[:15]
    except Exception as e:
        log.warning(f"{source_tag} search failed: {e}")
        return []


def search_arbeitnow(query: str, location: str = "", visa_sponsorship=None, **kwargs) -> list[dict]:
    """
    Arbeitnow (main board, arbeitnow.com) — free, no key, aggregates from ATS
    platforms (Greenhouse, SmartRecruiters, etc.) and skews Germany/DACH-heavy.
    Decent for broad-Europe tech & engineering roles, internships and
    graduate schemes — but NOT UK-specific; use search_arbeitnow_uk instead
    when the candidate's geography is the UK specifically, since that's a
    separate, dedicated UK board with actual UK listings. Supports an
    explicit visa_sponsorship=true/false filter (Arbeitnow's own documented
    API parameter) — use it when the candidate's dealbreakers require visa
    sponsorship, rather than guessing from description text.
    """
    return _search_arbeitnow_impl(
        "https://www.arbeitnow.com/api/job-board-api", "arbeitnow", "Remote",
        query, location, visa_sponsorship,
    )


def search_arbeitnow_uk(query: str, location: str = "", visa_sponsorship=None, **kwargs) -> list[dict]:
    """
    Arbeitnow UK (arbeitnow.co.uk) — free, no key, Arbeitnow's dedicated UK
    job board API, launched 2026, separate from the main arbeitnow.com board
    (which is DACH/Europe-heavy and largely misses UK roles). Use this one
    whenever the candidate's geography is the UK specifically — it's the
    Arbeitnow variant actually populated with UK-based tech & engineering
    roles, internships and graduate schemes. Same visa_sponsorship=true/false
    filter as the main board.
    """
    return _search_arbeitnow_impl(
        "https://www.arbeitnow.co.uk/api/job-board-api", "arbeitnow_uk", "United Kingdom",
        query, location, visa_sponsorship,
    )


def search_remoteok(query: str, location: str = "", **kwargs) -> list[dict]:
    """
    RemoteOK — free, no key, remote-only tech roles globally.
    Best when candidate is open to fully remote positions.
    """
    try:
        r = requests.get("https://remoteok.com/api", timeout=10, headers=REQUEST_HEADERS)
        data = r.json()
        out = []
        for job in data:
            if not isinstance(job, dict) or "position" not in job:
                continue
            text = f"{job.get('position','')} {job.get('description','')}".lower()
            if query.lower() not in text and not any(w in text for w in query.lower().split()):
                continue
            out.append({
                "name": job.get("position", ""),
                "org": job.get("company", ""),
                "location": "Remote",
                "url": job.get("url", ""),
                "description": (job.get("description", "") or "")[:600],
                "posted_date": job.get("date", ""),
                "source": "remoteok",
            })
        return out[:15]
    except Exception as e:
        log.warning(f"RemoteOK search failed: {e}")
        return []


MUSE_CATEGORY_VALUES = {
    _normalize_key(v): v for v in [
        "Accounting", "Accounting and Finance", "Account Management",
        "Account Management/Customer Success", "Administration and Office",
        "Advertising and Marketing", "Animal Care", "Arts", "Business Operations",
        "Cleaning and Facilities", "Computer and IT", "Construction", "Corporate",
        "Customer Service", "Data and Analytics", "Data Science", "Design",
        "Design and UX", "Editor", "Education", "Energy Generation and Mining",
        "Entertainment and Travel Services", "Farming and Outdoors",
        "Food and Hospitality Services", "Healthcare", "HR",
        "Human Resources and Recruitment", "Installation/Maintenance/Repairs", "IT",
        "Law", "Legal Services", "Management", "Manufacturing and Warehouse",
        "Marketing", "Mechanic", "Media/PR/Communications", "Mental Health",
        "Nurses", "Office Administration", "Personal Care and Services",
        "Physical Assistant", "Product", "Product Management",
        "Project Management", "Protective Services", "Public Relations",
        "Real Estate", "Recruiting", "Retail", "Sales",
        "Science and Engineering", "Social Services", "Software Engineer",
        "Software Engineering", "Sports/Fitness/Recreation",
        "Transportation and Logistics", "UX", "Videography", "Writer",
        "Writing and Editing",
    ]
}
MUSE_LEVEL_VALUES = {
    _normalize_key(v): v for v in ["Entry Level", "Mid Level", "Senior Level", "Management", "Internship"]
}


def search_themuse(query: str, location: str = "", category=None, level=None, **kwargs) -> list[dict]:
    """
    The Muse — free, no key, decent for grad schemes and early-career roles,
    mostly US with some international listings. `category` and `level` are
    The Muse's own documented filters, NOT free text — pass the single
    closest matching value, exactly as spelled here. category options:
    Computer and IT, Data Science, IT, Product, Product Management, Science
    and Engineering, Software Engineer, Software Engineering, UX, Design,
    Design and UX (plus many non-technical categories not worth listing
    here). level options: Entry Level, Mid Level, Senior Level, Management,
    Internship — set this from years of experience, e.g. under 2 years ->
    Entry Level, to filter out senior-track roles server-side rather than
    hoping "junior"/"graduate" appears in the query. The `query` string
    itself has no server-side equivalent here (The Muse has no keyword-search
    param) — it's matched client-side against title/description on top of
    whatever category/level filters are set.
    """
    try:
        params = {"page": 0}
        category_value = _normalize_enum(category, MUSE_CATEGORY_VALUES, "The Muse category")
        if category_value:
            params["category"] = category_value
        level_value = _normalize_enum(level, MUSE_LEVEL_VALUES, "The Muse level")
        if level_value:
            params["level"] = level_value

        r = requests.get("https://www.themuse.com/api/public/jobs",
                          params=params, timeout=10, headers=REQUEST_HEADERS)
        data = r.json().get("results", [])
        out = []
        for job in data:
            text = f"{job.get('name','')} {job.get('contents','')}".lower()
            if query and query.lower() not in text and not any(w in text for w in query.lower().split()):
                continue
            locations = ", ".join(l.get("name", "") for l in job.get("locations", []))
            if not _location_allowed(locations, location):
                continue
            out.append({
                "name": job.get("name", ""),
                "org": job.get("company", {}).get("name", ""),
                "location": locations,
                "url": job.get("refs", {}).get("landing_page", ""),
                "description": _extract_relevant_description(job.get("contents", "") or ""),
                "posted_date": job.get("publication_date", ""),
                "source": "themuse",
            })
        return out[:15]
    except Exception as e:
        log.warning(f"The Muse search failed: {e}")
        return []


def search_remotive(query: str, location: str = "", **kwargs) -> list[dict]:
    """
    Remotive — free, no key, remote-only tech/design/marketing roles globally.
    Different underlying board coverage than RemoteOK — use both if remote
    results are thin from one alone.
    """
    try:
        r = requests.get("https://remotive.com/api/remote-jobs",
                          params={"search": query}, timeout=10, headers=REQUEST_HEADERS)
        data = r.json().get("jobs", [])
        out = []
        for job in data:
            out.append({
                "name": job.get("title", ""),
                "org": job.get("company_name", ""),
                "location": job.get("candidate_required_location", "") or "Remote",
                "url": job.get("url", ""),
                "description": (job.get("description", "") or "")[:600],
                "posted_date": job.get("publication_date", ""),
                "source": "remotive",
            })
        return out[:15]
    except Exception as e:
        log.warning(f"Remotive search failed: {e}")
        return []


HIMALAYAS_SENIORITY_VALUES = {
    _normalize_key(v): v for v in
    ["Entry-level", "Mid-level", "Senior", "Manager", "Director", "Executive"]
}


def search_himalayas(query: str, location: str = "", seniority=None, **kwargs) -> list[dict]:
    """
    Himalayas — free, no key, remote-only jobs with a REAL country filter
    (ISO code, country name, or 'worldwide') AND a REAL seniority filter —
    Himalayas' own documented API param, one of exactly these values:
    Entry-level, Mid-level, Senior, Manager, Director, Executive. Set this
    from the candidate's actual years of experience whenever plausible (e.g.
    1 year -> Entry-level) instead of trusting title keywords like "junior"
    or "graduate" in the query string, which other sources don't reliably
    honor. Unlike RemoteOK/Remotive/Arbeitnow, this is the one job source
    that can actually be filtered by BOTH geography and seniority
    server-side — best pick for APAC/Middle East/non-Europe candidates who
    only want remote roles open to their region, or whenever a seniority
    mismatch has been a recurring problem in feedback.
    """
    try:
        params = {"q": query}
        if location:
            params["country"] = location
        seniority_value = _normalize_enum(seniority, HIMALAYAS_SENIORITY_VALUES, "Himalayas seniority")
        if seniority_value:
            params["seniority"] = seniority_value

        r = requests.get("https://himalayas.app/jobs/api/search",
                          params=params, timeout=10, headers=REQUEST_HEADERS)
        payload = r.json()
        data = payload if isinstance(payload, list) else (
            payload.get("jobs") or payload.get("data") or payload.get("results") or []
        )
        out = []
        for job in data[:15]:
            locs = job.get("locationRestrictions") or ["Worldwide"]
            out.append({
                "name": job.get("title", ""),
                "org": job.get("companyName", ""),
                "location": ", ".join(locs) if isinstance(locs, list) else str(locs),
                "url": job.get("applicationLink", ""),
                "description": _extract_relevant_description(job.get("excerpt", "") or ""),
                "posted_date": job.get("pubDate", ""),
                "source": "himalayas",
            })
        return out
    except Exception as e:
        log.warning(f"Himalayas search failed: {e}")
        return []


# ── ACADEMIC SOURCES ─────────────────────────────────────────

def search_openalex(query: str, location: str = "", **kwargs) -> list[dict]:
    """
    OpenAlex — free, no key, 10M+ researchers. Best broad academic source —
    filterable by topic and returns structured author + institution data.
    """
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={"search": query, "per_page": 15, "sort": "publication_date:desc"},
            timeout=12, headers=REQUEST_HEADERS,
        )
        data = r.json().get("results", [])
        out = []
        for work in data:
            for authorship in work.get("authorships", [])[:2]:  # first 2 authors only
                author = authorship.get("author", {})
                insts = authorship.get("institutions", [])
                inst_name = insts[0].get("display_name", "") if insts else ""
                inst_country = insts[0].get("country_code", "") if insts else ""
                if not author.get("display_name"):
                    continue
                out.append({
                    "name": author.get("display_name", ""),
                    "org": inst_name,
                    "location": inst_country,
                    "url": author.get("id", ""),
                    "description": f"Paper: {work.get('title','')} ({work.get('publication_year','')})",
                    "posted_date": str(work.get("publication_year", "")),
                    "source": "openalex",
                })
        return out[:15]
    except Exception as e:
        log.warning(f"OpenAlex search failed: {e}")
        return []


def search_dblp(query: str, location: str = "", **kwargs) -> list[dict]:
    """
    DBLP — free, no key, clean CS-specific author search by keyword.
    Good for cross-checking OpenAlex results against CS-specific publication record.
    """
    try:
        r = requests.get(
            "https://dblp.org/search/publ/api",
            params={"q": query, "format": "json", "h": 15},
            timeout=10, headers=REQUEST_HEADERS,
        )
        hits = r.json().get("result", {}).get("hits", {}).get("hit", [])
        out = []
        for hit in hits:
            info = hit.get("info", {})
            authors_raw = info.get("authors", {}).get("author", [])
            if isinstance(authors_raw, dict):
                authors_raw = [authors_raw]
            for a in authors_raw[:2]:
                name = a.get("text", "") if isinstance(a, dict) else str(a)
                if not name:
                    continue
                out.append({
                    "name": name,
                    "org": "",  # DBLP doesn't give affiliation directly
                    "location": "",
                    "url": info.get("ee", ""),
                    "description": f"Paper: {info.get('title','')} ({info.get('year','')})",
                    "posted_date": info.get("year", ""),
                    "source": "dblp",
                })
        return out[:15]
    except Exception as e:
        log.warning(f"DBLP search failed: {e}")
        return []


def search_semantic_scholar(query: str, location: str = "", **kwargs) -> list[dict]:
    """
    Semantic Scholar — free, no key. Returns paper authors with abstracts,
    useful for judging research relevance in more depth than DBLP/OpenAlex alone.
    """
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": query, "limit": 15, "fields": "title,year,authors,abstract"},
            timeout=10, headers=REQUEST_HEADERS,
        )
        papers = r.json().get("data", [])
        out = []
        for paper in papers:
            year = paper.get("year") or 0
            if year and year < 2020:
                continue
            for author in paper.get("authors", [])[:2]:
                name = author.get("name", "")
                if not name:
                    continue
                out.append({
                    "name": name,
                    "org": "",
                    "location": "",
                    "url": f"https://www.semanticscholar.org/author/{author.get('authorId','')}",
                    "description": f"{paper.get('title','')} — {(paper.get('abstract') or '')[:250]}",
                    "posted_date": str(year),
                    "source": "semantic_scholar",
                })
        return out[:15]
    except Exception as e:
        log.warning(f"Semantic Scholar search failed: {e}")
        return []


def search_arxiv(query: str, location: str = "", **kwargs) -> list[dict]:
    """
    arXiv — free, no key, official API. Best for the NEWEST preprints —
    surfaces papers before they're indexed by OpenAlex/Semantic Scholar, so
    it's the freshness-focused academic source, not a replacement for them.
    """
    try:
        import xml.etree.ElementTree as ET
        r = requests.get(
            "http://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "sortBy": "submittedDate",
                    "sortOrder": "descending", "max_results": 15},
            timeout=12, headers=REQUEST_HEADERS,
        )
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(r.text)
        out = []
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
            published = (entry.findtext("atom:published", default="", namespaces=ns) or "")[:10]
            paper_url = entry.findtext("atom:id", default="", namespaces=ns) or ""
            authors = entry.findall("atom:author", ns)[:2]  # first 2 authors only
            for a in authors:
                name = (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
                if not name:
                    continue
                out.append({
                    "name": name,
                    "org": "",  # arXiv doesn't give affiliation directly
                    "location": "",
                    "url": paper_url,
                    "description": f"Paper: {title} ({published}) — {summary[:250]}",
                    "posted_date": published,
                    "source": "arxiv",
                })
        return out[:15]
    except Exception as e:
        log.warning(f"arXiv search failed: {e}")
        return []


# ── REGISTRY ──────────────────────────────────────────────────
# This is what gets exposed to Gemini as function-calling tools.
# Descriptions matter a lot here — the model picks based on these.

JOB_SOURCES = {
    "search_arbeitnow": search_arbeitnow,
    "search_arbeitnow_uk": search_arbeitnow_uk,
    "search_remoteok": search_remoteok,
    "search_themuse": search_themuse,
    "search_remotive": search_remotive,
    "search_himalayas": search_himalayas,
}

ACADEMIC_SOURCES = {
    "search_openalex": search_openalex,
    "search_dblp": search_dblp,
    "search_semantic_scholar": search_semantic_scholar,
    "search_arxiv": search_arxiv,
}

ALL_SOURCES = {**JOB_SOURCES, **ACADEMIC_SOURCES}


# ── GEOGRAPHY GATING ─────────────────────────────────────────
# Which regions each source actually has usable coverage for. This is what
# makes geography gating STRUCTURAL rather than a hint left for the model to
# maybe notice — a source whose coverage doesn't overlap the candidate's
# region is never even offered as an option, rather than being offered and
# hoping the model skips it.
#
# "remote_global" / "global" sources are never region-limited and always
# pass the gate. Everything else lists its actual known coverage — kept
# honest and narrow rather than optimistic, since an overclaimed source just
# wastes a search round on empty results.

COVERAGE = {
    "search_arbeitnow": {"europe"},
    "search_arbeitnow_uk": {"europe"},
    "search_remoteok": {"remote_global"},
    "search_themuse": {"us", "remote_global"},
    "search_remotive": {"remote_global"},
    "search_himalayas": {"remote_global"},
    "search_openalex": {"global"},
    "search_dblp": {"global"},
    "search_semantic_scholar": {"global"},
    "search_arxiv": {"global"},
}

# Geographies with NO free, no-key source today — onsite (not remote) roles
# in these regions are a known, real gap. Surfaced explicitly in the report
# rather than silently returning nothing (see nodes/report.py). The only
# programmatic access to boards like Bayt/GulfTalent/NaukriGulf is paid
# third-party scraping — out of scope while this stays free-only.
KNOWN_COVERAGE_GAPS = {
    "middle_east": "No free source for onsite Middle East/Gulf roles (Bayt, GulfTalent, "
                    "NaukriGulf have no free API). Himalayas covers REMOTE roles open to "
                    "Middle East candidates, but not onsite Gulf hiring.",
    "apac": "No free source for onsite APAC roles outside what's indexed by general boards. "
            "Himalayas covers REMOTE roles open to APAC candidates, but not local/onsite "
            "hiring in most APAC markets.",
}


# Canonical country -> recognized text variants. Grouped by canonical name
# (rather than one flat keyword list) because _country_hits below needs to
# return the SAME key for "UK" and "United Kingdom" — comparisons downstream
# (onsite_location_mismatch, _location_allowed) test set membership, so two
# spellings of the same country that resolved to different strings would
# either wrongly drop a genuine match or wrongly pass a real mismatch,
# depending on which spelling each side happened to use.
EUROPE_COUNTRY_VARIANTS = {
    "uk": ["uk", "united kingdom", "britain", "england", "scotland", "wales"],
    "germany": ["germany"],
    "france": ["france"],
    "spain": ["spain"],
    "netherlands": ["netherlands"],
    "poland": ["poland"],
    "ireland": ["ireland"],
    "portugal": ["portugal"],
    "italy": ["italy"],
    "sweden": ["sweden"],
    "switzerland": ["switzerland"],
    "austria": ["austria"],
    "belgium": ["belgium"],
    "denmark": ["denmark"],
    "norway": ["norway"],
    "finland": ["finland"],
}
US_COUNTRY_VARIANTS = {
    "us": ["us", "usa", "united states", "america"],
}
APAC_COUNTRY_VARIANTS = {
    "india": ["india"], "singapore": ["singapore"], "japan": ["japan"], "china": ["china"],
    "australia": ["australia"], "new zealand": ["new zealand"], "korea": ["korea"],
    "philippines": ["philippines"], "vietnam": ["vietnam"], "indonesia": ["indonesia"],
    "hong kong": ["hong kong"], "thailand": ["thailand"], "malaysia": ["malaysia"],
}
MIDDLE_EAST_COUNTRY_VARIANTS = {
    "uae": ["uae", "dubai", "abu dhabi"], "saudi": ["saudi"], "qatar": ["qatar"],
    "kuwait": ["kuwait"], "bahrain": ["bahrain"], "oman": ["oman"], "israel": ["israel"],
}

_REGION_COUNTRY_VARIANTS = {
    "europe": EUROPE_COUNTRY_VARIANTS,
    "us": US_COUNTRY_VARIANTS,
    "apac": APAC_COUNTRY_VARIANTS,
    "middle_east": MIDDLE_EAST_COUNTRY_VARIANTS,
}

# Broad (continent/region-level) tags — used for coarse SOURCE-level gating,
# where being generous is correct: a candidate who said "Europe" broadly
# should still see Arbeitnow offered as an option.
REGION_KEYWORDS = {
    "europe": ["europe", "eu"] + [v for variants in EUROPE_COUNTRY_VARIANTS.values() for v in variants],
    "us": ["north america"] + [v for variants in US_COUNTRY_VARIANTS.values() for v in variants],
    "apac": ["apac", "asia"] + [v for variants in APAC_COUNTRY_VARIANTS.values() for v in variants],
    "middle_east": ["middle east", "gulf", "gcc"] + [v for variants in MIDDLE_EAST_COUNTRY_VARIANTS.values() for v in variants],
}


def _kw_in(text: str, kw: str) -> bool:
    """Word-boundary match for short, ambiguous keywords (us/uk/eu) to avoid
    false positives inside unrelated words (e.g. 'campus', 'museum')."""
    if len(kw) <= 3:
        return re.search(rf"\b{re.escape(kw)}\b", text) is not None
    return kw in text


def classify_geography(geography_text: str) -> set[str]:
    """Maps the profile's free-text geography preference to broad region
    tags for SOURCE-level eligibility gating. Deliberately coarse — 'Europe'
    matches any European country, since a candidate who said 'Europe'
    broadly wants that breadth. Returns an empty set if nothing confidently
    matches — callers should treat that as 'fail open, don't narrow the
    search' rather than guessing."""
    text = (geography_text or "").lower()
    matched = set()
    for region, keywords in REGION_KEYWORDS.items():
        if any(_kw_in(text, kw) for kw in keywords):
            matched.add(region)
    return matched


def _country_hits(text: str) -> set[str]:
    """Specific-country-level matches only (not the broad continent labels
    'europe'/'apac'/'middle east') — used for onsite LISTING-level mismatch
    checks, where specificity matters: a candidate who said 'Europe' broadly
    shouldn't have a Germany listing dropped, but one who said 'UK'
    specifically should. Returns CANONICAL country keys (e.g. 'uk', not
    whichever variant text matched) so 'UK' and 'United Kingdom' are treated
    as the same country by callers comparing hits via set membership."""
    text = (text or "").lower()
    hits = set()
    for variants_by_country in _REGION_COUNTRY_VARIANTS.values():
        for canonical, variants in variants_by_country.items():
            if any(_kw_in(text, kw) for kw in variants):
                hits.add(canonical)
    return hits


def onsite_location_mismatch(location_text: str, geography_text: str) -> bool:
    """True if an ONSITE listing's location clearly conflicts with the
    candidate's stated geography at the specific-country level — e.g. a
    'Berlin, Germany' listing when the candidate said 'UK'. Remote/hybrid/
    unclear listings are never flagged here — they pass through regardless,
    since remote work isn't location-bound the same way. Fails open: if
    either side isn't specific enough to compare (e.g. candidate said
    'Europe' broadly, or the listing has no location text), nothing is
    dropped."""
    loc = (location_text or "").lower()
    if not loc or any(w in loc for w in ("remote", "anywhere", "worldwide", "hybrid")):
        return False
    geo_hits = _country_hits(geography_text)
    loc_hits = _country_hits(loc)
    if not geo_hits or not loc_hits:
        return False
    return geo_hits.isdisjoint(loc_hits)


def eligible_sources(pool: dict, geography_text: str) -> tuple[dict, list[str]]:
    """Filters a source pool down to sources whose known coverage overlaps
    the candidate's geography. Sources tagged 'remote_global'/'global' always
    pass. Fails open (returns the full pool, no exclusions) if the geography
    text doesn't confidently map to any known region, rather than silently
    narrowing someone's search on a guess.

    Returns (eligible_pool, excluded_names) — excluded_names is used to build
    an explicit, user-visible note about coverage gaps."""
    region_tags = classify_geography(geography_text)
    if not region_tags:
        return pool, []

    eligible, excluded = {}, []
    for name, fn in pool.items():
        coverage = COVERAGE.get(name, {"global"})
        if coverage & ({"remote_global", "global"} | region_tags):
            eligible[name] = fn
        else:
            excluded.append(name)
    return eligible, excluded
