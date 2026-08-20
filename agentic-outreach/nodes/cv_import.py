r"""
nodes/cv_import.py — Drop a resume PDF in input/, run this once, get:
  1. cv_base.tex — reconstructed into our polished LaTeX skeleton
     (same macros/preamble we hardened earlier: auto-wrapping resumeProject,
     clean centered header, lmodern font). Content only, formatting is frozen.
  2. profile/cv_fingerprint.txt — a plain-text summary of the CV, generated
     from the actual uploaded PDF rather than hardcoded per-person text.

This is what makes the pipeline shareable: onboarding a new person (or
updating your own CV) is "drop a PDF in input/, run one command" instead of
hand-editing LaTeX.

IMPORTANT DESIGN NOTE — the model's own preamble output is NEVER trusted or
used. Earlier versions asked the model to reproduce the whole preamble
byte-identically and then reverted any drift back to skeleton's placeholder
text — but \name/\emaila/\linkedin/\githuburl are DEFINED inside that same
preamble, so any attempt to fill in the real candidate's contact info
counted as "drift" and got silently wiped back to placeholders every time.
Fixed by never asking the model to produce a preamble at all: it only
outputs the 4 identity \newcommand lines + the document body, and we splice
those onto skeleton's real frozen formatting ourselves. See
_skeleton_frozen_block / _extract_identity_values / import_cv below.
"""

import os
import re
import glob
import logging
import shutil
from pypdf import PdfReader
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, BASE_CV_TEX, CV_VERSIONS_DIR
from nodes.cv_tailor import compile_with_log, _extract_sections
from nodes.latex_react import render_pdf_pages, judge_pdf_visual

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)

INPUT_DIR = "input"
SKELETON_PATH = "templates/skeleton.tex"
FINGERPRINT_PATH = "profile/cv_fingerprint.txt"

MAX_REACT_ATTEMPTS = 3
FROZEN_END_MARKER = "%----------END FROZEN FORMATTING BLOCK----------"

IDENTITY_DEFAULTS = {
    "name": "FULL NAME",
    "emaila": "email@example.com",
    "linkedin": "linkedin-handle",
    "githuburl": "github.com/handle",
}


def find_input_pdf() -> str | None:
    """Looks for exactly one PDF in input/. Returns its path, or None."""
    pdfs = glob.glob(os.path.join(INPUT_DIR, "*.pdf"))
    if not pdfs:
        return None
    if len(pdfs) > 1:
        log.warning(f"Multiple PDFs found in {INPUT_DIR}/, using the first: {pdfs[0]}")
    return pdfs[0]


def extract_pdf_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text.strip()


def _skeleton_frozen_block(skeleton: str) -> str:
    """Everything up to and including the frozen-formatting end marker —
    used verbatim, always. The model never sees or produces this text, so
    it structurally cannot drift, regardless of what the model outputs."""
    if FROZEN_END_MARKER in skeleton:
        return skeleton.split(FROZEN_END_MARKER, 1)[0] + FROZEN_END_MARKER
    # Fallback for a hand-edited skeleton missing the marker: treat the
    # whole preamble as frozen — errs toward protecting formatting.
    log.warning(f"  {FROZEN_END_MARKER!r} not found in skeleton — treating entire preamble as frozen.")
    return skeleton.split("\\begin{document}")[0]


def _extract_identity_values(model_output: str) -> dict:
    """Pulls whatever the model set for each identity command out of its
    response, wherever it appears — falls back to the skeleton's own
    placeholder for any field the model didn't set (rather than leaving a
    blank or malformed line)."""
    values = {}
    for cmd, default in IDENTITY_DEFAULTS.items():
        m = re.search(rf"\\newcommand\{{\\{cmd}\}}\{{(.*?)\}}", model_output)
        val = m.group(1).strip() if m else ""
        values[cmd] = val if val else default
    return values


def _build_identity_block(values: dict) -> str:
    lines = [f"\\newcommand{{\\{cmd}}}{{{values[cmd]}}}" for cmd in IDENTITY_DEFAULTS]
    return "\n".join(lines)


