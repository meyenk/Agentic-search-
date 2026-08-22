# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A LangGraph pipeline that discovers professors (for research internship outreach) or jobs/grad
schemes, ranks them against the user's CV, and produces tailored CVs + draft emails/answers for
human review. Nothing is ever sent automatically — the pipeline stops at an HTML review page.

All code lives in `agentic-outreach/` — **`cd agentic-outreach` before running any command below.**

## Commands

```bash
cd agentic-outreach
pip install -r requirements.txt        # deps

cp .env.example .env                   # then fill in GEMINI_API_KEY etc.

python run.py --import-cv              # one-time: PDF in input/ -> cv_base.tex + fingerprint
python run.py --setup                  # one-time: questionnaire -> profile/profile.json
python run.py                          # run the pipeline (track from profile.json)
python run.py --track job              # override track for this run only
python run.py --track professor
```

There is no test suite, linter, or build step configured in this repo (no `tests/`, no pytest
config). Verifying a change generally means running the pipeline live or reasoning through the
node in isolation — see "Testing status" in `README.md` for what has and hasn't been exercised
against real APIs.

Two system packages must be on PATH (not pip-installable): `latexmk` (a LaTeX toolchain) and
`pdftoppm` (from poppler-utils, used to rasterize compiled CVs for the vision quality check).

## Architecture

### The graph (`graph.py`, `state.py`)

```
search ──> rank ──(not done)──> search      [the only genuinely agentic loop]
             │
             └──(done)──> finish ──> report ──> END
```

`GraphState` (a `TypedDict` in `state.py`) is the single object threaded through every node —
every node reads and mutates it directly and returns it. There's no other communication channel
between nodes.

- **search** (`nodes/search.py`) — asks Gemini which 1-3 free sources to query this round and
  with what query, given the profile and the *previous* round's feedback from Rank. Executes the
  calls against `sources/registry.py`, normalizes results into `Candidate` dicts, dedups via
  `seen_ids`.
- **rank** (`nodes/rank.py`) — scores new candidates 0-10 against the profile *and* the CV
  fingerprint (hard requirements like language/visa/clearance are checked explicitly and weighted
  heavily), and writes one concrete feedback sentence back for Search's next round. This
  Search↔Rank exchange (`state["feedback_log"]`) is the actual feedback loop — everything after it
  is a fixed, single-pass sequence.
