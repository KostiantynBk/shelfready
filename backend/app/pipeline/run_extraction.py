"""Run extraction over submission folders and save what the model read.

    cd backend
    LLM_MODE=cached LLM_API_KEY=... uv run python -m app.pipeline.run_extraction [--only SYN-00]

Per submission: the spec sheet → SpecExtraction + evidence boxes; every image → PhotoAnalysis; images the
model sees as a care label or a dimension drawing get a second call that reads them. Results go to
data/results/extraction/<SUBMISSION>.json. Every call goes through LLMClient (rate limit, retries,
recording), so an interrupted run resumes for free in cached mode and can be rebuilt offline in replay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.clients.llm import LLMClient
from app.pipeline.ingest import ingest_folder
from app.pipeline.photos import analyse_photo, read_drawing, read_label
from app.pipeline.spec import extract_spec, spec_evidence
from app.schemas.documents import IngestedFile

ROOT = Path(__file__).resolve().parents[3]
SUBMISSIONS = ROOT / "data" / "synthetic"
RESULTS = ROOT / "data" / "results" / "extraction"
ABO_ITEMS = ROOT / "data" / "abo" / "items.jsonl"
AUDIT_RESULTS = ROOT / "data" / "results" / "audit_photos"


async def extract_submission(llm: LLMClient, folder: Path) -> dict[str, Any]:
    submission = ingest_folder(folder, llm.settings)
    sid = submission.submission_id
    out: dict[str, Any] = {
        "submission_id": sid,
        "spec": None,
        "spec_evidence": {},
        "images": {},
        "errors": {},
    }

    async def read_spec(file: IngestedFile) -> None:
        spec, result = await extract_spec(llm, file, purpose=f"spec:{sid}")
        out["spec"] = spec.model_dump()
        out["spec_evidence"] = {key: e.model_dump() for key, e in spec_evidence(spec, file).items()}
        out["model"] = result.model

    async def read_image(file: IngestedFile) -> None:
        path = Path(file.path or "")
        seen, _ = await analyse_photo(llm, path, purpose=f"photo:{sid}:{file.name}")
        entry: dict[str, Any] = {
            "analysis": seen.model_dump(),
            "size": [file.width, file.height],
            "low_res": file.low_res,
        }
        if seen.role == "label":
            label, _ = await read_label(llm, path, purpose=f"label:{sid}:{file.name}")
            entry["label"] = label.model_dump()
        elif seen.role == "dimension_drawing":
            drawing, _ = await read_drawing(llm, path, purpose=f"drawing:{sid}:{file.name}")
            entry["drawing"] = drawing.model_dump()
        out["images"][file.name] = entry

    async def guarded(file: IngestedFile) -> None:
        try:
            await (read_spec(file) if file.kind == "document" else read_image(file))
        except Exception as error:  # one failed file must not lose the rest of the submission
            out["errors"][file.name] = f"{type(error).__name__}: {error}"[:300]

    for file in submission.files:
        if file.path is None:
            out["errors"][file.name] = "file not available (run data/scripts/fetch_abo.py)"
    await asyncio.gather(*(guarded(f) for f in submission.files if f.path is not None))
    return out


async def run(folders: list[Path], results: Path) -> list[dict[str, Any]]:
    results.mkdir(parents=True, exist_ok=True)
    async with LLMClient() as llm:

        async def one(folder: Path) -> dict[str, Any]:
            out = await extract_submission(llm, folder)
            (results / f"{out['submission_id']}.json").write_text(
                json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"{out['submission_id']}: spec {'ok' if out['spec'] else 'FAILED'}, "
                  f"{len(out['images'])} images, {len(out['errors'])} errors", flush=True)  # fmt: skip
            return out

        return await asyncio.gather(*(one(f) for f in folders))


async def analyse_listing_photos(llm: LLMClient, item: dict[str, Any]) -> dict[str, Any]:
    """Every photo of one existing catalogue listing (audit set); the audit reuses these recorded answers."""
    out: dict[str, Any] = {"item_id": item["item_id"], "photos": {}, "errors": {}}

    async def one(image: dict[str, Any]) -> None:
        path = llm.settings.abo_images_dir / image["path"]
        if not path.exists():
            out["errors"][image["image_id"]] = "file not available (run data/scripts/fetch_abo.py)"
            return
        try:
            seen, _ = await analyse_photo(
                llm, path, purpose=f"audit-photo:{item['item_id']}:{image['image_id']}"
            )
            out["photos"][image["image_id"]] = seen.model_dump()
        except Exception as error:
            out["errors"][image["image_id"]] = f"{type(error).__name__}: {error}"[:300]

    await asyncio.gather(*(one(image) for image in item["images"]))
    return out


async def run_audit_photos(items: list[dict[str, Any]], results: Path) -> list[dict[str, Any]]:
    results.mkdir(parents=True, exist_ok=True)
    async with LLMClient() as llm:

        async def one(item: dict[str, Any]) -> dict[str, Any]:
            out = await analyse_listing_photos(llm, item)
            (results / f"{item['item_id']}.json").write_text(
                json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return out

        outs = await asyncio.gather(*(one(item) for item in items))
    print(f"{len(outs)} listings, {sum(len(o['photos']) for o in outs)} photos analysed, "
          f"{sum(len(o['errors']) for o in outs)} errors; results in {results}")  # fmt: skip
    return outs


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", help="submission id prefix, e.g. SYN-00")
    parser.add_argument(
        "--audit-photos", action="store_true", help="analyse the audit listings' photos instead"
    )
    args = parser.parse_args()
    if args.audit_photos:
        items = [json.loads(line) for line in ABO_ITEMS.read_text(encoding="utf-8").splitlines() if line]
        outs = asyncio.run(run_audit_photos([x for x in items if x["set"] == "audit"], AUDIT_RESULTS))
        return 1 if any(o["errors"] for o in outs) else 0
    folders = sorted(p for p in SUBMISSIONS.glob("SYN-*") if not args.only or p.name.startswith(args.only))
    outs = asyncio.run(run(folders, RESULTS))
    failed = sum(1 for o in outs if not o["spec"]) + sum(len(o["errors"]) for o in outs)
    print(f"{len(outs)} submissions; {failed} failed files; results in {RESULTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
