# ============================================================
#  CONFIGURATION
# ============================================================
# Secrets and personal details live in a local .env file (gitignored),
# never in this file — this file is committed to git, .env is not.
# Copy .env.example to .env and fill in your real values.

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional — env vars can also be set directly (e.g. in CI)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Copy .env.example to .env and fill in your "
        "key, or export GEMINI_API_KEY in your shell before running."
    )

# ── Your details ──────────────────────────────────────────────
YOUR_NAME     = os.environ.get("YOUR_NAME", "")
YOUR_EMAIL    = os.environ.get("YOUR_EMAIL", "")
YOUR_LINKEDIN = os.environ.get("YOUR_LINKEDIN", "")
YOUR_GITHUB   = os.environ.get("YOUR_GITHUB", "")

# ── Search loop control ──────────────────────────────────────
MAX_SEARCH_STEPS   = 15     # hard cap on tool calls per run
DRAFTS_PER_RUN      = 5      # target candidates to fully process
QUALITY_THRESHOLD   = 6.0    # score out of 10 to count as "qualified"
ROUNDS_BEFORE_GIVE_UP = 2     # consecutive weak rounds before stopping early

# ── Rate limiting (Gemini free tier) ─────────────────────────
GEMINI_CALL_DELAY_SECS = 4

# ── Paths ─────────────────────────────────────────────────────
PROFILE_PATH     = "profile/profile.json"
BASE_CV_TEX      = "cv_base.tex"
OUTPUT_DIR       = "output"
CV_VERSIONS_DIR  = "cv_versions"
LOG_FILE         = "logs/pipeline.log"
DB_FILE          = "logs/tracker.db"
