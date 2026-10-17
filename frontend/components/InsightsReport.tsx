"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type EvalReport, type Insights, type SpotcheckScore } from "@/lib/api";
import { Badge, Card, EmptyState } from "./ui";

// Names from data/defects.yaml, in shop-staff words
const DEFECTS: Record<string, string> = {
  D1: "Seat height missing (the drawing has it)",
  D2: "Mixed units (mm among cm)",
  D3: "Barcode (EAN) missing for a colour",
  D4: "Barcode (EAN) with a wrong check digit",
  D5: "Care text contradicts the care label",
  D6: "Material contradicts the photo",
  D7: "Safety warnings only in the supplier's language",
  D8: "Promo text on a photo",
  D9: "Low-resolution photo",
  D10: "Colour name contradicts the photo",
  D11: "Size in the text contradicts the drawing",
  D12: "Manufacturer address missing",
  D13: "No EU contact for a non-EU maker",
  D14: "Fibre content missing on a textile",
  D15: "Health or eco claim to check",
  D16: "Photo filed under the wrong colour",
  D17: "Implausible value (likely a typo)",
};

const SPOT_RULES: Record<string, string> = {
  "A1 (blocking)": "Wrong category: photos show another product",
  "A1 (review)": "Chair listed as sofa, or the reverse",
  "A2 (review)": "Colour name does not match the photos",
  "A7 (info)": "Text or badges on the main image",
  "A8 (info)": "Main image is not a plain product shot",
  "A8 (review)": "Several products in the main image",
  "A9 (blocking)": "No photo of the product",
};

const pct = (x: number | null, digits = 1) => (x == null ? "–" : `${(100 * x).toFixed(digits)}%`);
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);

