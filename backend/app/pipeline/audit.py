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

from app.pipeline.checks import SUBJECT, _majority
from app.pipeline.normalise import _fold, colour_families, colours_compatible, has_fibre_composition
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

# Photos that show the product; 'other' is a logo or an 'image coming soon' placeholder
PRODUCT_ROLES = {"packshot", "detail", "lifestyle", "promo", "dimension_drawing"}
COLOUR_ROLES = PRODUCT_ROLES - {"dimension_drawing"}
SEATING = r"chair|recliner|seat|rocker|glider|sofa|couch|loveseat|sectional|settee|daybed|stool|ottoman|footrest|footstool|pouf|bench|modul"
FAMILY = {"chair": SEATING, "sofa": SEATING, "lamp": r"lamp|light|lantern|sconce|chandelier|pendant|shade|bulb",
          "rug": r"rug|carpet|\bmat\b|runner"}  # fmt: skip
# The listing name already says which kind of seat it is (accent-free, lower case)
NAME_SAYS = {"chair": r"chair|fauteuil|sillon|silla|stuhl|sessel|poltrona|sedia|stoel|fatolj|\bstol|tabouret|stool|hocker|sgabello|taburete|ottoman|pouf|puff|repose-pieds|reposapies|footstool",
             "sofa": r"sofa|couch|canape|divano|loveseat|settee|sectional|seater|sitzer|zitsbank|soffa"}  # fmt: skip
SAMPLE = r"swatch|sample|muster|muestra|campione|echantillon|staal|tygprov|probka"  # fabric-sample listings
SET = r"\bset\b|juego|\blot\b|pack|paket|\bpair\b|coppia|\bpaar\b|\b\d+\s*(x|er|pcs|pz|uds|st)\b(?!\s*[\d,.])|\b(2|3|4|6|8)\s+(chairs|sillas|chaises|sedie|stuhle|stolar|stoelen)"
# Colour names that honestly sit between two families in a photo
BETWEEN = [(r"teal|petrol|ottanio|turquoise|turchese|aqua|sea ?foam", {"blue", "green"}),
           (r"taupe|greige|mushroom|stone|pietra", {"grey", "beige", "brown"}),
           (r"olive|oliva|sage|salbei|salvia|khaki", {"green", "grey", "beige"})]  # fmt: skip
DARK = r"charcoal|anthra|antracite|graphit|grafite|dark|scuro|oscuro|dunkel|fonce|donker|mork|slate|ardesia"
TWO_TONE = r"gaming|racing"  # black chairs named after their accent colour ('Red' = black with red)
FIBRES = {"wool": r"wool|\blana\b|laine|(?<!baum)wolle|\bull\b", "cotton": r"cotton|cotone|coton|algodon|baumwolle|bomull|katoen",
          "polypropylene": r"polypropyl|polipropil", "polyester": r"polyester|poliester", "jute": r"jute|yuta|iuta",
          "polyethylene": r"polyethyl|polietil", "viscose": r"viscos", "polyamide": r"polyamid|poliamid|nylon",
          "acrylic": r"acryl|acril", "polyurethane": r"polyureth|poliuretan", "silk": r"silk|seta|soie|seide|seda", "sisal": r"sisal"}  # fmt: skip


# Fibre percentages in a description ('100% laine', '57 % polypropylene'); sizes in text ('87 x 181 x 90cm')
PERCENT_FIBRE = re.compile(r"\d{1,3}\s?%\s*(?:" + "|".join(FIBRES.values()) + ")")
SIZE_IN_TEXT = re.compile(r"\d+([.,]\d+)?\s*[x×]\s*\d+|\d+([.,]\d+)?\s*(cm|mm)\b")


def _texts(listing: dict[str, Any], field: str) -> list[str]:
    return [v["value"] for v in listing.get(field, []) if isinstance(v, dict) and v.get("value")]


def _fibres(text: str) -> set[str]:
    return {fibre for fibre, pattern in FIBRES.items() if re.search(pattern, _fold(text))}


def _colour_contradicts(colour_name: str, seen: str) -> bool:
    """True if the photos clearly show a different colour than every colour in the name."""
    folded = _fold(colour_name)
    named = colour_families(colour_name)
    for pattern, families in BETWEEN:
        if re.search(pattern, folded):
            named |= families
    if not named:
        return False
    if seen in {"beige", "grey"} and named & {"beige", "grey", "white"} and not re.search(DARK, folded):
        return False  # light grey and beige are hard to tell apart in photos
    return not any(colours_compatible(n, seen) for n in named)


