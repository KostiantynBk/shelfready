"""A supplier submission after ingest: the email, its files, rendered PDF pages and their words."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in 0-1 of the page or image, origin top-left


class Word(BaseModel):
    text: str
    bbox: BBox


class Page(BaseModel):
    number: int = Field(description="1-based")
    width_pt: float
    height_pt: float
    image: str = Field(description="rendered PNG on disk")
    image_digest: str = Field(description="stable identity for the model cache: PDF hash, page and scale")
    words: list[Word]


class IngestedFile(BaseModel):
    name: str = Field(description="file name as the supplier sent it")
    kind: Literal["document", "image"]
    path: str | None = Field(description="local file; None when the photo has not been downloaded")
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    low_res: bool = Field(default=False, description="longest side under LOW_RES_PX (rule for D9)")
    pages: list[Page] = []


class Email(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sender: str = Field(alias="from")
    to: str
    date: str
    subject: str
    body: str
    attachments: list[str]


class IngestedSubmission(BaseModel):
    submission_id: str
    supplier: str
    email: Email
    files: list[IngestedFile]
