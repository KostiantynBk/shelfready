# ruff: noqa: E501  (multilingual wording tables read better one entry per line)
"""Generate the synthetic supplier submissions, with planted defects and their ground truth.

    cd backend && uv run python ../data/scripts/make_synthetic.py [--allow-unconfirmed]

Inputs
- data/abo/items.jsonl: colour families (one ABO model in 2-3 colours = one product with variants)
- data/synthetic/photo_truth.json: what each variant's photo shows, confirmed by a person
- data/defects.yaml: defect catalogue and plan (seed, clean products, minimum counts)

Outputs in data/synthetic/
- <PRODUCT>/spec.pdf (+ spec.html), care label, dimension drawing, promo and low-res photo copies
- <PRODUCT>/submission.json: the supplier email and its attachments; photos point to ABO files
- ground_truth.json: every planted defect per product ("clean" products have none)

Principles (defect_catalogue.md): photos are real and never edited, so the photo is the truth and the
defects live in the documents we generate. Deterministic: same inputs and seed, same plan and documents.
Needs Google Chrome (HTML -> PDF) and the Windows fonts; the generated set is committed so nobody else
has to run this.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
ITEMS = ROOT / "data" / "abo" / "items.jsonl"
IMAGES = ROOT / "data" / "raw" / "abo" / "images"
TRUTH = ROOT / "data" / "synthetic" / "photo_truth.json"
DEFECTS = ROOT / "data" / "defects.yaml"
OUT = ROOT / "data" / "synthetic"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

PRODUCTS_PER_CATEGORY = {"chair": 10, "sofa": 4, "lamp": 5, "rug": 5}  # plus LISBOA
SEATING = ("chair", "sofa")
MATERIALS = ("wood", "metal", "plastic", "glass", "ceramic", "stone")  # a confirmed, checkable material


# --------------------------------------------------------------------------- suppliers and wording
SUPPLIERS = {
    "lusa": {
        "name": "Móveis Lusa, Lda.", "lang": "pt", "country": "Portugal", "gs1": "5601234",
        "address": "Rua das Oficinas 12, 4590-000 Paços de Ferreira, Portugal · info@moveislusa.example",
        "email": "comercial@moveislusa.example", "contact": "Rita Moreira",
        "names": ["PORTO", "BRAGA", "FARO", "ÉVORA", "SINTRA", "AVEIRO", "COIMBRA", "TAVIRA", "VISEU", "OBIDOS"],
    },
    "brianza": {
        "name": "Arredo Brianza S.r.l.", "lang": "it", "country": "Italia", "gs1": "8012345",
        "address": "Via dei Mobilieri 7, 20821 Meda (MB), Italia · info@arredobrianza.example",
        "email": "ordini@arredobrianza.example", "contact": "Giulia Colombo",
        "names": ["COMO", "LECCO", "BRERA", "LUCCA", "SIENA", "ASOLO", "ORTA", "BELLAGIO", "VARENNA", "MONZA"],
    },
    "nordholz": {
        "name": "Nordholz Wohnen GmbH", "lang": "de", "country": "Deutschland", "gs1": "4012345",
        "address": "Industriestraße 21, 32051 Herford, Deutschland · service@nordholz-wohnen.example",
        "email": "vertrieb@nordholz-wohnen.example", "contact": "Jonas Weber",
        "names": ["ELBE", "ISAR", "WESER", "MOSEL", "LAHN", "FULDA", "LECH", "INN", "SAALE", "RUHR"],
    },
}  # fmt: skip
CATEGORY_SUPPLIERS = {"chair": ["lusa", "brianza", "nordholz"], "sofa": ["brianza", "lusa"],
                      "lamp": ["nordholz", "brianza"], "rug": ["nordholz", "lusa"]}  # fmt: skip
NON_EU = {"name": "Ningbo Brightway Home Co., Ltd.", "country": "China",
          "address": "No. 88 Xingning Road, Yinzhou District, Ningbo 315000, China · export@brightway.example"}  # fmt: skip

LABELS = {
    "pt": {"sheet": "Ficha Técnica", "product": "Produto", "frame": "Estrutura", "upholstery": "Estofo",
           "pile": "Pelo", "legs": "Pés", "base": "Base", "dims": "Dimensões (L x P x A)", "size": "Medidas",
           "lamp_dims": "Altura · Ø", "seat_height": "Altura do assento", "seat_depth": "Profundidade do assento",
           "max_load": "Carga máx.", "weight": "Peso", "care": "Limpeza", "bulb": "Lâmpada",
           "origin": "Origem", "manufacturer": "Fabricante", "eu_rp": "Responsável na UE",
           "highlights": "Destaques", "packaging": "Embalagem", "variants": "Variantes e preços",
           "code": "Código", "colour": "Cor", "price": "Preço grossista € (s/ IVA)", "rrp": "PVP €",
           "moq": "MOQ", "lead": "Prazo", "warnings": "Avisos de segurança", "pile_height": "Altura do pelo",
           "weeks": "sem.", "box": "caixa"},
    "it": {"sheet": "Scheda tecnica", "product": "Prodotto", "frame": "Struttura", "upholstery": "Rivestimento",
           "pile": "Vello", "legs": "Gambe", "base": "Base", "dims": "Dimensioni (L x P x A)", "size": "Misure",
           "lamp_dims": "Altezza · Ø", "seat_height": "Altezza seduta", "seat_depth": "Profondità seduta",
           "max_load": "Portata max.", "weight": "Peso", "care": "Manutenzione", "bulb": "Lampadina",
           "origin": "Origine", "manufacturer": "Produttore", "eu_rp": "Responsabile UE",
           "highlights": "Punti di forza", "packaging": "Imballo", "variants": "Listino prezzi",
           "code": "Codice", "colour": "Colore", "price": "Prezzo netto € (IVA escl.)", "rrp": "Prezzo consigliato €",
           "moq": "Ordine min.", "lead": "Consegna", "warnings": "Avvertenze di sicurezza",
           "pile_height": "Altezza vello", "weeks": "sett.", "box": "collo"},
    "de": {"sheet": "Produktdatenblatt", "product": "Produkt", "frame": "Gestell", "upholstery": "Bezug",
           "pile": "Flor", "legs": "Füße", "base": "Fuß", "dims": "Maße (B x T x H)", "size": "Maße",
           "lamp_dims": "Höhe · Ø", "seat_height": "Sitzhöhe", "seat_depth": "Sitztiefe",
           "max_load": "Max. Belastung", "weight": "Gewicht", "care": "Pflege", "bulb": "Leuchtmittel",
           "origin": "Herkunft", "manufacturer": "Hersteller", "eu_rp": "Verantwortliche Person in der EU",
           "highlights": "Highlights", "packaging": "Verpackung", "variants": "Varianten und Preise",
           "code": "Art.-Nr.", "colour": "Farbe", "price": "EK € (netto)", "rrp": "UVP €",
           "moq": "MOQ", "lead": "Lieferzeit", "warnings": "Sicherheitshinweise", "pile_height": "Florhöhe",
           "weeks": "Wo.", "box": "Karton"},
    "en": {"sheet": "Technical sheet", "product": "Product", "frame": "Frame", "upholstery": "Upholstery",
           "pile": "Pile", "legs": "Legs", "base": "Base", "dims": "Dimensions (W x D x H)", "size": "Size",
           "lamp_dims": "Height · Ø", "seat_height": "Seat height", "seat_depth": "Seat depth",
           "max_load": "Max. load", "weight": "Weight", "care": "Care", "bulb": "Bulb", "origin": "Origin",
           "manufacturer": "Manufacturer", "eu_rp": "EU responsible person", "highlights": "Highlights",
           "packaging": "Packaging", "variants": "Variants & prices", "code": "Code", "colour": "Colour",
           "price": "Wholesale €", "rrp": "RRP €", "moq": "MOQ", "lead": "Lead time",
           "warnings": "Safety warnings", "pile_height": "Pile height", "weeks": "wks", "box": "box"},
}  # fmt: skip

TYPES = {
    "armchair": {"pt": "Poltrona", "it": "Poltrona", "de": "Sessel", "en": "armchair"},
    "dining": {
        "pt": "Cadeira de jantar",
        "it": "Sedia da pranzo",
        "de": "Esszimmerstuhl",
        "en": "dining chair",
    },
    "recliner": {"pt": "Poltrona reclinável", "it": "Poltrona relax", "de": "Relaxsessel", "en": "recliner"},
    "sofa": {"pt": "Sofá", "it": "Divano", "de": "Sofa", "en": "sofa"},
    "table_lamp": {
        "pt": "Candeeiro de mesa",
        "it": "Lampada da tavolo",
        "de": "Tischleuchte",
        "en": "table lamp",
    },
    "floor_lamp": {
        "pt": "Candeeiro de pé",
        "it": "Lampada da terra",
        "de": "Stehleuchte",
        "en": "floor lamp",
    },
    "rug": {"pt": "Tapete", "it": "Tappeto", "de": "Teppich", "en": "rug"},
}

COLOUR_NAMES = {
    "pt": {"green": ["Musgo", "Verde"], "grey": ["Antracite", "Cinza"], "beige": ["Areia", "Bege"],
           "blue": ["Azul", "Marinho"], "brown": ["Caramelo", "Castanho"], "black": ["Preto", "Ébano"],
           "white": ["Branco", "Marfim"], "pink": ["Rosa", "Salmão"], "red": ["Vermelho", "Bordô"],
           "orange": ["Laranja", "Terracota"], "yellow": ["Mostarda", "Amarelo"], "purple": ["Roxo", "Lilás"],
           "gold": ["Dourado", "Latão"], "silver": ["Prateado", "Cromado"], "multicolour": ["Multicor", "Estampado"]},
    "it": {"green": ["Verde salvia", "Verde bosco"], "grey": ["Antracite", "Grigio"], "beige": ["Sabbia", "Beige"],
           "blue": ["Blu", "Blu notte"], "brown": ["Cognac", "Marrone"], "black": ["Nero", "Ebano"],
           "white": ["Bianco", "Avorio"], "pink": ["Rosa cipria", "Rosa"], "red": ["Rosso", "Bordeaux"],
           "orange": ["Arancio", "Terracotta"], "yellow": ["Senape", "Giallo"], "purple": ["Viola", "Lilla"],
           "gold": ["Ottone", "Oro"], "silver": ["Argento", "Cromo"], "multicolour": ["Multicolore", "Fantasia"]},
    "de": {"green": ["Moosgrün", "Salbei"], "grey": ["Anthrazit", "Grau"], "beige": ["Sand", "Beige"],
           "blue": ["Blau", "Marine"], "brown": ["Cognac", "Braun"], "black": ["Schwarz", "Ebenholz"],
           "white": ["Weiß", "Elfenbein"], "pink": ["Rosé", "Altrosa"], "red": ["Rot", "Bordeaux"],
           "orange": ["Orange", "Terrakotta"], "yellow": ["Senf", "Gelb"], "purple": ["Lila", "Flieder"],
           "gold": ["Messing", "Gold"], "silver": ["Silber", "Chrom"], "multicolour": ["Bunt", "Gemustert"]},
}  # fmt: skip
WRONG_COLOUR = {"green": "beige", "grey": "beige", "beige": "grey", "blue": "green", "brown": "grey",
                "black": "beige", "white": "grey", "pink": "blue", "red": "green", "orange": "blue",
                "yellow": "blue", "purple": "green", "gold": "silver", "silver": "gold", "multicolour": "grey"}  # fmt: skip

MATERIAL_TEXT = {
    "wood": {"pt": "Faia maciça", "it": "Faggio massello", "de": "Massive Buche", "en": "solid beech"},
    "metal": {"pt": "Aço lacado", "it": "Acciaio verniciato", "de": "Pulverbeschichteter Stahl", "en": "powder-coated steel"},
    "chrome": {"pt": "Aço cromado", "it": "Acciaio cromato", "de": "Verchromter Stahl", "en": "chrome-plated steel"},
    "oak": {"pt": "Carvalho maciço", "it": "Rovere massello", "de": "Massive Eiche", "en": "solid oak"},
    "ceramic": {"pt": "Cerâmica vidrada", "it": "Ceramica smaltata", "de": "Glasierte Keramik", "en": "glazed ceramic"},
    "glass": {"pt": "Vidro soprado", "it": "Vetro soffiato", "de": "Mundgeblasenes Glas", "en": "blown glass"},
    "plastic": {"pt": "Resina", "it": "Resina", "de": "Kunstharz", "en": "resin"},
    "stone": {"pt": "Mármore", "it": "Marmo", "de": "Marmor", "en": "marble"},
}  # fmt: skip
WRONG_MATERIAL = {"wood": "chrome", "metal": "oak", "plastic": "oak", "glass": "metal", "ceramic": "metal",
                  "stone": "wood"}  # fmt: skip
FINISH_TEXT = {
    "dark": {"pt": "acabamento nogueira", "it": "finitura noce", "de": "Nussbaum gebeizt", "en": "walnut stain"},
    "light": {"pt": "acabamento natural a óleo", "it": "finitura naturale a olio", "de": "natur geölt", "en": "natural oil finish"},
    "black": {"pt": "preto mate", "it": "nero opaco", "de": "schwarz matt", "en": "matt black"},
    "chrome": {"pt": "cromado", "it": "cromato", "de": "verchromt", "en": "chrome-plated"},
    "brass": {"pt": "acabamento latão", "it": "finitura ottone", "de": "Messing-Optik", "en": "brass finish"},
}  # fmt: skip
UPHOLSTERY = {
    "fabric": {"pt": "Tecido", "it": "Tessuto", "de": "Webstoff", "en": "woven fabric"},
    "velvet": {"pt": "Veludo", "it": "Velluto", "de": "Samt", "en": "velvet"},
    "boucle": {"pt": "Bouclé", "it": "Bouclé", "de": "Bouclé", "en": "bouclé"},
    "leather": {"pt": "Pele bovina", "it": "Pelle pieno fiore", "de": "Rindleder", "en": "top-grain leather"},
}
FIBRES = {
    "polyester": {"pt": "100% poliéster", "it": "100% poliestere", "de": "100 % Polyester", "en": "100% polyester"},
    "polypropylene": {"pt": "100% polipropileno", "it": "100% polipropilene", "de": "100 % Polypropylen",
                      "en": "100% polypropylene"},
    "wool": {"pt": "100% lã", "it": "100% lana", "de": "100 % Schurwolle", "en": "100% wool"},
    "jute": {"pt": "100% juta", "it": "100% iuta", "de": "100 % Jute", "en": "100% jute"},
}  # fmt: skip
CARE = {
    "wash30": {"pt": "Capa removível, lavar a 30 °C", "it": "Fodera sfoderabile, lavare a 30 °C",
               "de": "Bezug abnehmbar, waschbar bei 30 °C", "en": "removable cover, washable at 30 °C"},
    "dry_clean": {"pt": "Apenas limpeza a seco", "it": "Solo lavaggio a secco", "de": "Nur chemische Reinigung",
                  "en": "dry clean only"},
    "professional": {"pt": "Não lavar; limpeza profissional", "it": "Non lavare; pulizia professionale",
                     "de": "Nicht waschen; professionelle Reinigung", "en": "do not wash; professional cleaning only"},
    "machine": {"pt": "Lavável na máquina a 30 °C", "it": "Lavabile in lavatrice a 30 °C",
                "de": "Maschinenwaschbar bei 30 °C", "en": "machine washable at 30 °C"},
    "wipe": {"pt": "Limpar com pano húmido", "it": "Pulire con panno umido", "de": "Mit feuchtem Tuch abwischen",
             "en": "wipe with a damp cloth"},
    "lamp": {"pt": "Limpar com pano seco, desligado", "it": "Pulire con panno asciutto a lampada spenta",
             "de": "Ausgeschaltet mit trockenem Tuch reinigen", "en": "clean with a dry cloth when switched off"},
}  # fmt: skip
CONTRADICTING_CARE = {"wash30": "dry_clean", "dry_clean": "wash30", "professional": "machine"}
WARNINGS = {
    "seating": {"pt": ["Não utilizar no exterior.", "Manter afastado de fontes de calor.", "Não subir para o assento."],
                "it": ["Non utilizzare all'aperto.", "Tenere lontano da fonti di calore.", "Non salire in piedi sulla seduta."],
                "de": ["Nicht im Freien verwenden.", "Von Wärmequellen fernhalten.", "Nicht auf die Sitzfläche stellen."],
                "en": ["Not for outdoor use.", "Keep away from heat sources.", "Do not stand on the seat."]},
    "lamp": {"pt": ["Usar apenas lâmpadas E27 de máx. 40 W.", "Desligar antes de limpar.", "Apenas para uso interior."],
             "it": ["Usare solo lampadine E27 max 40 W.", "Scollegare prima della pulizia.", "Solo per uso interno."],
             "de": ["Nur Leuchtmittel E27 max. 40 W verwenden.", "Vor dem Reinigen vom Netz trennen.",
                    "Nur für den Innenbereich."],
             "en": ["Use only E27 bulbs, max. 40 W.", "Disconnect before cleaning.", "For indoor use only."]},
    "rug": {"pt": ["Usar base antiderrapante em pisos lisos.", "Manter afastado de chamas abertas."],
            "it": ["Usare un sottotappeto antiscivolo su pavimenti lisci.", "Tenere lontano da fiamme libere."],
            "de": ["Auf glatten Böden eine Antirutschunterlage verwenden.", "Von offenem Feuer fernhalten."],
            "en": ["Use an anti-slip underlay on smooth floors.", "Keep away from open flames."]},
}  # fmt: skip
CLAIMS = {  # D15: health, safety or environmental claims that need a human (AI Act guidance)
    "pt": [("Tecido antibacteriano e 100% ecológico", "antibacterial, 100% eco-friendly fabric"),
           ("Espuma à prova de fogo", "fireproof foam"), ("Materiais hipoalergénicos e não tóxicos", "hypoallergenic, non-toxic materials")],
    "it": [("Tessuto antibatterico ed ecologico", "antibacterial, eco-friendly fabric"),
           ("Imbottitura ignifuga", "fireproof padding"), ("Materiali anallergici e atossici", "hypoallergenic, non-toxic materials")],
    "de": [("Antibakterieller, umweltfreundlicher Stoff", "antibacterial, eco-friendly fabric"),
           ("Feuerfestes Material", "fireproof material"), ("Hypoallergen und schadstofffrei", "hypoallergenic, non-toxic")],
}  # fmt: skip
NEUTRAL = {"pt": ("Design intemporal", "timeless design"), "it": ("Design senza tempo", "timeless design"),
           "de": ("Zeitloses Design", "timeless design")}  # fmt: skip
PROMO = {"pt": "NOVIDADE -15%", "it": "NOVITÀ -20%", "de": "NEU -10%"}
VIEWS = {"pt": ("FRENTE / FRONT", "LADO / SIDE", "medidas em cm"), "it": ("FRONTE / FRONT", "LATO / SIDE", "misure in cm"),
         "de": ("VORNE / FRONT", "SEITE / SIDE", "Maße in cm")}  # fmt: skip
COLOUR_WORDS = {  # provisional truth from ABO colour names (only with --allow-unconfirmed)
    "green": "green emerald sage hunter moss olive", "grey": "grey gray charcoal graphite steel smoke flannel pewter dove storm otter",
    "beige": "beige linen sand oatmeal fawn flax stucco taupe driftwood tan cream ivory cannoli", "blue": "blue navy denim teal aqua indigo",
    "brown": "brown chocolate caramel chestnut adobe saddle cognac walnut acorn", "black": "black caviar pepper",
    "white": "white chalk", "pink": "pink rose blush", "red": "red rouge berry brick", "gold": "gold brass amber",
    "silver": "silver nickel chrome",
}  # fmt: skip


# --------------------------------------------------------------------------- helpers
def ean13(first12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return first12 + str((10 - total % 10) % 10)


assert ean13("560123400001") == "5601234000016"  # the LISBOA example's valid EAN


def ascii_upper(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if c.isalnum() and c.isascii()).upper()


def number(value: float, lang: str) -> str:
    text = f"{value:g}" if value == int(value) else f"{value:.1f}"
    return text.replace(".", ",") if lang != "en" else text


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for name in (("arialbd.ttf" if bold else "arial.ttf"), "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def english(listing: dict, field: str) -> str:
    values = [v["value"] for v in listing.get(field, []) if v.get("language_tag") == "en_US"]
    return values[0] if values else ""


def inches(listing: dict, key: str) -> float | None:
    dim = listing.get("item_dimensions", {}).get(key)
    return dim["normalized_value"]["value"] if dim else None


def provisional_truth(item: dict) -> dict:
    colour = english(item["listing"], "color").lower()
    family = next((f for f, words in COLOUR_WORDS.items() if any(w in colour for w in words.split())), "grey")
    text = (english(item["listing"], "material") + " " + english(item["listing"], "item_name")).lower()
    material = "metal" if "metal" in text else "wood" if "wood" in text else "unsure"
    return {"colour_family": family, "legs_material": material, "base_material": material,
            "legs_finish": None, "confirmed": False, "provisional": True}  # fmt: skip


# --------------------------------------------------------------------------- products (the truth)
def product_kind(category: str, name: str) -> str:
    name = name.lower()
    if category == "chair":
        return "dining" if "dining" in name else "recliner" if "recliner" in name else "armchair"
    if category == "lamp":
        return "floor_lamp" if any(w in name for w in ("floor", "standing", "torchiere")) else "table_lamp"
    return category


def build_product(
    pid: str, name: str, code: str, supplier: str, family: list[dict], truth: dict, rng
) -> dict:
    """The real product: dimensions from ABO, materials and colours from the confirmed photo truth."""
    first = family[0]
    listing, cat = first["listing"], first["category"]
    title = english(listing, "item_name")
    lang = SUPPLIERS[supplier]["lang"]
    w, d, h = (inches(listing, k) for k in ("width", "length", "height"))
    vals = sorted(round(v * 2.54) for v in (w, d, h) if v)
    cm = {k: round(v * 2.54) for k, v in (("width", w), ("length", d), ("height", h)) if v}
    p: dict = {"product_id": pid, "name": name, "code": code, "supplier": supplier, "category": cat,
               "kind": product_kind(cat, title), "family": first["family"], "abo_title": title}  # fmt: skip
    if cat in SEATING:
        if cat == "chair" and cm.get("height", 0) >= 70:
            height, width, depth = cm["height"], cm.get("width", vals[1]), cm.get("length", vals[0])
        elif cat == "chair":  # ABO swapped the labels (e.g. a "36-inch height" chair listed 26" high)
            height, width, depth = vals[-1], max(vals[:-1]), min(vals[:-1])
        else:  # sofas: ABO height is right; the longer of the other two is the width
            rest = sorted(v for k, v in cm.items() if k != "height")
            height, width, depth = cm["height"], rest[-1], rest[0]
        seat = 44 + rng.randint(0, 3) if cat == "chair" else 42 + rng.randint(0, 3)
        p["dims"] = {"width": width, "depth": depth, "height": height, "seat_height": min(seat, height - 20),
                     "seat_depth": round(depth * 0.6)}  # fmt: skip
        p["max_load_kg"] = 120 if cat == "chair" else 110 * max(2, round(width / 75))
    elif cat == "lamp":
        p["dims"] = {"height": round((h or 40) * 2.54), "diameter": round(max(w or 10, d or 10) * 2.54)}
    else:
        p["dims"] = {"length": vals[-1], "width": vals[-2] if len(vals) > 1 else vals[-1],
                     "pile_height": max(round((h or 0.5) * 2.54, 1), 0.6)}  # fmt: skip
    pounds = (listing.get("item_weight") or [{}])[0].get("normalized_value", {}).get("value")
    p["weight_kg"] = round(pounds * 0.4536, 1) if pounds else {"lamp": 2.8, "rug": 6.5}.get(cat, 15.0)

    text = (title + " " + english(listing, "material") + " " + english(listing, "fabric_type")).lower()
    if cat == "rug":
        fibre = next((f for f in ("wool", "jute", "polypropylene") if f in text), "polypropylene")
        p.update(textile=True, upholstery=None, fibre=fibre, care="professional")
    elif cat == "lamp":
        p.update(textile=False, upholstery=None, fibre=None, care="lamp")
    else:
        kind = "leather" if "leather" in text else "velvet" if "velvet" in text else "fabric"
        if first["family"] == "lisboa":
            kind = "boucle"
        p.update(textile=kind != "leather", upholstery=kind, fibre=None if kind == "leather" else "polyester",
                 care={"leather": "wipe", "velvet": "dry_clean"}.get(kind, "wash30"))  # fmt: skip

    facts = [truth[x["item_id"]] for x in family]
    material_key = "base_material" if cat == "lamp" else "legs_material"
    materials = {f[material_key] for f in facts}
    p["material"] = materials.pop() if len(materials) == 1 else "unsure"
    finishes = {(f.get("legs_finish") or "").lower() for f in facts}
    finish = finishes.pop() if len(finishes) == 1 else ""  # e.g. chrome legs on one colour, brass on another
    if p["material"] == "wood":
        p["finish"] = (
            "dark" if any(w in finish for w in ("dark", "walnut", "espresso", "brown", "black")) else "light"
        )
    elif p["material"] == "metal":
        p["finish"] = (
            "chrome"
            if any(w in finish for w in ("chrome", "silver", "nickel"))
            else ("brass" if any(w in finish for w in ("gold", "brass")) else "black")
        )
    else:
        p["finish"] = None
    p["origin"] = SUPPLIERS[supplier]["country"]
    p["manufacturer"] = {
        "name": SUPPLIERS[supplier]["name"],
        "address": SUPPLIERS[supplier]["address"],
        "eu": True,
    }
    if (
        cat == "lamp" and supplier == "nordholz"
    ):  # imported lamps: clean non-EU case with an EU responsible person
        p["manufacturer"] = {**NON_EU, "eu": False}
        p["origin"] = NON_EU["country"]

    used: dict[str, int] = {}
    p["variants"] = []
    for n, (item, fact) in enumerate(zip(family, facts, strict=True)):
        colour = fact["colour_family"]
        k = used.get(colour, 0)
        used[colour] = k + 1
        colour_name = COLOUR_NAMES[lang][colour][min(k, 1)]
        p["variants"].append({"code": "", "colour_family": colour, "colour_name": colour_name,
                              "item_id": item["item_id"], "images": item["images"], "n": n})  # fmt: skip
    if first["family"] == "lisboa":
        for v, colour_name in zip(p["variants"], ["Musgo", "Areia", "Antracite"], strict=True):
            v["colour_name"] = colour_name
    assign_codes(p)
    for v in p["variants"]:
        v["ean"] = ean13(f"{SUPPLIERS[supplier]['gs1']}{int(pid[-2:]):03d}{v['n'] + 1:02d}")
    lo, hi = {"chair": (120, 260), "sofa": (450, 900), "lamp": (25, 80), "rug": (60, 220)}[cat]
    p["price"] = rng.randint(lo, hi)
    return p


def assign_codes(p: dict) -> None:
    seen: set[str] = set()
    for v in p["variants"]:
        suffix = ascii_upper(v["colour_name"])[:3]
        while suffix in seen:
            suffix = suffix[:2] + str(len(seen))
        seen.add(suffix)
        v["code"] = f"{p['code']}-{suffix}"


def eligible(p: dict, defect_id: str, spec: dict, planted: list[str], plan: dict) -> bool:
    if p["category"] not in spec["applies_to"] or defect_id in planted:
        return False
    if any(other in planted for other in spec.get("excludes", [])):
        return False
    needs = set(spec.get("needs", []))
    colours = {v["colour_family"] for v in p["variants"]}
    checks = {
        "textile": p["textile"],
        "drawing": p["category"] in SEATING,
        "material": p["material"] in MATERIALS,
        "colours2": len(colours) >= 2,
        "variants2": len(p["variants"]) >= 2,
    }
    if defect_id in ("D3", "D4") and ({"D3", "D4"} & set(planted)) and len(p["variants"]) < 2:
        return False
    return all(checks[n] for n in needs) and len(planted) < plan["max_defects_per_product"]


def make_plan(products: list[dict], catalogue: dict, rng: random.Random) -> dict[str, list[str]]:
    """Which defects go into which product. LISBOA is fixed (D1-D9); the rest meets the minimum counts."""
    plan, defects = catalogue["plan"], catalogue["defects"]
    planted = {p["product_id"]: [] for p in products}
    hero = next(p for p in products if p["family"] == "lisboa")
    planted[hero["product_id"]] = [f"D{i}" for i in range(1, 10)]
    clean: set[str] = set()
    for cat, n in plan["clean_products"].items():
        pool = sorted(p["product_id"] for p in products if p["category"] == cat and p is not hero)
        clean |= set(rng.sample(pool, n))
    open_products = [p for p in products if p is not hero and p["product_id"] not in clean]

    def count(defect_id: str) -> int:
        return sum(defect_id in ds for ds in planted.values())

    def candidates(defect_id: str) -> list[dict]:
        return [
            p
            for p in open_products
            if eligible(p, defect_id, defects[defect_id], planted[p["product_id"]], plan)
        ]

    # Most constrained defect types first, each onto the products with the fewest defects so far
    order = sorted(defects, key=lambda d: (len(candidates(d)), d))
    for defect_id in order:
        while count(defect_id) < defects[defect_id]["min_count"]:
            pool = candidates(defect_id)
            if not pool:
                raise SystemExit(f"cannot plant {defect_id} {defects[defect_id]['min_count']} times")
            fewest = min(len(planted[p["product_id"]]) for p in pool)
            choice = rng.choice([p for p in pool if len(planted[p["product_id"]]) == fewest])
            planted[choice["product_id"]].append(defect_id)
    for p in open_products:  # every non-clean product gets at least one defect
        if not planted[p["product_id"]]:
            options = [d for d in sorted(defects) if eligible(p, d, defects[d], [], plan)]
            planted[p["product_id"]].append(rng.choice(options))
    for pid in planted:
        planted[pid].sort(key=lambda d: int(d[1:]))
    return planted


# --------------------------------------------------------------------------- the supplier document
def apply_defects(p: dict, defect_ids: list[str], rng: random.Random) -> tuple[dict, list[dict]]:
    """What the supplier's documents claim (the truth with defects planted), plus the ground truth."""
    lang = SUPPLIERS[p["supplier"]]["lang"]
    doc = copy.deepcopy(p)
    doc.update(warnings_en=True, claims=[], highlight=rng.random() < 0.4, fibre_shown=True,
               missing_address=False, eu_rp_missing=False, mm_field=None, legs_by_variant=None, photo_mix=None)  # fmt: skip
    truth: list[dict] = []
    hero = p["family"] == "lisboa"
    for defect_id in defect_ids:
        entry = {"id": defect_id, "variant": None}
        if defect_id == "D1":
            doc["dims"]["seat_height"] = None
            entry |= {"attribute": "seat_height", "spec_value": None, "true_value": p["dims"]["seat_height"],
                      "evidence": "dimension_drawing"}  # fmt: skip
        elif defect_id == "D2":
            doc["mm_field"] = "seat_depth"
            entry |= {"attribute": "seat_depth", "spec_value": f"{p['dims']['seat_depth'] * 10} mm",
                      "true_value": f"{p['dims']['seat_depth']} cm"}  # fmt: skip
        elif defect_id in ("D3", "D4"):
            taken = {t["variant"] for t in truth if t["id"] in ("D3", "D4")}
            order = doc["variants"][::-1] if defect_id == "D3" else doc["variants"]
            if hero:
                order = [
                    v for v in doc["variants"] if v["code"].endswith("ANT" if defect_id == "D3" else "ARE")
                ]
            v = next(x for x in order if x["code"] not in taken)
            true_ean = v["ean"]
            v["ean"] = None if defect_id == "D3" else true_ean[:-1] + str((int(true_ean[-1]) + 1) % 10)
            entry |= {
                "variant": v["code"],
                "attribute": "ean",
                "spec_value": v["ean"],
                "true_value": true_ean,
            }
        elif defect_id == "D5":
            doc["care"] = CONTRADICTING_CARE[p["care"]]
            entry |= {"attribute": "care", "spec_value": CARE[doc["care"]]["en"], "true_value": CARE[p["care"]]["en"],
                      "evidence": "care_label"}  # fmt: skip
        elif defect_id == "D6":
            wrong = WRONG_MATERIAL[p["material"]]
            if hero:  # one variant's line copied from another product: chrome legs on the anthracite chair
                ant = next(v for v in doc["variants"] if v["code"].endswith("ANT"))
                doc["legs_by_variant"] = {ant["code"]: "chrome"}
                entry["variant"] = ant["code"]
            else:
                doc["material"], doc["finish"] = wrong, None
            entry |= {"attribute": "legs_or_base", "spec_value": MATERIAL_TEXT[wrong]["en"],
                      "true_value": p["material"], "evidence": "photo"}  # fmt: skip
        elif defect_id == "D7":
            doc["warnings_en"] = False
            entry |= {
                "attribute": "safety_warnings",
                "spec_value": f"{lang} only",
                "true_value": "all shop languages",
            }
        elif defect_id in ("D8", "D9"):
            entry |= {"attribute": "photo"}  # file names are added when the photos are laid out
        elif defect_id == "D10":
            plain = [x for x in doc["variants"] if x["colour_family"] != "multicolour"]  # a clear colour only
            v = doc["variants"][-1] if hero else rng.choice(plain)
            wrong = WRONG_COLOUR[v["colour_family"]]
            v["colour_name"] = COLOUR_NAMES[lang][wrong][0]
            entry |= {"variant": v["code"], "attribute": "colour", "spec_value": v["colour_name"],
                      "true_value": v["colour_family"], "evidence": "photo"}  # fmt: skip
        elif defect_id == "D11":
            delta = rng.choice([-12, -10, 8, 10, 12])
            doc["dims"]["width"] = p["dims"]["width"] + delta
            entry |= {"attribute": "width", "spec_value": doc["dims"]["width"], "true_value": p["dims"]["width"],
                      "evidence": "dimension_drawing"}  # fmt: skip
        elif defect_id == "D12":
            doc["missing_address"] = True
            entry |= {
                "attribute": "manufacturer_address",
                "spec_value": None,
                "true_value": "required (GPSR a)",
            }
        elif defect_id == "D13":
            doc["manufacturer"] = {**NON_EU, "eu": False}
            doc["origin"], doc["eu_rp_missing"] = NON_EU["country"], True
            entry |= {
                "attribute": "eu_responsible_person",
                "spec_value": None,
                "true_value": "required (GPSR b)",
            }
        elif defect_id == "D14":
            doc["fibre_shown"] = False
            fibre = FIBRES[p["fibre"]]["en"]
            entry |= {"attribute": "upholstery", "spec_value": "no fibre composition", "true_value": fibre}
        elif defect_id == "D15":
            claim = rng.choice(CLAIMS[lang])
            doc["claims"] = [claim]
            entry |= {"attribute": "claims", "spec_value": claim[1], "true_value": "needs human review"}
        elif defect_id == "D16":
            pairs = [(a, b) for a in doc["variants"] for b in doc["variants"]
                     if a["colour_family"] != b["colour_family"]]  # fmt: skip
            a, b = rng.choice(pairs)
            doc["photo_mix"] = (a["code"], b["code"])
            entry |= {"variant": a["code"], "attribute": "photo", "true_value": b["code"]}
        elif defect_id == "D17":
            if p["category"] in SEATING:
                doc["max_load_kg"] = p["max_load_kg"] * 10
                entry |= {
                    "attribute": "max_load",
                    "spec_value": doc["max_load_kg"],
                    "true_value": p["max_load_kg"],
                }
            else:
                doc["weight_kg"] = round(p["weight_kg"] * 10, 1)
                entry |= {"attribute": "weight", "spec_value": doc["weight_kg"], "true_value": p["weight_kg"]}
        truth.append(entry)
    return doc, truth


