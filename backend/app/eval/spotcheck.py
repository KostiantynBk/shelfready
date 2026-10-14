# ruff: noqa: E501  (HTML and JS templates)
"""Human spot check of the catalogue audit's photo findings (defect_catalogue.md §5: 30 flagged + 20 unflagged).

    cd backend && uv run python -m app.eval.spotcheck make           # → data/results/spotcheck.html
    cd backend && uv run python -m app.eval.spotcheck score <file>   # the downloaded answers

Only the rules that depend on the model's reading of the photos are judged by a person (A1, A2, A7, A8,
A9); the text rules (sizes, colour field, fibres, inches) are checked in code. `make` draws the sample
(seeded; every finding of the rarer rules is included, the rest is random) into
data/eval/audit_spotcheck_sample.json and writes a local HTML checklist with one yes/no question per
finding; the answers download as JSON. `score` keeps them in data/eval/audit_spotcheck_answers.json and
reports precision per rule (yes ÷ yes + no) and the miss rate on unflagged listings.
"""

from __future__ import annotations

import html
import json
import random
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ITEMS = ROOT / "data" / "abo" / "items.jsonl"
AUDIT = ROOT / "data" / "results" / "audit.json"
SAMPLE = ROOT / "data" / "eval" / "audit_spotcheck_sample.json"
ANSWERS = ROOT / "data" / "eval" / "audit_spotcheck_answers.json"
PAGE = ROOT / "data" / "results" / "spotcheck.html"
SEED, FLAGGED, UNFLAGGED = 1014, 30, 20
PHOTO_RULES = {"A1", "A2", "A7", "A8", "A9"}  # depend on the model's reading of the photos


def question(f: dict[str, Any]) -> str:
    """The finding as a plain yes/no question: yes = the finding is right."""
    spec, found = f.get("spec_value"), f.get("found_value")
    if f["rule"] == "A1":
        return f"It is listed as a <b>{spec}</b>. Do the photos actually show a different product (the model saw: <b>{found}</b>)?"
    if f["rule"] == "A2":
        return f"The colour is named <b>{spec}</b>. Does the product in the photos look clearly different (the model saw: <b>{found}</b>)?"
    if f["rule"] == "A7":
        return "Is there text, a badge or graphics on the <b>MAIN</b> photo?"
    if f["rule"] == "A9":
        return "Do the photos show only a logo or a placeholder, with no product?"
    if f["severity"] == "review":  # A8: several products in the main photo
        return "Does the <b>MAIN</b> photo show several products, while the name does not say it is a set or pack?"
    return "Is the <b>MAIN</b> photo a room scene or a close-up, rather than the product alone on a plain white background?"


def _photo_findings(listing: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in listing["issues"] if i["rule"] in PHOTO_RULES]


def _always(listing: dict[str, Any]) -> bool:
    """Rare findings are always checked, not left to chance."""
    return any(
        i["rule"] in {"A1", "A2", "A7", "A9"} or i["severity"] == "review" for i in _photo_findings(listing)
    )


def draw() -> dict[str, Any]:
    listings = json.loads(AUDIT.read_text(encoding="utf-8"))["listings"]
    rng = random.Random(SEED)
    flagged = [x for x in listings if _photo_findings(x)]
    must = [x for x in flagged if _always(x)]
    rest = [x for x in flagged if not _always(x)]
    picked = must + rng.sample(rest, min(len(rest), max(0, FLAGGED - len(must))))
    rng.shuffle(picked)
    clean = rng.sample([x for x in listings if not _photo_findings(x)], UNFLAGGED)
    return {
        "seed": SEED,
        "flagged": [{"item_id": x["item_id"], "findings": [{"rule": i["rule"], "severity": i["severity"], "title": i["title"],
                     "spec_value": i.get("spec_value"), "found_value": i.get("found_value")} for i in _photo_findings(x)]}
                    for x in picked],
        "unflagged": [x["item_id"] for x in clean],
    }  # fmt: skip


def _texts(listing: dict[str, Any], field: str) -> list[str]:
    return [v["value"] for v in listing.get(field, []) if isinstance(v, dict) and v.get("value")]


