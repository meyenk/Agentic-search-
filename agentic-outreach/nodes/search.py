"""
nodes/search.py — The adaptive half of the Search↔Rank loop.

Each call to run_search_round():
  1. Builds a prompt with the profile, source registry descriptions,
     and the most recent feedback from Rank (if any).
  2. Asks Gemini which 1-3 sources to query this round, and with what
     query string — this is the "reasoning" step, not hardcoded routing.
  3. Executes the chosen calls against sources/registry.py.
  4. Normalizes raw results into Candidate dicts, dedups, appends to state.
"""

import json
import re
import time
import logging
import hashlib

from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_CALL_DELAY_SECS
from sources.registry import ALL_SOURCES, JOB_SOURCES, ACADEMIC_SOURCES
from nodes.memory import load_lessons

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)


def _source_descriptions(track: str) -> str:
    pool = JOB_SOURCES if track == "job" else ACADEMIC_SOURCES if track == "professor" else ALL_SOURCES
    lines = []
    for name, fn in pool.items():
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        lines.append(f"- {name}: {doc}")
    return "\n".join(lines)


def _candidate_id(name: str, org: str) -> str:
    return hashlib.md5(f"{name.lower()}|{org.lower()}".encode()).hexdigest()[:12]


def _check_date_match(text: str, target_start: str) -> bool:
    if not target_start:
        return False
    year = re.search(r"20\d{2}", target_start)
    if year and year.group(0) in text:
        return True
    return False


SEARCH_PROMPT = """
You are the search-planning step of a job/research-opportunity discovery agent.

CANDIDATE PROFILE:
{profile_summary}

AVAILABLE SOURCES (pick 1-3 per round, don't repeat a source+query pair you already tried):
{sources}

ALREADY TRIED THIS RUN:
{history}

FEEDBACK FROM LAST ROUND (use this to adjust strategy — this is the whole point of the loop):
{feedback}
{warm_start}

Decide which source(s) to query this round and with what search query string.
Think about which source is most likely to surface genuinely relevant, current results
given the profile and any feedback above. Vary your query terms round to round —
don't repeat an identical query that already returned nothing useful.

Return ONLY valid JSON, no markdown:
{{
  "calls": [
    {{"source": "<source_name>", "query": "<search terms>", "location": "<optional location filter>"}}
  ],
  "reasoning": "<one sentence on why these sources/queries this round>"
}}
"""


def _call_gemini(prompt: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            return resp.text.strip()
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                wait = 35 * (attempt + 1)
                log.warning(f"  Rate limited — waiting {wait}s...")
                time.sleep(wait)
            else:
                log.error(f"  Gemini error: {e}")
                return ""
    return ""


def run_search_round(state: dict) -> dict:
    """LangGraph node — one round of the search loop."""
    profile = state["profile"]
    track = state["track"]

    profile_summary = (
        f"Track: {track}\n"
        f"Domains: {', '.join(profile.get('domains', []))}\n"
        f"Geography: {profile.get('geography', '')}\n"
        f"Remote OK: {profile.get('remote_ok', True)}\n"
        f"Onsite preferred: {profile.get('onsite_preferred', True)}\n"
        f"Target start date: {profile.get('target_start_date', '')}\n"
        f"CV fingerprint: {profile.get('cv_fingerprint', '')[:500]}"
    )

    history_text = "\n".join(state["search_history"][-10:]) or "(none yet — first round)"

    feedback_log = state.get("feedback_log", [])
    feedback_text = feedback_log[-1]["notes"] if feedback_log else "(no feedback yet — first round)"

    # Warm start (CV-derived clarification + cross-run lessons) — only
    # relevant to shape the OPENING strategy, so only injected on round 1.
    # From round 2 onward, the real feedback loop above has already taken
    # over as the steering signal.
    is_first_round = not state["search_history"]
    warm_start_text = ""
    if is_first_round:
        warm_start = profile.get("search_warm_start", "").strip()
        lessons = load_lessons(track).strip()
        if warm_start:
            warm_start_text += f"\nCV-DERIVED WARM START (from setup): {warm_start}\n"
        if lessons:
            warm_start_text += f"\nLESSONS FROM PREVIOUS RUNS on this track: {lessons}\n"

    prompt = SEARCH_PROMPT.format(
        profile_summary=profile_summary,
        sources=_source_descriptions(track),
        history=history_text,
        feedback=feedback_text,
        warm_start=warm_start_text,
    )

    log.info(f"  [Search round] Asking Gemini which sources to query...")
    text = _call_gemini(prompt)
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)

    try:
        plan = json.loads(text)
        calls = plan.get("calls", [])
        log.info(f"  Plan: {plan.get('reasoning', '')}")
    except Exception as e:
        log.warning(f"  Could not parse search plan ({e}), falling back to first source")
        first_source = list((JOB_SOURCES if track == "job" else ACADEMIC_SOURCES).keys())[0]
        calls = [{"source": first_source, "query": " ".join(profile.get("domains", ["research"])), "location": ""}]

    new_candidates = []
    for call in calls[:3]:
        source_name = call.get("source", "")
        query = call.get("query", "")
        location = call.get("location", "")

        fn = ALL_SOURCES.get(source_name)
        if not fn:
            log.warning(f"  Unknown source '{source_name}', skipping")
            continue

        log.info(f"    → {source_name}(query='{query}', location='{location}')")
        state["search_history"].append(f"{source_name}: '{query}' / '{location}'")

        try:
            results = fn(query, location)
        except Exception as e:
            log.warning(f"    {source_name} call failed: {e}")
            results = []

        for r in results:
            cid = _candidate_id(r.get("name", ""), r.get("org", ""))
            if cid in state["seen_ids"]:
                continue
            state["seen_ids"].append(cid)

            candidate = {
                "id": cid,
                "kind": "job" if source_name in JOB_SOURCES else "professor",
                "name": r.get("name", ""),
                "org": r.get("org", ""),
                "location": r.get("location", ""),
                "source": source_name,
                "url": r.get("url", ""),
                "description": r.get("description", ""),
                "posted_date": r.get("posted_date", ""),
                "start_date_match": _check_date_match(
                    r.get("description", ""), profile.get("target_start_date", "")
                ),
                "score": None,
                "reason": None,
                "email": None,
                "email_conf": None,
                "cv_pdf_path": None,
                "email_subject": None,
                "email_body": None,
                "why_this_role": None,
            }
            new_candidates.append(candidate)

        time.sleep(1)  # be polite to free APIs

    state["candidates"].extend(new_candidates)
    state["step_count"] += len(calls)
    log.info(f"  Round produced {len(new_candidates)} new candidates (step {state['step_count']}/{state['max_steps']})")

    time.sleep(GEMINI_CALL_DELAY_SECS)
    return state