def spec_rows(doc: dict) -> list[tuple[str, str, str]]:
    """(local label, English label, value) rows for the spec sheet."""
    lang = SUPPLIERS[doc["supplier"]]["lang"]
    loc, en = LABELS[lang], LABELS["en"]
    kind = TYPES[doc["kind"]]
    dims, cat = doc["dims"], doc["category"]
    rows: list[tuple[str, str, str]] = [
        ("product", "product", f"{kind[lang]} {doc['name']} / {doc['name']} {kind['en']}")
    ]

    def material_text(material: str, finish: str | None) -> str:
        local, english_text = MATERIAL_TEXT[material][lang], MATERIAL_TEXT[material]["en"]
        if finish:
            local, english_text = (
                f"{local}, {FINISH_TEXT[finish][lang]}",
                f"{english_text}, {FINISH_TEXT[finish]['en']}",
            )
        return f"{local} / {english_text}"

    if cat in SEATING:
        frame = {"pt": "Pinho + contraplacado, espuma 30 kg/m³", "it": "Pino + multistrato, poliuretano 30 kg/m³",
                 "de": "Kiefer + Sperrholz, Schaum 30 kg/m³"}[lang]  # fmt: skip
        rows.append(("frame", "frame", f"{frame} / pine + plywood, foam 30 kg/m³"))
        up = UPHOLSTERY[doc["upholstery"]]
        if doc["textile"] and doc["fibre_shown"]:
            extra = ", Martindale 40.000" if doc["upholstery"] == "boucle" else ""
            value = (
                f"{up[lang]}, {FIBRES[doc['fibre']][lang]}{extra} / {up['en']}, {FIBRES[doc['fibre']]['en']}"
            )
        else:
            value = f"{up[lang]} / {up['en']}"
        rows.append(("upholstery", "upholstery", value))
        if doc["material"] in MATERIAL_TEXT:  # unknown (e.g. a swivel base): no row rather than a dash
            legs = material_text(doc["material"], doc["finish"])
            if doc["legs_by_variant"]:
                ((code, other),) = doc["legs_by_variant"].items()
                others = ", ".join(v["code"].split("-")[-1] for v in doc["variants"] if v["code"] != code)
                legs = f"{legs} ({others}) · {material_text(other, None)} ({code.split('-')[-1]})"
            rows.append(("legs", "legs", legs))
        rows.append(("dims", "dims", f"{dims['width']} x {dims['depth']} x {dims['height']} cm"))
        rows.append(
            ("seat_height", "seat_height", f"{dims['seat_height']} cm" if dims["seat_height"] else "—")
        )
        sd = dims["seat_depth"]
        rows.append(
            ("seat_depth", "seat_depth", f"{sd * 10} mm" if doc["mm_field"] == "seat_depth" else f"{sd} cm")
        )
        rows.append(("max_load", "max_load", f"{doc['max_load_kg']} kg"))
    elif cat == "lamp":
        if doc["material"] in MATERIAL_TEXT:
            rows.append(("base", "base", material_text(doc["material"], doc["finish"])))
        rows.append(("lamp_dims", "lamp_dims", f"{dims['height']} cm · Ø {dims['diameter']} cm"))
        rows.append(("bulb", "bulb", "E27, max. 40 W (LED)"))
    else:
        fibre = FIBRES[doc["fibre"]]
        weave = {"pt": "Tufado à mão", "it": "Taftato a mano", "de": "Handgetuftet"}[lang]
        value = (
            f"{weave}, {fibre[lang]} / hand-tufted, {fibre['en']}"
            if doc["fibre_shown"]
            else f"{weave} / hand-tufted"
        )
        rows.append(("pile", "pile", value))
        rows.append(("size", "size", f"{dims['length']} x {dims['width']} cm"))
        rows.append(("pile_height", "pile_height", f"{number(dims['pile_height'], lang)} cm"))
    rows.append(("weight", "weight", f"{number(doc['weight_kg'], lang)} kg"))
    care = CARE[doc["care"]]
    rows.append(("care", "care", f"{care[lang]} / {care['en']}"))
    hs = {"chair": "9401.61", "sofa": "9401.61", "lamp": "9405.21", "rug": "5703.39"}[cat]
    rows.append(("origin", "origin", f"{doc['origin']} · HS {hs}"))
    maker = doc["manufacturer"]
    maker_text = maker["name"] if doc["missing_address"] else f"{maker['name']}, {maker['address']}"
    rows.append(("manufacturer", "manufacturer", maker_text))
    if not maker["eu"]:
        supplier = SUPPLIERS[doc["supplier"]]
        rows.append(
            ("eu_rp", "eu_rp", "—" if doc["eu_rp_missing"] else f"{supplier['name']}, {supplier['address']}")
        )
    if doc["claims"]:
        rows.append(("highlights", "highlights", " · ".join(f"{a} / {b}" for a, b in doc["claims"])))
    elif doc["highlight"]:
        a, b = NEUTRAL[lang]
        rows.append(("highlights", "highlights", f"{a} / {b}"))
    if cat in SEATING:
        box = (dims["width"] + 6, dims["depth"] + 6, dims["height"] + 6)
        rows.append(("packaging", "packaging",
                     f"1 {loc['box']}, {box[0]} x {box[1]} x {box[2]} cm, {number(doc['weight_kg'] + 3, lang)} kg"))  # fmt: skip
    return [(loc[a], en[b], value) for a, b, value in rows]


