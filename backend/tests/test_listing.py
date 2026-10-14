"""Copy from verified facts only, and the code checks on translations (catalogue T-D1…T-D4)."""

import json
from pathlib import Path

from app.pipeline.checks import check_submission
from app.pipeline.listing import listing_photos, verified_facts
from app.pipeline.translate_qa import check_translation, numbers, protected_tokens, translation_issues
from app.schemas.listing import ListingCopy
from app.schemas.review import Decision

LISBOA = json.loads(
    (Path(__file__).parent / "fixtures" / "extraction" / "SYN-00.json").read_text(encoding="utf-8")
)


def test_open_findings_hold_facts_back():
    facts, held = verified_facts(LISBOA["spec"], check_submission(LISBOA))
    assert facts["model"] == "LISBOA" and facts["brand"] == "Móveis Lusa"
    assert (facts["width_cm"], facts["depth_cm"], facts["height_cm"]) == (88, 83, 90)
    assert facts["seat_depth_cm"] == 50  # '500 mm' (an info finding) is converted, not held back
    for key in ("seat_height_cm", "care", "legs_or_base", "safety_warnings"):
        assert key not in facts
    assert {h["attribute"] for h in held} == {"seat_height", "care", "legs_or_base", "safety_warnings"}


def test_decisions_release_and_correct_facts():
    issues = check_submission(LISBOA)
    decisions = {"D1::seat_height:": Decision(action="use_found"), "D5::care:": Decision(action="use_found"),
                 "D6:LIS-01-ANT:legs_or_base:": Decision(action="use_found"),
                 "D7::safety_warnings:": Decision(action="accept")}  # fmt: skip
    facts, held = verified_facts(LISBOA["spec"], issues, decisions)
    assert held == [] and facts["seat_height_cm"] == 47 and facts["care"] == "washable at 30 °C"
    assert facts["corrections"] == [{"attribute": "legs_or_base", "variant": "LIS-01-ANT", "value": "wood"}]
    assert len(facts["safety_warnings"]) == 3
    asked, held = verified_facts(
        LISBOA["spec"], issues, {"D1::seat_height:": Decision(action="ask_supplier")}
    )
    assert "seat_height_cm" not in asked and held[0]["asked_supplier"]


def test_shop_photos_skip_promo_low_res_and_labels():
    files = [
        p["file"] for p in listing_photos(LISBOA, check_submission(LISBOA), ["LIS-01-MUS", "LIS-01-ARE"])
    ]
    assert "LIS01_promo.jpg" not in files and "LIS01_ARE_back_small.jpg" not in files
    assert "LIS01_label.png" not in files and len(files) <= 8


EN = ListingCopy(
    title="LISBOA armchair by Móveis Lusa",
    bullets=[
        "88 x 83 x 90 cm, seat depth 50 cm",
        "Bouclé, 100% polyester, Martindale 40,000",
        "Solid beech legs with walnut stain",
        "Maximum load 120 kg, weight 23.5 kg",
    ],  # fmt: skip
    description="A compact armchair in bouclé.",
    colours=[{"code": "LIS-01-MUS", "name": "Moss green"}],
    safety_warnings=["Do not use outdoors."],
    alt_texts=[{"file": "LIS01_MUS_front.jpg", "text": "LISBOA armchair in moss green, front view"}],
)
ES = {
    "title": "Sillón LISBOA de Móveis Lusa",
    "bullets": [
        "88 x 83 x 90 cm, profundidad del asiento 50 cm",
        "Bouclé, 100 % poliéster, Martindale 40.000",
        "Patas de haya maciza con tinte nogal",
        "Carga máxima 120 kg, peso 23,5 kg",
    ],  # fmt: skip
    "description": "Un sillón compacto en bouclé.",
    "colours": [{"code": "LIS-01-MUS", "name": "Verde musgo"}],
    "safety_warnings": ["No utilizar en exteriores."],
    "alt_texts": [{"file": "LIS01_MUS_front.jpg", "text": "Sillón LISBOA en verde musgo, vista frontal"}],
}


def spanish(**changes):
    data = json.loads(json.dumps(ES))
    for old, new in changes.get("replace", []):
        data = json.loads(json.dumps(data, ensure_ascii=False).replace(old, new))
    return ListingCopy.model_validate(data)


def protected():
    return protected_tokens(
        {"brand": "Móveis Lusa", "model": "LISBOA", "variants": [{"code": "LIS-01-MUS"}]}, EN
    )


def test_a_faithful_translation_passes():
    assert protected() == ["Móveis Lusa", "LISBOA", "LIS-01-MUS", "Martindale"]
    qa = check_translation(EN, spanish(), "es", protected())
    assert qa.passed and qa.problems == []
    assert numbers("Martindale 40 000, 23,5 kg") == numbers("Martindale 40,000, 23.5 kg")  # FR/DE/EN formats


def test_ukrainian_units_in_cyrillic_are_fine():
    words = [(" cm", " см"), (" kg", " кг"), ("haya", "бук"), ("bouclé", "букле"), ("Bouclé", "Букле"),
             ("poliéster", "поліестер"), ("nogal", "горіх")]  # fmt: skip
    uk = spanish(replace=words)
    assert check_translation(EN, uk, "uk", protected()).passed


def test_planted_translation_defects_are_caught():
    cases = {  # catalogue T-D1…T-D4 → issue rules T1…T4
        "T1": [("90 cm", "90 mm")],  # unit changed
        "T2": [("120 kg", "210 kg")],  # number changed
        "T3": [("Móveis Lusa", "Muebles Lusa")],  # brand altered
        "T4": [
            ("Bouclé", "Terciopelo"),
            ("en bouclé", "en terciopelo"),
        ],  # material rendered as another fabric
    }
    for rule, replace in cases.items():
        qa = check_translation(EN, spanish(replace=replace), "es", protected())
        assert [i.rule for i in translation_issues("SYN-00", "es", qa)] == [rule], rule


def test_back_translation_must_keep_numbers_and_terms():
    assert check_translation(EN, spanish(), "es", protected(), back=EN.model_copy()).backtranslation_ok
    bullets = [b.replace("Bouclé", "Velvet") for b in EN.bullets]  # the back-translation lost "bouclé"
    lost = EN.model_copy(update={"bullets": bullets, "description": "A compact armchair."})
    qa = check_translation(EN, spanish(), "es", protected(), back=lost)
    assert qa.backtranslation_ok is False
    assert [i.rule for i in translation_issues("SYN-00", "es", qa)] == ["T5"]