def _extract_doc_body_example(skeleton: str) -> str:
    """Skeleton's own illustrative \\begin{document}...\\end{document} block,
    used as a structural reference in the rebuild prompt."""
    if "\\begin{document}" not in skeleton:
        return ""
    return skeleton[skeleton.index("\\begin{document}"):]


REBUILD_PROMPT = """
You are reconstructing a candidate's resume CONTENT into a specific LaTeX
template. You do NOT need to write any \\documentclass/\\usepackage/\\newcommand
formatting infrastructure — that's handled separately. You only produce:
(1) four identity lines, and (2) the document body.

RAW EXTRACTED TEXT (formatting/visual structure is lost from the original
PDF, but the content and facts are intact — extraction can occasionally
introduce kerning artifacts like "Jo hn Do e" instead of "John Doe"; use
context to correct these when they're obviously just broken spacing, not a
real fact):
{raw_text}

MACROS AVAILABLE — use only these, don't invent new commands:
- \\resumeSubheading{{Title}}{{}}{{Org}}{{Dates}} for jobs/education entries
- \\resumeProject{{Name}}{{Description}}{{Year}} for projects/achievements/skills blocks
- \\resumeItemListStart / \\item ... / \\resumeItemListEnd for bullet lists
- \\resumeSubHeadingListStart / \\resumeSubHeadingListEnd wrapping a group of the above

EXAMPLE DOCUMENT BODY STRUCTURE (illustrative — replace with real content,
keep the macro usage pattern, only include sections with real content in
the source):
{doc_body_example}

RULES:
1. Do NOT invent any content — names, dates, numbers, institutions must come
   directly from the raw text. If something is ambiguous or illegible, use
   your best reasonable interpretation but never fabricate a fact outright.
2. Group content into logical sections: Education, Experience, Projects
   (research/self/academic — split further if the source clearly does),
   Technical Skills, Achievements, Positions of Responsibility — only
   include sections that have real content in the source.
3. Preserve every substantive bullet/fact from the source — this is a
   faithful reformatting, not a summary. Comprehensive is fine, this is the
   base CV; trimming per-application happens later in a separate step.
4. Do not add new LaTeX commands or packages.

Return your response as EXACTLY this shape, nothing else — no explanation,
no markdown fences:

\\newcommand{{\\name}}{{<real full name from source>}}
\\newcommand{{\\emaila}}{{<real email from source — leave as email@example.com only if genuinely not in the source>}}
\\newcommand{{\\linkedin}}{{<real linkedin handle from source — leave as linkedin-handle only if genuinely not in the source>}}
\\newcommand{{\\githuburl}}{{<real github url from source — leave as github.com/handle only if genuinely not in the source>}}
\\begin{{document}}
<full document body content here, using the macros above>
\\end{{document}}
"""

FIX_IMPORT_COMPILE_ERROR_PROMPT = """
The document body below (a reconstructed CV, from \\begin{{document}} to
\\end{{document}}) failed to compile. Fix ONLY the LaTeX syntax issue causing
the failure — keep every \\section{{...}} that currently exists, and don't
change the underlying facts/content, only the LaTeX that's breaking.

COMPILE ERROR OUTPUT:
{error_log}

DOCUMENT BODY THAT FAILED:
{doc_body}

Return ONLY the corrected document body, from \\begin{{document}} to
\\end{{document}}. No explanation, no markdown fences.
"""

FIX_IMPORT_VISUAL_ISSUE_PROMPT = """
The document body below compiled successfully, but a visual check of the
rendered PDF (image(s) attached) found a rendering defect: {reason}

This is a fresh reconstruction from raw extracted resume text, not a small
edit to an already-polished CV — if the defect comes from content being too
dense or overflowing, you may trim wording or restructure the affected
section's layout using the same macros. Keep every \\section{{...}} that
currently exists and don't drop any facts — restructure the presentation,
not the content.

DOCUMENT BODY:
{doc_body}

Return ONLY the corrected document body, from \\begin{{document}} to
\\end{{document}}. No explanation, no markdown fences.
"""

FINGERPRINT_PROMPT = """
Summarize this resume into a dense plain-text fingerprint (200-350 words) for
use as context in an AI matching pipeline — this will be fed into other AI
calls that judge job/opportunity fit, so prioritize the facts that
distinguish this candidate: technical skills, specific project outcomes,
domain focus, seniority level, and any standout achievements. Plain
paragraphs, no headers, no bullet points, no markdown.

RESUME TEXT:
{raw_text}
"""


