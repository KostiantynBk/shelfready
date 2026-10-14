# ruff: noqa: E501  (prompt text)
"""Shop copy from verified facts only (runbook §5 "Listing").

The facts come from the supplier's spec as read by spec.py. Anything with an open finding of the checks
(blocking or review) is held back until a person decides; a decision can supply the corrected value
(the drawing's seat height, the care label's washing instructions, the colour seen in the photos).
The model then writes English copy from those facts and nothing else; translate_qa.py translates it and
checks every number, unit and protected name in code.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from app.clients.llm import LLMClient, LLMResult, user_message
from app.pipeline.normalise import parse_number, to_cm, to_kg
from app.schemas.listing import ListingCopy
from app.schemas.review import Decision, Issue

LENGTHS = ("width", "depth", "height", "seat_height", "seat_depth")
MASSES = ("max_load", "weight")
TEXTS = ("product_type", "legs_or_base", "upholstery", "care", "country_of_origin")
VARIANT_LEVEL = {"colour", "ean"}  # held back for that variant only
PHOTO_ROLES = ("packshot", "detail", "lifestyle")
MAX_PHOTOS = 8
VIEWS = re.compile(r"front|back|side|top|detail|34|lifestyle", re.I)
LEGAL_SUFFIX = re.compile(r",?\s*(lda|s\.?r\.?l|s\.?p\.?a|gmbh|s\.?l|sa|ltd)\.?$", re.I)

PROMPT = (
    "Write an English product listing for an EU online furniture and home shop using ONLY the verified facts below. "
    "Do not add facts, claims or measurements that are not listed. Facts may be written in Portuguese, Italian or German, "
    "often followed by an English version after '/': write natural English. Corrections override the supplier's text. "
    "Keep every number exactly as given with its unit (cm, kg, °C), and keep the brand, product name and codes unchanged. "
    "colours: one entry per variant code, with an English colour name. safety_warnings: every warning, translated "
    "faithfully into English (empty list if none are given). alt_texts: one per photo listed, saying what the photo shows "
    "for a blind shopper (product, colour, view), without marketing words.\n"
    "Facts: {facts}\nPhotos: {photos}"
)


def _number(value: float | None) -> float | int | None:
    return None if value is None else int(value) if float(value).is_integer() else value


def model_name(spec: dict[str, Any]) -> str | None:
    """The product's model name, written in capitals by every supplier: 'Poltrona LISBOA / LISBOA armchair' → LISBOA."""
    words = re.findall(r"\b[A-ZÀ-Ý]{3,}\b", spec.get("product_name") or "")
    return Counter(words).most_common(1)[0][0] if words else None


def brand(spec: dict[str, Any]) -> str | None:
    """'Móveis Lusa, Lda.' → 'Móveis Lusa'."""
    name = (spec.get("manufacturer") or spec.get("supplier") or "").strip()
    return LEGAL_SUFFIX.sub("", name).strip() or None


