# Agentic Outreach Pipeline (LangGraph)

Search↔Rank feedback loop discovers professors/jobs, scores them against your
profile, and hands off to a fixed Tailor→Draft→Report sequence with a human
checkpoint before anything gets sent.

## How it actually works

```
  search ──> rank ──(not done yet)──> search   [loops, adapts each round]
                │
                └──(done)──> finish ──> report ──> opens in browser
```

**Search** asks Gemini which free source(s) to query this round (Arbeitnow,
RemoteOK, The Muse, Remotive for jobs; OpenAlex, DBLP, Semantic Scholar,
arXiv for academia) based on your profile and — critically — feedback from
the *previous* round. **Rank** scores what Search found and writes back a
concrete note like "results skew too senior, try postdoc-level terms" or
"good hits, keep going" — that's the actual feedback loop, not just a
linear pipeline.

Once 5 qualified candidates are found, the step budget (15 rounds) runs out,
or 2 rounds in a row come back empty, the loop exits and hands off to a
fixed sequence: find email (professor track) → tailor CV → draft content →
generate the HTML review page.

**Nothing is sent automatically.** You review every draft, edit if needed,
and either click "Open in Gmail" (professor track) or copy the "why this
role" answer into the application portal yourself (job track).

### CV formatting — compile + vision ReAct loop

Compile success alone doesn't catch a visually broken PDF — overflow,
overlapping text, an orphaned section header can all happen while latexmk
exits clean. Per-application CV tailoring (`cv_tailor.py`) now runs through
a loop: compile → render the page(s) with `pdftoppm` → Gemini (vision)
checks the image for structural defects (not style/taste) → if flagged,
the defect image + reason go back to Gemini for a small, targeted fix →
recompile → recheck. Up to 3 attempts; every corrective edit is
re-validated structurally (preamble/section guardrails) before being
trusted. Still broken after 3 attempts → falls back to your untouched base
CV, same as before — now visible in the report itself (each candidate's
CV line is tagged "tailored" or "base CV (tailoring unavailable)").

The base CV itself (`cv_base.tex`, from `--import-cv`) is treated as
content-only and isn't run through this loop — formatting only drifts
during per-application tailoring, so that's where the loop lives.

### Cross-run memory + CV-derived warm start

Two mechanisms feed Search's **first round only** with better context than
a cold start, on top of the in-run feedback loop:

