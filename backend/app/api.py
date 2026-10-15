# ruff: noqa: E501  (checklist texts)
"""HTTP API for the review app (technical_plan §5): inbox, product review, decisions, copy, publish gate,
catalogue audit, insights, and the supplier files (PDF pages rendered as PNG)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image

from app.clients.llm import LLMClient
from app.config import get_settings
from app.pipeline.checks import product_category
from app.pipeline.ingest import render_pdf
from app.pipeline.listing import model_name
from app.pipeline.run_copy import write_copy
from app.schemas.review import Decision, Issue
from app.store import Store

router = APIRouter()
ABO_URL = "https://amazon-berkeley-objects.s3.amazonaws.com/images/{size}/{path}"
BLOCKS = ("blocking", "review")  # info findings never block publishing
_WRITING: dict[str, asyncio.Task[None]] = {}  # product id → copy being written in the background
# Compliance checklist: the item is met when no finding of these rules is still open (runbook §4 ComplianceItem)
CHECKLIST = [
    ("gpsr_a", "Manufacturer name, postal and electronic address", "GPSR Art. 19(a)", {"D12"}),
    ("gpsr_b", "EU responsible person for non-EU manufacturers", "GPSR Art. 19(b)", {"D13"}),
    ("gpsr_d", "Safety warnings in every shop language", "GPSR Art. 19(d)", {"D7"}),
    ("fibre", "Fibre composition with percentages", "Textile Regulation (EU) 1007/2011", {"D14"}),
    (
        "claims",
        "Health, safety and eco claims checked by a person",
        "Unfair Commercial Practices Directive",
        {"D15"},
    ),
    ("ean", "Valid EAN for every variant", "Marketplace requirement", {"D3", "D4"}),
]


@lru_cache
def get_store() -> Store:
    return Store(get_settings())


StoreDep = Annotated[Store, Depends(get_store)]


def _product(store: Store, pid: str) -> tuple[Path, dict[str, Any]]:
    if pid not in store.submissions:
        raise HTTPException(404, f"unknown product {pid}")
    return store.submissions[pid]


def _all_issues(store: Store, pid: str, copy: dict[str, Any] | None) -> list[Issue]:
    """The checks' findings plus the translation checks of the copy in use."""
    issues = store.issues(pid)
    for t in (copy or {}).get("translations", {}).values():
        issues += [Issue.model_validate(i) for i in t.get("issues", [])]
    return issues


def _status(issues: list[Issue], decisions: dict[str, Decision], published: str | None) -> str:
    if published:
        return "published"
    if any(i.severity in BLOCKS and i.id not in decisions for i in issues):
        return "needs_review"
    if any(d.action == "ask_supplier" for d in decisions.values()):
        return "waiting_supplier"
    return "ready"


def _open(issues: list[Issue], decisions: dict[str, Decision]) -> list[Issue]:
    return [i for i in issues if i.id not in decisions or decisions[i.id].action == "ask_supplier"]


def _checklist(
    issues: list[Issue], decisions: dict[str, Decision], copy: dict[str, Any] | None
) -> list[dict[str, Any]]:
    still_open = {i.rule for i in _open(issues, decisions)}
    items = [
        {"code": code, "label": label, "law": law, "ok": not (rules & still_open)}
        for code, label, law, rules in CHECKLIST
    ]
    photos = {p["file"] for p in (copy or {}).get("photos", [])}
    described = {a["file"] for a in (copy or {}).get("en", {}).get("alt_texts", [])}
    items.append({"code": "alt_text", "label": "Alt text for every shop photo", "law": "European Accessibility Act",
                  "ok": bool(copy) and photos <= described})  # fmt: skip
    return items


def _files(
    store: Store, pid: str, folder: Path, manifest: dict[str, Any], result: dict[str, Any] | None
) -> list[dict[str, Any]]:
    images = (result or {}).get("images", {})
    files = []
    for a in manifest["attachments"]:
        entry: dict[str, Any] = {"name": a["file"], "kind": a["kind"], "url": f"/files/{pid}/{a['file']}"}
        if a["file"] in images:
            entry["analysis"] = images[a["file"]].get("analysis")
            entry["size"] = images[a["file"]].get("size")
            entry["low_res"] = images[a["file"]].get("low_res")
            entry["reading"] = images[a["file"]].get("drawing") or images[a["file"]].get("label")
        if a["kind"] == "spec":
            entry["pages"] = [{"number": p.number, "url": f"/files/{pid}/{a['file']}/pages/{p.number}",
                               "width_pt": p.width_pt, "height_pt": p.height_pt} for p in _pages(folder / a["path"], str(store.settings.data_dir))]  # fmt: skip
        else:
            entry["thumb_url"] = f"/files/{pid}/{a['file']}?size=thumb"
        files.append(entry)
    return files


_RENDER = threading.Lock()  # two first requests must not write the same page PNG at once


@lru_cache(maxsize=64)
def _pages(path: Path, data_dir: str) -> list[Any]:
    """The document's pages rendered to PNG (cached by file hash under DATA_DIR/pages)."""
    with _RENDER:
        return render_pdf(path, hashlib.sha256(path.read_bytes()).hexdigest(), Path(data_dir) / "pages")


