"""
nodes/latex_react.py — Shared LaTeX ReAct primitives.

A compiled PDF can "succeed" (latexmk exits clean) while still looking
visually broken — overflow, overlapping text, an orphaned section header.
Compile success alone doesn't catch that. This module renders the compiled
PDF to page images and has Gemini (vision) judge whether it actually looks
right, structurally — not a style/taste review.

Currently wired into cv_tailor.py only (the hot path that runs once per
application, where formatting can actually drift from the base CV).
cv_import.py's one-time reconstruction can reuse these same primitives later
if needed — it isn't wired in yet.
"""

import os
import re
import json
import logging
import subprocess
import tempfile

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)
log = logging.getLogger(__name__)


def render_pdf_pages(pdf_path: str, max_pages: int = 2) -> list[bytes]:
    """Rasterizes up to max_pages of a PDF into PNG bytes via pdftoppm
    (poppler-utils — a system package, not a pip install; see README)."""
    images: list[bytes] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        out_prefix = os.path.join(tmpdir, "page")
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", "100", "-l", str(max_pages), pdf_path, out_prefix],
                capture_output=True, timeout=30, check=True,
            )
        except Exception as e:
            log.warning(f"  pdftoppm rendering failed: {e}")
            return []
        for fname in sorted(os.listdir(tmpdir)):
            if fname.endswith(".png"):
                with open(os.path.join(tmpdir, fname), "rb") as f:
                    images.append(f.read())
    return images


VISION_JUDGE_PROMPT = """
You are checking a compiled CV/resume PDF (rendered as page image(s) attached)
for STRUCTURAL rendering defects only — not aesthetic taste or style preference.

Flag it as BROKEN only for things like:
- Text overflowing a page margin or box
- Overlapping or visually colliding text
- A section header orphaned alone at the bottom of a page with its content
  pushed to the next page
- Severe, obviously-unintended whitespace gaps (e.g. an almost-empty second
  page caused by one overflowing line)
- Garbled or cut-off text

Do NOT flag: ordinary two-page length, normal spacing choices, font choices,
or anything that is simply "not as pretty as it could be." This is a defect
check, not a design review.

Return ONLY valid JSON, no markdown:
{"clean": <true/false>, "reason": "<if broken, one specific sentence naming the defect; empty string if clean>"}
"""


def judge_pdf_visual(images: list[bytes]) -> tuple[bool, str]:
    """Returns (clean, reason). Fails OPEN (treated as clean) if the vision
    call itself errors out, or if rendering produced no images — a judgment
    failure shouldn't burn a retry attempt or block a candidate whose PDF
    already compiled successfully."""
    if not images:
        return True, ""

    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in images]
    parts.append(VISION_JUDGE_PROMPT)

    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=parts)
        text = resp.text.strip()
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        return bool(result.get("clean", True)), result.get("reason", "")
    except Exception as e:
        log.warning(f"  Vision quality check failed ({e}) — treating as clean.")
        return True, ""