STYLE = {
    "lusa": """body{font-family:Georgia,serif;color:#222;font-size:12.5px}
.h{display:flex;justify-content:space-between;border-bottom:3px solid #222;padding-bottom:8px;margin-bottom:14px}
.h b{font-size:20px;letter-spacing:1px}table{border-collapse:collapse;width:100%;margin-bottom:12px}
td,th{border:1px solid #bbb;padding:5px 8px;text-align:left;vertical-align:top}td.l{width:36%;color:#555}
h3{font-size:14px;margin:16px 0 6px}.f{margin-top:18px;font-size:10px;color:#666}""",
    "brianza": """body{font-family:Helvetica,Arial,sans-serif;color:#1d2b36;font-size:12px}
.h{background:#1d2b36;color:#fff;padding:12px 16px;margin-bottom:14px}.h b{font-size:18px}.h span{float:right}
.g{display:grid;grid-template-columns:1fr 1fr;gap:6px 18px}.g div{border-bottom:1px solid #d5dde3;padding:4px 0}
.g i{display:block;font-style:normal;color:#6b7c88;font-size:10.5px}table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #d5dde3;padding:6px;text-align:left}th{background:#eef2f5}
h3{font-size:14px;margin:16px 0 6px;color:#1d2b36}.pb{page-break-before:always}.f{margin-top:18px;font-size:10px;color:#6b7c88}""",
    "nordholz": """body{font-family:Arial,sans-serif;color:#111;font-size:10px}
.h{border-left:6px solid #2e7d32;padding:4px 10px;margin-bottom:10px}.h b{font-size:16px}
table{border-collapse:collapse;width:100%;margin-bottom:8px}td,th{border:1px solid #999;padding:3px 5px;text-align:left}
th{background:#e8f0e8}h3{font-size:11px;margin:10px 0 4px}.f{margin-top:10px;font-size:8.5px;color:#555}""",
}


