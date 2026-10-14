# ruff: noqa: E501  (multilingual word lists read better one category per line)
"""Normalisation in code, never by the model: numbers, units, dimensions, EANs, and supplier words
(colours, materials, care) mapped onto the same categories the photo model uses.

Suppliers write "23,5 kg", "520 mm", "Antracite", "Faia maciça"; the model reads them exactly as written
(spec.py), and this module turns them into comparable values. Everything here is deterministic and tested.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- numbers and units
_NUMBER = re.compile(r"[-+]?\d[\d.,\s]*")


def parse_number(text: str | None) -> float | None:
    """'23,5' → 23.5, '1.234,5' → 1234.5, '1,234.5' → 1234.5, '40.000' → 40000, '14.5' → 14.5."""
    if not text:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    raw = match.group(0).strip().replace(" ", "").rstrip(".,")
    if "," in raw and "." in raw:  # the later separator is the decimal one
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        raw = raw.replace(thousands, "").replace(decimal, ".")
    elif "," in raw:  # one comma: decimal comma (EU); several: thousands
        raw = raw.replace(",", "") if raw.count(",") > 1 else raw.replace(",", ".")
    elif raw.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", raw):  # 40.000 / 1.234.567: thousands
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


_LENGTH_CM = {"mm": 0.1, "cm": 1.0, "m": 100.0, "in": 2.54, "inch": 2.54, "inches": 2.54, '"': 2.54,
              "ft": 30.48, "'": 30.48}  # fmt: skip
_MASS_KG = {
    "g": 0.001,
    "kg": 1.0,
    "lb": 0.45359237,
    "lbs": 0.45359237,
    "pound": 0.45359237,
    "pounds": 0.45359237,
}


def _unit(unit: str | None) -> str | None:
    return unit.strip().lower().rstrip(".") if unit else None


def to_cm(value: float | None, unit: str | None) -> float | None:
    factor = _LENGTH_CM.get(_unit(unit) or "cm")
    return None if value is None or factor is None else round(value * factor, 1)


def to_kg(value: float | None, unit: str | None) -> float | None:
    factor = _MASS_KG.get(_unit(unit) or "kg")
    return None if value is None or factor is None else round(value * factor, 2)


def is_metric_length(unit: str | None) -> bool:
    return (_unit(unit) or "cm") in ("mm", "cm", "m")


_DIMS = re.compile(
    r"(\d[\d.,]*)\s*[x×]\s*(\d[\d.,]*)(?:\s*[x×]\s*(\d[\d.,]*))?\s*(mm|cm|m|inches|inch|in|\")?", re.I
)


def parse_dims(text: str | None) -> tuple[list[float], str | None] | None:
    """'88 x 83 x 90 cm' → ([88, 83, 90], 'cm'); '160 x 230 cm' → ([160, 230], 'cm')."""
    match = _DIMS.search(text or "")
    if not match:
        return None
    values = [parse_number(g) for g in match.groups()[:3] if g]
    return [v for v in values if v is not None], match.group(4)


# --------------------------------------------------------------------------- EAN / GTIN
def gtin_valid(code: str | None) -> bool:
    """GS1 check digit for EAN-8, UPC-A (12), EAN-13 and GTIN-14 (digits only, nothing else)."""
    code = (code or "").strip()
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


# --------------------------------------------------------------------------- words → categories
def _fold(text: str) -> str:
    """Lower case without accents: 'Moosgrün' → 'moosgrun', 'Faia maciça' → 'faia macica'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def _table(spec: dict[str, str]) -> list[tuple[re.Pattern[str], str]]:
    return [(re.compile(r"(?:" + words + r")"), category) for category, words in spec.items()]


# Colour words in EN/ES/PT/IT/DE/FR, accent-free; the earliest match in the text wins
_COLOURS = _table({
    "green": "green|verde|musgo|moss|sage|salbei|salvia|emerald|smeraldo|olive|oliva|grun|vert|khaki|forest|bosco",
    "grey": "grey|gray|grigio|cinza|gris|grau|antracite|anthrazit|anthracite|charcoal|graphite|grafite|graphit|smoke|slate|ardesia|pewter|taupe",
    "beige": "beige|bege|areia|sand|sabbia|arena|natural|ecru|linen|leinen|oatmeal|flax|fawn|cream|crema|creme|champagne",
    "blue": "blue|blu|azul|blau|bleu|navy|marine|marinho|denim|jeans|indigo|teal|petrol|ottanio|turquoise|turchese|aqua|celeste|cobalt",
    "brown": "brown|marrone|marron|castanho|braun|brun|cognac|caramel|chocolate|cioccolato|walnut|nogueira|nussbaum|chestnut|tabacco|tabaco|camel|mocha|saddle",
    "black": "black|nero|preto|schwarz|noir|negro|ebony|ebano|ebenholz",
    "white": "white|bianco|branco|weiss|blanc|blanco|snow|chalk|ivory|avorio|marfim|elfenbein",
    "pink": "pink|rosa|rose|cipria|blush|salmon|salmao|salmone|fuchsia",
    "red": "red|rosso|vermelho|rot|rouge|rojo|bordeaux|bordo|burgundy|wine|cherry|ciliegia|brick|mattone",
    "orange": "orange|arancio|laranja|naranja|terracot|terrakotta|rust|ruggine|coral",
    "yellow": "yellow|giallo|amarelo|gelb|jaune|amarillo|mustard|senape|mostarda|senf|ochre|ocra|ocre",
    "purple": "purple|viola|roxo|lila|violet|lilac|lilla|lilas|flieder|aubergine|plum|prugna",
    "gold": "gold|oro|dourado|dore|brass|ottone|latao|messing|bronze|bronzo",
    "silver": "silver|argento|prateado|silber|argent|chrome|cromo|cromado|chrom|nickel|nichel",
    "multicolour": "multicolou?r|multicolore|multicor|bunt|patterned|gemustert|fantasia|estampado",
})  # fmt: skip
# Neighbouring families a person would not call a contradiction (cream vs white, charcoal vs black …)
_COMPATIBLE = {frozenset(p) for p in (("beige", "white"), ("grey", "silver"), ("grey", "black"), ("brown", "orange"),
                                       ("gold", "yellow"), ("pink", "red"), ("beige", "brown"),
                                       ("blue", "grey"))}  # dark navy vs charcoal  # fmt: skip


