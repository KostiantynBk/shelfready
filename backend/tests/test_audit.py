"""Catalogue audit rules on small hand-made listings (no model, no downloads)."""

from app.pipeline.audit import audit_listing, score


def photo(product_type="armchair", colour="green", role="packshot", white=True, in_frame=1, overlay=False):
    return {"role": role, "product_type": product_type, "colour_family": colour, "white_background": white,
            "products_in_frame": in_frame, "has_text_overlay": overlay}  # fmt: skip


def item(
    category="chair", domain="amazon.es", colour="Verde", material="Madera", dims=True, images=2, small=False,
    name="Sillón tapizado", bullets=(),
):  # fmt: skip
    listing = {"item_name": [{"language_tag": "es_ES", "value": name}],
               "color": [{"language_tag": "es_ES", "value": colour}] if colour else [],
               "material": [{"language_tag": "es_ES", "value": material}],
               "bullet_point": [{"language_tag": "es_ES", "value": b} for b in bullets]}  # fmt: skip
    if dims:
        listing["item_dimensions"] = {"width": {"unit": "centimeters", "value": 80}}
    pics = [{"image_id": f"img{n}", "role": "main" if n == 0 else "other",
             "width": 400 if small and n == 0 else 1500, "height": 400 if small and n == 0 else 1500}
            for n in range(images)]  # fmt: skip
    return {"item_id": "B0TEST", "category": category, "domain": domain, "listing": listing, "images": pics}


def two(**kwargs):
    return {"img0": photo(**kwargs), "img1": photo(**kwargs)}


def rules(issues):
    return sorted(i.rule for i in issues)


def test_a_clean_listing_has_no_issues():
    issues = audit_listing(item(), two())
    assert issues == [] and score(issues) == 100


def test_wrong_category_when_every_photo_shows_something_else():  # e.g. a wristwatch filed under CHAIR
    issues = audit_listing(item(images=1), {"img0": photo(product_type="wristwatch", colour="silver")})
    assert rules(issues) == ["A1"] and issues[0].severity == "blocking" and score(issues) == 75


def test_fabric_sample_listings_and_other_seats_are_not_wrong_category():
    swatch = {"img0": photo(product_type="fabric swatch", role="detail")}
    issues = audit_listing(item(images=1, name="Alloy Swatch", dims=False), swatch)
    assert [(i.title, i.severity) for i in issues] == [("Fabric sample listed as a chair", "info")]
    assert audit_listing(item(name="Tabouret Wye"), two(product_type="stool")) == []


def test_a_sofa_filed_under_chairs_unless_the_name_says_chair():
    issues = audit_listing(item(name="Andromeda 2 Seater"), two(product_type="sofa"))
    assert rules(issues) == ["A1"] and issues[0].severity == "review" and issues[0].found_value == "sofa"
    assert audit_listing(item(name="Fauteuil modulaire City"), two(product_type="sofa")) == []


def test_no_photo_of_the_product():
    issues = audit_listing(item(images=1), {"img0": photo(product_type="logo", role="other", colour="black")})
    assert rules(issues) == ["A9"] and issues[0].severity == "blocking"


def test_colour_name_contradicts_two_photos():
    issues = audit_listing(item(colour="Gris"), two(colour="green"))
    assert rules(issues) == ["A2"] and issues[0].found_value == "green"


def test_colour_names_that_are_not_contradictions():
    assert audit_listing(item(colour="Ivory Blue"), two(colour="blue")) == []  # one of two colours
    assert audit_listing(item(colour="Taupe"), two(colour="beige")) == []  # between grey and beige
    assert audit_listing(item(colour="Beige"), two(colour="grey")) == []  # light neutrals look alike
    assert audit_listing(item(colour="Red"), two(product_type="gaming chair", colour="black")) == []  # accent
    lamp = item(category="lamp", colour="Nickel", name="Table lamp")
    assert audit_listing(lamp, two(product_type="table lamp", colour="white")) == []  # base finish vs shade
    assert rules(audit_listing(item(colour="Charcoal"), two(colour="beige"))) == ["A2"]  # dark vs light


def test_inches_on_a_european_marketplace_and_missing_attributes():
    listing = item(domain="amazon.de", dims=False, colour=None, name='Sessel, 58,5" hoch')
    issues = audit_listing(listing, two())
    assert rules(issues) == ["A3", "A4", "A4"]
    assert audit_listing(item(domain="amazon.com"), two()) == []  # inches are fine in the US


def test_rug_fibre_composition():
    rug = two(product_type="area rug")
    one_fibre = audit_listing(item(category="rug", material="Baumwolle", name="Teppich"), rug)
    assert rules(one_fibre) == ["A5"] and one_fibre[0].severity == "info"  # cotton only, not wool
    mixed = audit_listing(item(category="rug", material="Coton, Laine", name="Tapis"), rug)
    assert mixed[0].severity == "blocking" and mixed[0].title == "Fibre percentages missing"
    assert audit_listing(item(category="rug", material="100% wool", name="Rug"), rug) == []


def test_the_description_counts_for_size_colour_and_fibres():
    rug = two(product_type="area rug")
    described = item(category="rug", material="Coton / Laine", name="Tapis",
                     bullets=["100% laine, tapis de main avec dos 100% coton"])  # fmt: skip
    assert audit_listing(described, rug) == []
    suede = item(category="rug", material="Camoscio", name="Tappeto", bullets=["100% lana"])  # suede vs wool
    assert [i.title for i in audit_listing(suede, rug)] == ["Material field does not match the description"]
    sofa_bed = item(dims=False, colour=None, name="Sofa Bed, 87 x 181 x 90cm, Grey")  # only the text has them
    issues = audit_listing(sofa_bed, two(colour="grey"))
    assert [i.title for i in issues] == ["Size only in the text", "Colour only in the name"]
    assert {i.severity for i in issues} == {"info"}


def test_image_quality_rules():
    photos = {"img0": photo(overlay=True), "img1": photo(overlay=True)}  # text on a secondary image is fine
    issues = audit_listing(item(small=True), photos)
    assert rules(issues) == ["A6", "A7"] and score(issues) == 94


def test_main_image_rules():
    assert rules(audit_listing(item(), {"img0": photo(role="lifestyle"), "img1": photo()})) == ["A8"]
    pair = {"img0": photo(in_frame=2), "img1": photo()}
    issues = audit_listing(item(name="Silla Lukas"), pair)
    assert rules(issues) == ["A8"] and issues[0].severity == "review"
    assert audit_listing(item(name="Juego de 2 sillas, 51 x 61 cm"), pair) == []
    assert rules(audit_listing(item(name="Silla, 51 x 61 cm"), pair)) == ["A8"]  # '51 x 61' is a size