def spec_html(doc: dict, date: str) -> str:
    supplier = SUPPLIERS[doc["supplier"]]
    lang, style = supplier["lang"], doc["supplier"]
    loc, en = LABELS[lang], LABELS["en"]
    e = html.escape
    rows = spec_rows(doc)
    variants = [(v["code"], v["colour_name"], v["ean"] or "—", doc["price"] + (10 if n == 2 else 0))
                for n, v in enumerate(doc["variants"])]  # fmt: skip
    warnings = WARNINGS["seating" if doc["category"] in SEATING else doc["category"]]
    if doc["warnings_en"]:
        warning_lines = [f"{a} / {b}" for a, b in zip(warnings[lang], warnings["en"], strict=True)]
    else:
        warning_lines = warnings[lang]
    head = f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>@page{{size:A4;margin:14mm}}{STYLE[style]}</style></head><body>"
    title = f"{loc['sheet']} / {en['sheet']} · Ref. {doc['code']} · Rev. 2 · {date}"
    footer = f"<div class='f'>{e(supplier['name'])} · {e(supplier['address'])}</div>"
    var_head = f"<tr><th>{loc['code']}</th><th>{loc['colour']}</th><th>EAN</th><th>{loc['price']}</th><th>{loc['rrp']}</th></tr>"
    var_rows = "".join(f"<tr><td>{e(c)}</td><td>{e(n)}</td><td>{e(ean)}</td><td>{number(pr, lang)}</td>"
                       f"<td>{number(round(pr * 2.4), lang)}</td></tr>" for c, n, ean, pr in variants)  # fmt: skip
    warn_html = "".join(f"<li>{e(w)}</li>" for w in warning_lines)
    if style == "lusa":
        body = (f"<div class='h'><b>{e(supplier['name'].upper())}</b><span>{e(title)}</span></div><table>"
                + "".join(f"<tr><td class='l'>{e(a)} / {e(b)}</td><td>{e(v)}</td></tr>" for a, b, v in rows)
                + f"</table><h3>{loc['variants']} / {en['variants']}</h3><table>{var_head}{var_rows}</table>"
                f"<h3>{loc['warnings']}</h3><ul>{warn_html}</ul>{footer}")  # fmt: skip
    elif style == "brianza":
        body = (f"<div class='h'><b>{e(supplier['name'])}</b><span>{e(title)}</span></div><div class='g'>"
                + "".join(f"<div><i>{e(a)} / {e(b)}</i>{e(v)}</div>" for a, b, v in rows)
                + f"</div><h3>{loc['warnings']} / {en['warnings']}</h3><ul>{warn_html}</ul>{footer}"
                f"<div class='pb'></div><div class='h'><b>{loc['variants']} / {en['variants']}</b>"
                f"<span>{e(doc['name'])} · {date}</span></div><table>{var_head}{var_rows}</table>{footer}")  # fmt: skip
    else:
        body = (f"<div class='h'><b>{e(supplier['name'])} — {e(doc['name'])}</b><br>{e(title)}</div>"
                f"<table><tr><th>Merkmal</th><th>Feature</th><th>Wert / Value</th></tr>"
                + "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(v)}</td></tr>" for a, b, v in rows)
                + f"</table><h3>{loc['variants']} / {en['variants']}</h3><table>{var_head}{var_rows}</table>"
                f"<h3>{loc['warnings']} / {en['warnings']}</h3><ul>{warn_html}</ul>"
                f"{footer}<div class='f'>Amtsgericht Bad Oeynhausen HRB 12345 · USt-IdNr. DE123456789</div>")  # fmt: skip
    return head + body + "</body></html>"


