import type { Metadata } from "next";
import { InboxTable } from "@/components/InboxTable";
import { Card, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Supplier inbox" };

export default function InboxPage() {
  return (
    <div className="stack">
      <PageHeader
        title="Supplier inbox"
        meta="Supplier emails with spec sheets and photos. Every submission has been checked: open the ones that need a decision."
      />
      <Card>
        <InboxTable />
      </Card>
    </div>
  );
}
