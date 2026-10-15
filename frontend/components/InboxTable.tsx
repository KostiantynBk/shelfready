"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, STATUS, type Summary } from "@/lib/api";
import { Badge, EmptyState } from "./ui";

export function InboxTable() {
  const [rows, setRows] = useState<Summary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Summary[]>("/api/submissions")
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <EmptyState heading="The API is not reachable">{error}</EmptyState>;
  if (!rows) return <p className="muted cb">Loading submissions…</p>;

  const order = { needs_review: 0, waiting_supplier: 1, ready: 2, published: 3 };
  const sorted = [...rows].sort((a, b) => order[a.status] - order[b.status] || a.id.localeCompare(b.id));
  return (
    <table className="inbox">
      <thead>
        <tr>
          <th>Product</th>
          <th>Supplier</th>
          <th>Received</th>
          <th>Status</th>
          <th>Open findings</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr key={row.id}>
            <td>
              <Link href={`/products/${row.id}`} className="prod">
                {row.thumb_url ? (
                  // eslint-disable-next-line @next/next/no-img-element -- public ABO photo, no optimisation needed
                  <img src={row.thumb_url} alt="" width={40} height={40} />
                ) : (
                  <span className="noimg" />
                )}
                <span>
                  <b>{row.model}</b>
                  <span className="small muted">{row.product_name}</span>
                </span>
              </Link>
            </td>
            <td>{row.supplier}</td>
            <td className="num">{row.received}</td>
            <td>
              <Badge tone={STATUS[row.status].tone}>{STATUS[row.status].label}</Badge>
            </td>
            <td>
              <span className="counts">
                {row.open.blocking > 0 && <Badge tone="crit">{row.open.blocking} blocking</Badge>}
                {row.open.review > 0 && <Badge tone="att">{row.open.review} to review</Badge>}
                {row.open.info > 0 && <Badge>{row.open.info} notes</Badge>}
                {row.open.blocking + row.open.review + row.open.info === 0 && <span className="muted">None</span>}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