def render_pdf(html_path: Path, pdf_path: Path) -> None:
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={profile}",
             f"--print-to-pdf={pdf_path}", html_path.as_uri()],
            check=True, capture_output=True, timeout=120,
        )  # fmt: skip
    if not pdf_path.exists():
        raise SystemExit(f"Chrome did not write {pdf_path}")


# --------------------------------------------------------------------------- generated images
def care_label(doc: dict, true_product: dict, path: Path) -> None:
    """A sewn-in label showing the TRUE composition and care (the spec may contradict it)."""
    lang = SUPPLIERS[doc["supplier"]]["lang"]
    im = Image.new("RGB", (900, 1100), "#e9e5dc")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([120, 60, 780, 1040], 18, fill="#fbfaf6", outline="#cfc8b8", width=4)
    fibre = FIBRES[true_product["fibre"]]
    lines = [(SUPPLIERS[doc["supplier"]]["name"].split(",")[0].upper(), font(40, True)), (doc["code"], font(34)),
             (fibre[lang].upper(), font(38, True)), (fibre["en"].upper(), font(38, True))]  # fmt: skip
    if true_product["care"] == "wash30":
        lines += [({"pt": "Capa removível", "it": "Fodera sfoderabile", "de": "Bezug abnehmbar"}[lang], font(36)),
                  ("Removable cover", font(36))]  # fmt: skip
    y = 110
    for text, f in lines:
        d.text(((900 - d.textlength(text, font=f)) / 2, y), text, fill="#222", font=f)
        y += 68
    y0 = 640
    # wash tub: "30" (washable) or crossed out (do not wash)
    d.line([(190, y0), (210, y0 + 110), (330, y0 + 110), (350, y0)], fill="#222", width=6)
    d.arc([180, y0 - 30, 360, y0 + 30], 0, 180, fill="#222", width=6)
    if true_product["care"] == "wash30":
        d.text((232, y0 + 30), "30", fill="#222", font=font(46, True))
    else:
        d.line([(185, y0 - 20), (355, y0 + 120)], fill="#222", width=6)
        d.line([(355, y0 - 20), (185, y0 + 120)], fill="#222", width=6)
    # no bleach
    d.polygon([(400, y0 + 110), (480, y0 - 10), (560, y0 + 110)], outline="#222", width=6)
    d.line([(395, y0 - 5), (565, y0 + 115)], fill="#222", width=6)
    d.line([(565, y0 - 5), (395, y0 + 115)], fill="#222", width=6)
    # professional cleaning: circle with P (dry clean / professional), or a low iron for washables
    if true_product["care"] == "wash30":
        d.polygon([(600, y0 + 110), (620, y0 + 20), (720, y0 + 20), (740, y0 + 110)], outline="#222", width=6)
        d.ellipse([662, y0 + 55, 678, y0 + 71], fill="#222")
    else:
        d.ellipse([600, y0, 720, y0 + 120], outline="#222", width=6)
        d.text((640, y0 + 28), "P", fill="#222", font=font(56, True))
    made = {"pt": "Fabricado em", "it": "Prodotto in", "de": "Hergestellt in"}[lang]
    d.text((260, 860), f"{made} {true_product['origin']}", fill="#444", font=font(34))
    im.save(path)


