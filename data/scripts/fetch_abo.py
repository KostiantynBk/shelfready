"""Fetch the Amazon Berkeley Objects (ABO, CC BY 4.0) subset that ShelfReady uses.

    python data/scripts/fetch_abo.py             # download the photos in data/abo/items.jsonl, verify sha256
    python data/scripts/fetch_abo.py --select    # maintainers: rebuild items.jsonl from the ABO metadata

Two sets, chosen deterministically (no randomness, same result on every machine):
- audit (250): existing listings for the catalogue health audit. Chairs, sofas, lamps and rugs, taken
  round-robin across marketplaces with amazon.es first (Casa Nord is in Barcelona), then other European
  ones, then amazon.com. Text stays in the marketplace language; at most 4 photos per item.
- synthetic (31 colour families): base products whose real photos are reused for the synthetic supplier
  submissions (make_synthetic.py uses 25 after a photo check). A family is one amazon.com model in 2-3
  colours (each colour = one variant), each with dimensions, >= 4 photos and a main photo >= 1000 px.
  The demo hero LISBOA is pinned to the Rivet Revolve armchair. Never overlaps the audit set.

Photos are stored unmodified in data/raw/abo/images/<abo path> (git-ignored); items.jsonl is committed.
Only the standard library is used, so it runs with any Python >= 3.10.
"""

from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "abo" / "items.jsonl"
IMAGES = ROOT / "data" / "raw" / "abo" / "images"
CACHE = ROOT / "data" / "cache" / "abo"
BASE = "https://amazon-berkeley-objects.s3.amazonaws.com"

# Marketplace -> listing language, in round-robin order
MARKETS = {
    "amazon.es": "es_ES",
    "amazon.co.uk": "en_GB",
    "amazon.de": "de_DE",
    "amazon.it": "it_IT",
    "amazon.fr": "fr_FR",
    "amazon.nl": "nl_NL",
    "amazon.se": "sv_SE",
    "amazon.com": "en_US",
}
AUDIT_QUOTA = {"chair": 90, "sofa": 60, "lamp": 50, "rug": 50}
# Colour families per category; make_synthetic.py uses 25 of them, the rest are spares
SYNTHETIC_QUOTA = {"chair": 12, "sofa": 6, "lamp": 6, "rug": 6}
AUDIT_PHOTOS, SYNTHETIC_PHOTOS = 4, 4  # per item (per colour variant for the synthetic set)
RUG_NODE = re.compile(r"rug|alfombra|teppich|tappet|tapis|vloerkleed|matta", re.I)
NOT_A_CHAIR = re.compile(r"ottoman|stool|bench|sectional|sleeper", re.I)
# Demo hero LISBOA = Rivet Revolve armchair: moss green (MUS), sand (ARE), anthracite (ANT); wooden legs
HERO_FAMILY, HERO_ITEMS, HERO_PHOTOS = "lisboa", ["B07P7NVGB4", "B07BWKFNF6", "B07BW8PWGV"], 6
BRAND_PREFIX = re.compile(r"^amazon brand\s*[–-]\s*", re.I)

# Listing fields worth keeping (the rest is SEO keywords and 3D-model ids)
KEEP = [
    "item_id",
    "item_name",
    "brand",
    "bullet_point",
    "product_description",
    "color",
    "material",
    "fabric_type",
    "finish_type",
    "pattern",
    "style",
    "item_shape",
    "item_dimensions",
    "item_weight",
    "model_number",
    "model_name",
    "product_type",
    "node",
    "country",
    "domain_name",
]


# --------------------------------------------------------------------------- selection
def category(listing: dict) -> str | None:
    kind = listing["product_type"][0]["value"]
    if kind in ("CHAIR", "SOFA", "LAMP", "RUG"):
        return kind.lower()
    nodes = " ".join(n.get("node_name") or "" for n in listing.get("node", []))
    if kind == "HOME_FURNITURE_AND_DECOR" and RUG_NODE.search(nodes):
        return "rug"
    return None


def texts(listing: dict, field: str, languages: set[str]) -> list[str]:
    return [v["value"] for v in listing.get(field, []) if v.get("language_tag") in languages]


def trimmed(listing: dict, language: str) -> dict:
    """The listing with only the fields we use, in its own language plus English."""
    languages = {language, "en_GB", "en_US"}
    out = {}
    for key in KEEP:
        if key not in listing:
            continue
        value = listing[key]
        if isinstance(value, list) and value and isinstance(value[0], dict) and "language_tag" in value[0]:
            value = [v for v in value if v["language_tag"] in languages]
            if not value:
                continue
        out[key] = value
    return out


def stable_order(item_id: str) -> str:
    return hashlib.sha1(item_id.encode()).hexdigest()


