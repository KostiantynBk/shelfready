"""Extraction on the committed LISBOA submission with scripted model answers (mock mode, no network)."""

from pathlib import Path

import pytest

from app.clients.llm import LLMClient
from app.config import Settings
from app.pipeline.run_extraction import extract_submission

LISBOA = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "SYN-00-LISBOA"


def value(v, unit, text):
    return {"value": v, "unit": unit, "source_text": text}


SPEC = {  # what a good reading of LISBOA's sheet looks like (values as written)
    "product_name": "LISBOA", "supplier": "Móveis Lusa, Lda.", "product_type": "Poltrona / armchair",
    "manufacturer": "Móveis Lusa, Lda.",
    "manufacturer_address": "Rua das Oficinas 12, 4590-000 Paços de Ferreira",
    "eu_responsible_person": None, "country_of_origin": "Portugal",
    "width": value("88", "cm", "88 x 83 x 90 cm"), "depth": value("83", "cm", "88 x 83 x 90 cm"),
    "height": value("90", "cm", "88 x 83 x 90 cm"), "seat_height": value(None, None, None),
    "seat_depth": value("500", "mm", "500 mm"), "max_load": value("120", "kg", "120 kg"),
    "weight": value("23,5", "kg", "23,5 kg"), "legs_or_base": "Faia maciça, acabamento nogueira",
    "upholstery": "Bouclé, 100% poliéster", "care": "Apenas limpeza a seco / dry clean only",
    "variants": [{"code": "LIS-01-MUS", "colour_name": "Musgo", "ean": "5601234000016"},
                 {"code": "LIS-01-ARE", "colour_name": "Areia", "ean": "5601234000024"},
                 {"code": "LIS-01-ANT", "colour_name": "Antracite", "ean": None}],
    "safety_warnings": ["Não utilizar no exterior."], "safety_warnings_languages": ["pt"], "claims": [],
}  # fmt: skip
PHOTO = {"role": "packshot", "product_type": "armchair", "products_in_frame": 1, "colour_family": "green",
         "upholstery_material": "boucle", "legs_material": "wood", "base_material": "none_visible",
         "legs_finish": "dark walnut", "text_in_image": [], "has_text_overlay": False,
         "white_background": True, "confidence": 0.9, "boxes": []}  # fmt: skip


@pytest.mark.anyio
async def test_lisboa_extraction_reads_spec_label_and_drawing(tmp_path):
    seen_requests = {}

    def model(purpose, request):
        seen_requests[purpose] = request
        if purpose.startswith("spec:"):
            return SPEC
        if purpose.startswith("photo:"):
            role = (
                "label"
                if purpose.endswith("_label.png")
                else "dimension_drawing"
                if "_dimensions" in purpose
                else "packshot"
            )
            return PHOTO | {"role": role}
        if purpose.startswith("label:"):
            return {"composition": "100% POLIÉSTER", "wash_max_temp_c": 30, "do_not_wash": False,
                    "dry_clean_only": False, "removable_cover": True, "other_text": []}  # fmt: skip
        if purpose.startswith("drawing:"):
            return {
                "width_cm": 88,
                "depth_cm": 83,
                "height_cm": 90,
                "seat_height_cm": 47,
                "product_code": "LIS-01",
            }
        raise AssertionError(purpose)

    settings = Settings(
        _env_file=None, llm_mode="mock", data_dir=tmp_path / "runtime", abo_images_dir=tmp_path / "none"
    )
    async with LLMClient(settings, mock_responder=model) as llm:
        out = await extract_submission(llm, LISBOA)

    assert out["spec"]["seat_depth"] == {"value": "500", "unit": "mm", "source_text": "500 mm"}
    # Every value is found back on the page, so the review screen can highlight it
    evidence = out["spec_evidence"]
    assert evidence["seat_depth"]["page"] == 1 and evidence["seat_depth"]["text"] == "500 mm"
    assert {"care", "max_load", "weight", "ean:LIS-01-ARE", "ean:LIS-01-MUS"} <= set(evidence)
    # The spec call sends the rendered page keyed by the PDF, for replay on any machine
    spec_part = seen_requests["spec:SYN-00"]["messages"][-1]["content"][0]
    assert spec_part["_digest"].startswith("pdf:") and spec_part["_digest"].endswith(":p1@2x@1536")
    # Labels and drawings get a second, specialised reading
    assert out["images"]["LIS01_label.png"]["label"]["wash_max_temp_c"] == 30
    assert out["images"]["LIS01_dimensions.png"]["drawing"]["seat_height_cm"] == 47
    assert out["images"]["LIS01_ARE_back_small.jpg"]["low_res"] is True
    # ABO photos were not available in this test: listed as errors, nothing else failed
    assert out["errors"] and all("not available" in e for e in out["errors"].values())
