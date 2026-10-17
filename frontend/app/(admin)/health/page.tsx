import type { Metadata } from "next";
import { HealthReport } from "@/components/HealthReport";
import { PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Catalogue health" };

export default function HealthPage() {
  return (
    <div className="stack">
      <PageHeader
        title="Catalogue health"
        meta="Real Amazon listings checked against their own photos and EU listing rules: wrong category, colour, missing sizes and fibre content, weak photos."
      />
      <HealthReport />
    </div>
  );
}