def product_payload(store: Store, pid: str) -> dict[str, Any]:
    folder, manifest = _product(store, pid)
    result = store.result(pid)
    spec = (result or {}).get("spec") or {}
    decisions, decided_at = store.decisions(pid), store.decided_at(pid)
    facts, held = store.facts(pid)
    copy = store.copy_for(pid, facts) if facts else None
    issues = _all_issues(store, pid, copy)
    published = store.published_at(pid)
    return {
        "id": pid,
        "model": model_name(spec) or pid,
        "product_name": spec.get("product_name"),
        "supplier": manifest["supplier"],
        "category": product_category(spec) if spec else None,
        "email": manifest["email"],
        "status": _status(issues, decisions, published),
        "published_at": published,
        "files": _files(store, pid, folder, manifest, result),
        "spec": spec,
        "spec_evidence": (result or {}).get("spec_evidence", {}),
        "issues": [
            {
                **i.model_dump(),
                "id": i.id,
                "decision": decisions[i.id].model_dump() if i.id in decisions else None,
                "decided_at": decided_at.get(i.id),
            }
            for i in issues
        ],  # fmt: skip
        "facts": facts,
        "held_back": held,
        "copy": copy and {k: copy[k] for k in ("en", "translations", "photos", "protected", "model")},
        "copy_state": "ready"
        if copy
        else "writing"
        if pid in _WRITING
        else "needs_model"
        if facts
        else "no_spec",
        "checklist": _checklist(issues, decisions, copy),
        "events": store.events(pid),
        "model_used": (result or {}).get("model"),
    }


def summary(store: Store, pid: str) -> dict[str, Any]:
    _, manifest = _product(store, pid)
    spec = (store.result(pid) or {}).get("spec") or {}
    decisions = store.decisions(pid)
    facts, _ = store.facts(pid)
    issues = _all_issues(store, pid, store.copy_for(pid, facts) if facts else None)
    still_open = [i for i in issues if i.id not in decisions]
    first_photo = next((a["file"] for a in manifest["attachments"] if a["kind"] == "photo"), None)
    thumb = f"/files/{pid}/{first_photo}?size=thumb" if first_photo else None
    return {"id": pid, "model": model_name(spec) or pid, "product_name": spec.get("product_name"),
            "supplier": manifest["supplier"], "received": manifest["email"]["date"], "subject": manifest["email"]["subject"],
            "category": product_category(spec) if spec else None,
            "status": _status(issues, decisions, store.published_at(pid)),
            "open": {s: sum(1 for i in still_open if i.severity == s) for s in ("blocking", "review", "info")},
            "thumb_url": thumb}  # fmt: skip


# ---------------------------------------------------------------------------------------------- routes
@router.get("/api/submissions")
def submissions(store: StoreDep) -> list[dict[str, Any]]:
    return [summary(store, pid) for pid in store.submissions]


@router.get("/api/products/{pid}")
def product(pid: str, store: StoreDep) -> dict[str, Any]:
    return product_payload(store, pid)


@router.post("/api/products/{pid}/issues/{issue_id}/decision")
def decide(pid: str, issue_id: str, decision: Decision, store: StoreDep) -> dict[str, Any]:
    _product(store, pid)
    if store.published_at(pid):
        raise HTTPException(409, "already published")
    store.decide(pid, issue_id, decision)
    return product_payload(store, pid)


@router.delete("/api/products/{pid}/issues/{issue_id}/decision")
def reopen(pid: str, issue_id: str, store: StoreDep) -> dict[str, Any]:
    _product(store, pid)
    store.undo(pid, issue_id)
    return product_payload(store, pid)


@router.post("/api/products/{pid}/copy")
async def write_product_copy(pid: str, store: StoreDep) -> dict[str, Any]:
    """Start writing the copy for the current decisions (needs the model; takes minutes, so it runs in the
    background and the payload says copy_state "writing" until it is saved)."""
    _product(store, pid)
    facts, _ = store.facts(pid)
    if store.copy_for(pid, facts) or pid in _WRITING:
        return product_payload(store, pid)
    if store.settings.llm_mode == "replay":
        raise HTTPException(
            409, "The model is not connected (replay mode); copy exists only for the recorded decisions."
        )
    result, decisions = store.result(pid), store.decisions(pid)

    async def job() -> None:
        try:
            async with LLMClient(store.settings) as llm:
                copy = await write_copy(llm, result, decisions)
            store.save_copy(pid, copy)
            store.log(pid, "copy", f"written by {copy.get('model')}")
        except Exception as error:  # shown on the review screen; the person can try again
            text = str(error)
            if "no_available_workers" in text or "503" in text or "Timeout" in type(error).__name__:
                text = "The model service (AMD Token Factory) is not answering right now. Try again in a few minutes."
            store.log(pid, "copy_failed", text[:300])
        finally:
            _WRITING.pop(pid, None)

    _WRITING[pid] = asyncio.create_task(job())
    store.log(pid, "copy_started", "")
    return product_payload(store, pid)