def load_metadata() -> tuple[list[dict], dict[str, dict]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    names = [f"listings/metadata/listings_{h}.json.gz" for h in "0123456789abcdef"]
    names.append("images/metadata/images.csv.gz")
    for name in names:
        target = CACHE / Path(name).name
        if not target.exists():
            print(f"downloading {name}")
            target.write_bytes(download(f"{BASE}/{name}"))
    listings, seen = [], set()
    for name in names[:-1]:
        with gzip.open(CACHE / Path(name).name, "rt", encoding="utf-8") as fh:
            for line in fh:
                listing = json.loads(line)
                if listing["item_id"] not in seen:  # 22 items appear in two marketplaces: keep the first
                    seen.add(listing["item_id"])
                    listings.append(listing)
    with gzip.open(CACHE / "images.csv.gz", "rt", encoding="utf-8") as fh:
        images = {row["image_id"]: row for row in csv.DictReader(fh)}
    return listings, images


def photos(listing: dict, images: dict[str, dict], limit: int) -> list[dict]:
    ids = [listing.get("main_image_id"), *listing.get("other_image_id", [])]
    out = []
    for n, image_id in enumerate(ids):
        if image_id in images and len(out) < limit:
            row = images[image_id]
            out.append(
                {
                    "image_id": image_id,
                    "role": "main" if n == 0 else "other",
                    "path": row["path"],
                    "width": int(row["width"]),
                    "height": int(row["height"]),
                }
            )
    return out


def candidate(listing: dict, images: dict[str, dict]) -> dict | None:
    """Category and language if the listing is usable at all, else None."""
    cat, language = category(listing), MARKETS.get(listing["domain_name"])
    if not cat or not language or listing.get("main_image_id") not in images:
        return None
    if not texts(listing, "item_name", {language}):
        return None
    facts = [
        bool(texts(listing, "color", {language, "en_GB", "en_US"})),
        bool(texts(listing, "material", {language, "en_GB", "en_US"})),
        "item_dimensions" in listing,
        bool(texts(listing, "bullet_point", {language, "en_GB", "en_US"})),
    ]
    return {"category": cat, "language": language} if sum(facts) >= 2 else None


def family_name(listing: dict) -> str | None:
    """The model name without its colour, e.g. 'Amazon Brand – Rivet Edgewest Low Back Accent Chair, 34"W'.

    None unless the colour appears in the name, so every family member differs only by colour.
    """
    name, colour = texts(listing, "item_name", {"en_US"}), texts(listing, "color", {"en_US"})
    if not name or not colour:
        return None
    base = re.sub(re.escape(colour[0]) + r".*$", "", name[0], flags=re.I)
    base = re.sub(r"[\s,\-–]+$", "", base)
    return base if len(base) >= 25 and base != name[0] else None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", BRAND_PREFIX.sub("", text).lower()).strip("-")[:60]


def select_synthetic(listings: list[dict], images: dict[str, dict]) -> list[dict]:
    """Colour families: one model in 2-3 colours; each colour becomes a variant of a synthetic product."""
    families: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for listing in listings:
        cat, base = category(listing), family_name(listing)
        if cat not in SYNTHETIC_QUOTA or not base or listing["domain_name"] != "amazon.com":
            continue
        if cat == "chair" and NOT_A_CHAIR.search(base):
            continue
        pics = photos(listing, images, SYNTHETIC_PHOTOS)
        sharp = bool(pics) and max(pics[0]["width"], pics[0]["height"]) >= 1000
        if "item_dimensions" in listing and len(pics) >= 4 and sharp:
            families[(cat, base)].append(listing)

    by_id = {listing["item_id"]: listing for listing in listings}
    hero = [by_id[i] for i in HERO_ITEMS]
    chosen = [
        entry(x, "synthetic", "chair", "en_US", photos(x, images, HERO_PHOTOS), HERO_FAMILY) for x in hero
    ]
    models = {" ".join(BRAND_PREFIX.sub("", family_name(hero[0]) or "").lower().split()[:4])}
    for cat, quota in SYNTHETIC_QUOTA.items():
        taken = 0
        for key in sorted((k for k in families if k[0] == cat), key=lambda k: stable_order(k[1])):
            # Left/right versions of one sectional look alike: one family per model
            model = " ".join(BRAND_PREFIX.sub("", key[1]).lower().split()[:4])
            variants, colours = [], set()
            for listing in sorted(families[key], key=lambda x: stable_order(x["item_id"])):
                colour = texts(listing, "color", {"en_US"})[0].lower()
                if colour not in colours and len(variants) < 3:
                    colours.add(colour)
                    variants.append(listing)
            if len(variants) < 2 or model in models or taken == quota:
                continue
            models.add(model)
            taken += 1
            family = slug(key[1])
            chosen += [
                entry(x, "synthetic", cat, "en_US", photos(x, images, SYNTHETIC_PHOTOS), family)
                for x in variants
            ]
    return chosen


def select_audit(listings: list[dict], images: dict[str, dict], exclude: set[str]) -> list[dict]:
    buckets: dict[tuple[str, str], list[tuple[dict, dict]]] = collections.defaultdict(list)
    for listing in listings:
        info = candidate(listing, images)
        if info and listing["item_id"] not in exclude:
            buckets[(info["category"], listing["domain_name"])].append((listing, info))
    chosen = []
    for cat, quota in AUDIT_QUOTA.items():
        queues = [sorted(buckets[(cat, m)], key=lambda x: stable_order(x[0]["item_id"])) for m in MARKETS]
        taken: list[tuple[dict, dict]] = []
        while len(taken) < quota and any(queues):
            for queue in queues:  # round-robin: one per marketplace per pass
                if queue and len(taken) < quota:
                    taken.append(queue.pop(0))
        chosen += [
            entry(x, "audit", cat, info["language"], photos(x, images, AUDIT_PHOTOS)) for x, info in taken
        ]
    return chosen


def entry(listing: dict, subset: str, cat: str, language: str, pics: list[dict], family: str = "") -> dict:
    item = {
        "item_id": listing["item_id"],
        "set": subset,
        "category": cat,
        "domain": listing["domain_name"],
        "language": language,
        "listing": trimmed(listing, language),
        "images": pics,
    }
    return item | {"family": family} if family else item


# --------------------------------------------------------------------------- download + verify
def download(url: str, attempts: int = 4) -> bytes:
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch_image(image: dict) -> tuple[dict, str]:
    """Download one photo unless a verified copy exists. Returns (image, status).

    With no recorded sha256 (--select), a file already on disk is trusted: files are only ever
    written whole (temp file + rename), so a present file is a complete download.
    """
    target = IMAGES / image["path"]
    if target.exists():
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if image.get("sha256", digest) == digest:
            return {**image, "bytes": len(data), "sha256": digest}, "cached"
    data = download(f"{BASE}/images/original/{image['path']}")
    digest = hashlib.sha256(data).hexdigest()
    if "sha256" in image and digest != image["sha256"]:
        return image, "checksum mismatch"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_bytes(data)
    partial.replace(target)
    return {**image, "bytes": len(data), "sha256": digest}, "downloaded"


def fetch_all(items: list[dict], workers: int) -> tuple[list[dict], collections.Counter]:
    jobs = [(i, j, image) for i, item in enumerate(items) for j, image in enumerate(item["images"])]
    status: collections.Counter = collections.Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (i, j, _), (image, state) in zip(
            jobs, pool.map(lambda job: fetch_image(job[2]), jobs), strict=True
        ):
            items[i]["images"][j] = image
            status[state] += 1
            if state == "checksum mismatch":
                print(f"  checksum mismatch: {items[i]['item_id']} {image['path']}", file=sys.stderr)
    return items, status


def summary(items: list[dict], status: collections.Counter) -> None:
    for subset in ("audit", "synthetic"):
        chosen = [x for x in items if x["set"] == subset]
        families = len({x["family"] for x in chosen if "family" in x})
        print(f"{subset}: {len(chosen)} items, {sum(len(x['images']) for x in chosen)} photos"
              + (f", {families} colour families" if families else ""))  # fmt: skip
        print("  by category:", dict(collections.Counter(x["category"] for x in chosen)))
        print("  by marketplace:", dict(collections.Counter(x["domain"] for x in chosen)))
    size = sum(image.get("bytes", 0) for x in items for image in x["images"])
    small = sum(1 for x in items for image in x["images"] if max(image["width"], image["height"]) < 500)
    print(f"photos: {dict(status)}; {size / 1e6:.0f} MB; {small} under 500 px")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--select", action="store_true", help="rebuild data/abo/items.jsonl from ABO metadata"
    )
    parser.add_argument("--set", choices=["audit", "synthetic", "all"], default="all")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    if args.select:
        listings, images = load_metadata()
        synthetic = select_synthetic(listings, images)
        items = select_audit(listings, images, {x["item_id"] for x in synthetic}) + synthetic
    else:
        if not MANIFEST.exists():
            print(f"{MANIFEST} is missing; run with --select first", file=sys.stderr)
            return 1
        items = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line]

    # --select needs every checksum for the manifest, so it always fetches both sets
    wanted = [x for x in items if args.select or args.set in ("all", x["set"])]
    _, status = fetch_all(wanted, args.workers)
    if args.select:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        with MANIFEST.open("w", encoding="utf-8", newline="\n") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        print(f"wrote {MANIFEST.relative_to(ROOT)}")
        # Photos dropped from the selection are only a download cache: remove them
        keep = {IMAGES / image["path"] for item in items for image in item["images"]}
        stale = [p for p in IMAGES.rglob("*") if p.is_file() and p not in keep]
        for path in stale:
            path.unlink()
        if stale:
            print(f"removed {len(stale)} photos no longer in the selection")
    summary(wanted, status)
    return 1 if status["checksum mismatch"] else 0


if __name__ == "__main__":
    sys.exit(main())
