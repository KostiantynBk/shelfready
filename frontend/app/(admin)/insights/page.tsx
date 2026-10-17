import type { Metadata } from "next";
import { InsightsReport } from "@/components/InsightsReport";
import { PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Insights" };

export default function InsightsPage() {
  return (
    <div className="stack">
      <PageHeader
        title="Insights"
        meta="Measured, not claimed: how many known problems ShelfReady finds, how often it is right, and what a person found when checking its work."
      />
      <InsightsReport />
    </div>
  );
}
