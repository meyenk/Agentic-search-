"""
nodes/rank.py — Scores unranked candidates against the profile rubric,
and produces the feedback that Search uses to adjust its next round.

This is the other half of the "Search and Rank talk to each other" loop:
Rank doesn't just score — it tells Search what to try differently.
"""

import json
import re
import time
import logging

from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_CALL_DELAY_SECS, QUALITY_THRESHOLD, DRAFTS_PER_RUN

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)


RANK_PROMPT = """
You are the ranking step of an opportunity-discovery agent. Score each candidate
against the profile below and give ONE piece of concrete, actionable feedback
for the next search round — this feedback directly steers what gets searched next,
so be specific (e.g. "results skew senior/PI level, try emphasising 'postdoc' or
'PhD student' terms" or "Arbeitnow returned nothing relevant, try RemoteOK instead"
or "good hits, keep searching this direction").

CANDIDATE PROFILE:
{profile_summary}

TRACK: {track}

CANDIDATES TO SCORE:
{candidates_json}

For each candidate, score 0-10 based on:
- Domain match with the profile's stated interests
- SENIORITY/EXPERIENCE FIT — read the actual job/role description for signals like
  required years of experience, seniority language ("senior", "lead", "principal"),
  or specific technical depth (e.g. a role wanting hands-on production model
  PRE-TRAINING experience is a much bigger ask than one wanting fine-tuning or
  inference work). Compare this against the candidate's stated years of experience.
  This is NOT a hard filter — do not zero out a role just because it looks senior —
  but a role clearly wanting 5+ years of a specific specialized skill the candidate
  has 0 years in should score meaningfully lower than a role at the candidate's
  actual level, even if the domain matches well. A domain match with a large
  seniority gap is a WEAK match, not a strong one.
- Geography fit
- Recency (prefer recent postings/papers over stale ones)
- Bonus: if start_date_match is true, add +1.5 (this is a rare early-window match
  for a specific fixed-start program, valuable but secondary to seniority fit)

Return ONLY valid JSON, no markdown:
{{
  "scored": [
    {{"id": "<candidate id>", "score": <float 0-10>, "reason": "<one sentence, mention seniority fit explicitly if it affected the score>"}}
  ],
  "round_verdict": "good" | "weak" | "empty",
  "feedback_notes": "<one concrete, actionable sentence for the next search round>"
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


def run_rank_round(state: dict) -> dict:
    """LangGraph node — score all unranked candidates from the latest search round."""
    unranked = [c for c in state["candidates"] if c["score"] is None]

    round_num = len(state["feedback_log"]) + 1

    if not unranked:
        log.info("  [Rank] No new candidates to score this round.")
        state["feedback_log"].append({
            "round": round_num,
            "verdict": "empty",
            "good_count": 0,
            "notes": "Last round returned zero new candidates — try a different source or broader query terms.",
        })
        return state

    profile = state["profile"]
    profile_summary = (
        f"Domains: {', '.join(profile.get('domains', []))}\n"
        f"Geography: {profile.get('geography', '')}\n"
        f"Years of experience: {profile.get('years_experience', '0')}\n"
        f"Available now: {profile.get('available_now', True)}\n"
        f"Target start date (fixed-cycle programs only): {profile.get('target_start_date', '')}\n"
        f"Dealbreakers: {profile.get('dealbreakers', 'none')}"
    )

    # Trim candidate payload sent to Gemini to keep tokens reasonable.
    # Description gets more room than before — seniority/experience signals
    # ("5+ years", "pre-training experience", "senior") are often further into
    # the text than the first 300 chars, and truncating too early was hiding
    # exactly the information Rank needs to judge seniority fit.
    trimmed = [{
        "id": c["id"], "name": c["name"], "org": c["org"], "location": c["location"],
        "description": c["description"][:600], "start_date_match": c["start_date_match"],
    } for c in unranked[:10]]  # slightly smaller batch to offset the longer descriptions

    prompt = RANK_PROMPT.format(
        profile_summary=profile_summary,
        track=state["track"],
        candidates_json=json.dumps(trimmed, indent=2),
    )

    log.info(f"  [Rank] Scoring {len(trimmed)} candidates...")
    text = _call_gemini(prompt)
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)

    try:
        result = json.loads(text)
        scored = result.get("scored", [])
        verdict = result.get("round_verdict", "weak")
        notes = result.get("feedback_notes", "")
    except Exception as e:
        log.warning(f"  Could not parse rank result ({e})")
        scored = []
        verdict = "weak"
        notes = "Ranking failed to parse — try varying query terms next round."

    score_map = {s["id"]: s for s in scored}
    good_count = 0
    for c in state["candidates"]:
        if c["id"] in score_map:
            c["score"] = float(score_map[c["id"]].get("score", 0))
            c["reason"] = score_map[c["id"]].get("reason", "")
            if c["score"] >= QUALITY_THRESHOLD:
                good_count += 1

    state["feedback_log"].append({
        "round": round_num,
        "verdict": verdict,
        "good_count": good_count,
        "notes": notes,
    })
    log.info(f"  Round verdict: {verdict} ({good_count} qualified) — {notes}")

    # Update qualified list
    qualified_ids = {q["id"] for q in state["qualified"]}
    for c in sorted(state["candidates"], key=lambda x: x["score"] or 0, reverse=True):
        if c["score"] and c["score"] >= QUALITY_THRESHOLD and c["id"] not in qualified_ids:
            state["qualified"].append(c)
            qualified_ids.add(c["id"])
        if len(state["qualified"]) >= DRAFTS_PER_RUN:
            break

    time.sleep(GEMINI_CALL_DELAY_SECS)
    return state