export function InsightsReport() {
  const [data, setData] = useState<Insights | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Insights>("/api/insights")
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error)
    return (
      <Card>
        <EmptyState heading="The API is not reachable">{error}</EmptyState>
      </Card>
    );
  if (!data) return <p className="muted">Loading results…</p>;

  const { eval: ev, audit, spotcheck, model_calls: calls } = data;
  const rules = ev ? Object.values(ev.per_rule) : [];
  const planted = sum(rules.map((r) => r.planted));
  const found = sum(rules.map((r) => r.found));
  const flags = sum(rules.map((r) => r.flags));
  const correct = sum(rules.map((r) => r.correct_flags));
  const spot = spotcheck ? spotTotals(spotcheck) : null;

  return (
    <>
      <section className="card kpis">
        <div className="kpi">
          <div className="lbl">Planted problems found</div>
          <div className="val">
            {ev ? found : "–"}
            <small> of {ev ? planted : "–"}</small>
          </div>
          <div className="small muted">{ev ? `${pct(ev.recall)} found (recall)` : "no evaluation yet"}</div>
        </div>
        <div className="kpi">
          <div className="lbl">Flags that were right</div>
          <div className="val">
            {ev ? correct : "–"}
            <small> of {ev ? flags : "–"}</small>
          </div>
          <div className="small muted">{ev ? `${pct(ev.precision)} right (precision)` : "no evaluation yet"}</div>
        </div>
        <div className="kpi">
          <div className="lbl">Clean products wrongly flagged</div>
          <div className="val">
            {ev ? ev.clean_products_with_false_alarms : "–"}
            <small> of {ev ? ev.clean_products : "–"}</small>
          </div>
          <div className="small muted">products with nothing planted</div>
        </div>
        <div className="kpi">
          <div className="lbl">Photo findings a person confirmed</div>
          <div className="val">
            {spot ? spot.correct : "–"}
            <small> of {spot ? spot.judged : "–"}</small>
          </div>
          <div className="small muted">
            {spot ? `${pct(spotcheck?.precision_photo_findings ?? null, 0)} right in a hand check` : "no spot check saved"}
          </div>
        </div>
      </section>

      <div className="grid side">
        <div className="stack">
          {ev ? (
            <EvalCard ev={ev} totals={{ planted, found, flags, correct }} />
          ) : (
            <Card title="Test with planted problems">
              <EmptyState heading="No evaluation run yet">
                Results appear after the first run on the planted-problem set.
              </EmptyState>
            </Card>
          )}
          <Card title="How these numbers are measured">
            <div className="how small">
              <p>
                <b>Planted problems.</b> We built test products from real product photos and wrote the supplier spec
                sheets ourselves, hiding known problems in them.
                {ev &&
                  ` ${ev.products} products: ${planted} problems of ${rules.length} kinds in ${ev.products - ev.clean_products} of them, ${ev.clean_products} left clean.`}{" "}
                ShelfReady checked them without seeing the answer key.
              </p>
              <p>
                <b>Found (recall)</b> = planted problems it raised ÷ all planted problems
                {ev && ` = ${found} ÷ ${planted} = ${pct(ev.recall)}`}. A problem counts as found when the same check
                fires on the same product, and on the same colour or photo where that matters.
              </p>
              <p>
                <b>Right (precision)</b> = flags that match a planted problem ÷ all flags it raised
                {ev && ` = ${correct} ÷ ${flags} = ${pct(ev.precision)}`}. A check that fires twice on the same product
                and colour counts once.
              </p>
              <p>
                <b>Spot check.</b> Real listings have no answer key. So a person looked at the photos behind audit
                findings that depend on the model reading photos, and said whether each finding was right.
                &ldquo;Not sure&rdquo; answers are left out. Text checks (size fields, fibre content, inches) are plain
                code with automatic tests.
              </p>
              <p>
                <b>Model use</b> is counted from this server&apos;s log of live calls to the model. The median is the
                middle value: half the calls were faster.
              </p>
            </div>
          </Card>
        </div>

        <div className="stack">
          <SpotcheckCard spotcheck={spotcheck} totals={spot} />
          <Card title="Catalogue audit" aside={<Link href="/health" className="small">Open</Link>}>
            {audit ? (
              <table className="kv">
                <tbody>
                  <tr>
                    <td>Health score</td>
                    <td className="num">{audit.health_score}/100</td>
                  </tr>
                  <tr>
                    <td>Real listings checked</td>
                    <td className="num">{audit.listings}</td>
                  </tr>
                  <tr>
                    <td>Photos read by the model</td>
                    <td className="num">{audit.photos_analysed}</td>
                  </tr>
                  <tr>
                    <td>Listings with blocking issues</td>
                    <td className="num">{audit.listings_with_blocking_issues}</td>
                  </tr>
                </tbody>
              </table>
            ) : (
              <p className="muted">No audit yet.</p>
            )}
          </Card>
          <Card title="Model use on this server">
            {calls.live > 0 ? (
              <table className="kv">
                <tbody>
                  <tr>
                    <td>Live model calls</td>
                    <td className="num">{calls.live.toLocaleString("en")}</td>
                  </tr>
                  <tr>
                    <td>Median time per call</td>
                    <td className="num">
                      {calls.median_latency_s == null ? "–" : `${calls.median_latency_s.toFixed(1)} s`}
                    </td>
                  </tr>
                  <tr>
                    <td>Tokens read</td>
                    <td className="num">{calls.tokens_in.toLocaleString("en")}</td>
                  </tr>
                  <tr>
                    <td>Tokens written</td>
                    <td className="num">{calls.tokens_out.toLocaleString("en")}</td>
                  </tr>
                </tbody>
              </table>
            ) : (
              <p className="muted small">
                No live model calls on this server yet. The results on this page come from saved runs.
              </p>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function EvalCard({
  ev,
  totals,
}: {
  ev: EvalReport;
  totals: { planted: number; found: number; flags: number; correct: number };
}) {
  return (
    <Card title="Test with planted problems" aside={<span className="muted small">{ev.products} test products</span>}>
      <table className="kv">
        <thead>
          <tr>
            <th>Problem we planted</th>
            <th>Planted</th>
            <th>Found</th>
            <th>False alarms</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(ev.per_rule).map(([code, r]) => {
            const wrong = r.flags - r.correct_flags;
            return (
              <tr key={code}>
                <td>{DEFECTS[code] ?? code}</td>
                <td className="num">{r.planted}</td>
                <td className="num">
                  {r.found < r.planted ? <Badge tone="att">{r.found}</Badge> : r.found}
                </td>
                <td className="num">{wrong > 0 ? <Badge tone="att">{wrong}</Badge> : <span className="muted">0</span>}</td>
              </tr>
            );
          })}
          <tr className="total">
            <td>All problems</td>
            <td className="num">{totals.planted}</td>
            <td className="num">{totals.found}</td>
            <td className="num">{totals.flags - totals.correct}</td>
          </tr>
        </tbody>
      </table>
      {(ev.misses.length > 0 || ev.false_alarms.length > 0) && (
        <div className="how small" style={{ marginTop: 12 }}>
          {ev.misses.map((m) => (
            <p key={`${m.product}-${m.rule}-${m.variant}`}>
              <Badge tone="att">Missed</Badge> {DEFECTS[m.rule] ?? m.rule} in {m.product}
              {m.variant && `, colour ${m.variant}`}
              {m.file && `, photo ${m.file}`}.
            </p>
          ))}
          {ev.false_alarms.map((f) => (
            <p key={`${f.product}-${f.rule}-${f.variant}`}>
              <Badge tone="att">False alarm</Badge> {f.product}: {f.title}.
            </p>
          ))}
        </div>
      )}
    </Card>
  );
}

type SpotTotals = { correct: number; wrong: number; unsure: number; judged: number };

function spotTotals(s: SpotcheckScore): SpotTotals {
  const rows = Object.values(s.precision_by_rule);
  const correct = sum(rows.map((r) => r.correct ?? 0));
  const wrong = sum(rows.map((r) => r.wrong ?? 0));
  return { correct, wrong, unsure: sum(rows.map((r) => r.unsure ?? 0)), judged: correct + wrong };
}

function SpotcheckCard({ spotcheck, totals }: { spotcheck: SpotcheckScore | null; totals: SpotTotals | null }) {
  if (!spotcheck || !totals)
    return (
      <Card title="Human spot check">
        <p className="muted small">The spot check score is not saved on this server.</p>
      </Card>
    );
  const missed = spotcheck.unflagged.missed ?? 0;
  const unflagged = missed + (spotcheck.unflagged.fine ?? 0);
  const notes = Object.entries(spotcheck.missed_notes);
  return (
    <Card title="Human spot check" aside={<span className="muted small">real listings</span>}>
      <p className="small muted" style={{ margin: "0 0 8px" }}>
        A person looked at the photos and judged each audit finding. {totals.correct} of {totals.judged} were right
        {totals.unsure > 0 && ` (${totals.unsure} “not sure” left out)`}.
      </p>
      <table className="kv">
        <thead>
          <tr>
            <th>Finding</th>
            <th>Right</th>
            <th>Wrong</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(spotcheck.precision_by_rule).map(([key, r]) => (
            <tr key={key}>
              <td>{SPOT_RULES[key] ?? key}</td>
              <td className="num">{r.correct ?? 0}</td>
              <td className="num">{r.wrong ? <Badge tone="att">{r.wrong}</Badge> : <span className="muted">0</span>}</td>
            </tr>
          ))}
          <tr className="total">
            <td>All photo findings</td>
            <td className="num">{totals.correct}</td>
            <td className="num">{totals.wrong}</td>
          </tr>
        </tbody>
      </table>
      {unflagged > 0 && (
        <div className="how small" style={{ marginTop: 12 }}>
          <p>
            <b>Listings the audit did not flag:</b> the person saw a problem in {missed} of {unflagged}.
          </p>
          {notes.length > 0 && (
            <details>
              <summary className="muted">What they noted</summary>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {notes.map(([item, note]) => (
                  <li key={item}>
                    <span className="muted">{item}: </span>
                    {note}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Card>
  );
}