def dimension_drawing(doc: dict, true_dims: dict, path: Path) -> None:
    """Front + side line drawing with the TRUE measurements, incl. the seat height."""
    lang = SUPPLIERS[doc["supplier"]]["lang"]
    front, side, unit = VIEWS[lang]
    im = Image.new("RGB", (1600, 900), "white")
    d = ImageDraw.Draw(im)
    w, dep, h, seat = true_dims["width"], true_dims["depth"], true_dims["height"], true_dims["seat_height"]
    s = min(620 / w, 560 / h, 520 / dep)
    fb, f = font(34, True), font(28)
    x0, base = 140, 760
    d.rounded_rectangle([x0, base - h * s, x0 + w * s, base - 60], 30, outline="#111", width=5)
    d.line([(x0 + 50, base - seat * s), (x0 + w * s - 50, base - seat * s)], fill="#111", width=4)
    for lx in (x0 + 30, x0 + w * s - 30):
        d.line([(lx, base - 60), (lx, base)], fill="#111", width=8)
    d.line([(x0, base + 50), (x0 + w * s, base + 50)], fill="#c00", width=3)
    d.text((x0 + w * s / 2 - 50, base + 58), f"{w} cm", fill="#c00", font=fb)
    d.line([(x0 - 50, base), (x0 - 50, base - h * s)], fill="#c00", width=3)
    d.text((x0 - 125, base - h * s / 2 - 20), f"{h}", fill="#c00", font=fb)
    d.line([(x0 + w * s + 40, base), (x0 + w * s + 40, base - seat * s)], fill="#c00", width=3)
    d.text((x0 + w * s + 52, base - seat * s / 2 - 20), f"{seat}", fill="#c00", font=fb)
    d.text((x0, 40), front, fill="#111", font=fb)
    x1 = 980
    d.polygon([(x1, base - h * s), (x1 + 90, base - h * s), (x1 + dep * s, base - seat * s),
               (x1 + dep * s, base - 60), (x1, base - 60)], outline="#111", width=5)  # fmt: skip
    for lx in (x1 + 25, x1 + dep * s - 25):
        d.line([(lx, base - 60), (lx, base)], fill="#111", width=8)
    d.line([(x1, base + 50), (x1 + dep * s, base + 50)], fill="#c00", width=3)
    d.text((x1 + dep * s / 2 - 50, base + 58), f"{dep} cm", fill="#c00", font=fb)
    d.text((x1, 40), side, fill="#111", font=fb)
    footer = f"{doc['code']} · {unit}"
    d.text((1580 - d.textlength(footer, font=f), 862), footer, fill="#555", font=f)
    im.save(path)


