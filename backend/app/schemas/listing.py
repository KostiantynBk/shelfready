"""Shop copy written from verified facts, its translations, and the code checks on each translation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ColourName(BaseModel):
    code: str = Field(description="variant code, unchanged")
    name: str = Field(description="the colour name in the listing's language")


class AltText(BaseModel):
    file: str = Field(description="photo file name, unchanged")
    text: str = Field(description="what the photo shows, for a blind shopper")


class ListingCopy(BaseModel):
    title: str = Field(description="under 80 characters")
    bullets: list[str] = Field(description="3-5 short selling points, facts only")
    description: str = Field(description="2-3 sentences")
    colours: list[ColourName] = Field(description="one entry per variant code")
    safety_warnings: list[str] = Field(description="every safety warning; empty if none")
    alt_texts: list[AltText] = Field(description="one entry per photo file listed")


class TranslationQA(BaseModel):
    """Checks in code (no model): what must survive translation unchanged."""

    numbers_ok: bool
    units_ok: bool
    protected_ok: bool  # brand, product name, codes
    glossary_ok: bool  # key material terms rendered with an accepted word
    backtranslation_ok: bool | None = None  # only for languages that get a back-translation
    problems: list[str] = []
    human_checked: bool = False

    @property
    def passed(self) -> bool:
        return all((self.numbers_ok, self.units_ok, self.protected_ok, self.glossary_ok)) and (
            self.backtranslation_ok is not False
        )