def _call_gemini(prompt: str) -> str:
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return resp.text.strip()
    except Exception as e:
        log.error(f"Gemini call failed: {e}")
        return ""


def _clean_latex_response(text: str) -> str:
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _fix_import_compile_error(doc_body: str, error_log: str) -> str:
    prompt = FIX_IMPORT_COMPILE_ERROR_PROMPT.format(error_log=error_log[:2000], doc_body=doc_body)
    return _clean_latex_response(_call_gemini(prompt))


def _fix_import_visual_issue(doc_body: str, images: list[bytes], reason: str) -> str:
    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in images]
    parts.append(FIX_IMPORT_VISUAL_ISSUE_PROMPT.format(reason=reason, doc_body=doc_body))
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=parts)
        return _clean_latex_response(resp.text.strip())
    except Exception as e:
        log.error(f"  Visual-fix Gemini call failed: {e}")
        return ""


def _validate_import_body(original_sections: set, doc_body: str) -> tuple[bool, str]:
    """Structural check on a document body — no preamble concerns here at
    all (we control the preamble ourselves), just: does it have proper
    document tags, and were any sections lost relative to the original
    reconstruction."""
    if "\\begin{document}" not in doc_body or "\\end{document}" not in doc_body:
        return False, "missing \\begin{document}/\\end{document}"
    missing = original_sections - _extract_sections(doc_body)
    if missing:
        return False, f"sections dropped: {missing}"
    return True, "ok"


def _react_fix_import(frozen_block: str, identity_block: str, doc_body: str,
                       original_sections: set) -> tuple[str | None, str, bool]:
    """Same compile+vision ReAct pattern as cv_tailor.py's loop, adapted for
    reconstruction: only the document body is ever sent to the model for
    fixing (the frozen block and identity block are already known-good and
    never touched). Returns (pdf_path, final_full_latex, success)."""
    current_body = doc_body

    for attempt in range(1, MAX_REACT_ATTEMPTS + 1):
        full_latex = frozen_block + "\n\n" + identity_block + "\n\n" + current_body
        pdf_path, error_log = compile_with_log(full_latex, f"cv_base_react_a{attempt}")

        if not pdf_path:
            log.warning(f"  [Import ReAct {attempt}/{MAX_REACT_ATTEMPTS}] compile failed — asking Gemini to fix...")
            fixed = _fix_import_compile_error(current_body, error_log)
            ok, reason = _validate_import_body(original_sections, fixed) if fixed else (False, "empty response")
            if not ok:
                log.warning(f"  Fix attempt failed validation ({reason}) — giving up on ReAct loop.")
                return None, frozen_block + "\n\n" + identity_block + "\n\n" + doc_body, False
            current_body = fixed
            continue

        images = render_pdf_pages(pdf_path)
        clean, reason = judge_pdf_visual(images)
        if clean:
            return pdf_path, full_latex, True

        log.warning(f"  [Import ReAct {attempt}/{MAX_REACT_ATTEMPTS}] visual defect flagged: {reason}")
        if attempt == MAX_REACT_ATTEMPTS:
            # Save the best-effort result rather than blocking — there's no
            # separate "base" to fall back to here, this reconstruction IS
            # the base. Caller surfaces `reason` in the final message.
            return pdf_path, full_latex, False

        fixed = _fix_import_visual_issue(current_body, images, reason)
        ok, val_reason = _validate_import_body(original_sections, fixed) if fixed else (False, "empty response")
        if not ok:
            log.warning(f"  Fix attempt failed validation ({val_reason}) — keeping last good attempt.")
            return pdf_path, full_latex, False
        current_body = fixed

    return None, frozen_block + "\n\n" + identity_block + "\n\n" + current_body, False