def _first(table: list[tuple[re.Pattern[str], str]], text: str | None) -> str | None:
    folded = _fold(text or "")
    hits = [(m.start(), category) for pattern, category in table if (m := pattern.search(folded))]
    return min(hits)[1] if hits else None


def colour_family(name: str | None) -> str | None:
    """'Antracite' → 'grey', 'Verde salvia' → 'green', 'Moosgrün' → 'green'."""
    return _first(_COLOURS, name)


def colours_compatible(a: str | None, b: str | None) -> bool:
    """True if equal, adjacent (beige/white, grey/black …) or either side unknown/multicolour."""
    if not a or not b or a == b or "multicolour" in (a, b):
        return True
    return frozenset((a, b)) in _COMPATIBLE


_MATERIALS = _table({
    "wood": "wood|madeira|legno|holz|bois|madera|oak|carvalho|rovere|eiche|chene|roble|beech|faia|faggio|buche|hetre|haya|walnut|noce|nussbaum|pine|pinho|pino|kiefer|teak|frassino|esche|acacia|bamboo|birch|betulla|birke",
    "metal": "metal|steel|aco|acciaio|stahl|acier|acero|iron|ferro|eisen|chrom|cromad|cromat|alumin|ottone|latao|messing|brass",
    "glass": r"glass|vidro|vetro|\bglas\b|verre|vidrio|crystal|cristal",  # not "glasierte" (glazed)
    "ceramic": "ceramic|ceramica|keramik|porcelain|porcellana|porcelana|porzellan|stoneware|gres",
    "plastic": "plastic|plastica|plastico|kunststoff|resin|resina|kunstharz|polypropylen|acrylic|acrilico",
    "stone": "marble|marmo|marmore|marmor|marbre|granite|granito|travertin|concrete|cemento|beton",
    "fabric": "fabric|tessuto|tecido|stoff|suede|velvet|velluto|veludo|samt",
})  # fmt: skip


def material_category(text: str | None) -> str | None:
    """The first material named in a legs/base description: 'Faia maciça, acabamento nogueira' → 'wood'."""
    return _first(_MATERIALS, text)


_VARIANT_GROUP = re.compile(r"([^()·;]+?)\s*\(([A-Z0-9 ,/-]+)\)")


def material_by_variant(text: str | None, variant_codes: list[str]) -> dict[str, str | None]:
    """Per-variant materials when the sheet splits them, e.g. 'Faia maciça (MUS, ARE) · Aço cromado (ANT)';
    otherwise the same material for every variant. Keys are the full variant codes."""
    result = {code: material_category(text) for code in variant_codes}
    for chunk, suffixes in _VARIANT_GROUP.findall(text or ""):
        category = material_category(chunk)
        for suffix in re.split(r"[ ,/]+", suffixes.strip()):
            for code in variant_codes:
                if suffix and code.endswith(suffix):
                    result[code] = category
    return result


_CARE = [  # order matters: the more specific phrases first
    ("professional", r"do not wash|nao lavar|non lavare|nicht waschen|ne pas laver|no lavar|professional|profissional|professionale|professionelle"),
    ("dry_clean", r"dry.?clean|limpeza a seco|lavaggio a secco|chemische reinigung|nettoyage a sec|limpieza en seco"),
    ("wash", r"wash|lavar|lavare|lavabile|lavavel|waschbar|waschen|lavable|machine|maquina|lavatrice|maschine"),
    ("wipe", r"wipe|damp cloth|pano humido|panno umido|feuchtem tuch|pano seco|panno asciutto|trockenem tuch|dry cloth"),
]  # fmt: skip


def care_category(text: str | None) -> str | None:
    """'Apenas limpeza a seco / dry clean only' → 'dry_clean'; 'Capa removível, lavar a 30 °C' → 'wash'."""
    folded = _fold(text or "")
    for category, pattern in _CARE:
        if re.search(pattern, folded):
            return category
    return None


def has_fibre_composition(text: str | None) -> bool:
    """A textile description states its fibres with percentages: 'Bouclé, 100% poliéster' → True."""
    return bool(re.search(r"\d{1,3}\s?%\s*\w", text or ""))
