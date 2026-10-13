"""Spec extraction: the supplier's spec sheet (all pages in one call) → SpecExtraction + evidence boxes.

The model reads values exactly as written (units and decimal commas included); normalising and checking
happen later in code. Every value it read is then found again in the PDF's words, so the review screen
can highlight it on the page.
"""

from __future__ import annotations

from app.clients.llm import LLMClient, LLMResult, user_message
from app.pipeline.ingest import locate_in, page_image
from app.schemas.documents import Evidence, IngestedFile
from app.schemas.extraction import SpecExtraction, SpecValue

PROMPT = (
    "This is a supplier's technical spec sheet for a furniture or home product. It may mix Portuguese, "
    "Italian, German and English, and may span several pages (for example a separate price list). "
    "Extract the fields exactly as written: keep the original units (do not convert), keep decimal "
    "commas, and put the exact text you read in source_text. Use null for empty fields or dashes. "
    "variants: one entry per row of the variants or price table. safety_warnings: every warning sentence "
    "as written, one entry per language version. claims: only statements about health, safety or the "
    "environment (e.g. antibacterial, fireproof, eco-friendly), not design or comfort."
)
MEASURES = ("width", "depth", "height", "seat_height", "seat_depth", "max_load", "weight")
TEXTS = ("legs_or_base", "upholstery", "care", "manufacturer_address", "eu_responsible_person")


async def extract_spec(
    llm: LLMClient, document: IngestedFile, *, purpose: str = "spec"
) -> tuple[SpecExtraction, LLMResult]:
    images = [page_image(page) for page in document.pages]
    return await llm.chat_json(
        [user_message(PROMPT, images)], SpecExtraction, purpose=purpose, max_tokens=2500
    )


def spec_evidence(spec: SpecExtraction, document: IngestedFile) -> dict[str, Evidence]:
    """Where each extracted value sits in the PDF. Keys: field names, 'ean:<variant code>', 'claim:<n>'."""
    found: dict[str, Evidence] = {}

    def add(key: str, *candidates: str | None) -> None:
        for text in candidates:
            if text and (hit := locate_in(document, text)):
                found[key] = Evidence(source="pdf", file=document.name, page=hit[0], bbox=hit[1], text=text)
                return

    for name in MEASURES:
        value: SpecValue = getattr(spec, name)
        if value.value:
            add(name, value.source_text, f"{value.value} {value.unit or ''}".strip(), value.value)
    for name in TEXTS:
        add(name, getattr(spec, name))
    for variant in spec.variants:
        add(f"ean:{variant.code}", variant.ean)
    for n, claim in enumerate(spec.claims):
        add(f"claim:{n}", claim)
    return found
