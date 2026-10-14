"""How well the checks find the planted defects: precision and recall per defect type, false alarms.

    cd backend && uv run python -m app.eval.metrics        # after app.pipeline.run_extraction

A planted defect (data/synthetic/ground_truth.json) is found when the checks raise the same rule on the
same product, on the same variant if the defect has one, or on the same file for photo defects. Flags are
counted once per (product, rule, variant/file), so a rule that fires on two dimensions is one flag.
Writes data/results/eval.json.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.pipeline.checks import check_submission
from app.schemas.review import Issue

ROOT = Path(__file__).resolve().parents[3]
GROUND_TRUTH = ROOT / "data" / "synthetic" / "ground_truth.json"
RESULTS = ROOT / "data" / "results" / "extraction"
OUT = ROOT / "data" / "results" / "eval.json"
FILE_RULES = {"D8", "D9", "D16"}  # judged per file


def _flag_key(issue: Issue) -> tuple[str, str, str | None]:
    return issue.product_id, issue.rule, issue.file if issue.rule in FILE_RULES else issue.variant


def _matches(planted: dict[str, Any], issue: Issue) -> bool:
    if planted["id"] != issue.rule:
        return False
    if issue.rule in FILE_RULES and planted.get("file"):
        return issue.file == planted["file"]
    return planted.get("variant") is None or issue.variant in (None, planted["variant"])


def evaluate(ground_truth: list[dict[str, Any]], issues_by_product: dict[str, list[Issue]]) -> dict[str, Any]:
    per_rule: dict[str, dict[str, int]] = defaultdict(
        lambda: {"planted": 0, "found": 0, "flags": 0, "correct_flags": 0}
    )
    misses, false_alarms, clean_alarms = [], [], []
    for product in ground_truth:
        pid = product["product_id"]
        issues = issues_by_product.get(pid, [])
        planted = product["planted_defects"]
        for defect in planted:
            stats = per_rule[defect["id"]]
            stats["planted"] += 1
            if any(_matches(defect, i) for i in issues):
                stats["found"] += 1
            else:
                misses.append(
                    {
                        "product": pid,
                        "rule": defect["id"],
                        "variant": defect.get("variant"),
                        "file": defect.get("file"),
                    }
                )
        flags: dict[tuple, Issue] = {}
        for issue in issues:
            flags.setdefault(_flag_key(issue), issue)
        for issue in flags.values():
            stats = per_rule[issue.rule]
            stats["flags"] += 1
            if any(_matches(d, issue) for d in planted):
                stats["correct_flags"] += 1
            else:
                false_alarms.append(
                    {
                        "product": pid,
                        "rule": issue.rule,
                        "variant": issue.variant,
                        "file": issue.file,
                        "title": issue.title,
                    }
                )
                if product["clean"]:
                    clean_alarms.append(pid)

    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 3) if b else None

    totals = {k: sum(s[k] for s in per_rule.values()) for k in ("planted", "found", "flags", "correct_flags")}
    clean = [p["product_id"] for p in ground_truth if p["clean"]]
    return {
        "products": len(ground_truth),
        "evaluated": sum(1 for p in ground_truth if p["product_id"] in issues_by_product),
        "precision": ratio(totals["correct_flags"], totals["flags"]),
        "recall": ratio(totals["found"], totals["planted"]),
        "clean_products": len(clean),
        "clean_products_with_false_alarms": len(set(clean_alarms)),
        "per_rule": {
            rule: s
            | {"precision": ratio(s["correct_flags"], s["flags"]), "recall": ratio(s["found"], s["planted"])}
            for rule, s in sorted(per_rule.items(), key=lambda kv: int(kv[0][1:]))
        },  # fmt: skip
        "misses": misses,
        "false_alarms": false_alarms,
    }


def main() -> int:
    ground_truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    issues = {}
    for path in sorted(RESULTS.glob("SYN-*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        issues[result["submission_id"]] = check_submission(result)
    report = evaluate([p for p in ground_truth if p["product_id"] in issues], issues)
    OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    clean = f"{report['clean_products_with_false_alarms']}/{report['clean_products']}"
    print(f"{report['evaluated']} products · precision {report['precision']} · recall {report['recall']} · "
          f"clean products with false alarms: {clean}")  # fmt: skip
    print(f"{'rule':5} {'planted':>7} {'found':>6} {'flags':>6} {'precision':>9} {'recall':>7}")
    for rule, s in report["per_rule"].items():
        counts = f"{s['planted']:>7} {s['found']:>6} {s['flags']:>6}"
        print(f"{rule:5} {counts} {s['precision']!s:>9} {s['recall']!s:>7}")
    for miss in report["misses"]:
        print("MISS ", miss)
    for alarm in report["false_alarms"]:
        print("FALSE", alarm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
