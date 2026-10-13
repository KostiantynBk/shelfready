"""What the model extracts from supplier documents and photos (runbook §4 data contracts).

These models double as the JSON schemas sent to the model (llm.chat_json), so field descriptions are
written for the model as much as for us.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Material = Literal[
    "wood", "metal", "plastic", "glass", "ceramic", "stone", "fabric", "none_visible", "unsure"
]
ColourFamily = Literal[
    "black", "white", "grey", "beige", "brown", "red", "orange", "yellow", "green", "blue", "purple", "pink",
    "gold", "silver", "multicolour",
]  # fmt: skip


class Box(BaseModel):
    label: str
    box_2d: list[int] = Field(min_length=4, max_length=4, description="[y0, x0, y1, x1] normalised to 0-1000")


class PhotoAnalysis(BaseModel):
    role: Literal[
        "packshot", "detail", "lifestyle", "label", "dimension_drawing", "packaging", "promo", "other"
    ]
    product_type: str = Field(description="e.g. armchair, sofa, table lamp, area rug")
    products_in_frame: int
    colour_family: ColourFamily = Field(description="main colour of the product's upholstery, body or pile")
    upholstery_material: str | None = Field(
        description="visible covering, e.g. velvet, leather, boucle; null if none"
    )
    legs_material: Material = Field(description="seating: legs; 'none_visible' if no legs show")
    base_material: Material = Field(description="lamps: base or body; 'none_visible' for other products")
    legs_finish: str | None = Field(
        description="e.g. light oak, dark walnut, black matt, chrome; null if unsure"
    )
    text_in_image: list[str] = Field(description="every piece of text printed on or in the photo, as read")
    has_text_overlay: bool = Field(
        description="text or graphics added on top of the photo (e.g. a sale badge)"
    )
    white_background: bool
    confidence: float = Field(ge=0, le=1)
    boxes: list[Box] = Field(
        description="'leg' for each visible leg, 'base' for a lamp base, 'text' for text"
    )


class LabelReading(BaseModel):
    """A sewn-in care label: composition and care symbols, as printed."""

    composition: str | None = Field(description="fibre composition exactly as printed, e.g. '100% polyester'")
    wash_max_temp_c: int | None = Field(
        description="temperature in the wash-tub symbol; null if not washable"
    )
    do_not_wash: bool = Field(description="the wash tub is crossed out")
    dry_clean_only: bool = Field(description="only professional / dry cleaning is allowed")
    removable_cover: bool = Field(description="the label says the cover is removable")
    other_text: list[str] = Field(description="any other text on the label, as printed")


class DrawingReading(BaseModel):
    """A technical dimension drawing: the labelled measurements in centimetres."""

    width_cm: float | None
    depth_cm: float | None
    height_cm: float | None
    seat_height_cm: float | None = Field(description="null if the drawing does not show it")
    product_code: str | None = Field(description="code printed on the drawing, if any")


class SpecValue(BaseModel):
    value: str | None = Field(description="the value exactly as written, null if blank or a dash")
    unit: str | None = Field(description="the unit exactly as written, e.g. cm, mm, kg; null if none")
    source_text: str | None = Field(description="the exact text this was read from")


class Variant(BaseModel):
    code: str
    colour_name: str = Field(description="colour name exactly as written by the supplier")
    ean: str | None = Field(description="EAN/GTIN digits exactly as written; null if missing")


class SpecExtraction(BaseModel):
    product_name: str
    supplier: str
    product_type: str = Field(description="as written, e.g. 'Poltrona / Armchair'")
    manufacturer: str | None = Field(description="manufacturer name as written")
    manufacturer_address: str | None = Field(description="postal address and contact as written")
    eu_responsible_person: str | None = Field(description="EU responsible person / importer as written")
    country_of_origin: str | None
    width: SpecValue
    depth: SpecValue
    height: SpecValue
    seat_height: SpecValue
    seat_depth: SpecValue
    max_load: SpecValue
    weight: SpecValue
    legs_or_base: str | None = Field(description="material of the legs (seating) or base (lamps) as written")
    upholstery: str | None = Field(description="covering or pile, including fibre composition, as written")
    care: str | None = Field(description="care / cleaning instructions as written")
    variants: list[Variant]
    safety_warnings: list[str] = Field(description="each safety warning exactly as written")
    safety_warnings_languages: list[str] = Field(description="ISO 639-1 codes of the warnings' languages")
    claims: list[str] = Field(description="marketing claims about health, safety or environment, as written")