def import_cv() -> tuple[bool, str]:
    """
    Full import flow. Returns (success, message).
    On success: cv_base.tex and profile/cv_fingerprint.txt are written.
    """
    pdf_path = find_input_pdf()
    if not pdf_path:
        return False, f"No PDF found in {INPUT_DIR}/. Drop your resume PDF there and try again."

    if not os.path.exists(SKELETON_PATH):
        return False, f"No skeleton template found at {SKELETON_PATH}. This is a bundled file — check your install."

    log.info(f"Extracting text from {pdf_path}...")
    raw_text = extract_pdf_text(pdf_path)
    if len(raw_text) < 100:
        return False, (
            "Extracted almost no text from the PDF — it may be a scanned image "
            "rather than a text PDF. This pipeline needs a text-based PDF "
            "(exported directly from Word/LaTeX/Google Docs, not a scan)."
        )

    with open(SKELETON_PATH, "r", encoding="utf-8") as f:
        skeleton = f.read()

    frozen_block = _skeleton_frozen_block(skeleton)
    doc_body_example = _extract_doc_body_example(skeleton)

    log.info("Reconstructing into LaTeX template...")
    prompt = REBUILD_PROMPT.format(raw_text=raw_text, doc_body_example=doc_body_example)
    model_output = _clean_latex_response(_call_gemini(prompt))

    if not model_output or "\\begin{document}" not in model_output:
        return False, "CV reconstruction failed — Gemini did not return valid content. Try again."

    # Identity values + document body are extracted independently and never
    # trusted as a single blob — we rebuild the final file ourselves from
    # skeleton's real formatting + these two extracted pieces. This is the
    # structural fix: the model's own preamble (if it emitted one anyway)
    # is simply discarded, so there's no "drift back to placeholder" failure
    # mode possible anymore.
    identity_values = _extract_identity_values(model_output)
    identity_block = _build_identity_block(identity_values)
    doc_body = model_output[model_output.index("\\begin{document}"):]

    if "\\end{document}" not in doc_body:
        return False, "CV reconstruction failed — Gemini's response was cut off before \\end{document}. Try again."

    original_sections = _extract_sections(doc_body)

    log.info("Compiling to PDF, checking for visual defects (ReAct loop)...")
    os.makedirs(CV_VERSIONS_DIR, exist_ok=True)
    preview_pdf, full_latex, clean = _react_fix_import(frozen_block, identity_block, doc_body, original_sections)

    latex = full_latex

    if not preview_pdf:
        with open(BASE_CV_TEX, "w", encoding="utf-8") as f:
            f.write(latex)
        return False, (
            f"cv_base.tex was generated but failed to compile even after retries. "
            f"Check the LaTeX manually — this can happen with unusual characters or "
            f"formatting from the source PDF. File saved at {BASE_CV_TEX} for you to fix."
        )

    with open(BASE_CV_TEX, "w", encoding="utf-8") as f:
        f.write(latex)

    # Copy preview next to the real base CV for easy access
    shutil.copy(preview_pdf, "cv_base_preview.pdf")

    log.info("Generating CV fingerprint for matching...")
    fingerprint = _call_gemini(FINGERPRINT_PROMPT.format(raw_text=raw_text))
    os.makedirs(os.path.dirname(FINGERPRINT_PATH), exist_ok=True)
    with open(FINGERPRINT_PATH, "w", encoding="utf-8") as f:
        f.write(fingerprint or raw_text[:1500])  # fallback to raw text if summarization fails

    sections = _extract_sections(latex)
    review_note = (
        "   Everything checked out (compile clean, no visual defects found).\n"
        if clean else
        "   ⚠️ Compiled OK but a visual defect was still flagged after 3 fix attempts —\n"
        "   review the preview PDF closely before relying on it; the base LaTeX may\n"
        "   need a manual touch-up in the section that looked off.\n"
    )
    return True, (
        f"✅ CV imported successfully.\n"
        f"   Name/email/links extracted: {identity_values['name']} | {identity_values['emaila']}\n"
        f"   Sections found: {', '.join(sorted(sections))}\n"
        f"   Preview: {os.path.abspath('cv_base_preview.pdf')}\n"
        f"   Fingerprint: {os.path.abspath(FINGERPRINT_PATH)}\n"
        f"{review_note}"
        f"   If anything's still off, you can re-run --import-cv, or edit "
        f"{BASE_CV_TEX} by hand — it's just LaTeX."
    )
