from __future__ import annotations

import logging
import time

from google import genai
from google.genai import types

from src.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

_PROMPT = (
    "This is a CAPTCHA image from a Chilean government website. "
    "Read and return ONLY the characters shown — no explanation, "
    "no punctuation, just the characters."
)


def solve(img_bytes: bytes, max_attempts: int = 3) -> str:
    """Solve a CAPTCHA image using Gemini vision. Returns uppercased answer or empty string."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    client = genai.Client(api_key=GEMINI_API_KEY)

    for attempt in range(max_attempts):
        try:
            response = client.models.generate_content(
                model="gemma-4-31b-it",
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                    _PROMPT,
                ],
            )
            return response.text.strip().upper()
        except Exception:
            if attempt < max_attempts - 1:
                logger.warning("Gemini error on attempt %d — retrying", attempt + 1, exc_info=True)
                time.sleep(5)
            else:
                logger.error("Gemini failed after %d attempts", max_attempts, exc_info=True)

    return ""
