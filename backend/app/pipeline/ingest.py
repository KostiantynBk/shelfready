"""Ingest: turn a supplier submission (email + attachments) into files the pipeline can read.

PDFs are rendered page by page (PNG for the vision model) and their words are kept with boxes, so a value
the model reads can be highlighted on the page as evidence. Images get their size and a low-resolution
flag (a rule, no model). The supplier's own labels for attachments are not trusted: a file is either a
document or an image, and the photo model decides what an image shows (packshot, care label, drawing…).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from app.clients.llm import ImageRef
from app.config import Settings, get_settings
from app.schemas.documents import BBox, Email, IngestedFile, IngestedSubmission, Page, Word

RENDER_SCALE = 2  # 144 dpi: an A4 page becomes 1190 x 1684 px, enough to read 9 pt text
LOW_RES_PX = 800  # longest side; marketplaces ask for zoomable images of 1000 px and more
DOCUMENT_SUFFIXES = {".pdf"}


def ingest_folder(folder: Path, settings: Settings | None = None) -> IngestedSubmission:
    """A submission folder (data/synthetic/SYN-*): submission.json, generated files, ABO photo references."""
    settings = settings or get_settings()
    manifest = json.loads((folder / "submission.json").read_text(encoding="utf-8"))
    files = []
    for attachment in manifest["attachments"]:
        if "path" in attachment:
            path = folder / attachment["path"]
        else:
            path = settings.abo_images_dir / attachment["abo_path"]
        found = path if path.exists() else None
        files.append(
            ingest_file(attachment["file"], found, settings, expected_sha256=attachment.get("sha256"))
        )
    return IngestedSubmission(
        submission_id=manifest["product_id"],
        supplier=manifest["supplier"],
        email=Email.model_validate(manifest["email"]),
        files=files,
    )


def ingest_file(
    name: str, path: Path | None, settings: Settings | None = None, *, expected_sha256: str | None = None
) -> IngestedFile:
    settings = settings or get_settings()
    kind = "document" if Path(name).suffix.lower() in DOCUMENT_SUFFIXES else "image"
    if path is None:  # e.g. ABO photos not downloaded: the file is listed, analysis needs it later
        return IngestedFile(name=name, kind=kind, path=None, sha256=expected_sha256)
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if expected_sha256 and sha256 != expected_sha256:
        raise ValueError(f"{name}: checksum mismatch ({path})")
    if kind == "document":
        pages = render_pdf(path, sha256, Path(settings.data_dir) / "pages")
        return IngestedFile(name=name, kind=kind, path=str(path), sha256=sha256, pages=pages)
    with Image.open(path) as image:
        width, height = image.size
    return IngestedFile(name=name, kind=kind, path=str(path), sha256=sha256, width=width, height=height,
                        low_res=max(width, height) < LOW_RES_PX)  # fmt: skip


def render_pdf(path: Path, sha256: str, cache_dir: Path) -> list[Page]:
    """Every page as a PNG (cached by PDF hash) plus its words with boxes in 0-1 page coordinates."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    document = pdfium.PdfDocument(str(path))
    try:
        with pdfplumber.open(path) as plumber:
            for n, text_page in enumerate(plumber.pages, start=1):
                image = cache_dir / f"{sha256[:16]}-p{n}@{RENDER_SCALE}x.png"
                if not image.exists():
                    document[n - 1].render(scale=RENDER_SCALE).to_pil().save(image)
                w, h = float(text_page.width), float(text_page.height)
                words = [_word(word, w, h) for word in text_page.extract_words(use_text_flow=True)]
                digest = f"pdf:{sha256}:p{n}@{RENDER_SCALE}x"
                pages.append(
                    Page(
                        number=n, width_pt=w, height_pt=h, image=str(image), image_digest=digest, words=words
                    )
                )
    finally:
        document.close()
    return pages


def _word(word: dict, width: float, height: float) -> Word:
    """A pdfplumber word (points, origin top-left) with its box in 0-1 page coordinates."""
    box = (word["x0"] / width, word["top"] / height, word["x1"] / width, word["bottom"] / height)
    return Word(text=word["text"], bbox=_clamp(box))


def page_image(page: Page) -> ImageRef:
    """The rendered page for the vision model, keyed by the PDF (not the PNG bytes) for replay."""
    return ImageRef(Path(page.image), page.image_digest)


# --------------------------------------------------------------------------- evidence boxes
_EDGE_PUNCTUATION = re.compile(r"^[^\w%°]+|[^\w%°]+$")


def _norm(token: str) -> str:
    return _EDGE_PUNCTUATION.sub("", token.casefold())


def _clamp(box: tuple[float, float, float, float]) -> BBox:
    x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in box)
    return x0, y0, x1, y1


def locate(words: list[Word], text: str) -> BBox | None:
    """Box around the first run of words that spells `text` (case and edge punctuation ignored)."""
    needle = [t for t in (_norm(part) for part in text.split()) if t]
    hay = [(i, t) for i, t in ((i, _norm(w.text)) for i, w in enumerate(words)) if t]  # skip "·", "/" …
    tokens = [t for _, t in hay]
    for start in range(len(tokens) - len(needle) + 1) if needle else ():
        if tokens[start : start + len(needle)] == needle:
            boxes = [words[i].bbox for i, _ in hay[start : start + len(needle)]]
            return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes))  # fmt: skip
    return None


def locate_in(file: IngestedFile, text: str) -> tuple[int, BBox] | None:
    """(page number, box) of the first place `text` appears in a document, or None."""
    for page in file.pages:
        if (box := locate(page.words, text)) is not None:
            return page.number, box
    return None
