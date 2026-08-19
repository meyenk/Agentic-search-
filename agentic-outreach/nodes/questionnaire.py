"""
nodes/questionnaire.py — One-time questionnaire that supplements the CV.
Run once via `python run.py --setup`. Saves to profile/profile.json.
Every subsequent run loads this file rather than re-asking.

Requires `python run.py --import-cv` to have been run first, so the CV
fingerprint here comes from your actual uploaded resume rather than being
hardcoded per-person — this is what makes the whole pipeline reusable by
anyone: drop your PDF, import it, then answer this questionnaire.
"""

import json
import os
import re
import logging
from google import genai

from config import PROFILE_PATH, GEMINI_API_KEY, GEMINI_MODEL
from nodes.cv_import import FINGERPRINT_PATH

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)


def _load_fingerprint() -> str:
    if not os.path.exists(FINGERPRINT_PATH):
        raise FileNotFoundError(
            f"No CV fingerprint found at {FINGERPRINT_PATH}. "
            f"Run `python run.py --import-cv` first (drop your resume PDF in input/)."
        )
    with open(FINGERPRINT_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


WARM_START_PROMPT = """
You are reviewing a candidate's CV fingerprint and their questionnaire
answers, looking for genuine ambiguity that would cost a job/opportunity
search agent a wasted first round — e.g. a CV spanning two distinct domains
where the "domains" given is vague or overloaded, a seniority signal in the
CV that conflicts with the stated years of experience, or an ambiguous term
that could mean two different things in a job/research search.

Only flag this if there's a REAL disambiguation worth making, specific to
THIS candidate. If the CV and answers already give a focused, unambiguous
picture, do not invent a question — most candidates should get
needs_clarification: false.

CV FINGERPRINT:
{fingerprint}

QUESTIONNAIRE ANSWERS SO FAR:
{answers}

Return ONLY valid JSON, no markdown:
{{"needs_clarification": <true/false>, "question": "<question text, empty string if false>"}}
"""


def _generate_warm_start_question(cv_fingerprint: str, profile_so_far: dict) -> str | None:
    """Conditionally generates one CV-derived disambiguating question — a
    warm start for Search's first round, not a replacement for the fixed
    questionnaire fields. Returns None if nothing's genuinely ambiguous."""
    prompt = WARM_START_PROMPT.format(
        fingerprint=cv_fingerprint[:1500],
        answers=json.dumps(profile_so_far, indent=2),
    )
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = resp.text.strip()
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        if result.get("needs_clarification") and result.get("question"):
            return result["question"]
        return None
    except Exception as e:
        log.warning(f"  Warm-start question generation failed ({e}) — skipping.")
        return None


def run_questionnaire_cli() -> dict:
    """Interactive CLI questionnaire. Run once, saved to disk."""
    print("\n" + "=" * 60)
    print("  ONE-TIME SETUP — supplements your CV for better matching")
    print("=" * 60 + "\n")

    cv_fingerprint = _load_fingerprint()
    print(f"Loaded CV fingerprint from {FINGERPRINT_PATH}:\n")
    print(f"  {cv_fingerprint[:200]}...\n")

    track = input("Target track — (1) Research/Professor outreach, (2) Jobs/Grad schemes, (3) Both [3]: ").strip() or "3"
    track_map = {"1": "professor", "2": "job", "3": "both"}
    track = track_map.get(track, "both")

    domains = input("Domain priority, comma-separated (e.g. SLAM, 3D perception, autonomous vehicles): ").strip()
    geography = input("Geography preference (e.g. Europe, UK, remote-open): ").strip() or "Europe"
    remote_ok = input("Open to remote? (y/n) [y]: ").strip().lower() != "n"
    onsite_pref = input("Onsite preferred if available? (y/n) [y]: ").strip().lower() != "n"

    available_now = input("Available for internships/roles right now? (y/n) [y]: ").strip().lower() != "n"

    start_date = input(
        "Target start date for a specific grad scheme/PhD cycle, if any "
        "(e.g. 'July 2027' — leave blank if not applicable, this only affects "
        "scoring for programs with a known fixed start): "
    ).strip()

    years_exp = input(
        "Years of full-time work/research experience (used to judge seniority "
        "fit — e.g. a role wanting 5+ years of production model pre-training "
        "experience should rank lower for a 1-YOE candidate, not filtered out, "
        "just weighted down): "
    ).strip() or "0"

    dealbreakers = input("Deal-breakers, if any (e.g. unpaid only, no visa sponsorship): ").strip()

    print(
        "\nAny instructions to keep in mind while the system modifies your CV per "
        "application? Be as specific as you like. Example: 'Do not touch Formula "
        "Student or Education/Achievements — those stay exactly as-is. BioSky "
        "internship and the self-projects (Waymax, MedSAM) can be reordered or "
        "reworded toward the role. Only make minor tweaks — reordering, small "
        "rewording, adding a relevant skill keyword for ATS — never a major "
        "rewrite or full section removal unless space genuinely requires it.'"
    )
    cv_instructions = input("> ").strip()

    profile_so_far = {
        "track": track,
        "domains": [d.strip() for d in domains.split(",") if d.strip()],
        "geography": geography,
        "remote_ok": remote_ok,
        "onsite_preferred": onsite_pref,
        "available_now": available_now,
        "target_start_date": start_date,
        "years_experience": years_exp,
        "dealbreakers": dealbreakers,
    }

    print("\nChecking your CV for anything worth a heads-up before the first search round...")
    warm_start_question = _generate_warm_start_question(cv_fingerprint, profile_so_far)
    search_warm_start = ""
    if warm_start_question:
        print(f"\n{warm_start_question}")
        search_warm_start = input("> ").strip()
    else:
        print("(CV looks focused enough — no extra clarification needed.)")

    profile = {
        **profile_so_far,
        "cv_instructions": cv_instructions,
        "cv_fingerprint": cv_fingerprint,
        "search_warm_start": search_warm_start,
    }

    os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    print(f"\n✅ Profile saved to {PROFILE_PATH}")
    print("   Edit this file directly anytime to update your preferences.\n")
    return profile


def load_profile() -> dict:
    if not os.path.exists(PROFILE_PATH):
        raise FileNotFoundError(
            f"No profile found at {PROFILE_PATH}. Run `python run.py --setup` first."
        )
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