- **Cross-run lessons.** At the end of every run, this run's Search↔Rank
  feedback gets distilled (via Gemini) into an evolving "lessons" memo per
  track, stored at `profile/search_lessons_<track>.txt`. Future runs read
  it back in — so a recurring failure mode (e.g. "RemoteOK rarely returns
  X for this domain") doesn't have to be rediscovered every run. This is a
  merged, pruned summary, not a growing raw log.
- **CV-derived warm start.** After the fixed questionnaire fields,
  `--setup` makes one more Gemini call that reads your CV fingerprint +
  answers and looks for real ambiguity worth disambiguating before the
  first search round (e.g. a CV spanning two domains, a seniority
  mismatch). It's conditional — most CVs won't trigger a question. Your
  answer is saved as `search_warm_start` and injected straight into
  Search's first-round prompt.

## Setup (one-time, ~5 minutes)

```
pip install -r requirements.txt
```

You'll also need two system packages (not pip-installable) already on your
PATH: a LaTeX toolchain providing `latexmk`, and `poppler-utils` providing
`pdftoppm` (used to render compiled CVs for the vision quality check).

Edit `config.py`:
```python
GEMINI_API_KEY = "your key from aistudio.google.com/apikey"
GEMINI_MODEL   = "gemini-3.5-flash"
YOUR_GITHUB    = "https://github.com/your-actual-username"
```

**Drop your resume PDF into `input/`** (any name, must be a text-based PDF —
exported from Word/LaTeX/Google Docs, not a scanned image), then:

```
python run.py --import-cv
```

This extracts your resume's content and reconstructs it into a polished
LaTeX template (the same hardened formatting used throughout — proper
font, auto-wrapping descriptions, clean header) and derives a CV
fingerprint used for matching. Review `cv_base_preview.pdf` afterward — if
anything looks off, either re-run `--import-cv` or edit `cv_base.tex`
directly, it's just LaTeX.

Note: PDF text extraction quality varies by source. Most PDFs extract
cleanly; some (particularly certain LaTeX-generated ones) can show minor
spacing artifacts like "Ma y ank" instead of "Mayank" — Gemini generally
corrects this contextually during reconstruction, but it's worth a glance
at the preview.

Then run the questionnaire, which loads your CV fingerprint automatically:
```
python run.py --setup
```
This asks about track, domains, geography, remote/onsite preference,
availability (yes/no), a specific target start date if relevant (for
fixed-cycle programs only), years of experience (used to judge seniority
fit against job postings), dealbreakers, and free-text instructions for
how your CV should be tailored per application — e.g. which sections are
protected and should never be reworded or cut. After those fixed
questions, it makes one more Gemini call against your CV fingerprint and
your answers, and *conditionally* asks a follow-up if it finds a genuine
ambiguity worth resolving before the first search round — most CVs won't
trigger one. Saved to `profile/profile.json` — edit that file directly
anytime instead of re-running setup.

## Updating your CV later

Drop a new PDF in `input/` (replacing the old one) and re-run
`python run.py --import-cv`. This regenerates both `cv_base.tex` and your
fingerprint. Your questionnaire answers in `profile/profile.json` are
untouched — only re-run `--setup` if your preferences themselves changed.

## Sharing this with someone else

Nothing in this pipeline is hardcoded to one person. Anyone can clone the
project, drop their own resume PDF in `input/`, run `--import-cv` then
`--setup`, and get their own tailored version — the LaTeX skeleton,
fingerprint generation, and questionnaire all work from scratch per person.

## Usage

```
python run.py                    # uses track from your profile
python run.py --track job        # override for this run
python run.py --track professor
```

Takes a few minutes (rate-limited to stay within Gemini's free tier — 4
second pause between calls). Opens the review page in your browser when
done. Safe to run in the background (Windows Task Scheduler) while you work
on something else.

## What's verified vs. what needs a live test

Tested this round, against the real toolchain in the build sandbox (not
mocked):
- `latexmk` compile + real error-log capture on a genuinely broken `.tex`
  file, and on a well-formed one ✓
- `pdftoppm` rendering a real compiled PDF to real PNG bytes ✓
- The ReAct loop's control flow: compile-fail → fix → re-validate →
  succeed; a fix that breaks structural validation aborting the loop
  immediately rather than looping needlessly ✓
- The lessons-memo persistence round-trip (`nodes/memory.py`) — write,
  read back, and safely no-op (not wipe) on a failed merge ✓

Everything else — source parsing, Search's Gemini-plan parsing → candidate
normalization, Rank's scoring, full graph routing — is tested with mocked
API/Gemini responses, same as before.

**Not yet tested against the real internet + your real Gemini key** — the
sandbox this was built in only allowlists a handful of domains (pypi,
github), so Arbeitnow/OpenAlex/Remotive/arXiv/Gemini calls (including the
new vision quality check) couldn't be exercised for real here — the vision
call was confirmed to fail open (treats a blocked/errored call as "clean"
rather than blocking a candidate) when it hit the sandbox's network
allowlist, which is the one live signal available. Your first live run is
the real test. If something breaks, the log at `logs/pipeline.log` will
show exactly which round/source/call failed.

## File structure

```
agentic-outreach/
├── run.py                 ← entry point
├── graph.py                ← LangGraph wiring (the loop lives here)
├── state.py                 ← shared state schema
├── config.py                ← your API key + tuning knobs
├── input/                    ← drop your resume PDF here
├── templates/skeleton.tex     ← person-agnostic LaTeX template used by import
├── cv_base.tex                 ← generated from your PDF (edit directly if needed)
├── sources/registry.py          ← the 8 free-API tools Search picks from
├── nodes/
│   ├── cv_import.py                 ← PDF → LaTeX reconstruction + fingerprint
│   ├── questionnaire.py               ← one-time setup + CV-derived warm-start question
│   ├── search.py                        ← adaptive search round
│   ├── rank.py                            ← scoring + feedback generation
│   ├── finish.py                            ← orchestrates email/CV/draft per candidate
│   ├── email_finder.py                        ← professor email lookup
│   ├── cv_tailor.py                             ← per-application LaTeX tailoring + ReAct compile loop
│   ├── latex_react.py                            ← shared PDF-render + vision quality-check primitives
│   ├── drafter.py                                  ← email / why-this-role writing
│   ├── db.py                                        ← dedup tracker across runs
│   ├── memory.py                                     ← cross-run "lessons" memo per track
│   └── report.py                                       ← HTML review page
├── profile/
│   ├── cv_fingerprint.txt    ← generated by --import-cv
│   ├── profile.json           ← your questionnaire answers (generated by --setup)
│   └── search_lessons_<track>.txt  ← evolving cross-run lessons memo, generated after each run
├── output/                     ← HTML reports land here
├── cv_versions/                  ← tailored CV PDFs land here
└── logs/                           ← pipeline.log + tracker.db
```

## Tuning knobs (config.py)

- `MAX_SEARCH_STEPS` — hard cap on search rounds per run (default 15)
- `DRAFTS_PER_RUN` — target candidates to fully process (default 5)
- `QUALITY_THRESHOLD` — score out of 10 to count as qualified (default 6.0)
- `ROUNDS_BEFORE_GIVE_UP` — consecutive weak rounds before stopping early (default 2)
