import type { Metadata } from "next";
import { Card, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <div className="stack">
      <PageHeader title="Settings" meta="Shop languages, model endpoint and Evolus connection." />
      <Card title="Shop">
        <p className="muted">Casa Nord · Barcelona · furniture</p>
      </Card>
    </div>
  );
}