def audit_listing(item: dict[str, Any], photos: dict[str, dict[str, Any]]) -> list[Issue]:
    """Issues for one existing listing; `photos` maps image_id → PhotoAnalysis (as dict)."""
    listing, category, pid = item["listing"], item["category"], item["item_id"]
    issues: list[Issue] = []

    def add(rule: str, **fields: Any) -> None:
        issues.append(Issue(rule=rule, product_id=pid, **fields))

    name = (_texts(listing, "item_name") or [""])[0]
    folded_name = _fold(name)
    sample = re.search(SAMPLE, folded_name) is not None
    analysed = {i["image_id"]: photos[i["image_id"]] for i in item["images"] if i["image_id"] in photos}
    complete = bool(analysed) and len(analysed) == len(item["images"])
    product_photos = {k: a for k, a in analysed.items() if a["role"] in PRODUCT_ROLES}

    def kind(a: dict[str, Any]) -> str:
        return _fold(a.get("product_type") or "")

    # A9: no photo shows the product (only a logo or an 'image coming soon' placeholder)
    if complete and not product_photos:
        add("A9", type="compliance", severity="blocking", return_risk="missing_info", attribute="photo",
            title="No photo of the product", detail="The images show a logo or a placeholder. GPSR Art. 19(c) requires a picture of the product in online offers.",
            evidence=[Evidence(source="photo", file=k) for k in list(analysed)[:2]])  # fmt: skip

    # A1: the photos show a different kind of product than the category (a wristwatch filed under chairs) …
    in_family = [k for k, a in product_photos.items() if re.search(FAMILY[category], kind(a))]
    if complete and product_photos and not in_family and not sample:
        seen = _majority([a.get("product_type") for a in product_photos.values()])
        add("A1", type="contradictory", severity="blocking", return_risk="missing_info", attribute="category",
            title=f"Listed as {category}, photos show {seen}", spec_value=category, found_value=seen,
            detail="The product category does not match what the photos show; it may be in the wrong place in the catalogue.",
            evidence=[Evidence(source="photo", file=k) for k in list(product_photos)[:2]])  # fmt: skip
    # … or a sofa filed under chairs (and vice versa) when the name does not say which it is
    elif category in ("chair", "sofa"):
        other = "sofa" if category == "chair" else "chair"
        theirs = [k for k, a in product_photos.items()
                  if re.search(SUBJECT[other], kind(a)) and not re.search(SUBJECT[category], kind(a))]  # fmt: skip
        ours = [k for k, a in product_photos.items() if re.search(SUBJECT[category], kind(a))]
        if len(theirs) >= 2 and len(theirs) > len(ours) and not re.search(NAME_SAYS[category], folded_name):
            add("A1", type="contradictory", severity="review", return_risk="missing_info", attribute="category",
                title=f"Listed as {category}, photos show a {other}", spec_value=category, found_value=other,
                detail=f"Shoppers browsing {category}s will not expect a {other}. Check the category.",
                evidence=[Evidence(source="photo", file=k) for k in theirs[:2]])  # fmt: skip
    if complete and product_photos and not in_family and sample:  # the spot check counted these as a problem
        add("A1", type="quality", severity="info", return_risk="none", attribute="category",
            title=f"Fabric sample listed as a {category}", spec_value=category, found_value="fabric sample",
            detail=f"This listing sells a fabric sample, not a {category}; shoppers browsing {category}s land on it. A separate 'samples' category avoids that.",
            evidence=[Evidence(source="photo", file=k) for k in list(product_photos)[:2]])  # fmt: skip

    # A2: colour name vs photos of this product (not lamps: the name describes the base, photos show the shade)
    on_subject = {k: a for k, a in analysed.items()
                  if a["role"] in COLOUR_ROLES and re.search(SUBJECT[category], kind(a))}  # fmt: skip
    readings = [a["colour_family"] for a in on_subject.values()]
    seen = _majority(readings)
    colour = (_texts(listing, "color") or [""])[0]
    two_tone = any(re.search(TWO_TONE, kind(a)) for a in on_subject.values())
    if (category != "lamp" and colour and seen and readings.count(seen) >= 2 and not two_tone
            and _colour_contradicts(colour, seen) and not colour_families(colour) & set(readings)):  # fmt: skip
        add("A2", type="contradictory", severity="review", return_risk="colour", attribute="colour",
            title=f"Colour '{colour}' but the photos look {seen}", spec_value=colour, found_value=seen,
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

    # A4: size and colour fields empty (a fabric sample has no size); milder when the text gives them
    if not dims and not sample and SIZE_IN_TEXT.search(text):
        add("A4", type="missing", severity="info", return_risk="dimensions", attribute="dimensions",
            title="Size only in the text", detail="The name or description gives the size, but the size fields are empty, so shoppers cannot filter or compare by size.")  # fmt: skip
    elif not dims and not sample:
        add("A4", type="missing", severity="review", return_risk="dimensions", attribute="dimensions",
            title="No dimensions", detail="Size is the top reason for furniture returns; add width, depth and height.")  # fmt: skip
    if not colour:
        add("A4", type="missing", severity="info", return_risk="colour", attribute="colour",
            title="Colour only in the name" if colour_families(name) else "No colour",
            detail="The colour field is empty; fill it so shoppers can filter and compare.")  # fmt: skip

    # A5: rug fibre composition (EU Textile Regulation 1007/2011), in the material field or the description
    materials = " ".join(_texts(listing, "material") + _texts(listing, "fabric_type"))
    fibres = _fibres(materials)
    stated = [m.group(0) for m in PERCENT_FIBRE.finditer(_fold(" ".join(_texts(listing, "bullet_point"))))]
    if category == "rug" and not has_fibre_composition(materials) and stated:
        if materials.strip() and not (fibres and fibres <= _fibres(" ".join(stated))):
            add("A5", type="contradictory", severity="review", return_risk="material", attribute="material",
                title="Material field does not match the description", spec_value=materials, found_value=", ".join(stated),
                detail=f"The material field says '{materials}', the description says '{', '.join(stated)}'.")  # fmt: skip
    elif category == "rug" and not has_fibre_composition(materials):
        if not materials.strip():
            add("A5", type="compliance", severity="blocking", return_risk="material", attribute="material",
                title="Fibre composition missing",
                detail="Textile products must state their fibre composition (Regulation 1007/2011, Art. 14).")  # fmt: skip
        elif len(fibres) > 1:
            add("A5", type="compliance", severity="blocking", return_risk="material", attribute="material",
                title="Fibre percentages missing", spec_value=materials,
                detail="With more than one fibre, each must be given with its percentage by weight, e.g. '80% wool, 20% cotton' (Regulation 1007/2011, Art. 9).")  # fmt: skip
        elif fibres:
            add("A5", type="quality", severity="info", return_risk="material", attribute="material",
                title="Write the fibre as '100% …'", spec_value=materials,
                detail=f"'{materials}' alone does not say the rug is pure {next(iter(fibres))}; '100% {next(iter(fibres))}' is clearer for shoppers.")  # fmt: skip
        else:
            add("A5", type="compliance", severity="review", return_risk="material", attribute="material",
                title="Fibre composition unclear", spec_value=materials,
                detail=f"'{materials}' is not a textile fibre name under Regulation 1007/2011 (Annex I).")  # fmt: skip

    # A6–A8: image quality
    for image in item["images"]:
        if max(image["width"], image["height"]) < LOW_RES_PX:
            add("A6", type="quality", severity="info", return_risk="none", attribute="photo", file=image["image_id"],
                title=f"Low-resolution image ({image['width']} × {image['height']} px)", detail="Too small for zoom; replace it.")  # fmt: skip
    main = next((i["image_id"] for i in item["images"] if i["role"] == "main"), None)
    first = analysed.get(main) if main in product_photos else None
    if first and first.get("has_text_overlay"):  # infographics are fine as secondary images
        add("A7", type="quality", severity="info", return_risk="none", attribute="photo", file=main,
            title="Text or graphics on the main image", detail="Marketplaces do not allow text or badges on the main image; move it to a secondary image.",
            evidence=[Evidence(source="photo", file=main)])  # fmt: skip
    if first and not sample and first["role"] != "packshot":
        add("A8", type="quality", severity="info", return_risk="none", attribute="photo", file=main,
            title=f"Main image is a {first['role'].replace('_', ' ')} photo",
            detail="Marketplaces expect the main image to show the product alone on a white background.",
            evidence=[Evidence(source="photo", file=main)])  # fmt: skip
    elif first and not sample and first["products_in_frame"] > 1 and not re.search(SET, folded_name):
        add("A8", type="quality", severity="review", return_risk="missing_info", attribute="quantity", file=main,
            title=f"Main image shows {first['products_in_frame']} products, the name does not say it is a set",
            detail="Shoppers may expect several items. Say 'set of 2' in the name or show one product.",
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
