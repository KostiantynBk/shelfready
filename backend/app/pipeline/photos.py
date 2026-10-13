"""Photo analysis: what a product photo really shows, with boxes as evidence.

One model call per photo (attributes + boxes together), using the spike prompt that scored best on
Gemma 4 31B plus the spike lesson that dark-stained wood is easily read as black metal.
"""

from __future__ import annotations

from pathlib import Path

from app.clients.llm import LLMClient, LLMResult, user_message
from app.schemas.extraction import PhotoAnalysis

PROMPT = (
    "You are a product-data inspector for an online furniture and home shop. Describe only what is "
    "visible in this photo. Do not guess hidden details: use 'unsure' or 'none_visible' when you cannot "
    "see something. Dark-stained wood can look black: answer 'wood' or 'metal' only when wood grain or "
    "metal sheen is clearly visible, otherwise 'unsure'.\n"
    "Put a box_2d around every visible leg (label 'leg'), around a lamp's base (label 'base') and around "
    "any text, logo or badge printed on the image (label 'text'). box_2d is [y0, x0, y1, x1] normalised "
    "to 0-1000."
)


async def analyse_photo(
    llm: LLMClient, image: Path | bytes, *, purpose: str = "photo"
) -> tuple[PhotoAnalysis, LLMResult]:
    return await llm.chat_json(
        [user_message(PROMPT, [image])], PhotoAnalysis, purpose=purpose, max_tokens=1200
    )


def to_bbox(box_2d: list[int]) -> tuple[float, float, float, float]:
    """Gemma's [y0, x0, y1, x1] in 0-1000 → our Evidence bbox (x0, y0, x1, y1) in 0-1, ordered and clamped."""
    y0, x0, y1, x1 = (min(max(v / 1000, 0.0), 1.0) for v in box_2d)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
