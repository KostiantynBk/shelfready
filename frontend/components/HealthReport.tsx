"use client";

/* eslint-disable @next/next/no-img-element -- public ABO photos (Amazon Berkeley Objects, CC BY 4.0) */
import { useEffect, useState } from "react";
import { api, type AuditListing, type AuditReport, type Severity } from "@/lib/api";
import { Badge, Card, EmptyState, type Tone } from "./ui";

const SEVERITY: Record<Severity, { label: string; tone: Tone }> = {
  blocking: { label: "Blocking", tone: "crit" },
  review: { label: "To review", tone: "att" },
  info: { label: "Note", tone: "info" },
};
const RANK: Record<Severity, number> = { blocking: 0, review: 1, info: 2 };

const RULES: Record<string, string> = {
  A1: "Wrong category: the photos show another product",
  A2: "Colour name does not match the photos",
  A3: "Sizes in inches on a European shop",
  A4: "Size or colour fields left empty",
  A5: "Rug fibre content missing or unclear",
  A6: "Image too small to zoom",
  A7: "Text or badges on the main image",
  A8: "Main image is not a plain product shot",
  A9: "No photo of the product",
};

const CATEGORIES: Record<string, string> = { chair: "Chairs", sofa: "Sofas", lamp: "Lamps", rug: "Rugs" };
const MARKETS: Record<string, string> = {
  "amazon.de": "Germany",
  "amazon.es": "Spain",
  "amazon.fr": "France",
  "amazon.it": "Italy",
  "amazon.nl": "Netherlands",
  "amazon.se": "Sweden",
  "amazon.co.uk": "United Kingdom",
  "amazon.com": "United States",
};

const PAGE = 25;
type Filter = "all" | Severity;

function worst(listing: AuditListing): Severity {
  return listing.issues.reduce<Severity>((w, i) => (RANK[i.severity] < RANK[w] ? i.severity : w), "info");
}

const count = (n: number, one: string, many: string) => `${n.toLocaleString("en")} ${n === 1 ? one : many}`;