def promo_copy(src: Path, text: str, path: Path) -> None:
    im = Image.open(src).convert("RGB")
    im.thumbnail((1600, 1600))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, im.width, int(im.height * 0.11)], fill="#e4002b")
    d.text((40, int(im.height * 0.02)), text, fill="white", font=font(int(im.height * 0.065), True))
    im.save(path, quality=88)


def low_res_copy(src: Path, path: Path) -> None:
    im = Image.open(src).convert("RGB")
    im.thumbnail((400, 400))
    im.save(path, quality=80)


# --------------------------------------------------------------------------- submission (email + files)
PHOTO_SUFFIX = ["front", "34", "side", "detail", "back", "top"]


def photo_name(doc: dict, variant_code: str, n: int) -> str:
    short = variant_code.split("-")[-1]
    compact = doc["code"].replace("-", "")
    if doc["supplier"] == "lusa":
        return f"{compact}_{short}_{PHOTO_SUFFIX[n % len(PHOTO_SUFFIX)]}.jpg"
    if doc["supplier"] == "brianza":
        return f"{ascii_upper(doc['name'])}_{short}_{n + 1:02d}.jpg"
    return f"{ascii_upper(doc['name'])}-{short}-{n + 1}.jpg"


def lay_out_files(doc, true_product, planted, truth_entries, folder, rng) -> tuple[list[dict], dict]:
    """Attachments as the supplier sends them, and what each file really shows (ground truth)."""
    lang = SUPPLIERS[doc["supplier"]]["lang"]
    compact = doc["code"].replace("-", "")
    attachments, files = [], {}
    by_code = {v["code"]: v for v in doc["variants"]}

    def planted_entry(defect_id: str) -> dict:
        return next(t for t in truth_entries if t["id"] == defect_id)

    for v in doc["variants"]:
        for n, image in enumerate(v["images"]):
            name = photo_name(doc, v["code"], n)
            attachments.append(
                {"file": name, "kind": "photo", "abo_path": image["path"], "sha256": image["sha256"]}
            )
            files[name] = {"kind": "photo", "shows_variant": v["code"], "abo_item_id": v["item_id"]}
    # One camera-style file name per product: the model has to work out the variant itself
    last = attachments[len(doc["variants"][0]["images"]) - 1]
    camera = f"IMG_{4000 + rng.randint(0, 999)}.JPG"
    files[camera] = files.pop(last["file"])
    last["file"] = camera
    if doc["photo_mix"]:  # D16: a photo of variant B filed under variant A's name
        a, b = doc["photo_mix"]
        source = by_code[b]["images"][min(1, len(by_code[b]["images"]) - 1)]
        name = photo_name(doc, a, len(by_code[a]["images"]))
        attachments.append(
            {"file": name, "kind": "photo", "abo_path": source["path"], "sha256": source["sha256"]}
        )
        files[name] = {"kind": "photo", "shows_variant": b, "abo_item_id": by_code[b]["item_id"]}
        planted_entry("D16")["file"] = name
    if "D8" in planted:
        v = (
            next(x for x in doc["variants"] if x["code"].endswith("MUS"))
            if doc["family"] == "lisboa"
            else doc["variants"][0]
        )
        name = f"{compact}_promo.jpg"
        promo_copy(IMAGES / v["images"][0]["path"], PROMO[lang], folder / name)
        attachments.append({"file": name, "kind": "photo", "path": name})
        files[name] = {"kind": "photo", "shows_variant": v["code"], "promo_overlay": True}
        planted_entry("D8").update(file=name, variant=v["code"])
    if "D9" in planted:
        v = (
            next(x for x in doc["variants"] if x["code"].endswith("ARE"))
            if doc["family"] == "lisboa"
            else doc["variants"][-1]
        )
        name = f"{compact}_{v['code'].split('-')[-1]}_back_small.jpg"
        low_res_copy(IMAGES / v["images"][min(1, len(v["images"]) - 1)]["path"], folder / name)
        attachments.append({"file": name, "kind": "photo", "path": name})
        files[name] = {"kind": "photo", "shows_variant": v["code"], "low_res": True}
        planted_entry("D9").update(file=name, variant=v["code"])
    if true_product["textile"] and true_product["fibre"]:
        name = f"{compact}_label.png"
        care_label(doc, true_product, folder / name)
        attachments.append({"file": name, "kind": "label", "path": name})
        files[name] = {"kind": "care_label", "care": true_product["care"], "fibre": true_product["fibre"]}
    if true_product["category"] in SEATING:
        name = f"{compact}_dimensions.png"
        dimension_drawing(doc, true_product["dims"], folder / name)
        attachments.append({"file": name, "kind": "drawing", "path": name})
        files[name] = {"kind": "dimension_drawing", "dims": true_product["dims"]}
    for t in truth_entries:
        if t.get("evidence") == "care_label":
            t["evidence"] = f"{compact}_label.png"
        elif t.get("evidence") == "dimension_drawing":
            t["evidence"] = f"{compact}_dimensions.png"
    return attachments, files


