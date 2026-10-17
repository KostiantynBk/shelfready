import type { Metadata } from "next";
import { Suspense } from "react";
import { ProductPage } from "@/components/shop/ProductPage";

export const metadata: Metadata = { title: { absolute: "Casa Nord" } };

export default function ShopProductPage({ params }: PageProps<"/shop/[id]">) {
  return (
    <Suspense
      fallback={
        <div className="shop">
          <p className="shop-empty">Loading…</p>
        </div>
      }
    >
      <ProductPage params={params} />
    </Suspense>
  );
}
