import type { Metadata } from "next";
import { Card, EmptyState, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Supplier inbox" };

export default function InboxPage() {
  return (
    <div className="stack">
      <PageHeader title="Supplier inbox" meta="New supplier submissions: spec sheets and photos waiting for checks." />
      <Card>
        <EmptyState heading="No submissions yet">Supplier emails arrive here through the Evolus intake workflow.</EmptyState>
      </Card>
    </div>
  );
}
