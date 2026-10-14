# ruff: noqa: E501  (prompt text and word lists)
"""Translate the English copy and check every translation in code (runbook §5 "Translate").

The model translates; the code then checks what must not change: every number, every unit (Ukrainian
may write см/кг), the protected names (brand, model, variant codes, Martindale), and key material terms
against a small glossary. German and Ukrainian also get a back-translation into English, checked the same
way. Any failed check becomes a translation issue (T1–T5) for a person; nothing is published unchecked.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from app.clients.llm import LLMClient, LLMResult, user_message
from app.pipeline.normalise import _fold, parse_number
from app.schemas.listing import ListingCopy, TranslationQA
from app.schemas.review import Issue

LANGUAGES = {"es": "Spanish", "it": "Italian", "de": "German", "fr": "French", "pt": "European Portuguese",
             "pl": "Polish", "uk": "Ukrainian"}  # fmt: skip
BACK_TRANSLATED = ("de", "uk")  # uk: the language a person on the team can verify
UNITS = {"cm": r"cm|см", "mm": r"mm|мм", "kg": r"kg|кг", "°C": r"°\s?[cс]"}  # Latin or Cyrillic
# Key material words: the English term (as it appears in the copy) → accepted stems per language (accent-free)
GLOSSARY = {
    "boucle": {
        "es": "boucl",
        "it": "boucl",
        "de": "boucl",
        "fr": "boucl",
        "pt": "boucl",
        "pl": "boucl|bukl",
        "uk": "букл",
    },
    "velvet": {
        "es": "terciopelo",
        "it": "velluto",
        "de": "samt",
        "fr": "velours",
        "pt": "veludo",
        "pl": "aksamit|welur",
        "uk": "оксамит|велюр",
    },
    "beech": {
        "es": "haya",
        "it": "faggio",
        "de": "buche",
        "fr": "hetre",
        "pt": "faia",
        "pl": "buk",
        "uk": "бук",
    },
    "oak": {
        "es": "roble",
        "it": "rovere|quercia",
        "de": "eiche",
        "fr": "chene",
        "pt": "carvalho",
        "pl": "dab|deb",
        "uk": "дуб",
    },
    "walnut": {
        "es": "nogal",
        "it": "noce",
        "de": "nuss",
        "fr": "noyer",
        "pt": "nogueira",
        "pl": "orzech",
        "uk": "горіх|горiх",
    },
    "polyester": {
        "es": "poliester",
        "it": "poliester",
        "de": "polyester",
        "fr": "polyester",
        "pt": "poliester",
        "pl": "poliester",
        "uk": "поліестер|полиэстер",
    },
    "wool": {
        "es": "lana",
        "it": "lana",
        "de": "woll",
        "fr": "laine",
        "pt": "la",
        "pl": "weln|wel",
        "uk": "вовн",
    },
    "cotton": {
        "es": "algodon",
        "it": "cotone",
        "de": "baumwoll",
        "fr": "coton",
        "pt": "algodao",
        "pl": "baweln",
        "uk": "бавовн",
    },
    "steel": {
        "es": "acero",
        "it": "acciaio",
        "de": "stahl",
        "fr": "acier",
        "pt": "aco",
        "pl": "stal",
        "uk": "стал",
    },
}
_NUMBER = re.compile(r"\d+(?:[.,\u00a0\u202f ]\d{3})+(?![\d])|\d+(?:[.,]\d+)?")
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,\u00a0\u202f ]\d{3})+")

PROMPT = (
    "Translate this product listing JSON into {lang}. Translate all words into natural {lang}, including the product type, "
    "colours, materials and finishes (e.g. 'solid beech', 'bouclé' where a {lang} term exists), the safety warnings and "
    "the alt texts. Do NOT translate or change these protected tokens: {protected}. Keep every number exactly, with its unit "
    "({units}). Keep the JSON keys, variant codes and file names unchanged. Return JSON only.\n{listing}"
)
BACK_PROMPT = (
    "Translate this {lang} product listing JSON back into English, literally. Keep the JSON keys, variant codes and "
    "file names unchanged. Return JSON only.\n{listing}"
)


def protected_tokens(facts: dict[str, Any], copy: ListingCopy) -> list[str]:
    """Names that must survive translation unchanged, built from the data (never a fixed list)."""
    tokens = [facts.get("brand"), facts.get("model"), *(v["code"] for v in facts.get("variants", []))]
    text = _text(copy)
    tokens += [t for t in ("Martindale",) if t in text]
    return [t for t in dict.fromkeys(tokens) if t and t in text]


def _text(copy: ListingCopy) -> str:
    return json.dumps(copy.model_dump(), ensure_ascii=False)


def numbers(text: str) -> Counter[str]:
    """Every number as a value: '40,000' and '40.000' and '40 000' → 40000; '23,5' and '23.5' → 23.5."""
    found: Counter[str] = Counter()
    for match in _NUMBER.finditer(text):
        raw = match.group(0)
        value = float(re.sub(r"\D", "", raw)) if _THOUSANDS.fullmatch(raw) else parse_number(raw)
        if value is not None:
            found[f"{value:g}"] += 1
    return found


def units(text: str) -> Counter[str]:
    return Counter(
        {
            unit: len(re.findall(rf"\d\s*(?:{pattern})(?![a-zа-я])", text, re.I))
            for unit, pattern in UNITS.items()
        }
    )


def glossary_terms(english: str) -> list[str]:
    folded = _fold(english)
    return [term for term in GLOSSARY if re.search(rf"\b{term}", folded)]


def check_translation(
    en: ListingCopy, tr: ListingCopy, lang: str, protected: list[str], back: ListingCopy | None = None
) -> TranslationQA:
    source, target = _text(en), _text(tr)
    problems = []
    numbers_ok = numbers(source) == numbers(target)
    if not numbers_ok:
        missing, extra = numbers(source) - numbers(target), numbers(target) - numbers(source)
        problems.append(f"numbers changed: missing {sorted(missing)}, added {sorted(extra)}")
    units_ok = units(source) == units(target)
    if not units_ok:
        problems.append(f"units changed: {dict(units(source))} → {dict(units(target))}")
    lost = [t for t in protected if t not in target]
    if lost:
        problems.append(f"protected names changed: {', '.join(lost)}")
    folded = _fold(target)
    wrong = [
        term
        for term in glossary_terms(source)
        if lang in GLOSSARY[term] and not re.search(GLOSSARY[term][lang], folded)
    ]
    if wrong:
        problems.append(f"key terms not rendered: {', '.join(wrong)}")
    back_ok = None
    if back is not None:
        back_text = _text(back)
        back_terms = [t for t in glossary_terms(source) if not re.search(rf"\b{t}", _fold(back_text))]
        back_ok = (
            numbers(back_text) == numbers(source)
            and not back_terms
            and all(t in back_text for t in protected)
        )
        if not back_ok:
            problems.append(
                f"back-translation differs{': ' + ', '.join(back_terms) + ' missing' if back_terms else ''}"
            )
    return TranslationQA(numbers_ok=numbers_ok, units_ok=units_ok, protected_ok=not lost, glossary_ok=not wrong,
                         backtranslation_ok=back_ok, problems=problems)  # fmt: skip


def translation_issues(product_id: str, lang: str, qa: TranslationQA) -> list[Issue]:
    """One issue per failed check (catalogue T-D1…T-D4 map to T1…T4)."""
    checks = [("T1", qa.units_ok, "Units changed"), ("T2", qa.numbers_ok, "Numbers changed"),
              ("T3", qa.protected_ok, "Brand, name or code changed"), ("T4", qa.glossary_ok, "Key material term wrong"),
              ("T5", qa.backtranslation_ok is not False, "Back-translation differs")]  # fmt: skip
    detail = "; ".join(qa.problems)
    return [Issue(rule=rule, product_id=product_id, variant=None, type="translation", severity="blocking",
                  return_risk="missing_info", attribute=f"listing:{lang}", title=f"{lang.upper()}: {title}", detail=detail)
            for rule, ok, title in checks if not ok]  # fmt: skip


async def translate(
    llm: LLMClient, en: ListingCopy, lang: str, protected: list[str], *, purpose: str
) -> tuple[ListingCopy, LLMResult]:
    unit_words = "см, мм, кг, °C" if lang == "uk" else "cm, mm, kg, °C"
    prompt = PROMPT.format(lang=LANGUAGES[lang], protected=", ".join(f"'{t}'" for t in protected) or "none",
                           units=unit_words, listing=_text(en))  # fmt: skip
    return await llm.chat_json([user_message(prompt)], ListingCopy, purpose=purpose, max_tokens=3000)


async def back_translate(
    llm: LLMClient, tr: ListingCopy, lang: str, *, purpose: str
) -> tuple[ListingCopy, LLMResult]:
    prompt = BACK_PROMPT.format(lang=LANGUAGES[lang], listing=_text(tr))
    return await llm.chat_json([user_message(prompt)], ListingCopy, purpose=purpose, max_tokens=3000)
