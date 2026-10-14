# ruff: noqa: E501  (issue texts read better unwrapped)
"""Catalogue health audit: existing listings (ABO, data/abo/items.jsonl, set "audit") vs their photos.

    cd backend && uv run python -m app.pipeline.audit     # after run_extraction --audit-photos

The model only describes each photo (recorded in data/results/audit_photos); every finding below is a
rule in code, so it can be explained. Health score per listing = 100 − 25 per blocking issue − 10 per
issue to review − 3 per info item (minimum 0); the catalogue score is the average.
Writes data/results/audit.json.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.pipeline.checks import PHOTO_ROLES, SUBJECT, _majority
from app.pipeline.normalise import _fold, colour_family, colours_compatible, has_fibre_composition
from app.schemas.documents import Evidence
from app.schemas.review import Issue

ROOT = Path(__file__).resolve().parents[3]
ITEMS = ROOT / "data" / "abo" / "items.jsonl"
PHOTOS = ROOT / "data" / "results" / "audit_photos"
OUT = ROOT / "data" / "results" / "audit.json"
EU_MARKETS = {"amazon.es", "amazon.de", "amazon.it", "amazon.fr", "amazon.nl", "amazon.se"}
INCHES = re.compile(r"\binch|\bpulgada|\bzoll\b|\bpollic|\bpouce|\binches\b|\d\s*(\"|”|'')")
LOW_RES_PX = 800
WEIGHTS = {"blocking": 25, "review": 10, "info": 3}


def _texts(listing: dict[str, Any], field: str) -> list[str]:
    return [v["value"] for v in listing.get(field, []) if isinstance(v, dict) and v.get("value")]


def audit_listing(item: dict[str, Any], photos: dict[str, dict[str, Any]]) -> list[Issue]:
    """Issues for one existing listing; `photos` maps image_id → PhotoAnalysis (as dict)."""
    listing, category, pid = item["listing"], item["category"], item["item_id"]
    issues: list[Issue] = []

    def add(rule: str, **fields: Any) -> None:
        issues.append(Issue(rule=rule, product_id=pid, **fields))

    name = (_texts(listing, "item_name") or [""])[0]
    analysed = {i["image_id"]: photos[i["image_id"]] for i in item["images"] if i["image_id"] in photos}
    product_photos = {k: a for k, a in analysed.items() if a["role"] in PHOTO_ROLES}
    on_subject = {
        k: a
        for k, a in product_photos.items()
        if re.search(SUBJECT[category], _fold(a.get("product_type") or ""))
    }

    # A1: every product photo shows a different kind of product than the listing's category
    if product_photos and not on_subject and len(analysed) == len(item["images"]):
        seen = _majority([a.get("product_type") for a in product_photos.values()])
        add("A1", type="contradictory", severity="blocking", return_risk="missing_info", attribute="category",
            title=f"Listed as {category}, photos show {seen}", spec_value=category, found_value=seen,
            detail="The product category does not match what the photos show; it may be in the wrong place in the catalogue.",
            evidence=[Evidence(source="photo", file=k) for k in list(product_photos)[:2]])  # fmt: skip

    # A2: colour name vs photos (photos of this product only)
    named = colour_family(" ".join(_texts(listing, "color")))
    readings = [a["colour_family"] for a in on_subject.values()]
    seen = _majority(readings)
    if named and seen and readings.count(seen) >= 2 and not colours_compatible(named, seen):
        add("A2", type="contradictory", severity="review", return_risk="colour", attribute="colour",
            title=f"Colour '{_texts(listing, 'color')[0]}' but the photos look {seen}",
            spec_value=_texts(listing, "color")[0], found_value=seen,
            detail="Shoppers return products whose colour differs from the listing. Check the colour name or the photos.",
            evidence=[Evidence(source="photo", file=k) for k in list(on_subject)[:2]])  # fmt: skip

    # A3: inches on a European marketplace
    dims = listing.get("item_dimensions", {})
    units = {(d.get("unit") or "").lower() for d in dims.values() if isinstance(d, dict)}
    text = _fold(" ".join([name, *_texts(listing, "bullet_point")]))
    if item["domain"] in EU_MARKETS and ("inches" in units or INCHES.search(text)):
        add("A3", type="compliance", severity="review", return_risk="dimensions", attribute="dimensions",
            title="Dimensions in inches on a European shop",
            detail="EU shoppers expect metric units (Directive 80/181/EEC); inches cause size mistakes and returns. Convert to cm.")  # fmt: skip

    # A4: key attributes missing
    if not dims:
        add(
            "A4",
            type="missing",
            severity="review",
            return_risk="dimensions",
            attribute="dimensions",
            title="No dimensions",
            detail="Size is the top reason for furniture returns; add width, depth and height.",
        )
    if not _texts(listing, "color"):
        add(
            "A4",
            type="missing",
            severity="info",
            return_risk="colour",
            attribute="colour",
            title="No colour",
            detail="Add the colour so shoppers can filter and compare.",
        )

    # A5: a rug without fibre composition (EU Textile Regulation 1007/2011)
    materials = " ".join(_texts(listing, "material") + _texts(listing, "fabric_type"))
    if category == "rug" and not has_fibre_composition(materials):
        add("A5", type="compliance", severity="blocking", return_risk="material", attribute="material",
            title="Fibre composition missing", spec_value=materials or None,
            detail="Textile products must state their fibre composition with percentages, e.g. '100% wool'.")  # fmt: skip

    # A6–A8: image quality
    for image in item["images"]:
        if max(image["width"], image["height"]) < LOW_RES_PX:
            add("A6", type="quality", severity="info", return_risk="none", attribute="photo", file=image["image_id"],
                title=f"Low-resolution image ({image['width']} × {image['height']} px)", detail="Too small for zoom; replace it.")  # fmt: skip
    for image_id, analysis in product_photos.items():
        if analysis.get("has_text_overlay"):
            add("A7", type="quality", severity="info", return_risk="none", attribute="photo", file=image_id,
                title="Text or graphics on a product photo", detail="Marketplaces do not allow text on the main image.",
                evidence=[Evidence(source="photo", file=image_id)])  # fmt: skip
    main = next((i["image_id"] for i in item["images"] if i["role"] == "main"), None)
    if main in analysed and (
        not analysed[main]["white_background"] or analysed[main]["products_in_frame"] > 1
    ):
        add("A8", type="quality", severity="info", return_risk="none", attribute="photo", file=main,
            title="Main image is not a clean packshot",
            detail="Marketplaces expect the main image on a white background with the product alone.",
            evidence=[Evidence(source="photo", file=main)])  # fmt: skip
    return issues


def score(issues: list[Issue]) -> int:
    return max(0, 100 - sum(WEIGHTS[i.severity] for i in issues))


def run() -> dict[str, Any]:
    items = [json.loads(line) for line in ITEMS.read_text(encoding="utf-8").splitlines() if line]
    listings, by_rule, by_category, by_market = [], Counter(), defaultdict(list), defaultdict(list)
    for item in (x for x in items if x["set"] == "audit"):
        path = PHOTOS / f"{item['item_id']}.json"
        photos = json.loads(path.read_text(encoding="utf-8"))["photos"] if path.exists() else {}
        issues = audit_listing(item, photos)
        points = score(issues)
        by_rule.update(i.rule for i in issues)
        by_category[item["category"]].append(points)
        by_market[item["domain"]].append(points)
        listings.append({"item_id": item["item_id"], "domain": item["domain"], "category": item["category"],
                         "name": (_texts(item["listing"], "item_name") or [""])[0], "score": points,
                         "photos_analysed": len(photos), "issues": [i.model_dump() for i in issues]})  # fmt: skip

    def mean(values: list[int]) -> float:
        return round(sum(values) / len(values), 1) if values else 0.0

    return {
        "summary": {
            "listings": len(listings),
            "photos_analysed": sum(x["photos_analysed"] for x in listings),
            "health_score": mean([x["score"] for x in listings]),
            "listings_with_blocking_issues": sum(
                1 for x in listings if any(i["severity"] == "blocking" for i in x["issues"])
            ),
            "issues_by_rule": dict(sorted(by_rule.items())),
            "score_by_category": {k: mean(v) for k, v in sorted(by_category.items())},
            "score_by_marketplace": {k: mean(v) for k, v in sorted(by_market.items())},
            "how_calculated": "Per listing: 100 − 25 × blocking − 10 × review − 3 × info issues (minimum 0). Catalogue: the average.",
        },
        "listings": sorted(listings, key=lambda x: x["score"]),
    }


def main() -> int:
    report = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    s = report["summary"]
    print(f"{s['listings']} listings, {s['photos_analysed']} photos analysed · health {s['health_score']}/100 · "
          f"{s['listings_with_blocking_issues']} with blocking issues")  # fmt: skip
    print("issues by rule:", s["issues_by_rule"])
    print("by category:", s["score_by_category"])
    print("by marketplace:", s["score_by_marketplace"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
