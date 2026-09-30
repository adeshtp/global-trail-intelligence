"use client";

import { useState } from "react";

import type { ProductSearchResponse } from "@/app/explore/types";

/**
 * One shopping card for one gear requirement.
 *
 * The card arrives already decided by the backend and is rendered as
 * given, so the interface never re-classifies anything:
 *
 *   direct_product    name, picture and link all come from one real
 *                     result the provider returned
 *   shopping_fallback a category card: the gear item the route asked for,
 *                     a category picture, and a real destination
 *
 * On a fallback card the picture represents the CATEGORY, which is exactly
 * what the card claims to be, so it is never a product photo standing in
 * for a product. No price, rating or stock is shown: the source does not
 * return them and they are never invented.
 *
 * `referrerPolicy="no-referrer"` matters because retailer CDNs commonly
 * refuse hotlinked images when the referrer is another site.
 */
export default function ProductCard({
  card,
}: {
  card: NonNullable<
    ProductSearchResponse["groups"][number]["card"]
  >;
}) {
  const [imageBroken, setImageBroken] =
    useState(false);

  const isProduct = card.mode === "direct_product";
  const showImage = Boolean(card.image) && !imageBroken;

  return (
    <a
      href={card.url}
      target="_blank"
      rel="noreferrer"
      className="group flex flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.02] transition hover:border-emerald-300/35 hover:bg-white/[0.04]"
    >
      {showImage ? (
        // A fixed aspect-ratio area with `object-contain`, not a fixed
        // height with `object-cover`. Providers return whatever shape the
        // source has - square product shots, wide category banners, tall
        // portraits - and `cover` cropped roughly 40% off a square image at
        // this card width. Containing the image keeps the whole product
        // visible and cannot distort it. The small padding means the
        // leftover space reads as deliberate letterbox rather than a
        // cropping bug.
        <div className="flex aspect-[4/3] w-full items-center justify-center bg-white/[0.03] p-1.5">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={card.image ?? undefined}
            alt={
              isProduct
                ? card.name
                : `${card.gear_item} — category image`
            }
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setImageBroken(true)}
            className="max-h-full max-w-full rounded-lg object-contain"
          />
        </div>
      ) : (
        // Same box as the image case so cards with and without a photo keep
        // a consistent height in the grid row.
        <div className="flex aspect-[4/3] w-full items-center justify-center bg-white/[0.03] p-3 text-[12px] text-white/55">
          {card.gear_item}
        </div>
      )}

      <div className="flex flex-1 flex-col p-3">
        <p className="line-clamp-2 text-[13px] font-semibold leading-4 text-white/85">
          {card.name}
        </p>

        {card.retailer ? (
          <p className="mt-1 text-[12px] text-white/60">
            {card.retailer}
          </p>
        ) : null}

        {card.description ? (
          <p className="mt-1.5 line-clamp-2 text-[12px] leading-4 text-white/55">
            {card.description}
          </p>
        ) : null}

        <p className="mt-auto pt-2 text-[12px] font-medium text-emerald-200/85 group-hover:text-emerald-200">
          {card.cta} ↗
        </p>
      </div>
    </a>
  );
}
