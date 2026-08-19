"""
nodes/finish.py — For each qualified candidate: find email (professor track),
tailor the CV, draft the outreach content. Runs sequentially with rate limiting.
"""

import logging

from nodes.email_finder import find_email
from nodes.cv_tailor import build_tailored_cv
from nodes.drafter import draft_for_candidate
from nodes.db import already_contacted, mark_contacted

log = logging.getLogger(__name__)


def run_finish(state: dict) -> dict:
    """LangGraph node — process each qualified candidate into a finished draft."""
    profile = state["profile"]
    finished = []

    candidates = [c for c in state["qualified"] if not already_contacted(c["id"])]
    skipped = len(state["qualified"]) - len(candidates)
    if skipped:
        log.info(f"  Skipping {skipped} already-contacted candidates from a previous run.")

    for c in candidates:
        log.info(f"\n  ── Finishing: {c['name']} @ {c['org']} (score={c['score']:.1f}) ──")

        if c["kind"] == "professor":
            email, conf = find_email(c["name"], c["org"], c["url"])
            c["email"] = email
            c["email_conf"] = conf
            log.info(f"    Email: {email or '(not found)'} [{conf}]")

        log.info(f"    Tailoring CV...")
        c["cv_pdf_path"] = build_tailored_cv(c, profile)

        log.info(f"    Drafting content...")
        c = draft_for_candidate(c, profile)

        finished.append(c)
        mark_contacted(c)

    state["finished"] = finished
    return state