- **`_should_continue_searching`** (in `graph.py`) is the conditional edge: routes back to search
  unless `DRAFTS_PER_RUN` qualified candidates exist, `MAX_SEARCH_STEPS` is exhausted, or
  `ROUNDS_BEFORE_GIVE_UP` consecutive weak/empty rounds have occurred. It's routing-only and must
  not mutate state (LangGraph doesn't guarantee routing-function mutations persist) — `stop_reason`
  is computed separately by `_determine_stop_reason` inside the `finish` node.
- **finish** (`nodes/finish.py`) — for each qualified, not-already-contacted candidate: find email
  (professor track, `nodes/email_finder.py`), tailor CV (`nodes/cv_tailor.py`), draft content
  (`nodes/drafter.py`), then mark contacted in the SQLite tracker (`nodes/db.py`) so future runs
  never re-surface it.
- **report** (`nodes/report.py`) — renders `output/drafts_<timestamp>.html`, opened in the browser
  by `run.py`. This is the only user-facing checkpoint; review/edit/send happens by hand from here.

**Date grounding:** `SEARCH_PROMPT`, `RANK_PROMPT`, and `questionnaire.py`'s `WARM_START_PROMPT`
all interpolate a `TODAY'S DATE: {today}` line from `datetime.date.today()`. Without it, Gemini has
no way to know the real wall-clock date — only its training cutoff — and will reason about
`target_start_date`/posting recency/"is this ambiguous" using a stale or absent sense of "now"
(this is what caused the setup questionnaire to ask a spurious disambiguating question about a
target start date that was already unambiguous in the profile). If you add a new Gemini call that
reasons about dates (recency, "is this realistic/upcoming", a fixed-cycle start date), give it the
same `today` line rather than assuming the model can infer it.

### Source registry and geography gating (`sources/registry.py`)

Nine free, no-key APIs (5 job boards, 4 academic) are exposed as plain functions with a docstring
each — Gemini picks which to call based on the docstrings, not hardcoded routing. Geography
filtering is **structural, not a prompt hint**: `COVERAGE` tags per source + `classify_geography()`
determine which sources are even offered to the model (`eligible_sources`), and
`onsite_location_mismatch()` drops individual onsite listings that conflict with the candidate's
stated country before they reach Rank. Both fail open (no narrowing) when geography text doesn't
confidently classify. `KNOWN_COVERAGE_GAPS` (Middle East/APAC onsite) surfaces as an explicit
banner in the report rather than silently returning fewer results — see `geography_coverage_note`
in `GraphState`.

### CV pipeline (`nodes/cv_import.py`, `nodes/cv_tailor.py`, `nodes/latex_react.py`)

Two entry points share a compile→render→vision-check ReAct loop (`latex_react.py`):
`--import-cv` reconstructs a full CV from an uploaded PDF into `templates/skeleton.tex`;
`cv_tailor.py` makes a small per-application edit to the resulting `cv_base.tex`. In both cases,
`latexmk` compiling clean isn't trusted alone — the compiled PDF is rasterized via `pdftoppm` and
checked by Gemini vision for structural defects (overflow, overlap, orphaned headers), up to 3
correction attempts, each re-validated structurally before being trusted. `cv_tailor.py` falls
back to the untouched base CV if the loop is exhausted; this is visible in the report itself
(CV line tagged "tailored" vs "base CV (tailoring unavailable)").

**Important invariant, shared by both `cv_import.py` and `cv_tailor.py`:** the model's own LaTeX
preamble output is never requested or used, in either file. In `cv_import.py`, identity fields
(`\name`, `\emaila`, `\linkedin`, `\githuburl`) are extracted independently from the model's
response and spliced onto the skeleton's frozen preamble block (`_skeleton_frozen_block`, split on
the `%----------END FROZEN FORMATTING BLOCK----------` marker in `templates/skeleton.tex`) — the
model is only ever asked for 4 identity lines + the document body, never the preamble itself. In
`cv_tailor.py`, the model is only ever shown/asked for the document body (`\begin{document}` to
`\end{document}`); the real preamble is sliced off `cv_base.tex` once per call and spliced back on
before every compile attempt (`build_tailored_cv` / `_react_fix_and_compile`). If you touch either
file, preserve that split; the earlier design in both (ask the model to reproduce the whole
preamble byte-identically, then diff it) silently broke things in two different ways — `cv_import`
reverted real names back to placeholders because the identity commands lived inside the "frozen"
block being diffed, and `cv_tailor` fell back to the untailored base CV on essentially every run
because any whitespace-level drift in the model's preamble reproduction failed the diff, with no
retry.

Structural validation (`_validate_tailored_body` / `_validate_import_body`) checks the document
body only — document tags present, no `\section{...}` dropped — *before* every compile attempt in
both ReAct loops; a correction that fails this check aborts the loop rather than being used. There
is no preamble check in either validator anymore, since neither file ever gives the model a chance
to touch the preamble in the first place.

### Cross-run persistence

- `nodes/db.py` — SQLite (`logs/tracker.db`) dedup so a candidate is never re-contacted across runs.
- `nodes/memory.py` — at the end of every run, `state["feedback_log"]` is distilled via Gemini into
  an evolving, pruned `profile/search_lessons_<track>.txt` memo (capped ~500 words, not a growing
  raw log). Loaded back into Search's **first round only** on future runs.
- `--setup` additionally makes one conditional Gemini call against the CV fingerprint + answers to
  ask a disambiguating question when genuinely ambiguous; the answer (`search_warm_start`) is also
  injected only into round 1.

### Config and secrets (`config.py`)

Reads `GEMINI_API_KEY`, `GEMINI_MODEL`, and personal details (`YOUR_NAME`/`YOUR_EMAIL`/etc.) from
env vars via `.env` (gitignored, loaded with `python-dotenv`); raises immediately if the API key is
missing. Everything else in `config.py` is a plain committed constant — tuning knobs
(`MAX_SEARCH_STEPS`, `DRAFTS_PER_RUN`, `QUALITY_THRESHOLD`, `ROUNDS_BEFORE_GIVE_UP`,
`GEMINI_CALL_DELAY_SECS`) and file paths. Never hardcode secrets or personal details directly in
source — that's the entire point of this split (the repo is meant to be safely public/shareable).

## Gitignored/generated files

`input/*.pdf`, `cv_base.tex`, `cv_base_preview.pdf`, `profile/profile.json`,
`profile/cv_fingerprint.txt`, `profile/search_lessons_*.txt`, `output/`, `cv_versions/`, `logs/`
are all generated from a specific person's resume/answers and gitignored — don't assume they exist
in a fresh checkout, and don't add real personal content to them in commits.
