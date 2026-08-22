"""
nodes/cv_tailor.py — Tailor LaTeX CV per candidate, compile to PDF.

IMPORTANT DESIGN NOTE — the model's own preamble output is NEVER requested or
used, same fix as nodes/cv_import.py (see that file's docstring for the full
history). Earlier versions asked the model to reproduce the whole base CV,
preamble included, byte-for-byte identical, then rejected the whole tailoring
attempt on the slightest whitespace/formatting drift in the preamble it never
needed to touch in the first place — with no retry, straight to the untailored
base CV. Fixed the same way: the model is only ever shown/asked for the
document body (\\begin{document}...\\end{document}); the real preamble is
sliced off cv_base.tex once and spliced back on ourselves before every
compile attempt. See build_tailored_cv / _react_fix_and_compile below.
"""

import os
import re
import shutil
import logging
import subprocess
import tempfile
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, BASE_CV_TEX, CV_VERSIONS_DIR
from nodes.latex_react import render_pdf_pages, judge_pdf_visual

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)

MAX_REACT_ATTEMPTS = 3


def _load_base_cv() -> str:
    with open(BASE_CV_TEX, "r", encoding="utf-8") as f:
        return f.read()


TAILOR_PROMPT = """
You are an expert CV editor making a SMALL, TARGETED edit to a LaTeX CV for one
specific application. This is NOT a rewrite. Think of this as what a careful human
would do in 5 minutes before hitting submit — small, defensible tweaks, not a
redesign.

You do NOT need to write any \\documentclass/\\usepackage/\\newcommand formatting
infrastructure — that's handled separately and is never touched. You only
produce the document body, from \\begin{{document}} to \\end{{document}}.

OPPORTUNITY:
{opp_summary}

INSTRUCTIONS FROM THE CANDIDATE ABOUT WHAT TO PROTECT / HOW TO EDIT
(these override any default assumption you'd otherwise make — follow them exactly):
{cv_instructions}

BASE CV DOCUMENT BODY (LaTeX, from \\begin{{document}} to \\end{{document}}):
{base_body}

HARD RULES:
1. EVERY \\section{{...}} that exists in the base CV must still exist in your
   output, with the same section name. You may reorder sections, and you may
   shorten or trim entries within a section, but you may not delete a section
   outright or leave it looking sparse/empty — a thin-looking section reads
   worse than a slightly long CV.
2. Anything the candidate's instructions above mark as protected must be
   reproduced exactly as-is — same wording, same order, no trimming.
3. For sections NOT marked protected: prefer MINOR tweaks — reordering bullets
   within an entry, light rewording toward the opportunity's terminology, adding
   a real skill/tool already on the CV to a more prominent spot for ATS
   matching. Do NOT invent skills, projects, or experience that aren't already
   on the base CV.
4. One page is a nice-to-have, not a hard requirement — do not gut real content
   just to hit one page. If it doesn't comfortably fit one page after minor
   trims, two pages with full substance is better than one page that reads
   empty.
5. Do not add new LaTeX commands or packages not already used in the base CV.
6. Return ONLY the document body, from \\begin{{document}} to \\end{{document}}.
   No explanation, no markdown fences, no preamble.
"""


def _call_gemini(prompt: str) -> str:
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = resp.text.strip()
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        return text
    except Exception as e:
        log.error(f"CV tailoring failed: {e}")
        return ""


def _extract_sections(latex: str) -> set:
    return set(re.findall(r"\\section\{([^}]+)\}", latex))


def _validate_tailored_body(base_cv: str, tailored_body: str) -> tuple[bool, str]:
    """Structural sanity check on the document body only — catches the model
    silently dropping a section or truncating its response. No preamble
    concerns here at all: the model is never shown or asked for the preamble
    (see module docstring), so there's nothing to diff or drift there."""
    if "\\begin{document}" not in tailored_body or "\\end{document}" not in tailored_body:
        return False, "missing \\begin{document}/\\end{document}"

    base_sections = _extract_sections(base_cv)
    tailored_sections = _extract_sections(tailored_body)
    missing = base_sections - tailored_sections
    if missing:
        return False, f"sections dropped: {missing}"

    return True, "ok"


def compile_with_log(latex_source: str, output_name: str) -> tuple[str | None, str]:
    """Like compile_to_pdf, but also returns latexmk's error output on
    failure — used by the ReAct loop to feed a real compile error back to
    Gemini for a fix, instead of just failing silently."""
    os.makedirs(CV_VERSIONS_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "cv.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(latex_source)
        try:
            proc = subprocess.run(
                ["latexmk", "-pdf", "-interaction=nonstopmode", "-quiet", "cv.tex"],
                cwd=tmpdir, capture_output=True, text=True, timeout=60,
            )
            pdf_tmp = os.path.join(tmpdir, "cv.pdf")
            if os.path.exists(pdf_tmp):
                out_path = os.path.join(CV_VERSIONS_DIR, f"{output_name}.pdf")
                shutil.copy(pdf_tmp, out_path)
                return out_path, ""
            log.error(f"  latexmk failed for {output_name}")
            return None, (proc.stdout[-3000:] + "\n" + proc.stderr[-1000:])
        except Exception as e:
            log.error(f"  Compilation error: {e}")
            return None, str(e)


def compile_to_pdf(latex_source: str, output_name: str) -> str | None:
    pdf_path, _ = compile_with_log(latex_source, output_name)
    return pdf_path


FIX_COMPILE_ERROR_PROMPT = """
The LaTeX document body below (from \\begin{{document}} to \\end{{document}})
failed to compile. Fix ONLY what's necessary to make it compile — do not
restructure content, and keep every \\section{{...}} that currently exists.

COMPILE ERROR OUTPUT:
{error_log}

DOCUMENT BODY THAT FAILED:
{body}

Return ONLY the corrected document body, from \\begin{{document}} to
\\end{{document}}. No explanation, no markdown fences.
"""

FIX_VISUAL_ISSUE_PROMPT = """
The LaTeX document body below compiled successfully, but a visual check of the
rendered PDF (image(s) attached) found a rendering defect: {reason}

Fix ONLY this specific defect with a small, targeted edit — e.g. trim a line,
tighten a bullet, move a small amount of content — do not restructure the CV,
and keep every \\section{{...}} that currently exists.

DOCUMENT BODY:
{body}

Return ONLY the corrected document body, from \\begin{{document}} to
\\end{{document}}. No explanation, no markdown fences.
"""


def _fix_compile_error(body: str, error_log: str) -> str:
    prompt = FIX_COMPILE_ERROR_PROMPT.format(error_log=error_log[:2000], body=body)
    return _call_gemini(prompt)


def _fix_visual_issue(body: str, images: list[bytes], reason: str) -> str:
    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in images]
    parts.append(FIX_VISUAL_ISSUE_PROMPT.format(reason=reason, body=body))
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=parts)
        text = resp.text.strip()
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        return text
    except Exception as e:
        log.error(f"  Visual-fix Gemini call failed: {e}")
        return ""


