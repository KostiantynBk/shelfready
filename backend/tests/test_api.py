"""The review API end to end on LISBOA: inbox, decisions, publish gate, files, reset (no model, temp data)."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import get_store
from app.config import Settings
from app.main import app
from app.store import Store

REPO = Path(__file__).resolve().parents[2]
PLANNED = json.loads((REPO / "data" / "demo" / "decisions.json").read_text(encoding="utf-8"))["SYN-00"]


@pytest.fixture
def client(tmp_path):
    shutil.copytree(
        REPO / "data" / "synthetic" / "SYN-00-LISBOA", tmp_path / "data" / "synthetic" / "SYN-00-LISBOA"
    )
    extraction = tmp_path / "data" / "results" / "extraction"
    extraction.mkdir(parents=True)
    shutil.copy(Path(__file__).parent / "fixtures" / "extraction" / "SYN-00.json", extraction)
    store = Store(Settings(data_dir=tmp_path / "runtime"), root=tmp_path)
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app), store, tmp_path
    app.dependency_overrides.clear()


def decide(http, issue_id, decision):
    response = http.post(f"/api/products/SYN-00/issues/{issue_id}/decision", json=decision)
    assert response.status_code == 200, response.text
    return response.json()


def test_review_decide_and_publish(client):
    http, store, root = client
    inbox = http.get("/api/submissions").json()
    assert inbox[0]["id"] == "SYN-00" and inbox[0]["status"] == "needs_review"
    assert inbox[0]["open"] == {"blocking": 3, "review": 3, "info": 3}
    product = http.get("/api/products/SYN-00").json()
    assert len(product["issues"]) == 9 and product["copy_state"] == "needs_model"
    assert {h["attribute"] for h in product["held_back"]} == {
        "seat_height",
        "care",
        "legs_or_base",
        "safety_warnings",
    }

    for issue_id, decision in PLANNED.items():
        product = decide(http, issue_id, decision)
    assert (
        product["status"] == "waiting_supplier" and product["held_back"] == []
    )  # EANs asked, nothing else open
    blocked = http.post("/api/products/SYN-00/publish")
    reasons = blocked.json()["detail"]["reasons"]
    assert blocked.status_code == 409 and any(
        "2 questions are still open with the supplier" in r for r in reasons
    )

    decide(http, "D3:LIS-01-ANT:ean:", {"action": "edit", "value": "5601234000030"})  # the supplier answered
    product = decide(http, "D4:LIS-01-ARE:ean:", {"action": "edit", "value": "5601234000023"})
    assert product["status"] == "ready" and product["copy_state"] == "needs_model"

    facts, _ = store.facts("SYN-00")  # a recorded copy for exactly these facts makes it publishable
    (root / "data" / "results" / "copy").mkdir()
    copy = {"product_id": "SYN-00", "facts": facts, "en": {"alt_texts": []}, "translations": {}, "photos": [],
            "protected": [], "model": "test"}  # fmt: skip
    (root / "data" / "results" / "copy" / "SYN-00.reviewed.json").write_text(
        json.dumps(copy), encoding="utf-8"
    )
    assert all(c["ok"] for c in http.get("/api/products/SYN-00").json()["checklist"])
    published = http.post("/api/products/SYN-00/publish")
    assert published.status_code == 200 and published.json()["status"] == "published"
    assert published.json()["events"][-1]["kind"] == "published"

    http.post("/api/demo/reset")
    assert http.get("/api/submissions").json()[0]["status"] == "needs_review"


def test_supplier_files_and_pdf_pages(client):
    http, _, _ = client
    assert http.get("/files/SYN-00/LIS01_label.png").headers["content-type"] == "image/png"
    page = http.get("/files/SYN-00/LIS01_ficha_tecnica_v2.pdf/pages/1")
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"
    assert http.get("/api/products/NOPE").status_code == 404
