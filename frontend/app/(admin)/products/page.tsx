import type { Metadata } from "next";
import { Card, EmptyState, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "Products" };

export default function ProductsPage() {
  return (
    <div className="stack">
      <PageHeader title="Products" meta="Every product with its verified attributes, open issues and publish status." />
      <Card>
        <EmptyState heading="No products yet">Products appear here once a supplier submission has been checked.</EmptyState>
      </Card>
    </div>
  );
}