def _card(item: dict[str, Any], body: str) -> str:
    listing = item["listing"]
    rows = [
        ("Shop", item["domain"]),
        ("Listed as", item["category"]),
        ("Colour name", " / ".join(_texts(listing, "color")) or "—"),
    ]
    photos = "".join(
        f'<a href="../raw/abo/images/{im["path"]}" target="_blank"><img src="../raw/abo/images/{im["path"]}" loading="lazy">'
        f"<span>{'<b>MAIN</b>' if im['role'] == 'main' else '&nbsp;'}</span></a>"
        for im in item["images"]
    )
    facts = "".join(f"<dt>{k}</dt><dd>{html.escape(str(v))}</dd>" for k, v in rows)
    name = html.escape((_texts(listing, "item_name") or [""])[0])
    return (f'<section class="card"><div class="photos">{photos}</div><div class="info"><h3>{name}</h3>'
            f'<div class="id">{item["item_id"]}</div><dl>{facts}</dl>{body}</div></section>')  # fmt: skip


def _buttons(key: str, choices: list[tuple[str, str]]) -> str:
    return "".join(f'<button data-key="{key}" data-value="{v}">{label}</button>' for v, label in choices)


def page(sample: dict[str, Any]) -> str:
    items = {x["item_id"]: x for x in map(json.loads, ITEMS.read_text(encoding="utf-8").splitlines()) if x}
    yes_no = [("correct", "Yes"), ("wrong", "No"), ("unsure", "Not sure")]
    part1, part2 = [], []
    for n, entry in enumerate(sample["flagged"], 1):
        qs = "".join(f'<div class="q">{question(f)}<div class="btns">{_buttons(_key(entry["item_id"], k, f), yes_no)}</div></div>'
                     for k, f in enumerate(entry["findings"]))  # fmt: skip
        part1.append(
            _card(items[entry["item_id"]], f'<p class="n">Listing {n} of {len(sample["flagged"])}</p>{qs}')
        )
    for n, item_id in enumerate(sample["unflagged"], 1):
        item = items[item_id]
        body = (f'<p class="n">Listing {n} of {len(sample["unflagged"])}</p><div class="q">Do the photos match the listing? '
                f'They show a <b>{item["category"]}</b>, in the colour named above, with real product photos and a main photo of the product alone on a plain background.'
                '<div class="btns">' + _buttons(f"{item_id}|missed", [("fine", "Yes, all fine"), ("missed", "No, there is a problem"), ("unsure", "Not sure")])
                + f'</div><input data-note="{item_id}|note" placeholder="What is wrong? (optional)"></div>')  # fmt: skip
        part2.append(_card(item, body))
    total = sum(len(e["findings"]) for e in sample["flagged"]) + len(sample["unflagged"])
    return (TEMPLATE.replace("__PART1__", "".join(part1)).replace("__PART2__", "".join(part2))
            .replace("__TOTAL__", str(total)))  # fmt: skip


def _key(item_id: str, k: int, finding: dict[str, Any]) -> str:
    return f"{item_id}|{finding['rule']}|{k}"


def score(answers_path: Path) -> dict[str, Any]:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    answers = json.loads(answers_path.read_text(encoding="utf-8"))["answers"]
    per_rule: dict[str, Counter[str]] = {}
    for entry in sample["flagged"]:
        for k, f in enumerate(entry["findings"]):
            rule = f"{f['rule']} ({f['severity']})"
            per_rule.setdefault(rule, Counter())[answers.get(_key(entry["item_id"], k, f), "unanswered")] += 1

    def precision(c: Counter[str]) -> float | None:
        judged = c["correct"] + c["wrong"]
        return round(c["correct"] / judged, 3) if judged else None

    total = sum(per_rule.values(), Counter())
    missed = Counter(answers.get(f"{i}|missed", "unanswered") for i in sample["unflagged"])
    judged = missed["fine"] + missed["missed"]
    return {
        "precision_by_rule": {r: {**c, "precision": precision(c)} for r, c in sorted(per_rule.items())},
        "precision_photo_findings": precision(total),
        "unflagged": {**missed, "miss_rate": round(missed["missed"] / judged, 3) if judged else None},
        "missed_notes": {i: answers[f"{i}|note"] for i in sample["unflagged"] if answers.get(f"{i}|note")},
    }


