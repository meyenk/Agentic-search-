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

log = logging.getLogger(__name__)

REQUEST_HEADERS = {"User-Agent": "outreach-pipeline/1.0 (personal research tool)"}


# ── JOB SOURCES ──────────────────────────────────────────────

def search_arbeitnow(query: str, location: str = "") -> list[dict]:
    """
    Arbeitnow — free, no key, strong for UK/Europe tech & engineering roles,
    internships and graduate schemes included.
    """
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api",
                          timeout=10, headers=REQUEST_HEADERS)
        data = r.json().get("data", [])
        out = []
        for job in data:
            text = f"{job.get('title','')} {job.get('description','')}".lower()
            if query.lower() not in text and not any(w in text for w in query.lower().split()):
                continue
            out.append({
                "name": job.get("title", ""),
                "org": job.get("company_name", ""),
                "location": ", ".join(job.get("location", "").split(",")[:2]) if job.get("location") else "Remote/UK",
                "url": job.get("url", ""),
                "description": (job.get("description", "") or "")[:600],
                "posted_date": job.get("created_at", ""),
                "source": "arbeitnow",
            })
        return out[:15]
    except Exception as e:
        log.warning(f"Arbeitnow search failed: {e}")
        return []


def search_remoteok(query: str, location: str = "") -> list[dict]:
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


def search_themuse(query: str, location: str = "") -> list[dict]:
    """
    The Muse — free, no key, decent for grad schemes and early-career roles,
    mostly US with some international listings.
    """
    try:
        r = requests.get("https://www.themuse.com/api/public/jobs",
                          params={"category": query, "page": 0},
                          timeout=10, headers=REQUEST_HEADERS)
        data = r.json().get("results", [])
        out = []
        for job in data:
            locations = ", ".join(l.get("name", "") for l in job.get("locations", []))
            out.append({
                "name": job.get("name", ""),
                "org": job.get("company", {}).get("name", ""),
                "location": locations,
                "url": job.get("refs", {}).get("landing_page", ""),
                "description": (job.get("contents", "") or "")[:600],
                "posted_date": job.get("publication_date", ""),
                "source": "themuse",
            })
        return out[:15]
    except Exception as e:
        log.warning(f"The Muse search failed: {e}")
        return []


def search_remotive(query: str, location: str = "") -> list[dict]:
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


def search_himalayas(query: str, location: str = "") -> list[dict]:
    """
    Himalayas — free, no key, remote-only jobs with a REAL country filter
    (ISO code, country name, or 'worldwide'). Unlike RemoteOK/Remotive/
    Arbeitnow, this is the one job source that can actually be filtered by
    geography server-side — best pick for APAC/Middle East/non-Europe
    candidates who only want remote roles open to their region.
    """
    try:
        params = {"q": query}
        if location:
            params["country"] = location
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
                "description": (job.get("excerpt", "") or "")[:600],
                "posted_date": job.get("pubDate", ""),
                "source": "himalayas",
            })
        return out
    except Exception as e:
        log.warning(f"Himalayas search failed: {e}")
        return []


# ── ACADEMIC SOURCES ─────────────────────────────────────────

def search_openalex(query: str, location: str = "") -> list[dict]:
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


def search_dblp(query: str, location: str = "") -> list[dict]:
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


def search_semantic_scholar(query: str, location: str = "") -> list[dict]:
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


def search_arxiv(query: str, location: str = "") -> list[dict]:
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

EUROPE_COUNTRIES = ["germany", "uk", "united kingdom", "britain", "france", "spain",
                     "netherlands", "poland", "ireland", "portugal", "italy", "sweden",
                     "switzerland", "austria", "belgium", "denmark", "norway", "finland"]
US_COUNTRIES = ["us", "usa", "united states", "america"]
APAC_COUNTRIES = ["india", "singapore", "japan", "china", "australia", "new zealand", "korea",
                   "philippines", "vietnam", "indonesia", "hong kong", "thailand", "malaysia"]
MIDDLE_EAST_COUNTRIES = ["uae", "dubai", "abu dhabi", "saudi", "qatar", "kuwait", "bahrain",
                          "oman", "israel"]

# Broad (continent/region-level) tags — used for coarse SOURCE-level gating,
# where being generous is correct: a candidate who said "Europe" broadly
# should still see Arbeitnow offered as an option.
REGION_KEYWORDS = {
    "europe": ["europe", "eu"] + EUROPE_COUNTRIES,
    "us": ["north america"] + US_COUNTRIES,
    "apac": ["apac", "asia"] + APAC_COUNTRIES,
    "middle_east": ["middle east", "gulf", "gcc"] + MIDDLE_EAST_COUNTRIES,
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
    specifically should."""
    text = (text or "").lower()
    hits = set()
    for keywords in (EUROPE_COUNTRIES, APAC_COUNTRIES, MIDDLE_EAST_COUNTRIES, US_COUNTRIES):
        for kw in keywords:
            if _kw_in(text, kw):
                hits.add(kw)
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