def _react_fix_and_compile(base_preamble: str, body: str, base_cv: str, candidate_id: str) -> tuple[str | None, bool]:
    """Compile -> render -> vision-check -> self-correct, up to
    MAX_REACT_ATTEMPTS. The real preamble is spliced onto the model's body
    before every compile attempt — the model itself never sees or produces
    it, so there's no preamble drift possible at any point in the loop. Every
    corrective edit to the body is re-validated structurally (section
    completeness) before being trusted — a correction that drops a section
    aborts the loop rather than being used. Returns (pdf_path, success)."""
    current_body = body

    for attempt in range(1, MAX_REACT_ATTEMPTS + 1):
        full_latex = base_preamble + current_body
        pdf_path, error_log = compile_with_log(full_latex, f"cv_react_{candidate_id}_a{attempt}")

        if not pdf_path:
            log.warning(f"    [ReAct {attempt}/{MAX_REACT_ATTEMPTS}] compile failed — asking Gemini to fix...")
            fixed = _fix_compile_error(current_body, error_log)
            ok, reason = _validate_tailored_body(base_cv, fixed) if fixed else (False, "empty response")
            if not ok:
                log.warning(f"    Fix attempt failed validation ({reason}) — giving up on ReAct loop.")
                return None, False
            current_body = fixed
            continue

        images = render_pdf_pages(pdf_path)
        clean, reason = judge_pdf_visual(images)
        if clean:
            return pdf_path, True

        log.warning(f"    [ReAct {attempt}/{MAX_REACT_ATTEMPTS}] visual defect flagged: {reason}")
        if attempt == MAX_REACT_ATTEMPTS:
            break

        fixed = _fix_visual_issue(current_body, images, reason)
        ok, val_reason = _validate_tailored_body(base_cv, fixed) if fixed else (False, "empty response")
        if not ok:
            log.warning(f"    Fix attempt failed validation ({val_reason}) — giving up on ReAct loop.")
            return None, False
        current_body = fixed

    return None, False


def build_tailored_cv(candidate: dict, profile: dict) -> str | None:
    """Returns pdf_path or None. Runs the tailored edit through a compile +
    vision-check ReAct loop (_react_fix_and_compile) that catches not just
    compile failures but PDFs that "succeed" while looking visually broken.
    Falls back to the untouched base CV, compiled fresh, if it's still
    broken after retries — same safety net as before, just triggered by a
    stricter check."""
    base_cv = _load_base_cv()
    base_preamble = base_cv.split("\\begin{document}")[0]
    base_body = base_cv[base_cv.index("\\begin{document}"):]

    opp_summary = (
        f"Name/Title: {candidate['name']}\n"
        f"Organization: {candidate['org']}\n"
        f"Kind: {candidate['kind']}\n"
        f"Description: {candidate['description'][:500]}"
    )
    cv_instructions = profile.get("cv_instructions", "").strip() or (
        "(none given — use judgment: keep Education, Achievements, and any "
        "full-time/primary experience roles untouched; light tweaks only "
        "elsewhere)"
    )
    prompt = TAILOR_PROMPT.format(
        opp_summary=opp_summary, cv_instructions=cv_instructions, base_body=base_body
    )

    body = _call_gemini(prompt)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", candidate["name"])[:40]
    used_fallback = False
    pdf_path = None

    if not body or "\\begin{document}" not in body or "\\end{document}" not in body:
        log.warning("  Tailoring returned no usable content — falling back to base CV.")
        used_fallback = True

    if not used_fallback:
        pdf_path, success = _react_fix_and_compile(base_preamble, body, base_cv, candidate["id"])
        if not success:
            log.warning("  ReAct compile/visual-check loop exhausted — falling back to base CV.")
            used_fallback = True

    if used_fallback:
        pdf_path = compile_to_pdf(base_cv, f"cv_{safe_name}_{candidate['id']}_base")
    else:
        # Give the successful ReAct output a clean, non-attempt-numbered
        # final filename (the attempt-numbered files stay in cv_versions/
        # too — harmless, and useful if you want to see what got fixed).
        final_path = os.path.join(CV_VERSIONS_DIR, f"cv_{safe_name}_{candidate['id']}.pdf")
        shutil.copy(pdf_path, final_path)
        pdf_path = final_path

    return pdf_path
