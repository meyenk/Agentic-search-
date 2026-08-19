# ============================================================
#  CONFIGURATION
# ============================================================

GEMINI_API_KEY = "get it from your google dev account"
GEMINI_MODEL   = "gemini-3.5-flash"   # current stable GA model, free tier, as of Aug 2026

# ── Your details ──────────────────────────────────────────────
# put your details, have left mine here as it is 
YOUR_NAME     = "Mayank Arya"
YOUR_EMAIL    = "mayankarya510@gmail.com"
YOUR_LINKEDIN = "https://www.linkedin.com/in/mayank-arya-b9b632223/"
YOUR_GITHUB   = "https://github.com/meyenk"   # CONFIRM — placeholder

# ── Search loop control ──────────────────────────────────────
MAX_SEARCH_STEPS   = 15     # hard cap on tool calls per run
DRAFTS_PER_RUN      = 4      # target candidates to fully process
QUALITY_THRESHOLD   = 6.0    # score out of 10 to count as "qualified"
ROUNDS_BEFORE_GIVE_UP = 3    # consecutive weak rounds before stopping early

# ── Rate limiting (Gemini free tier) ─────────────────────────
GEMINI_CALL_DELAY_SECS = 5

# ── Paths ─────────────────────────────────────────────────────
PROFILE_PATH     = "profile/profile.json"
BASE_CV_TEX      = "cv_base.tex"
OUTPUT_DIR       = "output"
CV_VERSIONS_DIR  = "cv_versions"
LOG_FILE         = "logs/pipeline.log"
DB_FILE          = "logs/tracker.db"
