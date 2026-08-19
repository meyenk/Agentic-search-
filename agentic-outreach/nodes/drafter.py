"""
nodes/drafter.py — Writes the outreach content per candidate.
Professor track: cold email (subject + body).
Job track: short "why this role" blurb (no full application filling — out of scope).
"""

import re
import time
import logging
from google import genai

from config import (
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_CALL_DELAY_SECS,
    YOUR_NAME, YOUR_EMAIL, YOUR_LINKEDIN, YOUR_GITHUB
)

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)

STYLE_RULES = """
STYLE RULES (strict):
- No em dashes.
- No "rule of three" lists (e.g. "X, Y, and Z" patterns) — vary sentence structure instead.
- No words: "passionate", "leverage", "cutting-edge", "keen", "excited to".
- Sound like a competent engineer writing a direct, specific message — not marketing copy.
- Be concrete: reference something specific about the opportunity, not generic enthusiasm.
"""

EMAIL_PROMPT = """
Write a cold email requesting a research internship. Reference something specific
about their actual recent work — not generic enthusiasm.

STUDENT: {name}, {email}, {linkedin}
CV fingerprint: {cv_fingerprint}

PROFESSOR: {prof_name} at {org}
Context: {description}

{style_rules}

Length: 180-220 words. No bullet points.
Return exactly:
SUBJECT: <subject line>
BODY:
<email body>
"""

WHY_ROLE_PROMPT = """
Write a short "why this role" answer for a job/internship application text box.

CANDIDATE: {name}
CV fingerprint: {cv_fingerprint}

ROLE: {job_title} at {org}
Description: {description}

{style_rules}

Length: 80-120 words. Direct, specific to this role, no filler opening line.
Return ONLY the answer text, nothing else.
"""


def _call_gemini(prompt: str) -> str:
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return resp.text.strip()
    except Exception as e:
        log.error(f"Draft generation failed: {e}")
        return ""


def draft_for_candidate(candidate: dict, profile: dict) -> dict:
    """Fills in email_subject/email_body OR why_this_role depending on kind."""
    cv_fp = profile.get("cv_fingerprint", "")[:600]

    if candidate["kind"] == "professor":
        prompt = EMAIL_PROMPT.format(
            name=YOUR_NAME, email=YOUR_EMAIL, linkedin=YOUR_LINKEDIN,
            cv_fingerprint=cv_fp,
            prof_name=candidate["name"], org=candidate["org"],
            description=candidate["description"][:500],
            style_rules=STYLE_RULES,
        )
        text = _call_gemini(prompt)
        subject_match = re.search(r"SUBJECT:\s*(.+)", text)
        body_match = re.search(r"BODY:\s*(.+)", text, re.DOTALL)
        candidate["email_subject"] = subject_match.group(1).strip() if subject_match else f"Research Internship Inquiry"
        candidate["email_body"] = body_match.group(1).strip() if body_match else text

    else:  # job
        prompt = WHY_ROLE_PROMPT.format(
            name=YOUR_NAME, cv_fingerprint=cv_fp,
            job_title=candidate["name"], org=candidate["org"],
            description=candidate["description"][:500],
            style_rules=STYLE_RULES,
        )
        candidate["why_this_role"] = _call_gemini(prompt)

    time.sleep(GEMINI_CALL_DELAY_SECS)
    return candidate
