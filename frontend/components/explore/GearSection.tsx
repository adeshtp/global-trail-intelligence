"use client";

import { missingEvidenceText } from "@/app/explore/helpers";
import type { TrailIntelligenceResponse } from "@/app/explore/types";

type GearSectionProps = {
  selectedIntelligence: TrailIntelligenceResponse | null;
};

export default function GearSection({
  selectedIntelligence,
}: GearSectionProps) {
  return (
    <>
      <section id="gear" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

        <p className="text-[13px] uppercase tracking-[0.2em] text-white/60">
          Preparation
        </p>


        <h3 className="mt-3 text-[22px] font-semibold">
          What to bring
        </h3>

        {selectedIntelligence ? (
          <>
            <p className="mt-2 max-w-[70ch] text-[13px] leading-6 text-white/60">
              Built from this route&apos;s measured length, ascent,
              steepest section and recorded surface, plus the weather
              observed on it right now. Each item appears because
              something about this route asked for it.
            </p>

            {selectedIntelligence.gear.activity ? (
              <p className="mt-3 max-w-[70ch] rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[13px] leading-5 text-white/75">
                <span className="font-semibold text-white/85">
                  {selectedIntelligence.gear.activity.label}.
                </span>{" "}
                {selectedIntelligence.gear.activity.reasons.join("; ")}.
              </p>
            ) : null}

            {(
              [
                ["essential", "Essential"],
                ["recommended", "Recommended"],
                ["conditional", "Only if relevant"],
              ] as const
            ).map(([tier, label]) => {
              const group =
                selectedIntelligence.gear.items.filter(
                  (item) => item.priority === tier
                );
              if (group.length === 0) {
                return null;
              }
              return (
                <div key={tier} className="mt-5">
                  <p className="text-[13px] font-semibold uppercase tracking-[0.16em] text-white/60">
                    {label}
                  </p>
                  <ul className="mt-2 space-y-1.5">
                    {group.map((item) => (
                      <li
                        key={`${item.category}-${item.item}`}
                        className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3"
                      >
                        <p className="text-[13px] font-semibold text-white/90">
                          {item.item}
                        </p>
                        <p className="mt-1 text-[13px] leading-5 text-white/70">
                          {item.reason}
                        </p>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}

            {selectedIntelligence.gear.missing_evidence.length > 0 ? (
              <p className="mt-4 text-[13px] leading-5 text-amber-200/80">
                Not available for this route:{" "}
                {missingEvidenceText(
                  selectedIntelligence.gear.missing_evidence
                )}
                . Recommendations that would have used them were left
                out.
              </p>
            ) : null}
          </>
        ) : (
          <p className="mt-5 text-sm leading-7 text-white/70">
            Select a verified trail to see the preparation its measured
            route and current conditions actually justify.
          </p>
        )}

      </section>
    </>
  );
}
