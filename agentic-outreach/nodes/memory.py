"""
nodes/memory.py — Cross-run memory for the search loop.

Within a single run, Rank writes concrete feedback that steers Search's next
round (that's state["feedback_log"]) — but it lives only in GraphState and
is gone once the run ends. This module closes that gap: at the end of every
run, this run's feedback_log gets folded into one evolving, distilled
"lessons" memo per track (professor vs job — their failure modes don't
transfer to each other). Future runs load that memo and feed it into
Search's FIRST round only, as a warm start — not a growing raw log to
re-read and re-summarize every time.
"""

import os
import logging

from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)

LESSONS_DIR = "profile"
LESSONS_MAX_WORDS = 500


def _lessons_path(track: str) -> str:
    return os.path.join(LESSONS_DIR, f"search_lessons_{track}.txt")


def load_lessons(track: str) -> str:
    """Returns the persisted lessons memo for this track, or "" if none yet."""
    path = _lessons_path(track)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


MERGE_PROMPT = """
You maintain a running "lessons learned" memo for a job/opportunity search
agent, specific to the "{track}" track. This memo is read at the START of
every future run to give the search step a warm start — it should read like
durable, generalizable advice, not a diary of any one run.

EXISTING MEMO (may be empty on a first run):
{existing}

THIS RUN'S RAW FEEDBACK LOG (round-by-round notes from this run's
Search<->Rank loop):
{new_notes}

Merge the new run's notes into the existing memo. Keep only lessons that
would plausibly still apply to a FUTURE run — durable source-quality notes
("RemoteOK rarely returns X for this domain"), durable seniority/domain
mismatches, durable dead ends. Drop anything that was clearly one-off or
already superseded by a more recent, better lesson. Do not just append —
actually rewrite/merge/prune so the memo stays useful and under {max_words}
words.

Return ONLY the updated memo as plain text. No headers, no markdown, no
preamble.
"""


def _call_gemini(prompt: str) -> str:
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return resp.text.strip()
    except Exception as e:
        log.warning(f"  Lessons merge failed ({e}) — keeping existing memo untouched.")
        return ""


def update_lessons(track: str, feedback_log: list[dict]) -> None:
    """Called once at the end of a run (graph.py's finish node, where
    stop_reason is finalized) — folds this run's feedback_log into the
    persisted, evolving lessons memo for this track. No-ops safely if the
    merge call fails, rather than wiping the existing memo."""
    if not feedback_log:
        return

    existing = load_lessons(track)
    new_notes = "\n".join(
        f"Round {f['round']} ({f['verdict']}, {f['good_count']} qualified): {f['notes']}"
        for f in feedback_log
    )
    prompt = MERGE_PROMPT.format(
        track=track,
        existing=existing or "(empty — first run)",
        new_notes=new_notes,
        max_words=LESSONS_MAX_WORDS,
    )

    merged = _call_gemini(prompt)
    if not merged:
        return

    os.makedirs(LESSONS_DIR, exist_ok=True)
    with open(_lessons_path(track), "w", encoding="utf-8") as f:
        f.write(merged.strip())
    log.info(f"  Updated search-lessons memo for track='{track}' (~{len(merged.split())} words).")
