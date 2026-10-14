"""What the review screen shows: issues found by the checks, each with its evidence (runbook §4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.documents import Evidence

Severity = Literal["blocking", "review", "info"]
ReturnRisk = Literal["dimensions", "colour", "material", "care", "missing_info", "none"]


class Issue(BaseModel):
    rule: str  # D1…D17, the defect catalogue's id (data/defects.yaml)
    product_id: str
    variant: str | None = None  # variant code, or None for the whole product
    type: Literal["missing", "contradictory", "compliance", "quality"]
    severity: Severity
    return_risk: ReturnRisk
    title: str
    detail: str
    attribute: str
    spec_value: str | None = None  # what the supplier's document says
    found_value: str | None = None  # what the photo, label or drawing shows
    proposed_value: str | None = None  # a value we can fill in for a person to confirm
    file: str | None = None  # the image the issue is about (overlay, low-res, misfiled photo)
    evidence: list[Evidence] = []
    status: Literal["open", "resolved", "asked_supplier"] = "open"
