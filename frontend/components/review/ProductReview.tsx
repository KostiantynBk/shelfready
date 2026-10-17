"use client";

/* eslint-disable @next/next/no-img-element -- supplier files and public ABO photos, shown with evidence overlays */
import Link from "next/link";
import { use, useEffect, useState } from "react";
import {
  api,
  ApiError,
  fileUrl,
  LANGUAGE_NAMES,
  STATUS,
  type BBox,
  type Decision,
  type Evidence,
  type FileEntry,
  type Issue,
  type ListingCopy,
  type Product,
} from "@/lib/api";
import { Badge, Card, type Tone } from "../ui";

const SEVERITY: Record<Issue["severity"], { label: string; tone: Tone }> = {
  blocking: { label: "Blocking", tone: "crit" },
  review: { label: "To review", tone: "att" },
  info: { label: "Note", tone: "info" },
};

const FACT_LABELS: Record<string, string> = {
  brand: "Brand",
  model: "Model",
  product_type: "Type",
  width_cm: "Width",
  depth_cm: "Depth",
  height_cm: "Height",
  seat_height_cm: "Seat height",
  seat_depth_cm: "Seat depth",
  max_load_kg: "Maximum load",
  weight_kg: "Weight",
  legs_or_base: "Legs / base",
  upholstery: "Upholstery",
  care: "Care",
  country_of_origin: "Made in",
};

type Action = { label: string; decision?: Decision; edit?: boolean; primary?: boolean };

/** The choices a person has for one finding. */
function actionsFor(issue: Issue): Action[] {
  const found = issue.found_value ?? issue.proposed_value;
  if (issue.type === "translation") return [{ label: "I checked it", decision: { action: "accept" }, primary: true }];
  if (issue.attribute === "ean")
    return [
      { label: "Ask supplier", decision: { action: "ask_supplier" }, primary: true },
      { label: "Enter EAN…", edit: true },
    ];
  if (issue.severity === "info")
    return [found ? { label: `Use ${found}`, decision: { action: "use_found" } } : { label: "OK", decision: { action: "accept" } }];
  const actions: Action[] = [];
  if (found) actions.push({ label: `Use ${found}`, decision: { action: "use_found" } });
  if (issue.type === "contradictory" && issue.spec_value) actions.push({ label: "Keep supplier's", decision: { action: "use_spec" } });
  if (issue.rule === "D7") actions.push({ label: "Use our checked translations", decision: { action: "accept" }, primary: true });
  else if ((issue.type === "compliance" || issue.type === "quality") && issue.severity !== "blocking")
    actions.push({ label: "Accept", decision: { action: "accept" } });
  actions.push({ label: "Ask supplier", decision: { action: "ask_supplier" }, primary: issue.rule !== "D7" });
  return actions;
}

/** One settled finding as a short phrase, e.g. "colour pink (photo, ROS)". */
function settledText(issue: Issue): string {
  const source = issue.evidence.find((e) => e.source !== "pdf")?.source ?? "photo";
  return `${issue.attribute} ${issue.found_value ?? ""} (${source}${issue.variant ? `, ${issue.variant}` : ""})`;
}

function describe(decision: Decision, issue: Issue): string {
  const found = decision.value ?? issue.found_value ?? issue.proposed_value;
  switch (decision.action) {
    case "use_found":
      return `Using ${found}`;
    case "use_spec":
      return "Keeping the supplier's value";
    case "edit":
      return `Set to ${decision.value}`;
    case "ask_supplier":
      return "Asked the supplier";
    default:
      return "Accepted";
  }
}

