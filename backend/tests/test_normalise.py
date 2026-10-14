"""Normalisation: numbers, units, EANs and supplier words → categories (pure code, no model)."""

import sys
from pathlib import Path

import pytest

from app.pipeline.normalise import (
    care_category,
    colour_family,
    colours_compatible,
    gtin_valid,
    has_fibre_composition,
    material_by_variant,
    material_category,
    parse_dims,
    parse_number,
    to_cm,
    to_kg,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "scripts"))
import make_synthetic as gen  # noqa: E402  (the generator's wording tables are the contract)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("23,5", 23.5), ("14.5", 14.5), ("40.000", 40000), ("1.234,5", 1234.5), ("1,234.5", 1234.5),
     ("520", 520), ("Ø 25 cm", 25), ("0,6", 0.6), ("1100 kg", 1100), ("—", None), (None, None)],
)  # fmt: skip
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_units_to_cm_and_kg():
    assert to_cm(520, "mm") == 52.0  # D2: mixed units
    assert to_cm(34.6, "inches") == 87.9 and to_cm(35.4, '"') == 89.9  # ABO listings in inches
    assert to_cm(88, None) == 88  # cm is the default
    assert to_kg(51.9, "pounds") == 23.54 and to_kg(23.5, "kg") == 23.5
    assert to_cm(3, "furlongs") is None


def test_parse_dims():
    assert parse_dims("88 x 83 x 90 cm") == ([88, 83, 90], "cm")
    assert parse_dims("160 x 230 cm") == ([160, 230], "cm")
    assert parse_dims("Medidas: 44 x 48 x 91,3 cm") == ([44, 48, 91.3], "cm")
    assert parse_dims("no dimensions here") is None


def test_gtin_check_digit():
    assert gtin_valid("5601234000016")  # LISBOA MUS
    assert not gtin_valid("5601234000024")  # LISBOA ARE: planted D4
    assert gtin_valid(gen.ean13("401234501701"))
    assert not gtin_valid("560123400001")  # 12 digits but a wrong UPC-A check digit
    assert not gtin_valid(None) and not gtin_valid("—") and not gtin_valid("5601234 000016")


def test_every_generated_colour_name_maps_to_its_family():
    for lang, families in gen.COLOUR_NAMES.items():
        for family, names in families.items():
            for name in names:
                assert colour_family(name) == family, (lang, name)


def test_colour_words_from_real_listings():
    assert colour_family("Heritage Green") == "green" and colour_family("Storm Grey") == "grey"
    assert colour_family("Cannoli Cream") == "beige" and colour_family("Gibraltar Sea Blue") == "blue"
    assert colour_family("Weiß") == "white" and colour_family("Lilás") == "purple"
    assert colour_family("Xyz") is None


def test_colour_compatibility():
    assert colours_compatible("beige", "white") and colours_compatible("grey", "black")
    assert colours_compatible("multicolour", "pink") and colours_compatible(None, "green")
    assert not colours_compatible("grey", "beige")  # the D10 swaps must stay contradictions
    for family, wrong in gen.WRONG_COLOUR.items():
        if family != "multicolour":  # the generator never plants D10 on a multicolour variant
            assert not colours_compatible(family, wrong), (family, wrong)


@pytest.mark.parametrize(
    ("key", "category"),
    [("wood", "wood"), ("oak", "wood"), ("metal", "metal"), ("chrome", "metal"), ("ceramic", "ceramic"),
     ("glass", "glass"), ("plastic", "plastic"), ("stone", "stone")],
)  # fmt: skip
def test_every_generated_material_text_maps_to_its_category(key, category):
    for lang, text in gen.MATERIAL_TEXT[key].items():
        assert material_category(text) == category, (lang, text)


def test_per_variant_materials_as_the_model_reads_them():
    text = (
        "Faia maciça, acabamento nogueira / solid beech, walnut stain (MUS, ARE) - "
        "Aço cromado / chrome-plated steel (ANT)"
    )  # fmt: skip  (LISBOA, D6 on ANT, as Gemma read it)
    codes = ["LIS-01-MUS", "LIS-01-ARE", "LIS-01-ANT"]
    assert material_by_variant(text, codes) == {
        "LIS-01-MUS": "wood",
        "LIS-01-ARE": "wood",
        "LIS-01-ANT": "metal",
    }
    assert material_by_variant("Massive Eiche / solid oak", codes) == dict.fromkeys(codes, "wood")


@pytest.mark.parametrize(
    ("key", "category"),
    [("wash30", "wash"), ("machine", "wash"), ("dry_clean", "dry_clean"), ("professional", "professional"),
     ("wipe", "wipe"), ("lamp", "wipe")],
)  # fmt: skip
def test_every_generated_care_text_maps_to_its_category(key, category):
    for lang, text in gen.CARE[key].items():
        assert care_category(text) == category, (lang, text)


def test_fibre_composition_present_or_missing():
    for lang, text in gen.FIBRES["polyester"].items():
        assert has_fibre_composition(f"Bouclé, {text}"), lang
    for lang, text in gen.UPHOLSTERY["velvet"].items():
        assert not has_fibre_composition(text), lang  # D14: no composition
