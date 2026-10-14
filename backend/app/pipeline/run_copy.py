"""Write and translate the shop copy for every submission, and record the model's answers.

    cd backend
    LLM_MODE=cached LLM_API_KEY=... uv run python -m app.pipeline.run_copy [--only SYN-00]

Per product: the checks' findings decide which facts are verified (listing.py); the model writes English
copy from them, translates it (translate_qa.LANGUAGES), back-translates German and Ukrainian, and the code
checks each translation. Products listed in data/demo/decisions.json are written twice: before review,
and after the demo's planned decisions, so the demo replays without a model. Results go to
data/results/copy/<product>.json and <product>.reviewed.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.clients.llm import LLMClient
from app.pipeline.checks import check_submission
from app.pipeline.listing import listing_photos, verified_facts, write_listing
from app.pipeline.translate_qa import (
    BACK_TRANSLATED,
    LANGUAGES,
    back_translate,
    check_translation,
    protected_tokens,
    translate,
    translation_issues,
)
from app.schemas.review import Decision

ROOT = Path(__file__).resolve().parents[3]
EXTRACTION = ROOT / "data" / "results" / "extraction"
DECISIONS = ROOT / "data" / "demo" / "decisions.json"
RESULTS = ROOT / "data" / "results" / "copy"


async def write_copy(
    llm: LLMClient,
    result: dict[str, Any],
    decisions: dict[str, Decision] | None = None,
    languages=tuple(LANGUAGES),
) -> dict[str, Any]:
    pid = result["submission_id"]
    issues = check_submission(result)
    facts, held = verified_facts(result["spec"], issues, decisions)
    photos = listing_photos(result, issues, [v["code"] for v in facts.get("variants", [])])
    en, first = await write_listing(llm, facts, photos, purpose=f"listing:{pid}")
    protected = protected_tokens(facts, en)

    async def one(lang: str) -> tuple[str, dict[str, Any]]:
        tr, _ = await translate(llm, en, lang, protected, purpose=f"translate:{pid}:{lang}")
        back = None
        if lang in BACK_TRANSLATED:
            back, _ = await back_translate(llm, tr, lang, purpose=f"back:{pid}:{lang}")
        qa = check_translation(en, tr, lang, protected, back)
        return lang, {"copy": tr.model_dump(), "qa": qa.model_dump(), "passed": qa.passed,
                      "issues": [i.model_dump() for i in translation_issues(pid, lang, qa)],
                      "back": back.model_dump() if back else None}  # fmt: skip

    translations = dict(await asyncio.gather(*(one(lang) for lang in languages)))
    return {"product_id": pid, "decisions": {k: d.model_dump() for k, d in (decisions or {}).items()},
            "facts": facts, "held_back": held, "photos": photos, "protected": protected,
            "en": en.model_dump(), "translations": translations, "model": first.model}  # fmt: skip


async def run(results: list[dict[str, Any]], planned: dict[str, dict[str, Decision]]) -> list[dict[str, Any]]:
    RESULTS.mkdir(parents=True, exist_ok=True)
    jobs = [(r, None, "") for r in results] + [
        (r, planned[r["submission_id"]], ".reviewed") for r in results if r["submission_id"] in planned
    ]
    async with LLMClient() as llm:

        async def one(
            result: dict[str, Any], decisions: dict[str, Decision] | None, suffix: str
        ) -> dict[str, Any]:
            name = f"{result['submission_id']}{suffix}"
            try:
                out = await write_copy(llm, result, decisions)
            except Exception as error:  # one failed product must not lose the rest
                print(f"{name}: FAILED {type(error).__name__}: {error}"[:300], flush=True)
                return {"product_id": name, "error": str(error)}
            (RESULTS / f"{name}.json").write_text(
                json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            failed = [lang for lang, t in out["translations"].items() if not t["passed"]]
            print(
                f"{name}: {len(out['held_back'])} facts held back; QA failed: {failed or 'none'}", flush=True
            )
            return out

        return await asyncio.gather(*(one(*job) for job in jobs))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", help="submission id prefix, e.g. SYN-00")
    args = parser.parse_args()
    results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXTRACTION.glob("SYN-*.json"))
               if not args.only or p.stem.startswith(args.only)]  # fmt: skip
    raw = json.loads(DECISIONS.read_text(encoding="utf-8")) if DECISIONS.exists() else {}
    planned = {pid: {k: Decision.model_validate(d) for k, d in ds.items()} for pid, ds in raw.items()}
    outs = asyncio.run(run([r for r in results if r.get("spec")], planned))
    errors = sum(1 for o in outs if "error" in o)
    print(f"{len(outs)} copies written, {errors} failed; results in {RESULTS}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
