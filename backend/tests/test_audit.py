"""Catalogue audit rules on small hand-made listings (no model, no downloads)."""

from app.pipeline.audit import audit_listing, score


def photo(product_type="armchair", colour="green", role="packshot", white=True, in_frame=1, overlay=False):
    return {"role": role, "product_type": product_type, "colour_family": colour, "white_background": white,
            "products_in_frame": in_frame, "has_text_overlay": overlay}  # fmt: skip


def item(
    category="chair", domain="amazon.es", colour="Verde", material="Madera", dims=True, images=2, small=False
):
    listing = {"item_name": [{"language_tag": "es_ES", "value": "Sillón tapizado"}],
               "color": [{"language_tag": "es_ES", "value": colour}] if colour else [],
               "material": [{"language_tag": "es_ES", "value": material}]}  # fmt: skip
    if dims:
        listing["item_dimensions"] = {"width": {"unit": "centimeters", "value": 80}}
    pics = [{"image_id": f"img{n}", "role": "main" if n == 0 else "other",
             "width": 400 if small and n == 0 else 1500, "height": 400 if small and n == 0 else 1500}
            for n in range(images)]  # fmt: skip
    return {"item_id": "B0TEST", "category": category, "domain": domain, "listing": listing, "images": pics}


def rules(issues):
    return sorted(i.rule for i in issues)


def test_a_clean_listing_has_no_issues():
    issues = audit_listing(item(), {"img0": photo(), "img1": photo()})
    assert issues == [] and score(issues) == 100


def test_wrong_category_when_every_photo_shows_something_else():  # e.g. a wristwatch filed under CHAIR
    issues = audit_listing(item(images=1), {"img0": photo(product_type="wristwatch", colour="silver")})
    assert rules(issues) == ["A1"] and issues[0].severity == "blocking" and score(issues) == 75


def test_colour_name_contradicts_two_photos():
    issues = audit_listing(
        item(colour="Gris"), {"img0": photo(colour="green"), "img1": photo(colour="green")}
    )
    assert rules(issues) == ["A2"] and issues[0].found_value == "green"


def test_inches_on_a_european_marketplace_and_missing_attributes():
    listing = item(domain="amazon.de", dims=False, colour=None)
    listing["listing"]["item_name"][0]["value"] = 'Stehleuchte, 58,5" hoch'
    issues = audit_listing(listing, {"img0": photo(), "img1": photo()})
    assert rules(issues) == ["A3", "A4", "A4"]
    assert (
        audit_listing(item(domain="amazon.com"), {"img0": photo(), "img1": photo()}) == []
    )  # inches are fine in the US


def test_rug_without_fibre_percentages():
    rug = {"img0": photo(product_type="area rug"), "img1": photo(product_type="area rug")}
    assert rules(audit_listing(item(category="rug", material="Wool"), rug)) == ["A5"]
    assert audit_listing(item(category="rug", material="100% wool"), rug) == []


def test_image_quality_rules():
    photos = {"img0": photo(white=False, in_frame=2), "img1": photo(overlay=True)}
    issues = audit_listing(item(small=True), photos)
    assert rules(issues) == ["A6", "A7", "A8"] and score(issues) == 91
