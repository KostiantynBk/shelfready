"""The checks on real Gemma output (fixtures = data/results/extraction from the 2026-10-14 run).

These lock in the measured quality: if a rule change loses planted defects or adds false alarms,
the tests fail. Regenerate the fixtures after a new extraction run, then re-check the thresholds.
"""

import json
from pathlib import Path

from app.eval.metrics import evaluate
from app.pipeline.checks import check_submission, product_category

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"
GROUND_TRUTH = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "ground_truth.json"


def results():
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURES.glob("SYN-*.json"))}


def test_lisboa_raises_exactly_its_nine_planted_defects():
    issues = check_submission(results()["SYN-00"])
    assert [(i.rule, i.variant) for i in issues] == [
        ("D1", None), ("D2", None), ("D3", "LIS-01-ANT"), ("D4", "LIS-01-ARE"), ("D5", None),
        ("D6", "LIS-01-ANT"), ("D7", None), ("D8", None), ("D9", None),
    ]  # fmt: skip
    by_rule = {i.rule: i for i in issues}
    assert by_rule["D1"].proposed_value == "47 cm"  # read from the supplier's dimension drawing
    assert by_rule["D2"].proposed_value == "50 cm"  # 500 mm, converted in code
    assert {e.source for e in by_rule["D5"].evidence} == {"pdf", "label"}
    assert {e.source for e in by_rule["D6"].evidence} == {"pdf", "photo"}
    assert by_rule["D8"].file == "LIS01_promo.jpg" and by_rule["D9"].file == "LIS01_ARE_back_small.jpg"


def test_quality_on_all_25_products_stays_measured():
    ground_truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    report = evaluate(ground_truth, {sid: check_submission(r) for sid, r in results().items()})
    assert report["evaluated"] == 25
    assert report["precision"] >= 0.95 and report["recall"] >= 0.95, (
        report["misses"],
        report["false_alarms"],
    )
    assert report["clean_products_with_false_alarms"] == 0


def test_categories_come_from_the_suppliers_own_words():
    categories = {sid: product_category(r["spec"]) for sid, r in results().items()}
    assert categories["SYN-00"] == "chair" and categories["SYN-11"] == "sofa"
    assert categories["SYN-15"] == "lamp" and categories["SYN-20"] == "rug"