@router.post("/api/products/{pid}/publish")
def publish(pid: str, store: StoreDep) -> dict[str, Any]:
    payload = product_payload(store, pid)
    open_ = [i for i in payload["issues"] if not i["decision"] and i["severity"] in BLOCKS]
    asked = [i for i in payload["issues"] if (i["decision"] or {}).get("action") == "ask_supplier"]
    reasons = []
    if open_:
        reasons.append(
            f"{len(open_)} {'finding still needs' if len(open_) == 1 else 'findings still need'} your decision."
        )
    if asked:
        reasons.append(f"{len(asked)} {'question is' if len(asked) == 1 else 'questions are'} still open with the supplier: "
                       + "; ".join(i["title"] for i in asked) + ".")  # fmt: skip
    if payload["copy_state"] != "ready":
        reasons.append(
            "The shop copy has not been written for your latest decisions yet: click “Write copy”."
        )
    reasons += [f"Not met: {c['label']} ({c['law']})." for c in payload["checklist"]
                if not c["ok"] and not (c["code"] == "alt_text" and payload["copy_state"] != "ready")]  # fmt: skip
    if reasons:
        raise HTTPException(409, {"message": "Publishing is blocked", "reasons": reasons})
    store.publish(pid)
    return product_payload(store, pid)


@router.get("/api/audit")
def audit(store: StoreDep) -> dict[str, Any]:
    report = store.json_result("audit.json")
    if report is None:
        raise HTTPException(404, "no audit yet (run python -m app.pipeline.audit)")
    items = {
        x["item_id"]: x
        for x in map(
            json.loads,
            (store.synthetic.parent / "abo" / "items.jsonl").read_text(encoding="utf-8").splitlines(),
        )
        if x
    }
    for listing in report["listings"]:
        images = items[listing["item_id"]]["images"]
        listing["images"] = [{"image_id": i["image_id"], "role": i["role"], "thumb_url": ABO_URL.format(size="small", path=i["path"]),
                              "url": ABO_URL.format(size="original", path=i["path"])} for i in images]  # fmt: skip
    return report


@router.get("/api/insights")
def insights(store: StoreDep) -> dict[str, Any]:
    calls = Path(store.settings.data_dir) / "llm_calls.jsonl"
    rows = (
        [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines() if line]
        if calls.exists()
        else []
    )
    live = [r for r in rows if r.get("source") == "live" and r.get("latency_s") is not None]
    latencies = sorted(r["latency_s"] for r in live)
    return {
        "eval": store.json_result("eval.json"),
        "audit": (store.json_result("audit.json") or {}).get("summary"),
        "spotcheck": store.json_result("../eval/spotcheck_score.json"),
        "model_calls": {
            "live": len(live),
            "median_latency_s": latencies[len(latencies) // 2] if latencies else None,
            "tokens_in": sum(r.get("prompt_tokens") or 0 for r in live),
            "tokens_out": sum(r.get("completion_tokens") or 0 for r in live),
        },  # fmt: skip
    }


@router.post("/api/demo/reset")
def reset(store: StoreDep) -> dict[str, str]:
    store.reset()
    return {"status": "reset"}


@router.get("/files/{pid}/{name}")
def file(pid: str, name: str, store: StoreDep, size: str | None = None):
    """A supplier file; size=thumb gives a small JPEG (made from the local file, cached under DATA_DIR/thumbs)."""
    folder, manifest = _product(store, pid)
    attachment = next((a for a in manifest["attachments"] if a["file"] == name), None)
    if attachment is None:
        raise HTTPException(404, f"no file {name}")
    local = (
        folder / attachment["path"]
        if attachment.get("path")
        else store.settings.abo_images_dir / attachment["abo_path"]
    )
    if not local.exists():
        if not attachment.get("abo_path"):
            raise HTTPException(404, f"{name} is not available")
        # public ABO copy (CC BY 4.0) when the photos were not downloaded
        return RedirectResponse(
            ABO_URL.format(size="small" if size == "thumb" else "original", path=attachment["abo_path"])
        )
    if size == "thumb" and local.suffix.lower() != ".pdf":
        return FileResponse(_thumbnail(local, str(store.settings.data_dir)))
    return FileResponse(local)


def _thumbnail(path: Path, data_dir: str, side: int = 240) -> Path:
    target = Path(data_dir) / "thumbs" / f"{hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:20]}.jpg"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with _RENDER, Image.open(path) as image:
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, "white")
            flat.paste(image, mask=image.getchannel("A"))
            flat.thumbnail((side, side))
            flat.save(target, "JPEG", quality=85)
    return target


@router.get("/files/{pid}/{name}/pages/{number}")
def page(pid: str, name: str, number: int, store: StoreDep) -> FileResponse:
    folder, manifest = _product(store, pid)
    attachment = next((a for a in manifest["attachments"] if a["file"] == name and a.get("path")), None)
    if attachment is None:
        raise HTTPException(404, f"no document {name}")
    pages = _pages(folder / attachment["path"], str(store.settings.data_dir))
    if not 1 <= number <= len(pages):
        raise HTTPException(404, f"no page {number}")
    return FileResponse(pages[number - 1].image)
