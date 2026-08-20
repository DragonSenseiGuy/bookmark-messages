"""Configuration for the AI bookmark-classification pipeline."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- AI endpoint (OpenAI-compatible) ---------------------------------------
# Configurable so the backend can be swapped without touching the code.
# Defaults target OpenRouter, since that is the key present in .env; point
# AI_BASE_URL at a different base url to use that instead.
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://openrouter.ai/api/v1")
AI_API_KEY = os.environ.get("OPENROUTER_AI_KEY") or os.environ.get("AI_API_KEY", "")
AI_CHAT_URL = AI_BASE_URL.rstrip("/") + "/chat/completions"

# Primary model, with a free fallback tried on any error / 429.
PRIMARY_MODEL = os.environ.get("PRIMARY_MODEL", "google/gemini-3.7-flash")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "nvidia/nemotron-3.5-lightning:free")

# Low temperature keeps categorization consistent across runs.
TEMPERATURE = 0.2

# --- Taxonomy --------------------------------------------------------------
# The default tags seeded into a new database.
CATEGORIES = ["Article", "Video", "Repo/Tool", "Product", "Social", "Other"]

# --- Page fetching ---------------------------------------------------------
FETCH_TIMEOUT = 8  # seconds
MAX_TEXT_CHARS = 4000  # description + body text is truncated to this length
HEALTH_CHECK_INTERVAL_HOURS = float(
    os.environ.get("HEALTH_CHECK_INTERVAL_HOURS", "24")
)
HEALTH_CHECK_BATCH_SIZE = int(os.environ.get("HEALTH_CHECK_BATCH_SIZE", "100"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# --- Rate limiting ---------------------------------------------------------
RATE_LIMIT_SLEEP = 0.5  # seconds between AI calls

DEFAULT_RESULT = {
    "title": "",
    "tags": ["Other"],
    "summary": "",
    "keep_as_bookmark": False,
    "reason": "classification failed",
}