def main(argv: list[str]) -> int:
    if argv[:1] == ["make"]:
        SAMPLE.parent.mkdir(parents=True, exist_ok=True)
        sample = draw()
        SAMPLE.write_text(json.dumps(sample, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        PAGE.write_text(page(sample), encoding="utf-8")
        questions = sum(len(e["findings"]) for e in sample["flagged"])
        print(
            f"{len(sample['flagged'])} flagged ({questions} questions) + {len(sample['unflagged'])} unflagged → {PAGE}"
        )
        return 0
    if argv[:1] == ["score"] and len(argv) == 2:
        if Path(argv[1]).resolve() != ANSWERS.resolve():
            shutil.copyfile(argv[1], ANSWERS)
        print(json.dumps(score(ANSWERS), indent=1, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ShelfReady · photo spot check</title>
<style>
 body{font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;color:#202223;background:#f6f6f7}
 header{position:sticky;top:0;z-index:2;background:#fff;border-bottom:1px solid #e1e3e5;padding:12px 24px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
 header h1{font-size:16px;margin:0} #progress{color:#6d7175} header button{margin-left:auto}
 main{max-width:1200px;margin:0 auto;padding:16px 24px} h2{font-size:15px;margin:28px 0 4px} h2+p{margin:0 0 8px;color:#6d7175}
 .intro{background:#fff;border:1px solid #e1e3e5;border-radius:8px;padding:12px 16px}
 .card{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:16px;background:#fff;border:1px solid #e1e3e5;border-radius:8px;padding:12px;margin:12px 0}
 .card.done{border-color:#aee9d1;background:#fbfffd}
 .photos{display:flex;flex-wrap:wrap;gap:6px;align-content:flex-start}
 .photos a{display:flex;flex-direction:column;align-items:center;text-decoration:none;color:#202223;font-size:11px}
 .photos img{width:150px;height:150px;object-fit:contain;background:#fafafa;border:1px solid #eee;border-radius:4px}
 h3{font-size:14px;margin:0 0 2px} .id{color:#8c9196;font-size:12px}
 dl{display:grid;grid-template-columns:90px 1fr;gap:2px 8px;margin:8px 0;font-size:13px} dt{color:#6d7175} dd{margin:0}
 .n{font-weight:600;margin:10px 0 4px;color:#6d7175} .q{border-top:1px solid #f1f2f3;padding:8px 0}
 .btns{display:flex;gap:6px;margin-top:6px}
 button{font:inherit;border:1px solid #babfc3;background:#fff;border-radius:6px;padding:4px 12px;cursor:pointer}
 button.on[data-value=correct],button.on[data-value=fine]{background:#008060;border-color:#008060;color:#fff}
 button.on[data-value=wrong],button.on[data-value=missed]{background:#d72c0d;border-color:#d72c0d;color:#fff}
 button.on[data-value=unsure]{background:#5c5f62;border-color:#5c5f62;color:#fff}
 input{font:inherit;width:100%;box-sizing:border-box;margin-top:6px;padding:4px 8px;border:1px solid #c9cccf;border-radius:6px}
 .primary{background:#008060;border-color:#008060;color:#fff}
</style></head><body>
<header><h1>Photo spot check</h1><span id="progress"></span>
<button class="primary" id="download">Download answers</button></header>
<main>
<div class="intro">Each question is about <b>what you can see in the photos</b>, compared with what the listing says (category and colour name, shown next to the photos). Click a photo to open it full size. Answer <b>Yes</b> if the statement is true, <b>No</b> if it is not, <b>Not sure</b> if you cannot tell. Sizes, fibre content and empty fields are checked from the listing text by code, so they are not asked here. Answers are kept in this browser; when you are done, click <b>Download answers</b>.</div>
<h2>Part 1 · Listings the audit flagged</h2><p>Is the problem the audit found really there?</p>__PART1__
<h2>Part 2 · Listings the audit did not flag</h2><p>Did the audit miss a photo problem?</p>__PART2__
</main>
<script>
const KEY = 'shelfready-photo-spotcheck-v2', TOTAL = __TOTAL__;
let answers = {};
try { answers = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
function save() { try { localStorage.setItem(KEY, JSON.stringify(answers)); } catch (e) {} paint(); }
function paint() {
  document.querySelectorAll('button[data-key]').forEach(b => b.classList.toggle('on', answers[b.dataset.key] === b.dataset.value));
  document.querySelectorAll('input[data-note]').forEach(i => { if (document.activeElement !== i) i.value = answers[i.dataset.note] || ''; });
  document.querySelectorAll('.card').forEach(c => c.classList.toggle('done',
    [...c.querySelectorAll('button[data-key]')].map(b => b.dataset.key).every(k => answers[k])));
  const done = Object.keys(answers).filter(k => !k.endsWith('|note')).length;
  document.getElementById('progress').textContent = done + ' of ' + TOTAL + ' answered';
}
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-key]'); if (!b) return;
  answers[b.dataset.key] = b.dataset.value; save();
});
document.addEventListener('input', e => {
  const i = e.target.closest('input[data-note]'); if (!i) return;
  if (i.value.trim()) answers[i.dataset.note] = i.value.trim(); else delete answers[i.dataset.note];
  save();
});
document.getElementById('download').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify({ version: 2, answers }, null, 1)], { type: 'application/json' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'audit_spotcheck_answers.json'; a.click();
});
paint();
</script></body></html>
"""

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