def verified_facts(
    spec: dict[str, Any], issues: list[Issue], decisions: dict[str, Decision] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(facts for the copy, what is held back and why). Info findings never hold anything back."""
    decisions = decisions or {}
    facts: dict[str, Any] = {
        "product_name": spec.get("product_name"),
        "model": model_name(spec),
        "brand": brand(spec),
    }
    for key in LENGTHS:
        value = spec.get(key) or {}
        facts[f"{key}_cm"] = _number(to_cm(parse_number(value.get("value")), value.get("unit")))
    for key in MASSES:
        value = spec.get(key) or {}
        facts[f"{key}_kg"] = _number(to_kg(parse_number(value.get("value")), value.get("unit")))
    for key in TEXTS:
        facts[key] = spec.get(key)
    facts["safety_warnings"] = list(spec.get("safety_warnings") or [])
    variants = {
        v["code"]: {"code": v["code"], "colour_name": v.get("colour_name")}
        for v in spec.get("variants") or []
    }
    corrections: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []

    for issue in issues:
        if issue.severity == "info" or issue.attribute in (
            "photo",
            "ean",
        ):  # photos are chosen below; EANs aren't copy
            continue
        decision = decisions.get(issue.id)
        if decision is None or decision.action == "ask_supplier":
            held.append({"attribute": issue.attribute, "variant": issue.variant, "reason": issue.title,
                         "issue": issue.id, "asked_supplier": decision is not None})  # fmt: skip
            continue
        value = decision.value or (
            issue.found_value or issue.proposed_value if decision.action == "use_found" else None
        )
        if decision.action in ("use_found", "edit") and value:
            corrections.append({"attribute": issue.attribute, "variant": issue.variant, "value": value})

    for item in held:
        attribute, variant = item["attribute"], item["variant"]
        if attribute in VARIANT_LEVEL and variant in variants:
            variants.pop(variant)
        elif attribute in LENGTHS:
            facts[f"{attribute}_cm"] = None
        elif attribute in MASSES:
            facts[f"{attribute}_kg"] = None
        elif attribute == "dimensions":
            for key in LENGTHS:
                facts[f"{key}_cm"] = None
        else:
            facts[attribute] = [] if attribute == "safety_warnings" else None
    for fix in corrections:
        if fix["attribute"] in LENGTHS:
            facts[f"{fix['attribute']}_cm"] = _number(to_cm(parse_number(fix["value"]), _unit(fix["value"])))
        elif fix["attribute"] in MASSES:
            facts[f"{fix['attribute']}_kg"] = _number(to_kg(parse_number(fix["value"]), _unit(fix["value"])))
        elif fix["variant"] is None and fix["attribute"] in facts:
            facts[fix["attribute"]] = fix["value"]
    variant_fixes = [f for f in corrections if f["variant"] and f["attribute"] not in LENGTHS + MASSES]
    if variant_fixes:
        facts["corrections"] = variant_fixes
    facts["variants"] = list(variants.values())
    return {k: v for k, v in facts.items() if v not in (None, [], "")}, held


def _unit(value: str) -> str | None:
    match = re.search(r"\d\s*([a-zA-Z]+)", value)
    return match.group(1) if match else None


def listing_photos(
    result: dict[str, Any], issues: list[Issue], variant_codes: list[str]
) -> list[dict[str, Any]]:
    """Product photos fit for the shop (no promo text, no low resolution, no open photo finding), packshots first."""
    flagged = {i.file for i in issues if i.file}
    suffixes = {code.rsplit("-", 1)[-1].upper(): code for code in variant_codes}
    photos = []
    for name, entry in (result.get("images") or {}).items():
        seen = entry.get("analysis") or {}
        if (
            seen.get("role") not in PHOTO_ROLES
            or seen.get("has_text_overlay")
            or entry.get("low_res")
            or name in flagged
        ):
            continue
        tokens = set(re.split(r"[_\-. ]", name.upper()))
        view = VIEWS.search(name)
        photos.append({"file": name, "variant": next((c for s, c in suffixes.items() if s in tokens), None),
                       "view": (view.group(0).lower().replace("34", "three-quarter") if view else seen["role"]),
                       "colour_seen": seen.get("colour_family")})  # fmt: skip
    photos.sort(key=lambda p: (p["view"] not in ("front", "three-quarter"), p["file"]))
    return photos[:MAX_PHOTOS]


async def write_listing(
    llm: LLMClient, facts: dict[str, Any], photos: list[dict[str, Any]], *, purpose: str
) -> tuple[ListingCopy, LLMResult]:
    prompt = PROMPT.format(
        facts=json.dumps(facts, ensure_ascii=False), photos=json.dumps(photos, ensure_ascii=False)
    )
    return await llm.chat_json([user_message(prompt)], ListingCopy, purpose=purpose, max_tokens=2500)
