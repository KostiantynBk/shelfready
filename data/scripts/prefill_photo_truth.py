"""Pre-fill the photo truth of the synthetic base products with Gemma, for a person to confirm.

    cd backend
    LLM_MODE=cached LLM_API_KEY=... uv run python ../data/scripts/prefill_photo_truth.py --sheets <dir>

For every colour variant in data/abo/items.jsonl (set "synthetic") the main photo is analysed and the
answer goes to data/synthetic/photo_truth.json with "confirmed": false. Entries already confirmed are
kept as they are. A person checks the contact sheets (one row per family) and confirms or corrects;
make_synthetic.py plants photo contradictions (D6, D10, D16) only against confirmed truth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.clients.llm import LLMClient  # noqa: E402
from app.pipeline.photos import analyse_photo  # noqa: E402

ITEMS = ROOT / "data" / "abo" / "items.jsonl"
IMAGES = ROOT / "data" / "raw" / "abo" / "images"
TRUTH = ROOT / "data" / "synthetic" / "photo_truth.json"
TILE, CAPTION = 300, 96


def english(listing: dict, field: str) -> str | None:
    values = [v["value"] for v in listing.get(field, []) if v.get("language_tag") == "en_US"]
    return values[0] if values else None


async def analyse_all(items: list[dict], truth: dict) -> dict:
    async with LLMClient() as llm:

        async def one(item: dict) -> None:
            if truth.get(item["item_id"], {}).get("confirmed"):
                return
            fact = {
                "family": item["family"],
                "category": item["category"],
                "abo_colour": english(item["listing"], "color"),
                "abo_material": english(item["listing"], "material"),
                "photo": item["images"][0]["path"],
                "colour_family": None,
                "legs_material": None,
                "base_material": None,
                "legs_finish": None,
                "products_in_frame": None,
                "source": None,
                "confirmed": False,
            }
            try:
                seen, result = await analyse_photo(
                    llm, IMAGES / fact["photo"], purpose=f"photo-truth:{item['item_id']}"
                )
            except Exception as error:  # one bad answer must not sink the batch; a person fills the gap
                truth[item["item_id"]] = fact | {"source": f"failed: {type(error).__name__}"}
                return
            truth[item["item_id"]] = fact | {
                "colour_family": seen.colour_family,
                "legs_material": seen.legs_material,
                "base_material": seen.base_material,
                "legs_finish": seen.legs_finish,
                "products_in_frame": seen.products_in_frame,
                "source": f"model:{result.model}",
            }

        await asyncio.gather(*(one(item) for item in items))
    return truth


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheets(items: list[dict], truth: dict, out_dir: Path, families_per_sheet: int = 8) -> list[Path]:
    """One row per family, one tile per colour variant: photo + ABO colour + what the model saw."""
    families: dict[str, list[dict]] = {}
    for item in items:
        families.setdefault(item["family"], []).append(item)
    out_dir.mkdir(parents=True, exist_ok=True)
    names, sheets = list(families), []
    small, bold = font(15), font(17)
    for start in range(0, len(names), families_per_sheet):
        rows = names[start : start + families_per_sheet]
        sheet = Image.new("RGB", (3 * TILE + 20, len(rows) * (TILE + CAPTION + 34)), "white")
        draw = ImageDraw.Draw(sheet)
        y = 0
        for family in rows:
            first = truth[families[family][0]["item_id"]]
            draw.text((8, y + 6), f"{family}  [{first['category']}]", fill="#000", font=bold)
            for col, item in enumerate(families[family]):
                fact = truth[item["item_id"]]
                photo = Image.open(IMAGES / fact["photo"]).convert("RGB")
                photo.thumbnail((TILE - 10, TILE - 10))
                x = 8 + col * TILE
                sheet.paste(photo, (x, y + 34))
                lamp = fact["category"] == "lamp"
                flag = "  EXCLUDED" if fact.get("exclude") else "  REJECTED" if fact.get("reject") else ""
                material = fact["base_material"] if lamp else fact["legs_material"]
                lines = [
                    f"{item['item_id']}  ABO: {fact['abo_colour']}",
                    f"colour: {fact['colour_family']}{flag}",
                    f"{'base' if lamp else 'legs'}: {material} ({fact['legs_finish'] or '-'})",
                    f"in frame: {fact['products_in_frame']}",
                ]
                for n, line in enumerate(lines):
                    draw.text((x, y + 34 + TILE - 4 + n * 22), line, fill="#222", font=small)
            y += TILE + CAPTION + 34
        path = out_dir / f"photo_truth_{start // families_per_sheet + 1}.png"
        sheet.save(path)
        sheets.append(path)
    return sheets


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sheets", type=Path, help="write contact sheets (PNG) to this folder")
    args = parser.parse_args()
    items = [json.loads(line) for line in ITEMS.read_text(encoding="utf-8").splitlines() if line]
    items = [x for x in items if x["set"] == "synthetic"]
    truth = json.loads(TRUTH.read_text(encoding="utf-8")) if TRUTH.exists() else {}
    truth = asyncio.run(analyse_all(items, truth))
    TRUTH.parent.mkdir(parents=True, exist_ok=True)
    ordered = {item["item_id"]: truth[item["item_id"]] for item in items}
    TRUTH.write_text(json.dumps(ordered, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    confirmed = sum(1 for fact in ordered.values() if fact["confirmed"])
    print(f"wrote {TRUTH.relative_to(ROOT)}: {len(ordered)} variants, {confirmed} confirmed")
    if args.sheets:
        for path in contact_sheets(items, ordered, args.sheets):
            print(f"contact sheet: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
