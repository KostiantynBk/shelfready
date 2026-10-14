# ruff: noqa: E501  (issue texts read better unwrapped)
"""The checks: what the model read (spec sheet, photos, label, drawing) → issues with evidence.

Every rule is code, so every flag is explainable; the model only reads. Rule ids follow the defect
catalogue (data/defects.yaml): D1 missing dimension … D17 implausible value. Input is one extraction
result (app.pipeline.run_extraction): {"submission_id", "spec", "spec_evidence", "images", …}.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.pipeline.normalise import (
    _fold,
    care_category,
    colour_family,
    colours_compatible,
    gtin_valid,
    has_fibre_composition,
    material_by_variant,
    parse_number,
    to_cm,
    to_kg,
)
from app.pipeline.photos import to_bbox
from app.schemas.documents import Evidence
from app.schemas.review import Issue

PHOTO_ROLES = {"packshot", "detail", "lifestyle", "promo", "other"}  # what counts as a product photo
CONCRETE_MATERIALS = {"wood", "metal", "plastic", "glass", "ceramic", "stone", "fabric"}
DIMENSION_TOLERANCE_CM = 3.0
PLAUSIBLE = {  # (min, max) per category; outside → likely a typo (D17)
    "max_load_kg": {"chair": (60, 250), "sofa": (150, 900)},
    "weight_kg": {"chair": (2, 90), "sofa": (15, 250), "lamp": (0.2, 20), "rug": (0.3, 50)},
}
NON_EU = re.compile(
    r"\b(china|cn|prc|vietnam|viet nam|turkey|turkiye|india|usa|united states|indonesia|malaysia|"
    r"bangladesh|pakistan|taiwan|thailand|mexico|united kingdom|uk)\b"
)
NON_TEXTILE = re.compile(r"leather|pele|pelle|leder|cuir|cuero")
# A postal address has a street and a postcode; a company-register line (HRB, VAT number) is not one
STREET = re.compile(
    r"\b(rua|avenida|via|viale|piazza|corso|calle|carrer|rue|street|road|avenue|lane|no\.)\b|strasse|str\.|weg\b|platz\b|allee\b"
)
POSTCODE = re.compile(r"\b\d{4}-\d{3}\b|\b\d{4,6}\b")
# A legs/base value that is really the frame row (no legs row on the sheet): not comparable with legs
FRAME_ONLY = re.compile(r"plywood|contraplacado|sperrholz|multistrato|foam|espuma|schaum|poliuretano")
# The photo must show this product, not another object in the scene (a sofa in a rug shot, a lampshade)
SUBJECT = {"chair": r"chair|recliner|seat|rocker|glider", "sofa": r"sofa|couch|loveseat|sectional|settee|daybed",
           "lamp": r"lamp(?!shade)", "rug": r"rug|carpet|\bmat\b"}  # fmt: skip


def product_category(spec: dict[str, Any]) -> str:
    text = _fold(f"{spec.get('product_type') or ''} {spec.get('product_name') or ''}")
    if re.search(r"tapete|tappeto|teppich|\brug\b|alfombra|tapis", text):
        return "rug"
    if re.search(r"candeeiro|lampada|leuchte|\blamp|lampara|lampe", text):
        return "lamp"
    if re.search(r"sofa|divano|couch|loveseat|canape", text):
        return "sofa"
    return "chair"


def _variant_of(file_name: str, suffixes: dict[str, str]) -> str | None:
    """Which variant a file claims to show, from its name ('LIS01_MUS_front.jpg' → LIS-01-MUS)."""
    tokens = set(re.split(r"[_\-.\s]+", file_name.upper()))
    return next((code for suffix, code in suffixes.items() if suffix in tokens), None)


def _majority(values: list[str | None]) -> str | None:
    counted = Counter(v for v in values if v)
    return counted.most_common(1)[0][0] if counted else None


class _Checker:
    def __init__(self, result: dict[str, Any]):
        self.pid = result["submission_id"]
        self.spec: dict[str, Any] = result["spec"] or {}
        self.spec_evidence: dict[str, Any] = result.get("spec_evidence", {})
        self.images: dict[str, dict[str, Any]] = result.get("images", {})
        self.category = product_category(self.spec)
        self.variants: list[dict[str, Any]] = self.spec.get("variants", [])
        self.codes = [v["code"] for v in self.variants]
        self.suffixes = {code.rsplit("-", 1)[-1].upper(): code for code in self.codes}
        self.issues: list[Issue] = []

    # ------------------------------------------------------------------- helpers
    def add(self, rule: str, **fields: Any) -> None:
        self.issues.append(Issue(rule=rule, product_id=self.pid, **fields))

    def pdf(self, *keys: str) -> list[Evidence]:
        return [Evidence.model_validate(self.spec_evidence[k]) for k in keys if k in self.spec_evidence]

    def photo(self, name: str, label: str | None = None, note: str | None = None) -> Evidence:
        boxes = [
            b for b in self.images[name]["analysis"].get("boxes", []) if label is None or b["label"] == label
        ]
        bbox = to_bbox(boxes[0]["box_2d"]) if boxes else None
        return Evidence(source="photo", file=name, bbox=bbox, note=note)

    def measure(self, name: str) -> tuple[float | None, str | None]:
        value = self.spec.get(name) or {}
        return parse_number(value.get("value")), value.get("unit")

    def product_photos(self) -> dict[str, dict[str, Any]]:
        return {n: e for n, e in self.images.items() if e["analysis"]["role"] in PHOTO_ROLES}

    def shows_product(self, name: str) -> bool:
        return (
            re.search(SUBJECT[self.category], _fold(self.images[name]["analysis"].get("product_type") or ""))
            is not None
        )

    def colours(self, names: list[str]) -> dict[str, str]:
        """Colour per photo; for lamps a white reading is usually the shade, so it only counts if nothing else is seen."""
        seen = {n: self.images[n]["analysis"]["colour_family"] for n in names}
        if self.category == "lamp" and any(c != "white" for c in seen.values()):
            seen = {n: c for n, c in seen.items() if c != "white"}
        return seen

    def photos_by_variant(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {code: [] for code in self.codes}
        for name in filter(self.shows_product, self.product_photos()):
            if (code := _variant_of(name, self.suffixes)) is not None:
                grouped[code].append(name)
        return grouped

    def own_drawings(self) -> list[tuple[str, dict[str, Any]]]:
        """Drawings that carry this product's code; other infographics (e.g. in inches) are not trusted."""
        code = re.sub(r"\W", "", (self.codes[0].rsplit("-", 1)[0] if self.codes else "")).upper()
        return [(n, e["drawing"]) for n, e in self.images.items()
                if "drawing" in e and code and code in re.sub(r"\W", "", (e["drawing"].get("product_code") or "").upper())]  # fmt: skip

    # ------------------------------------------------------------------- rules
    def dimensions(self) -> None:
        seating = self.category in ("chair", "sofa")
        drawings = self.own_drawings()
        if seating and parse_number((self.spec.get("seat_height") or {}).get("value")) is None:  # D1
            proposal = next(((n, d["seat_height_cm"]) for n, d in drawings if d.get("seat_height_cm")), None)
            self.add("D1", type="missing", severity="review", return_risk="dimensions", attribute="seat_height",
                     title="Seat height missing",
                     detail="The spec sheet has no seat height." + (" The dimension drawing shows one; confirm it." if proposal else ""),
                     proposed_value=f"{proposal[1]:g} cm" if proposal else None,
                     evidence=[Evidence(source="drawing", file=proposal[0], note="seat height")] if proposal else [])  # fmt: skip
        lengths = {n: self.measure(n) for n in ("width", "depth", "height", "seat_height", "seat_depth")}
        for name, (value, unit) in lengths.items():  # D2: a millimetre value among centimetres
            if value is not None and (unit or "").lower().strip() == "mm":
                self.add("D2", type="quality", severity="info", return_risk="none", attribute=name,
                         title=f"{name.replace('_', ' ').capitalize()} given in mm",
                         detail=f"Converted automatically: {value:g} mm = {to_cm(value, 'mm'):g} cm.",
                         spec_value=f"{value:g} mm", proposed_value=f"{to_cm(value, 'mm'):g} cm", evidence=self.pdf(name))  # fmt: skip
        for drawing_file, drawing in drawings:  # D11: the text contradicts the product's own drawing
            for name in ("width", "depth", "height"):
                value, unit = lengths[name]
                spec_cm, drawn = to_cm(value, unit), drawing.get(f"{name}_cm")
                if spec_cm is not None and drawn and abs(spec_cm - drawn) > DIMENSION_TOLERANCE_CM:
                    self.add("D11", type="contradictory", severity="review", return_risk="dimensions", attribute=name,
                             title=f"{name.capitalize()}: spec {spec_cm:g} cm, drawing {drawn:g} cm",
                             detail="The spec sheet and the supplier's dimension drawing disagree. Ask the supplier.",
                             spec_value=f"{spec_cm:g} cm", found_value=f"{drawn:g} cm",
                             evidence=[*self.pdf(name), Evidence(source="drawing", file=drawing_file, note=name)])  # fmt: skip
            break  # one drawing is enough

    def eans(self) -> None:
        for variant in self.variants:
            ean = (variant.get("ean") or "").strip()
            if not ean:  # D3
                self.add("D3", variant=variant["code"], type="missing", severity="blocking", return_risk="none",
                         attribute="ean", title=f"{variant['code']}: EAN missing",
                         detail="Marketplaces need a GTIN/EAN per variant. Ask the supplier.")  # fmt: skip
            elif not gtin_valid(ean):  # D4
                self.add("D4", variant=variant["code"], type="contradictory", severity="blocking", return_risk="none",
                         attribute="ean", title=f"{variant['code']}: EAN check digit is wrong", spec_value=ean,
                         detail="The GS1 check digit does not match; it is probably a typo. Ask the supplier.",
                         evidence=self.pdf(f"ean:{variant['code']}"))  # fmt: skip

    def care(self) -> None:  # D5
        spec_care = care_category(self.spec.get("care"))
        for name, entry in self.images.items():
            label = entry.get("label")
            if not label or spec_care is None:
                continue
            no_wash = label.get("do_not_wash") or label.get("dry_clean_only")
            label_care = "no washing" if no_wash else "washable" if label.get("wash_max_temp_c") else None
            clash = (spec_care == "wash" and no_wash) or (
                spec_care in ("dry_clean", "professional") and label_care == "washable"
            )
            if label_care and clash:
                found = (
                    f"washable at {label['wash_max_temp_c']} °C"
                    if label_care == "washable"
                    else "do not wash / dry clean"
                )
                self.add("D5", type="contradictory", severity="review", return_risk="care", attribute="care",
                         title="Care instructions contradict the care label", spec_value=self.spec.get("care"),
                         found_value=found, detail="The spec sheet and the sewn-in label give different care. A person decides; ask the supplier.",
                         evidence=[*self.pdf("care"), Evidence(source="label", file=name, note=found)])  # fmt: skip
                return

    def photos(self) -> None:
        grouped = self.photos_by_variant()
        key = "base_material" if self.category == "lamp" else "legs_material"
        legs_text = self.spec.get("legs_or_base")
        spec_materials = material_by_variant(
            None if FRAME_ONLY.search(_fold(legs_text or "")) else legs_text, self.codes
        )
        clashes = []
        for code, names in grouped.items():  # D6: material in the text vs the photos
            seen = _majority([self.images[n]["analysis"][key] for n in names
                              if self.images[n]["analysis"][key] in CONCRETE_MATERIALS])  # fmt: skip
            claimed = spec_materials.get(code)
            if seen and claimed and seen != claimed:
                clashes.append((code, claimed, seen, names))
        if clashes:
            whole = len(clashes) == len(self.codes) and len({c[1] for c in clashes}) == 1
            for code, claimed, seen, names in clashes[:1] if whole else clashes:
                part = "legs" if key == "legs_material" else "base"
                self.add("D6", variant=None if whole else code, type="contradictory", severity="review",
                         return_risk="material", attribute="legs_or_base", spec_value=claimed, found_value=seen,
                         title=f"{part.capitalize()}: spec says {claimed}, photos show {seen}",
                         detail="The material in the spec sheet does not match the photos. A person decides; ask the supplier.",
                         evidence=[*self.pdf("legs_or_base"), *(self.photo(n, "leg" if part == "legs" else "base") for n in names[:2])])  # fmt: skip
        for variant in self.variants:  # D10: colour name vs the photos of that variant
            names = grouped.get(variant["code"], [])
            readings = list(self.colours(names).values())
            seen = _majority(readings)
            named = colour_family(variant.get("colour_name"))
            if seen and readings.count(seen) >= 2 and named and not colours_compatible(seen, named):
                self.add("D10", variant=variant["code"], type="contradictory", severity="review", return_risk="colour",
                         attribute="colour", spec_value=variant.get("colour_name"), found_value=seen,
                         title=f"{variant['code']}: '{variant.get('colour_name')}' but the photos look {seen}",
                         detail="The colour name in the spec sheet does not match the variant's photos.",
                         evidence=[self.photo(n) for n in names[:2]])  # fmt: skip
        names_by_code = {v["code"]: colour_family(v.get("colour_name")) for v in self.variants}
        for code, names in grouped.items():  # D16: one photo's colour differs from its variant's other photos
            colours = self.colours(names)
            named = names_by_code.get(code)
            for name, colour in colours.items():
                others = _majority([c for n, c in colours.items() if n != name])
                support = sum(1 for n, c in colours.items() if n != name and c == others)
                odd = not colours_compatible(colour, others) and (
                    named is None or not colours_compatible(colour, named)
                )
                if others and support >= 2 and odd:
                    self.add("D16", variant=code, type="contradictory", severity="review", return_risk="colour",
                             attribute="photo", file=name, found_value=colour, spec_value=others,
                             title=f"{name} looks {colour}, the other {code} photos are {others}",
                             detail="This photo is probably filed under the wrong variant. Reassign it or ask the supplier.",
                             evidence=[self.photo(name)])  # fmt: skip
        for name, entry in self.product_photos().items():
            if entry["analysis"].get("has_text_overlay"):  # D8
                self.add("D8", type="quality", severity="info", return_risk="none", attribute="photo", file=name,
                         title=f"{name}: text or badge on the photo",
                         detail="Marketplaces do not allow text on product images; keep it out of the main image.",
                         evidence=[self.photo(name, "text")])  # fmt: skip
        for name, entry in self.images.items():
            if entry.get("low_res"):  # D9
                w, h = entry.get("size") or [None, None]
                self.add("D9", type="quality", severity="info", return_risk="none", attribute="photo", file=name,
                         title=f"{name}: low resolution ({w} × {h} px)",
                         detail="Too small for zoom; request a high-resolution image.")  # fmt: skip

    def compliance(self) -> None:
        spec = self.spec
        languages = {(code or "").lower()[:2] for code in spec.get("safety_warnings_languages", [])}
        if spec.get("safety_warnings") and "en" not in languages:  # D7
            self.add("D7", type="compliance", severity="blocking", return_risk="none", attribute="safety_warnings",
                     title=f"Safety warnings only in {', '.join(sorted(languages)) or 'one language'}",
                     detail="GPSR Art. 19(d): warnings must be shown in the shop's languages. Translate them and have them checked.")  # fmt: skip
        maker = f"{spec.get('manufacturer') or ''} {spec.get('manufacturer_address') or ''}"
        if not (STREET.search(_fold(maker)) and POSTCODE.search(maker)):  # D12: no postal address
            self.add("D12", type="compliance", severity="blocking", return_risk="none", attribute="manufacturer_address",
                     title="Manufacturer address missing", spec_value=spec.get("manufacturer"),
                     detail="GPSR Art. 19(a): the listing must show the manufacturer's name, postal address and contact.")  # fmt: skip
        origin = _fold(f"{maker} {spec.get('country_of_origin') or ''}")
        rp = (spec.get("eu_responsible_person") or "").strip(" —-")
        if NON_EU.search(origin) and not rp:  # D13
            self.add("D13", type="compliance", severity="blocking", return_risk="none", attribute="eu_responsible_person",
                     title="Non-EU manufacturer without an EU responsible person",
                     detail="GPSR Art. 19(b): products made outside the EU need an EU responsible person in the listing.")  # fmt: skip
        upholstery = spec.get("upholstery")
        textile = self.category == "rug" or (upholstery and not NON_TEXTILE.search(_fold(upholstery)))
        if textile and upholstery is not None and not has_fibre_composition(upholstery):  # D14
            self.add("D14", type="compliance", severity="blocking", return_risk="material", attribute="upholstery",
                     title="Fibre composition missing", spec_value=upholstery,
                     detail="EU Textile Regulation 1007/2011: textile products must state their fibre composition.",
                     evidence=self.pdf("upholstery"))  # fmt: skip
        if spec.get("claims"):  # D15
            self.add("D15", type="compliance", severity="review", return_risk="none", attribute="claims",
                     title="Health/safety/environmental claim", spec_value="; ".join(spec["claims"]),
                     detail="Claims like 'antibacterial', 'fireproof' or 'eco-friendly' need proof before they are published.",
                     evidence=self.pdf(*(f"claim:{n}" for n in range(len(spec["claims"])))))  # fmt: skip

    def plausibility(self) -> None:  # D17
        load, load_unit = self.measure("max_load")
        weight, weight_unit = self.measure("weight")
        checks = [
            ("max_load", "max_load_kg", to_kg(load, load_unit)),
            ("weight", "weight_kg", to_kg(weight, weight_unit)),
        ]
        for attribute, key, value in checks:
            bounds = PLAUSIBLE[key].get(self.category)
            if value is not None and bounds and not bounds[0] <= value <= bounds[1]:
                self.add("D17", type="quality", severity="review", return_risk="missing_info", attribute=attribute,
                         title=f"{attribute.replace('_', ' ').capitalize()} {value:g} kg looks wrong for a {self.category}",
                         detail=f"Expected {bounds[0]:g}–{bounds[1]:g} kg; probably a typo. Ask the supplier.",
                         spec_value=f"{value:g} kg", evidence=self.pdf(attribute))  # fmt: skip

    def run(self) -> list[Issue]:
        if not self.spec:
            return []
        for rule in (self.dimensions, self.eans, self.care, self.photos, self.compliance, self.plausibility):
            rule()
        return self.issues


def check_submission(result: dict[str, Any]) -> list[Issue]:
    """All issues for one extraction result, in rule order."""
    return sorted(_Checker(result).run(), key=lambda i: int(i.rule[1:]))
