import { Suspense } from "react";
import { ProductReview } from "@/components/review/ProductReview";

export default function ProductPage({ params }: PageProps<"/products/[id]">) {
  return (
    <Suspense fallback={<p className="muted">Loading product…</p>}>
      <ProductReview params={params} />
    </Suspense>
  );
}
