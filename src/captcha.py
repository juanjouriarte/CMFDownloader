from __future__ import annotations

import logging
import os
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

    primary = os.getenv("CMF_CAPTCHA_MODEL", "gemma-4-31b-it")
    fallback = os.getenv("CMF_CAPTCHA_FALLBACK_MODEL", "gemini-3.1-flash-lite")
    models = [primary] * max_attempts
    if fallback and fallback != primary:
        models.append(fallback)
    for attempt, model in enumerate(models):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                    _PROMPT,
                ],
            )
            answer = (response.text or "").strip().upper()
            if answer:
                return answer
        except Exception as exc:
            # Log status/type only; provider exceptions may contain request details.
            logger.warning("CAPTCHA model %s failed (%s, status=%s)",
                           model, type(exc).__name__, getattr(exc, "code", None))
        if attempt + 1 < len(models):
            time.sleep(5)
    logger.error("CAPTCHA reading failed after %d attempts", len(models))

    return ""
