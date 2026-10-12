import type { Metadata } from "next";
import { Card, EmptyState, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Catalogue health" };

export default function HealthPage() {
  return (
    <div className="stack">
      <PageHeader title="Catalogue health" meta="Audit of your live listings: contradictions, missing safety info, alt-text gaps." />
      <Card>
        <EmptyState heading="No audit yet">Run an audit to check existing listings against their photos.</EmptyState>
      </Card>
    </div>
  );
}
