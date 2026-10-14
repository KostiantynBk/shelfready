# Human spot check of the catalogue audit (14 Oct 2026)

Real listings have no planted truth, so a person judged a sample of the audit's **photo findings**
(the rules that depend on the model reading the photos: A1 category, A2 colour, A7/A8 main image,
A9 no product photo). The text rules (size and colour fields, fibre content, inches) are plain code
over the listing text and are covered by `backend/tests/test_audit.py`.

- Sample: `audit_spotcheck_sample.json` (seed 1014): 30 flagged listings with 31 findings (every A1, A2,
  A9 and "several products" finding, plus random "main image" findings) and 20 listings without photo findings.
- Answers: `audit_spotcheck_answers.json`, given by the project owner in the checklist from
  `uv run python -m app.eval.spotcheck make`; scored with `… spotcheck score`.

## Result

| | Judged | Right | Precision |
|---|---|---|---|
| Wrong category (A1) | 4 | 4 | 100% |
| Colour contradicts photos (A2) | 2 (+1 not sure) | 2 | 100% |
| No product photo (A9) | 3 | 3 | 100% |
| Several products, name doesn't say set (A8) | 2 | 2 | 100% |
| Main image not a packshot (A8) | 19 | 18 | 95% |
| **All photo findings** | **30** | **29** | **97%** |

The one "No" is the wristwatch listing whose main image is a fabric close-up: the person read the question
as "is the main photo a room scene?", which it is not.

**Unflagged listings: 6 of 20 marked "there is a problem"; on review:**
- 1 real miss: a kids' gaming chair named "Negro/Rojo" whose photos show no red. The model reports one
  main colour per photo, so a missing second colour is not caught (known limitation).
- 2 judgement calls: a fabric sample listed under chairs (now reported as info, "Fabric sample listed as
  a chair"), and a lamp named "Nickel" that has several colours.
- 3 already handled: two packs whose names say "6-pack" / "Set of 2" (the question said "product alone"),
  and a rug whose empty colour field the text rules already flag.

So the strict miss rate on unflagged listings is 1 in 20 (5%), or 3 in 20 (15%) counting the judgement calls.
