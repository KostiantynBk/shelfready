"""Ingest on the committed LISBOA submission: PDF pages, words with boxes, image checks. No network."""

from pathlib import Path

import pytest
from PIL import Image

from app.clients.llm import user_message
from app.config import Settings
from app.pipeline.ingest import LOW_RES_PX, ingest_folder, locate, locate_in, page_image

LISBOA = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "SYN-00-LISBOA"


@pytest.fixture
def lisboa(tmp_path):
    # An empty photo folder: tests must not depend on the ~600 MB ABO download
    settings = Settings(_env_file=None, data_dir=tmp_path / "runtime", abo_images_dir=tmp_path / "no-abo")
    return ingest_folder(LISBOA, settings)


def by_name(submission, name):
    return next(f for f in submission.files if f.name == name)


def test_reads_the_email_and_lists_every_attachment(lisboa):
    assert lisboa.submission_id == "SYN-00"
    assert lisboa.supplier == "Móveis Lusa, Lda."
    assert lisboa.email.sender == "comercial@moveislusa.example"
    assert [f.name for f in lisboa.files] == lisboa.email.attachments


def test_spec_pdf_is_rendered_with_words_and_boxes(lisboa):
    spec = by_name(lisboa, "LIS01_ficha_tecnica_v2.pdf")
    assert spec.kind == "document" and len(spec.pages) == 1
    page = spec.pages[0]
    assert Image.open(page.image).size == (1190, 1684)  # A4 at 144 dpi
    assert page.image_digest == f"pdf:{spec.sha256}:p1@2x"
    assert "LISBOA" in [w.text for w in page.words]
    assert all(0 <= v <= 1 for w in page.words for v in w.bbox)


def test_images_are_typed_by_extension_not_by_the_suppliers_labels(lisboa):
    label = by_name(lisboa, "LIS01_label.png")
    assert label.kind == "image" and (label.width, label.height) == (900, 1100) and not label.low_res
    small = by_name(lisboa, "LIS01_ARE_back_small.jpg")
    assert max(small.width, small.height) < LOW_RES_PX and small.low_res  # D9 is a rule, not a model call


def test_photos_not_downloaded_are_listed_without_a_path(lisboa):
    photo = by_name(lisboa, "LIS01_MUS_front.jpg")
    assert photo.kind == "image" and photo.path is None and photo.sha256  # checksum kept for later download


def test_locate_finds_values_across_punctuation(lisboa):
    spec = by_name(lisboa, "LIS01_ficha_tecnica_v2.pdf")
    words = spec.pages[0].words
    x0, y0, x1, y1 = locate(words, "500 mm")  # D2: the seat depth in mm
    assert 0 < x0 < x1 < 1 and 0 < y0 < y1 < 1
    assert locate(words, "dry clean only") is not None  # D5: spec care text
    assert locate(words, "Portugal · HS 9401.61") is not None  # "·" is not a word
    assert locate(words, "machine washable") is None
    assert locate_in(spec, "5601234000024")[0] == 1  # D4: the ARE EAN, page 1


def test_rendered_pages_are_reused_and_keyed_by_the_pdf(lisboa, tmp_path):
    page = by_name(lisboa, "LIS01_ficha_tecnica_v2.pdf").pages[0]
    rendered_at = Path(page.image).stat().st_mtime_ns
    settings = Settings(_env_file=None, data_dir=tmp_path / "runtime", abo_images_dir=tmp_path / "no-abo")
    again = by_name(ingest_folder(LISBOA, settings), "LIS01_ficha_tecnica_v2.pdf").pages[0]
    assert Path(again.image).stat().st_mtime_ns == rendered_at
    part = user_message("Read the spec.", [page_image(page)])["content"][0]
    assert part["_digest"] == f"{page.image_digest}@1536"  # replay key follows the PDF, not PNG bytes
