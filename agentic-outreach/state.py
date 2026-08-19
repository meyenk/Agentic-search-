"""
state.py — Shared state schema for the outreach graph.

This is the single source of truth passed between every node.
Every node reads from and writes back into this dict.
"""

from typing import TypedDict, Literal, Optional


class Candidate(TypedDict):
    """A single discovered opportunity — professor or job."""
    id: str                      # stable hash of name+org, used for dedup
    kind: Literal["professor", "job"]
    name: str                    # professor name OR job title
    org: str                     # university OR company
    location: str
    source: str                  # which API/tool found this
    url: str                     # profile URL or job posting URL
    description: str             # raw snippet used for ranking context
    posted_date: Optional[str]   # if known
    start_date_match: bool       # true if posting mentions your target window

    # filled in by Rank node
    score: Optional[float]
    reason: Optional[str]

    # filled in by Tailor/Draft nodes
    email: Optional[str]
    email_conf: Optional[str]    # verified / inferred / missing
    cv_pdf_path: Optional[str]
    email_subject: Optional[str]
    email_body: Optional[str]
    why_this_role: Optional[str]


class SearchFeedback(TypedDict):
    """What Rank tells Search after each round — the actual feedback loop."""
    round: int
    verdict: Literal["good", "weak", "empty"]
    good_count: int
    notes: str                   # e.g. "results too senior, try RemoteOK instead of OpenAlex"


class GraphState(TypedDict):
    # ── set once at start ──────────────────────────────
    profile: dict                 # questionnaire answers + CV fingerprint
    track: Literal["professor", "job"]

    # ── search loop state ──────────────────────────────
    step_count: int
    max_steps: int
    search_history: list[str]     # log of every tool call made, for debugging
    feedback_log: list[SearchFeedback]
    candidates: list[Candidate]   # accumulates across the loop
    seen_ids: list[str]           # dedup guard

    # ── after loop exits ───────────────────────────────
    qualified: list[Candidate]    # score >= threshold, up to DRAFTS_PER_RUN
    stop_reason: str

    # ── after tailor/draft ─────────────────────────────
    finished: list[Candidate]     # fully processed, ready for report

    # ── output ──────────────────────────────────────────
    report_path: Optional[str]
