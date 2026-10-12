import type { Metadata } from "next";
import { Card, EmptyState, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Insights" };

export default function InsightsPage() {
  return (
    <div className="stack">
      <PageHeader title="Insights" meta="Measured, not claimed: detection precision and recall, review rate, cost per 1,000 products." />
      <Card>
        <EmptyState heading="No evaluation run yet">Results appear after the first evaluation on the planted-defect set.</EmptyState>
      </Card>
    </div>
  );
}
