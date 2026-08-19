"""
nodes/cv_import.py — Drop a resume PDF in input/, run this once, get:
  1. cv_base.tex — reconstructed into our polished LaTeX skeleton
     (same macros/preamble we hardened earlier: auto-wrapping resumeProject,
     clean centered header, lmodern font). Content only, formatting is frozen.
  2. profile/cv_fingerprint.txt — a plain-text summary of the CV, generated
     from the actual uploaded PDF rather than hardcoded per-person text.

This is what makes the pipeline shareable: onboarding a new person (or
updating your own CV) is "drop a PDF in input/, run one command" instead of
hand-editing LaTeX.
"""

import os
import re
import glob
import logging
import shutil
from pypdf import PdfReader
from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL, BASE_CV_TEX, CV_VERSIONS_DIR
from nodes.cv_tailor import compile_to_pdf, _extract_sections

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)

INPUT_DIR = "input"
SKELETON_PATH = "templates/skeleton.tex"
FINGERPRINT_PATH = "profile/cv_fingerprint.txt"


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


REBUILD_PROMPT = """
You are reconstructing a candidate's resume into a specific LaTeX template.
The raw text below was extracted from their PDF resume — formatting and
visual structure is lost, but the content and facts are intact.

RAW EXTRACTED TEXT:
{raw_text}

LATEX SKELETON (preamble + macros are FROZEN — reproduce exactly as given.
Only build the content between \\begin{{document}} and \\end{{document}}
using the macros already defined: \\resumeSubheading for jobs/education
entries with dates, \\resumeProject for projects/achievements/skills blocks,
\\resumeItemListStart / \\resumeItemListEnd wrapping \\item bullets):

{skeleton}

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
4. Use the \\name, \\emaila, \\linkedin, \\githuburl commands at the top for
   contact info exactly as found in the source (leave placeholder \\command
   definitions as-is if a field like GitHub isn't in the source).
5. Return ONLY the complete LaTeX document, from \\documentclass to
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

    log.info("Reconstructing into LaTeX template...")
    prompt = REBUILD_PROMPT.format(raw_text=raw_text, skeleton=skeleton)
    latex = _clean_latex_response(_call_gemini(prompt))

    if not latex or "\\begin{document}" not in latex:
        return False, "CV reconstruction failed — Gemini did not return valid LaTeX. Try again."

    # Validate preamble matches skeleton exactly (same guardrail as tailoring)
    base_preamble = skeleton.split("\\begin{document}")[0].strip()
    new_preamble = latex.split("\\begin{document}")[0].strip()
    if base_preamble != new_preamble:
        log.warning("Reconstructed preamble drifted from skeleton — patching it back.")
        latex = base_preamble + "\n\\begin{document}" + latex.split("\\begin{document}", 1)[1]

    # Save + compile
    with open(BASE_CV_TEX, "w", encoding="utf-8") as f:
        f.write(latex)

    log.info("Compiling to PDF for preview...")
    os.makedirs(CV_VERSIONS_DIR, exist_ok=True)
    preview_pdf = compile_to_pdf(latex, "cv_base_preview")
    if not preview_pdf:
        return False, (
            f"cv_base.tex was generated but failed to compile. Check the LaTeX "
            f"manually — this can happen with unusual characters or formatting "
            f"from the source PDF. File saved at {BASE_CV_TEX} for you to fix."
        )

    # Copy preview next to the real base CV for easy access
    shutil.copy(preview_pdf, "cv_base_preview.pdf")

    log.info("Generating CV fingerprint for matching...")
    fingerprint = _call_gemini(FINGERPRINT_PROMPT.format(raw_text=raw_text))
    os.makedirs(os.path.dirname(FINGERPRINT_PATH), exist_ok=True)
    with open(FINGERPRINT_PATH, "w", encoding="utf-8") as f:
        f.write(fingerprint or raw_text[:1500])  # fallback to raw text if summarization fails

    sections = _extract_sections(latex)
    return True, (
        f"✅ CV imported successfully.\n"
        f"   Sections found: {', '.join(sorted(sections))}\n"
        f"   Preview: {os.path.abspath('cv_base_preview.pdf')}\n"
        f"   Fingerprint: {os.path.abspath(FINGERPRINT_PATH)}\n\n"
        f"   Review the preview PDF. If anything looks wrong, you can either\n"
        f"   re-run --import-cv, or edit {BASE_CV_TEX} by hand — it's just LaTeX."
    )
