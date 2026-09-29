"use client";

import type { ProductSearchResponse, TrailIntelligenceResponse } from "@/app/explore/types";
import ProductCard from "@/components/explore/ProductCard";

type ProductsSectionProps = {
  handleProductSearch: () => Promise<void>;
  productResults: ProductSearchResponse | null;
  productsError: string | null;
  productsLoading: boolean;
  selectedIntelligence: TrailIntelligenceResponse | null;
};

export default function ProductsSection({
  handleProductSearch,
  productResults,
  productsError,
  productsLoading,
  selectedIntelligence,
}: ProductsSectionProps) {
  return (
    <>
      <section id="products" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

        <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
          Products
        </p>


        <h3 className="mt-3 text-[22px] font-semibold">
          Shop the preparation that matters
        </h3>


        {selectedIntelligence ? (
          <div className="mt-5">
            <p className="max-w-[72ch] text-[12px] leading-6 text-white/40">
              Every item your route asked for has a card here: a
              real product when the search found one, otherwise a
              shopping destination for that kind of gear. Prices,
              ratings and stock are never shown because the source
              does not return them reliably and they are not invented.
            </p>

            <button
              type="button"
              onClick={() => void handleProductSearch()}
              disabled={productsLoading}
              className="mt-4 rounded-xl border border-emerald-300/20 bg-emerald-300/[0.08] px-4 py-2 text-xs font-semibold text-emerald-200 transition hover:bg-emerald-300/[0.14] disabled:cursor-wait disabled:opacity-60"
            >
              {productsLoading
                ? "Searching products…"
                : productResults
                  ? "Search again"
                  : "Find real products"}
            </button>

            {productsError ? (
              <p className="mt-3 text-xs text-amber-200/70">
                {productsError}
              </p>
            ) : null}

            {productResults ? (
              productResults.groups.length > 0 ? (
                <div className="mt-5 space-y-5">
                  {(
                    [
                      ["essential", "Essential gear"],
                      ["recommended", "Recommended"],
                      ["conditional", "Only if relevant"],
                    ] as const
                  ).map(([tier, label]) => {
                    const cards =
                      productResults.groups.filter(
                        (group) =>
                          group.priority === tier &&
                          group.card
                      );
                    if (cards.length === 0) {
                      return null;
                    }
                    return (
                      <div key={tier}>
                        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-white/40">
                          {label}
                        </p>
                        <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                          {cards.map((group) =>
                            group.card ? (
                              <ProductCard
                                key={`${group.category}-${group.item}`}
                                card={group.card}
                              />
                            ) : null
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="mt-4 text-sm text-white/40">
                  None of the preparation items for this trail is a
                  product that can be bought, so no product search was
                  made.
                </p>
              )
            ) : (
              <p className="mt-4 text-sm text-white/40">
                Search for real products matching the preparation
                recommended for this trail.
              </p>
            )}
          </div>
        ) : (
          <p className="mt-5 text-sm text-white/40">
            Select a verified trail to see products for the
            preparation it actually calls for.
          </p>
        )}

      </section>
    </>
  );
}