export function HealthReport() {
  const [report, setReport] = useState<AuditReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [rule, setRule] = useState<string | null>(null);
  const [shown, setShown] = useState(PAGE);

  useEffect(() => {
    api<AuditReport>("/api/audit")
      .then(setReport)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error)
    return (
      <Card>
        <EmptyState heading="No audit to show">{error}</EmptyState>
      </Card>
    );
  if (!report) return <p className="muted">Loading the audit…</p>;

  const { summary, listings } = report;
  const flagged = listings
    .filter((l) => l.issues.length > 0)
    .sort((a, b) => a.score - b.score || RANK[worst(a)] - RANK[worst(b)] || a.name.localeCompare(b.name));
  const clean = listings.length - flagged.length;
  const bySeverity = (s: Severity) => flagged.filter((l) => worst(l) === s).length;
  const matching = flagged.filter(
    (l) => (filter === "all" || worst(l) === filter) && (!rule || l.issues.some((i) => i.rule === rule)),
  );
  const pct = (n: number) => `${Math.round((100 * n) / summary.listings)}% of listings`;
  const tabs: [Filter, string, number][] = [
    ["all", "All with findings", flagged.length],
    ["blocking", "Blocking", bySeverity("blocking")],
    ["review", "To review", bySeverity("review")],
    ["info", "Notes only", bySeverity("info")],
  ];
  const pick = (next: Filter, nextRule: string | null) => {
    setFilter(next);
    setRule(nextRule);
    setShown(PAGE);
  };

  return (
    <>
      <section className="card kpis">
        <Kpi label="Health score" value={summary.health_score} unit="/100" note={`average of ${summary.listings} listings`} />
        <Kpi
          label="Listings checked"
          value={summary.listings}
          note={`${summary.photos_analysed.toLocaleString("en")} photos read by the model`}
        />
        <Kpi
          label="Listings with blocking issues"
          value={summary.listings_with_blocking_issues}
          note={`${pct(summary.listings_with_blocking_issues)}: fix these first`}
          crit={summary.listings_with_blocking_issues > 0}
        />
        <Kpi label="Listings with no findings" value={clean} note={pct(clean)} />
      </section>

      <div className="grid side">
        <section className="card">
          <div className="tabs" role="tablist">
            {tabs.map(([key, label, n]) => (
              <button
                key={key}
                role="tab"
                aria-selected={key === filter}
                className={key === filter ? "tab on" : "tab"}
                onClick={() => pick(key, rule)}
              >
                {label} <span className="muted num">{n}</span>
              </button>
            ))}
          </div>
          <div className="filterline small muted">
            {rule ? (
              <>
                Only listings with: <b>{RULES[rule] ?? rule}</b>
                <button className="linkbtn" onClick={() => pick(filter, null)}>
                  Show all
                </button>
              </>
            ) : (
              <>Worst first. A red frame marks the photo a finding is about; click a photo to open it full size.</>
            )}
          </div>
          {matching.slice(0, shown).map((listing) => (
            <ListingRow key={listing.item_id} listing={listing} />
          ))}
          {matching.length === 0 && <EmptyState heading="No listings match this filter" />}
          <div className="listfoot small muted">
            <span>
              Showing {Math.min(shown, matching.length)} of {count(matching.length, "listing", "listings")}
              {filter === "all" && !rule && ` · ${count(clean, "more listing", "more listings")} with no findings (score 100)`}
            </span>
            {shown < matching.length && (
              <button className="btn sm" onClick={() => setShown((n) => n + PAGE)}>
                Show {Math.min(PAGE, matching.length - shown)} more
              </button>
            )}
          </div>
        </section>

        <div className="stack">
          <Card title="Findings by type" aside={<span className="muted small">click to filter</span>}>
            <table className="kv">
              <tbody>
                {Object.entries(summary.issues_by_rule)
                  .sort((a, b) => b[1] - a[1])
                  .map(([code, n]) => (
                    <tr key={code}>
                      <td>
                        <button
                          className={code === rule ? "linkbtn on" : "linkbtn"}
                          onClick={() => pick("all", code === rule ? null : code)}
                        >
                          {RULES[code] ?? code}
                        </button>
                      </td>
                      <td className="num">{n}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </Card>
          <ScoreTable
            title="Score by category"
            scores={summary.score_by_category}
            label={(key) => CATEGORIES[key] ?? key}
            listings={(key) => listings.filter((l) => l.category === key).length}
          />
          <ScoreTable
            title="Score by marketplace"
            scores={summary.score_by_marketplace}
            label={(key) => `${MARKETS[key] ?? key} (${key})`}
            listings={(key) => listings.filter((l) => l.domain === key).length}
          />
          <Card title="How this is calculated">
            <div className="how small">
              <p>
                <b>Score per listing.</b> Every listing starts at 100. Each blocking issue takes off 25 points, each
                issue to review 10, each note 3. The score never goes below 0.
              </p>
              <p>
                <b>Health score.</b> The average of all {summary.listings} listing scores: {summary.health_score}.
              </p>
              <p>
                <b>What the AI does.</b> The model only describes each photo: what product it shows, its main colour,
                and whether it is a plain product shot. Fixed rules then compare that with the listing text, so every
                finding can be explained.
              </p>
              <p>
                <b>Blocking</b> means a legal rule is broken or the product is in the wrong category. <b>To review</b>{" "}
                means it is likely to cause returns or confusion. A <b>note</b> is a small improvement.
              </p>
              <p className="muted">
                Data: {summary.listings} real Amazon listings from the Amazon Berkeley Objects dataset (CC BY 4.0). A
                person checked a sample of the photo findings by hand; see Insights.
              </p>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}

function Kpi({
  label,
  value,
  unit,
  note,
  crit,
}: {
  label: string;
  value: number;
  unit?: string;
  note: string;
  crit?: boolean;
}) {
  return (
    <div className="kpi">
      <div className="lbl">{label}</div>
      <div className={crit ? "val crit" : "val"}>
        {value.toLocaleString("en")}
        {unit && <small>{unit}</small>}
      </div>
      <div className="small muted">{note}</div>
    </div>
  );
}

function ListingRow({ listing }: { listing: AuditListing }) {
  const issues = [...listing.issues].sort((a, b) => RANK[a.severity] - RANK[b.severity]);
  const evidence = new Set(
    listing.issues.flatMap((i) => [...i.evidence.map((e) => e.file), ...(i.file ? [i.file] : [])]),
  );
  const tone = SEVERITY[worst(listing)].tone;
  return (
    <div className="listing">
      <div className="lthumbs">
        {listing.images.map((image, n) => (
          <a
            key={image.image_id}
            href={image.url}
            target="_blank"
            rel="noreferrer"
            className={evidence.has(image.image_id) ? "flag" : undefined}
            title={image.role === "main" ? "Main image" : `Image ${n + 1}`}
          >
            <img
              src={image.thumb_url}
              alt={image.role === "main" ? "Main image" : `Image ${n + 1}`}
              width={52}
              height={52}
              loading="lazy"
            />
          </a>
        ))}
      </div>
      <div className="lbody">
        <div className="lhead">
          <b>{listing.name || listing.item_id}</b>
          <Badge tone={tone}>{listing.score}/100</Badge>
        </div>
        <div className="small muted">
          {CATEGORIES[listing.category] ?? listing.category} · {listing.domain} · {listing.item_id}
        </div>
        <ul className="findings">
          {issues.map((issue, n) => (
            <li key={`${issue.rule}-${n}`}>
              <Badge tone={SEVERITY[issue.severity].tone}>{SEVERITY[issue.severity].label}</Badge>
              <div>
                <div>{issue.title}</div>
                <div className="small muted">{issue.detail}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ScoreTable({
  title,
  scores,
  label,
  listings,
}: {
  title: string;
  scores: Record<string, number>;
  label: (key: string) => string;
  listings: (key: string) => number;
}) {
  return (
    <Card title={title}>
      <table className="kv">
        <thead>
          <tr>
            <th></th>
            <th>Listings</th>
            <th>Score</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(scores)
            .sort((a, b) => a[1] - b[1])
            .map(([key, score]) => (
              <tr key={key}>
                <td>{label(key)}</td>
                <td className="num muted">{listings(key)}</td>
                <td className="num">{score.toFixed(1)}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </Card>
  );
}
