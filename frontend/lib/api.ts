// Client-side access to the FastAPI backend through the /backend/* rewrite (next.config.ts).
import type { Tone } from "@/components/ui";

export const API = "/backend";

export type Status = "needs_review" | "waiting_supplier" | "ready" | "published";
export type Severity = "blocking" | "review" | "info";
export type BBox = [number, number, number, number]; // x0, y0, x1, y1 in 0-1, origin top-left

export type Evidence = {
  source: "pdf" | "photo" | "label" | "drawing";
  file: string;
  page: number | null;
  bbox: BBox | null;
  text: string | null;
  note: string | null;
};

export type Decision = {
  action: "use_spec" | "use_found" | "edit" | "accept" | "ask_supplier";
  value?: string | null;
  note?: string | null;
};

export type Issue = {
  id: string;
  rule: string;
  variant: string | null;
  type: "missing" | "contradictory" | "compliance" | "quality" | "translation";
  severity: Severity;
  return_risk: string;
  title: string;
  detail: string;
  attribute: string;
  spec_value: string | null;
  found_value: string | null;
  proposed_value: string | null;
  file: string | null;
  evidence: Evidence[];
  decision: Decision | null;
  decided_at: string | null;
  auto: boolean; // settled by the supplier's own photos or drawing; nobody has decided it
};

export type PhotoBox = { label: string; box_2d: [number, number, number, number] }; // Gemma: y0, x0, y1, x1 in 0-1000

export type FileEntry = {
  name: string;
  kind: string; // spec | photo | care_label | dimension_drawing
  url: string;
  thumb_url?: string;
  analysis?: { role: string; product_type: string; colour_family: string; boxes: PhotoBox[] } | null;
  size?: [number, number];
  low_res?: boolean;
  pages?: { number: number; url: string; width_pt: number; height_pt: number }[];
  reading?: Record<string, unknown> | null; // what a dimension drawing or care label says
};

export type ListingCopy = {
  title: string;
  bullets: string[];
  description: string;
  colours: { code: string; name: string }[];
  safety_warnings: string[];
  alt_texts: { file: string; text: string }[];
};

export type Translation = {
  copy: ListingCopy;
  passed: boolean;
  qa: {
    numbers_ok: boolean;
    units_ok: boolean;
    protected_ok: boolean;
    glossary_ok: boolean;
    backtranslation_ok: boolean | null;
    problems: string[];
  };
};

export type Product = {
  id: string;
  model: string;
  product_name: string | null;
  supplier: string;
  category: string | null;
  email: { from: string; date: string; subject: string; body: string; attachments: string[] };
  status: Status;
  published_at: string | null;
  files: FileEntry[];
  spec: Record<string, unknown>;
  spec_evidence: Record<string, Evidence>;
  issues: Issue[];
  facts: Record<string, unknown>;
  held_back: { attribute: string; variant: string | null; reason: string; asked_supplier: boolean }[];
  copy: {
    en: ListingCopy;
    translations: Record<string, Translation>;
    photos: { file: string }[];
    protected: string[];
    model: string;
  } | null;
  copy_state: "ready" | "writing" | "needs_model" | "no_spec";
  checklist: { code: string; label: string; law: string; ok: boolean }[];
  events: { at: string; kind: string; detail: string }[];
  model_used: string | null;
};

export type Summary = {
  id: string;
  model: string;
  product_name: string | null;
  supplier: string;
  received: string;
  subject: string;
  category: string | null;
  status: Status;
  open: Record<Severity, number>;
  thumb_url: string | null;
};

// Catalogue health audit of existing listings (GET /api/audit, data/results/audit.json)
export type AuditIssue = Pick<
  Issue,
  "rule" | "type" | "severity" | "return_risk" | "title" | "detail" | "attribute" | "spec_value" | "found_value" | "file" | "evidence"
>;

export type AuditListing = {
  item_id: string;
  domain: string; // marketplace, e.g. amazon.de
  category: string;
  name: string;
  score: number;
  photos_analysed: number;
  issues: AuditIssue[];
  images: { image_id: string; role: string; thumb_url: string; url: string }[];
};

export type AuditSummary = {
  listings: number;
  photos_analysed: number;
  health_score: number;
  listings_with_blocking_issues: number;
  issues_by_rule: Record<string, number>;
  score_by_category: Record<string, number>;
  score_by_marketplace: Record<string, number>;
  how_calculated: string;
};

export type AuditReport = { summary: AuditSummary; listings: AuditListing[] };

// Measured results (GET /api/insights)
export type RuleStats = {
  planted: number;
  found: number;
  flags: number;
  correct_flags: number;
  precision: number | null;
  recall: number | null;
};

export type EvalReport = {
  products: number;
  evaluated: number;
  precision: number | null;
  recall: number | null;
  clean_products: number;
  clean_products_with_false_alarms: number;
  per_rule: Record<string, RuleStats>;
  misses: { product: string; rule: string; variant: string | null; file: string | null }[];
  false_alarms: { product: string; rule: string; variant: string | null; file: string | null; title: string }[];
};

export type SpotcheckScore = {
  precision_by_rule: Record<string, { correct?: number; wrong?: number; unsure?: number; precision: number | null }>;
  precision_photo_findings: number | null;
  unflagged: { fine?: number; missed?: number; unsure?: number; miss_rate: number | null };
  missed_notes: Record<string, string>;
};

export type Insights = {
  eval: EvalReport | null;
  audit: AuditSummary | null;
  spotcheck: SpotcheckScore | null;
  model_calls: { live: number; median_latency_s: number | null; tokens_in: number; tokens_out: number };
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = (await response.json()).detail;
    } catch {}
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

/** Backend file URLs are relative ("/files/…"); public ABO photos are absolute. */
export function fileUrl(url: string): string {
  return url.startsWith("http") ? url : `${API}${url}`;
}

export const STATUS: Record<Status, { label: string; tone: Tone }> = {
  needs_review: { label: "Needs review", tone: "att" },
  waiting_supplier: { label: "Waiting for supplier", tone: "info" },
  ready: { label: "Ready to publish", tone: "succ" },
  published: { label: "Published", tone: "succ" },
};

export const LANGUAGE_NAMES: Record<string, string> = {
  en: "English",
  es: "Spanish",
  it: "Italian",
  de: "German",
  fr: "French",
  pt: "Portuguese",
  pl: "Polish",
  uk: "Ukrainian",
};
