# config.py
# ============================================================
# Configuration — API keys and secrets
# UPDATED: Day 17 — Now loads from .env file (secure)
# ============================================================

import os
from dotenv import load_dotenv

# Load variables from .env file (in the same folder as this file)
load_dotenv()

# Read the Gemini API key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Safety check: warn if key is missing
if not GEMINI_API_KEY:
    print("=" * 60)
    print("⚠️  WARNING: GEMINI_API_KEY not found!")
    print("=" * 60)
    print("Please make sure:")
    print("  1. You have a file named '.env' in this folder")
    print("  2. It contains: GEMINI_API_KEY=your_actual_key")
    print("  3. No quotes, no spaces around '='")
    print("=" * 60)