def email(doc: dict, spec_file: str, attachments: list[dict], date: str) -> dict:
    supplier = SUPPLIERS[doc["supplier"]]
    lang, kind = supplier["lang"], TYPES[doc["kind"]]
    n = len(doc["variants"])
    subject = {
        "pt": f"Nova coleção AW26 – {kind['pt'].lower()} {doc['name']} – ficha técnica + fotos",
        "it": f"Novità collezione 2027 – {doc['name']} – scheda tecnica e foto",
        "de": f"Neuheit {doc['name']} ({kind['de']}) – Produktdatenblatt und Fotos",
    }[lang]
    body = {
        "pt": f"Bom dia,\n\nSegue em anexo a ficha técnica do produto {doc['name']} ({n} cores) e as fotos.\n"
              f"Hi team, please find attached the spec sheet for {doc['name']} and the photos.\n\n"
              f"Cumprimentos,\n{supplier['contact']}\n{supplier['name']}",
        "it": f"Buongiorno,\n\nin allegato la scheda tecnica di {doc['name']} ({n} colori), il listino e le foto.\n"
              f"Please find attached the data sheet, price list and photos.\n\n"
              f"Cordiali saluti,\n{supplier['contact']}\n{supplier['name']}",
        "de": f"Guten Tag,\n\nanbei das Produktdatenblatt für {doc['name']} ({n} Farben) und die Fotos.\n"
              f"Attached you will find the product data sheet and photos.\n\n"
              f"Mit freundlichen Grüßen\n{supplier['contact']}\n{supplier['name']}",
    }[lang]  # fmt: skip
    return {"from": supplier["email"], "to": "compras@casanord.example", "date": date, "subject": subject,
            "body": body, "attachments": [spec_file] + [a["file"] for a in attachments]}  # fmt: skip


# --------------------------------------------------------------------------- main
def choose_families(items: list[dict], truth: dict, allow_unconfirmed: bool) -> list[list[dict]]:
    families: dict[str, list[dict]] = {}
    for item in items:
        if item["set"] == "synthetic":
            families.setdefault(item["family"], []).append(item)
    usable = []
    for family in families.values():
        facts = [truth.get(x["item_id"]) for x in family]
        if any(f is None or f.get("reject") for f in facts):
            continue
        if not allow_unconfirmed and not all(f.get("confirmed") for f in facts):
            continue
        # Variants whose colour is debatable are left out, so no colour defect rests on a judgement call
        usable.append([x for x in family if not truth[x["item_id"]].get("exclude")])
    chosen = [f for f in usable if f[0]["family"] == "lisboa"]
    if not chosen:
        raise SystemExit("LISBOA (Rivet Revolve) needs confirmed photo truth")
    for cat, n in PRODUCTS_PER_CATEGORY.items():
        pool = [f for f in usable if f[0]["category"] == cat and f[0]["family"] != "lisboa"]
        if len(pool) < n:
            raise SystemExit(f"only {len(pool)} usable {cat} families, need {n}")
        chosen += pool[:n]
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--allow-unconfirmed", action="store_true",
                        help="use unconfirmed (or ABO-derived) photo truth; for development only")  # fmt: skip
    args = parser.parse_args()
    if not CHROME.exists():
        raise SystemExit(f"Chrome not found at {CHROME}")
    catalogue = yaml.safe_load(DEFECTS.read_text(encoding="utf-8"))
    rng = random.Random(catalogue["plan"]["seed"])
    items = [json.loads(line) for line in ITEMS.read_text(encoding="utf-8").splitlines() if line]
    truth = json.loads(TRUTH.read_text(encoding="utf-8")) if TRUTH.exists() else {}
    if args.allow_unconfirmed:
        for item in items:
            if item["set"] == "synthetic" and item["item_id"] not in truth:
                truth[item["item_id"]] = provisional_truth(item)

    families = choose_families(items, truth, args.allow_unconfirmed)
    used_names = {key: iter(s["names"]) for key, s in SUPPLIERS.items()}
    turn = {cat: 0 for cat in CATEGORY_SUPPLIERS}
    products = []
    for n, family in enumerate(families):
        cat = family[0]["category"]
        if family[0]["family"] == "lisboa":
            supplier, name, code = "lusa", "LISBOA", "LIS-01"
        else:
            supplier = CATEGORY_SUPPLIERS[cat][turn[cat] % len(CATEGORY_SUPPLIERS[cat])]
            turn[cat] += 1
            name = next(used_names[supplier])
            code = f"{ascii_upper(name)[:3]}-{n:02d}"
        products.append(build_product(f"SYN-{n:02d}", name, code, supplier, family, truth, rng))

    plan = make_plan(products, catalogue, rng)
    counts = {d: sum(d in ds for ds in plan.values()) for d in catalogue["defects"]}
    short = {d: c for d, c in counts.items() if c < catalogue["defects"][d]["min_count"]}
    assert not short, f"defects planted too rarely: {short}"
    clean = [pid for pid, ds in plan.items() if not ds]
    assert len(clean) == sum(catalogue["plan"]["clean_products"].values()), clean

    for old in OUT.glob("SYN-*"):
        shutil.rmtree(old)
    ground_truth = []
    for n, p in enumerate(products):
        folder = OUT / f"{p['product_id']}-{ascii_upper(p['name'])}"
        folder.mkdir(parents=True)
        date = f"2026-10-{1 + n % 9:02d}"
        doc, planted_truth = apply_defects(p, plan[p["product_id"]], rng)
        attachments, files = lay_out_files(doc, p, plan[p["product_id"]], planted_truth, folder, rng)
        spec_file = {"lusa": f"{p['code'].replace('-', '')}_ficha_tecnica_v2.pdf",
                     "brianza": f"{ascii_upper(p['name'])}_scheda_tecnica.pdf",
                     "nordholz": f"{ascii_upper(p['name'])}_Produktdatenblatt.pdf"}[p["supplier"]]  # fmt: skip
        (folder / "spec.html").write_text(spec_html(doc, date), encoding="utf-8")
        render_pdf(folder / "spec.html", folder / "spec.pdf")
        files[spec_file] = {"kind": "spec", "path": "spec.pdf"}
        submission = {"product_id": p["product_id"], "supplier": SUPPLIERS[p["supplier"]]["name"],
                      "email": email(doc, spec_file, attachments, date),
                      "attachments": [{"file": spec_file, "kind": "spec", "path": "spec.pdf"}, *attachments]}  # fmt: skip
        (folder / "submission.json").write_text(json.dumps(submission, indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8")  # fmt: skip
        ground_truth.append({
            "product_id": p["product_id"], "name": p["name"], "code": p["code"], "folder": folder.name,
            "supplier": SUPPLIERS[p["supplier"]]["name"], "category": p["category"], "family": p["family"],
            "abo_item_ids": [v["item_id"] for v in p["variants"]],
            "truth": {"dims": p["dims"], "weight_kg": p["weight_kg"], "max_load_kg": p.get("max_load_kg"),
                      "material": p["material"], "care": p["care"], "fibre": p["fibre"],
                      "variants": [{"code": v["code"], "colour_family": v["colour_family"], "ean": v["ean"]}
                                   for v in p["variants"]]},
            "files": files, "planted_defects": planted_truth, "clean": not planted_truth,
            "photo_truth_confirmed": all(truth[v["item_id"]].get("confirmed") for v in p["variants"]),
        })  # fmt: skip
        print(
            f"{p['product_id']} {p['name']:9} {p['category']:5} {p['supplier']:8} {plan[p['product_id']] or 'clean'}"
        )
    (OUT / "ground_truth.json").write_text(json.dumps(ground_truth, indent=1, ensure_ascii=False) + "\n",
                                           encoding="utf-8")  # fmt: skip
    digest = hashlib.sha256((OUT / "ground_truth.json").read_bytes()).hexdigest()[:12]
    print(f"{len(products)} products, {len(clean)} clean, defects per type: {counts}; ground truth {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