export function ProductReview({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [shownFile, setShownFile] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSettled, setShowSettled] = useState(false);

  useEffect(() => {
    api<Product>(`/api/products/${id}`)
      .then(setProduct)
      .catch((e: Error) => setError(e.message));
  }, [id]);

  const writing = product?.copy_state === "writing";
  useEffect(() => {
    if (!writing) return; // the copy is being written in the background: refresh until it is saved
    const timer = setInterval(() => {
      api<Product>(`/api/products/${id}`)
        .then(setProduct)
        .catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [writing, id]);

  async function call(path: string, init: RequestInit) {
    setBusy(true);
    setBlocked(null);
    try {
      setProduct(await api<Product>(path, init));
      setError(null);
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : null;
      if (detail && typeof detail === "object" && "reasons" in detail) setBlocked((detail as { reasons: string[] }).reasons);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const decide = (issue: Issue, decision: Decision) =>
    call(`/api/products/${id}/issues/${encodeURIComponent(issue.id)}/decision`, {
      method: "POST",
      body: JSON.stringify(decision),
    });
  const reopen = (issue: Issue) =>
    call(`/api/products/${id}/issues/${encodeURIComponent(issue.id)}/decision`, { method: "DELETE" });

  if (error && !product) return <p className="muted">Could not load the product: {error}</p>;
  if (!product) return <p className="muted">Loading product…</p>;

  const open = product.issues.filter((i) => !i.decision && i.severity !== "info");
  const notes = product.issues.filter((i) => !i.decision && i.severity === "info");
  const asked = product.issues.filter((i) => i.decision?.action === "ask_supplier");
  const settled = product.issues.filter((i) => i.auto);
  const decided = product.issues.filter((i) => i.decision && i.decision.action !== "ask_supplier" && !i.auto);
  const current = product.issues.find((i) => i.id === selected) ?? open[0] ?? asked[0] ?? notes[0] ?? null;
  const photos = product.files.filter((f) => f.kind !== "spec").length;
  const status = STATUS[product.status];
  const variants = (product.spec.variants as { code: string; colour_name: string }[] | undefined) ?? [];
  const colourOf = Object.fromEntries(variants.map((v) => [v.code, v.colour_name]));
  const row = (issue: Issue) => (
    <IssueRow
      key={issue.id}
      issue={issue}
      variant={issue.variant ? `${colourOf[issue.variant] ?? ""} · ${issue.variant}` : null}
      on={issue.id === current?.id}
      busy={busy}
      onSelect={() => {
        setSelected(issue.id);
        setShownFile(null);
      }}
      onDecide={(d) => decide(issue, d)}
      onReopen={() => reopen(issue)}
    />
  );

  return (
    <div>
      <div className="ph">
        <Link href="/inbox" className="back" aria-label="Back to the inbox">
          ←
        </Link>
        <h1>{product.model}</h1>
        <Badge tone={status.tone}>{status.label}</Badge>
        <div className="acts">
          <button
            className="btn pri"
            disabled={busy || product.status === "published"}
            onClick={() => call(`/api/products/${id}/publish`, { method: "POST" })}
          >
            {product.status === "published" ? "Published" : "Publish"}
          </button>
        </div>
      </div>
      <p className="meta">
        {product.supplier} · emailed {product.email.date} · spec sheet + {photos} images
        {product.category ? ` · ${product.category}` : ""} · read by {product.model_used ?? "the model"}
      </p>

      <StatusBanner product={product} open={open.length} asked={asked.length} blocked={blocked} error={error} />

      <div className="grid review">
        <div className="stack">
          <EvidenceViewer product={product} issue={current} shownFile={shownFile} onShowFile={setShownFile} />
          <CopyPanel product={product} busy={busy} onWrite={() => call(`/api/products/${id}/copy`, { method: "POST" })} />
          <FactsCard product={product} />
        </div>
        <div className="stack">
          <Card title="Needs your decision" aside={<span className="muted">{open.length}</span>}>
            {open.length === 0 && <p className="muted">Nothing left to decide.</p>}
            {open.map(row)}
          </Card>
          {asked.length > 0 && (
            <Card title="Waiting for the supplier" aside={<span className="muted">{asked.length}</span>}>
              {asked.map(row)}
              <SupplierEmail productId={id} events={product.events} />
            </Card>
          )}
          {settled.length > 0 && (
            <Card title="Checked from evidence" aside={<span className="muted">{settled.length}</span>}>
              <p className="small">
                Checked: {settled.map(settledText).join(", ")}.{" "}
                <button className="btn sm plain" onClick={() => setShowSettled((shown) => !shown)}>
                  {showSettled ? "Hide" : "Change"}
                </button>
              </p>
              {showSettled && settled.map(row)}
            </Card>
          )}
          <Checklist product={product} />
          <Languages product={product} />
          {notes.length > 0 && (
            <Card title="Notes" aside={<span className="muted">{notes.length}</span>}>
              {notes.map(row)}
            </Card>
          )}
          {decided.length > 0 && (
            <Card title="Decided" aside={<span className="muted">{decided.length}</span>}>
              {decided.map(row)}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function SupplierEmail({ productId, events }: { productId: string; events: Product["events"] }) {
  const last = [...events].reverse().find((e) => e.kind === "supplier_email")?.detail ?? null;
  const [email, setEmail] = useState<string | null>(last);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function write() {
    setBusy(true);
    setNote(null);
    try {
      setEmail((await api<{ email: string }>(`/api/products/${productId}/supplier-email`, { method: "POST" })).email);
    } catch (e) {
      setNote(e instanceof ApiError && typeof e.detail === "string" ? e.detail : "Could not write the email.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ padding: "12px 16px" }}>
      <div className="acts-row">
        <button className="btn sm pri" disabled={busy} onClick={write}>
          {busy ? "Writing…" : email ? "Write it again" : "Write the email to the supplier"}
        </button>
        {email && (
          <button className="btn sm" onClick={() => navigator.clipboard.writeText(email)}>
            Copy
          </button>
        )}
      </div>
      {note && <p className="small muted">{note}</p>}
      {email && (
        <pre className="small" style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", margin: "10px 0 0" }}>
          {email}
        </pre>
      )}
      <p className="small muted">Written by Evolus (email AI Tool) from the open questions above.</p>
    </div>
  );
}

function StatusBanner({
  product,
  open,
  asked,
  blocked,
  error,
}: {
  product: Product;
  open: number;
  asked: number;
  blocked: string[] | null;
  error: string | null;
}) {
  if (blocked)
    return (
      <div className="banner crit">
        <div>
          <b>Publishing is blocked</b>
          <ul className="reasons">
            {blocked.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      </div>
    );
  if (error)
    return (
      <div className="banner crit">
        <div>
          <b>Something went wrong</b>
          {error}
        </div>
      </div>
    );
  if (product.status === "published")
    return (
      <div className="banner succ">
        <div>
          <b>Published</b>On {product.published_at?.slice(0, 16).replace("T", " ")} UTC, with every check passed.
        </div>
      </div>
    );
  if (product.status === "needs_review")
    return (
      <div className="banner att">
        <div>
          <b>
            {open} {open === 1 ? "finding needs" : "findings need"} your decision
          </b>
          The supplier&apos;s data contradicts the photos, labels or drawings, or a legal item is missing. Nothing is
          published until a person has decided.
        </div>
      </div>
    );
  if (product.status === "waiting_supplier")
    return (
      <div className="banner info">
        <div>
          <b>Waiting for the supplier</b>
          {asked} {asked === 1 ? "question is" : "questions are"} open with the supplier.
        </div>
      </div>
    );
  return (
    <div className="banner succ">
      <div>
        <b>Ready to publish</b>Every finding is decided and every check passes.
      </div>
    </div>
  );
}

function IssueRow({
  issue,
  variant,
  on,
  busy,
  onSelect,
  onDecide,
  onReopen,
}: {
  issue: Issue;
  variant: string | null;
  on: boolean;
  busy: boolean;
  onSelect: () => void;
  onDecide: (d: Decision) => void;
  onReopen: () => void;
}) {
  const severity = SEVERITY[issue.severity];
  const found = issue.found_value ?? issue.proposed_value;
  const decision = issue.auto ? null : issue.decision;
  return (
    <div className={on ? "issue on" : "issue"} onClick={onSelect}>
      <div className="row">
        <b>{issue.title}</b>
        {issue.decision?.action === "ask_supplier" ? (
          <Badge tone="info">Asked</Badge>
        ) : issue.auto ? (
          <Badge tone="succ">Checked</Badge>
        ) : (
          <Badge tone={decision ? "succ" : severity.tone}>{decision ? "Decided" : severity.label}</Badge>
        )}
      </div>
      {variant && <div className="small variant">Only this colour: {variant}</div>}
      {(issue.spec_value || found) && (
        <div className="vs">
          <div>
            <div className="small muted">Supplier&apos;s spec</div>
            {issue.spec_value ?? "—"}
          </div>
          {found && (
            <div>
              <div className="small muted">{issue.found_value ? "Photos, label or drawing" : "Proposed"}</div>
              {found}
            </div>
          )}
        </div>
      )}
      {(on || (!issue.spec_value && !found)) && <p className="small muted detail">{issue.detail}</p>}
      {decision ? (
        <div className="acts-row">
          <span className="small">{describe(decision, issue)}</span>
          <button
            className="btn sm plain"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              onReopen();
            }}
          >
            Undo
          </button>
        </div>
      ) : (
        <div className="acts-row">
          {actionsFor(issue).map((action) => (
            <button
              key={action.label}
              className={action.primary ? "btn sm pri" : "btn sm"}
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                if (action.edit) {
                  const value = window.prompt(`${issue.title}: enter the correct value`);
                  if (value) onDecide({ action: "edit", value: value.trim() });
                } else if (action.decision) onDecide(action.decision);
              }}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ evidence
type Overlay = { bbox: BBox; label?: string; tone: "crit" | "soft" };

function Figure({ src, alt, overlays }: { src: string; alt: string; overlays: Overlay[] }) {
  return (
    <a className="fig" href={src} target="_blank" rel="noreferrer" title="Open full size">
      <img src={src} alt={alt} />
      {overlays.map((o, n) => (
        <span
          key={n}
          className={`hl ${o.tone}`}
          style={{
            left: `${o.bbox[0] * 100}%`,
            top: `${o.bbox[1] * 100}%`,
            width: `${(o.bbox[2] - o.bbox[0]) * 100}%`,
            height: `${(o.bbox[3] - o.bbox[1]) * 100}%`,
          }}
        >
          {o.label && <em>{o.label}</em>}
        </span>
      ))}
    </a>
  );
}

function pdfEvidence(product: Product, issue: Issue | null): Evidence | null {
  if (!issue) return null;
  const own = issue.evidence.find((e) => e.source === "pdf");
  if (own) return own;
  const key = issue.attribute === "ean" && issue.variant ? `ean:${issue.variant}` : issue.attribute;
  return product.spec_evidence[key] ?? null;
}

function EvidenceViewer({
  product,
  issue,
  shownFile,
  onShowFile,
}: {
  product: Product;
  issue: Issue | null;
  shownFile: string | null;
  onShowFile: (name: string) => void;
}) {
  const spec = product.files.find((f) => f.kind === "spec");
  const images = product.files.filter((f) => f.kind !== "spec");
  const pdf = pdfEvidence(product, issue);
  const page = spec?.pages?.[(pdf?.page ?? 1) - 1] ?? spec?.pages?.[0];
  const imageEvidence = issue?.evidence.filter((e) => e.source !== "pdf") ?? [];
  const name = shownFile ?? imageEvidence[0]?.file ?? issue?.file ?? images[0]?.name;
  const file = images.find((f) => f.name === name) ?? images[0];
  const found = issue?.found_value ?? issue?.proposed_value;

  const imageOverlays: Overlay[] = imageEvidence
    .filter((e) => e.file === file?.name && e.bbox)
    .map((e) => ({ bbox: e.bbox as BBox, label: found ?? e.note ?? undefined, tone: "crit" }));
  if (file && imageOverlays.length === 0)
    for (const box of file.analysis?.boxes ?? []) {
      const [y0, x0, y1, x1] = box.box_2d;
      imageOverlays.push({ bbox: [x0 / 1000, y0 / 1000, x1 / 1000, y1 / 1000], tone: "soft" });
    }
  const pdfOverlays: Overlay[] = pdf?.bbox
    ? [{ bbox: pdf.bbox, tone: "crit", label: issue?.spec_value ? `spec: ${issue.spec_value}` : undefined }]
    : [];

  return (
    <Card title={`Evidence${issue ? ` · ${issue.title}` : ""}`}>
      {issue && <p className="small muted detail">{issue.detail}</p>}
      <div className="split">
        <div className="pane">
          <div className="cap">Spec sheet{page ? ` · page ${page.number}` : ""}</div>
          <div className="stage">
            {page ? (
              <Figure src={fileUrl(page.url)} alt="Supplier spec sheet" overlays={pdfOverlays} />
            ) : (
              <span className="muted">No spec sheet</span>
            )}
          </div>
        </div>
        <div className="pane">
          <div className="cap">
            {file?.name}
            {file?.analysis && ` · ${file.analysis.role}, ${file.analysis.colour_family}`}
            {file?.low_res && " · low resolution"}
          </div>
          <div className="stage">
            {file ? (
              <Figure src={fileUrl(file.url)} alt={file.name} overlays={imageOverlays} />
            ) : (
              <span className="muted">No images</span>
            )}
          </div>
          {file && <Reading file={file} attribute={issue?.attribute} />}
        </div>
      </div>
      <div className="thumbs">
        {images.map((f) => (
          <button
            key={f.name}
            className={f.name === file?.name ? "on" : undefined}
            title={f.name}
            onClick={() => onShowFile(f.name)}
          >
            <img src={fileUrl(f.thumb_url ?? f.url)} alt={f.name} />
          </button>
        ))}
      </div>
    </Card>
  );
}

const DRAWING_PARTS: [string, string][] = [
  ["width_cm", "width"],
  ["depth_cm", "depth"],
  ["height_cm", "overall height"],
  ["seat_height_cm", "seat height"],
];

/** What the model read on a dimension drawing or care label; the value the finding is about is marked. */
function Reading({ file, attribute }: { file: FileEntry; attribute?: string }) {
  const r = file.reading;
  if (!r) return null;
  if ("height_cm" in r || "seat_height_cm" in r) {
    const parts = DRAWING_PARTS.filter(([key]) => r[key] != null);
    return (
      <p className="reading small">
        The drawing reads:{" "}
        {parts.map(([key, label], n) => (
          <span key={key}>
            {n > 0 && " · "}
            <span className={attribute && key === `${attribute}_cm` ? "hit" : undefined}>
              {label} {String(r[key])} cm
            </span>
          </span>
        ))}
      </p>
    );
  }
  const bits = [
    r.composition,
    r.wash_max_temp_c ? `wash at ${r.wash_max_temp_c} °C` : null,
    r.dry_clean_only ? "dry clean only" : null,
    r.do_not_wash ? "do not wash" : null,
    r.removable_cover ? "removable cover" : null,
  ].filter(Boolean);
  return <p className="reading small">The care label reads: {bits.join(" · ")}</p>;
}

// ------------------------------------------------------------------------------------------ copy, facts, checks
function CopyPanel({ product, busy, onWrite }: { product: Product; busy: boolean; onWrite: () => void }) {
  const [lang, setLang] = useState("en");
  const copy = product.copy;
  const shown: ListingCopy | undefined = lang === "en" ? copy?.en : copy?.translations[lang]?.copy;
  const heldNames = product.held_back.map(
    (h) => FACT_LABELS[`${h.attribute}_cm`] ?? FACT_LABELS[h.attribute] ?? h.attribute.replaceAll("_", " "),
  );
  return (
    <Card
      title="Shop copy"
      aside={copy && <span className="muted small">written by {copy.model} from verified facts only</span>}
    >
      {heldNames.length > 0 && (
        <p className="small held">
          <b>Held back until decided: </b>
          {heldNames.join(", ")}. The copy leaves these out rather than guess.
        </p>
      )}
      {product.copy_state === "writing" ? (
        <div className="empty">
          <p>
            <b>Writing the copy…</b>
          </p>
          <p className="muted">
            English from the verified facts, then 7 translations, each checked in code. The model takes a few minutes;
            this page updates by itself.
          </p>
        </div>
      ) : !copy ? (
        <div className="empty">
          {lastFailure(product) && <p className="small failure">The last attempt failed: {lastFailure(product)}</p>}
          <p className="muted">
            Your decisions changed the verified facts, so the copy has to be written again (8 languages, checked in
            code; about a minute with the model).
          </p>
          <button className="btn pri" disabled={busy} onClick={onWrite}>
            {busy ? "Writing…" : "Write copy"}
          </button>
        </div>
      ) : (
        <>
          <div className="tabs">
            {["en", ...Object.keys(copy.translations)].map((code) => (
              <button key={code} className={code === lang ? "tab on" : "tab"} onClick={() => setLang(code)}>
                {code.toUpperCase()}
                {code !== "en" && !copy.translations[code].passed && " ⚠"}
              </button>
            ))}
          </div>
          {shown && (
            <div className="copy">
              <h3>{shown.title}</h3>
              <ul>
                {shown.bullets.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
              <p>{shown.description}</p>
              {shown.colours.length > 0 && (
                <p className="small">
                  <b>Colours: </b>
                  {shown.colours.map((c) => `${c.name} (${c.code})`).join(", ")}
                </p>
              )}
              {shown.safety_warnings.length > 0 && (
                <p className="small">
                  <b>Safety: </b>
                  {shown.safety_warnings.join(" ")}
                </p>
              )}
              <details>
                <summary className="small">Alt text for {shown.alt_texts.length} photos</summary>
                <ul className="small">
                  {shown.alt_texts.map((a) => (
                    <li key={a.file}>
                      <span className="muted">{a.file}: </span>
                      {a.text}
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function lastFailure(product: Product): string | null {
  const last = [...product.events].reverse().find((e) => e.kind.startsWith("copy"));
  return last?.kind === "copy_failed" ? last.detail : null;
}

function FactsCard({ product }: { product: Product }) {
  const rows = Object.entries(FACT_LABELS).filter(([key]) => product.facts[key] !== undefined);
  const unit = (key: string) => (key.endsWith("_cm") ? " cm" : key.endsWith("_kg") ? " kg" : "");
  return (
    <Card title="Verified facts" aside={<span className="muted small">what the copy may use</span>}>
      <table>
        <tbody>
          {rows.map(([key, label]) => (
            <tr key={key}>
              <td className="muted">{label}</td>
              <td>
                {String(product.facts[key])}
                {unit(key)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Checklist({ product }: { product: Product }) {
  return (
    <Card title="Before publishing">
      {product.checklist.map((item) => (
        <div key={item.code} className="check">
          <span className={item.ok ? "dot ok" : "dot no"}>{item.ok ? "✓" : "✕"}</span>
          <div>
            {item.label}
            <div className="small muted">{item.law}</div>
          </div>
        </div>
      ))}
    </Card>
  );
}

function Languages({ product }: { product: Product }) {
  const translations = product.copy?.translations ?? {};
  return (
    <Card title="Languages" aside={<span className="muted small">checked in code</span>}>
      <table>
        <tbody>
          {Object.entries(translations).map(([code, t]) => (
            <tr key={code}>
              <td>{LANGUAGE_NAMES[code] ?? code}</td>
              <td style={{ textAlign: "right" }}>
                {t.passed ? (
                  <span className="muted small">
                    Passed checks{t.qa.backtranslation_ok ? " + back-translation" : ""}
                  </span>
                ) : (
                  <Badge tone="warn">{t.qa.problems[0]}</Badge>
                )}
              </td>
            </tr>
          ))}
          {Object.keys(translations).length === 0 && (
            <tr>
              <td className="muted">Translations follow the English copy.</td>
            </tr>
          )}
        </tbody>
      </table>
    </Card>
  );
}
