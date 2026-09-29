"use client";

import { type Dispatch, type SetStateAction } from "react";

import type { SelectedTrail } from "@/app/explore/types";

type SectionNavProps = {
  activeSection: string;
  selectedTrailGeometry: SelectedTrail | null;
  setActiveSection: Dispatch<SetStateAction<string>>;
};

export default function SectionNav({
  activeSection,
  selectedTrailGeometry,
  setActiveSection,
}: SectionNavProps) {
  return (
    <>
      {selectedTrailGeometry && (
        <div className="sticky top-0 z-30 mx-auto w-full max-w-[1440px] px-6 pt-3 md:px-10 lg:px-14">
          <nav
            aria-label="Selected trail sections"
            className="flex items-center gap-1 overflow-x-auto rounded-2xl border border-white/10 bg-[#0b1724] px-2 py-1.5 text-[11px] shadow-[0_8px_24px_rgba(0,0,0,0.28)]"
          >
            {[
              ["trail-discovery", "Trail"],
              ["elevation", "Elevation"],
              ["conditions", "Weather"],
              ["suitability", "Suitability"],
              ["gear", "Gear"],
              ["products", "Products"],
              ["assistant", "Assistant"],
            ].map(([target, label]) => {
              const isActive =
                activeSection === target;
              return (
                <button
                  key={target}
                  type="button"
                  aria-current={
                    isActive
                      ? "true"
                      : undefined
                  }
                  onClick={() => {
                    const element =
                      document.getElementById(
                        target
                      );
                    element?.scrollIntoView({
                      behavior: "smooth",
                      block: "start",
                    });
                    setActiveSection(target);
                  }}
                  className={`whitespace-nowrap rounded-xl px-3 py-2 font-semibold transition ${
                    isActive
                      ? "bg-sky-300/12 text-sky-200"
                      : "text-white/50 hover:bg-white/[0.06] hover:text-white/85"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </nav>
        </div>
      )}
    </>
  );
}
