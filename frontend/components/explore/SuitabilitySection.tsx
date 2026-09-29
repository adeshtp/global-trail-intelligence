"use client";

import { CONDITION_STATE_LABELS, missingEvidenceText } from "@/app/explore/helpers";
import type { TrailIntelligenceResponse } from "@/app/explore/types";
import { conditionHeadline, observedEvidence } from "@/components/conditionDisplay";
import { difficultyPresentation } from "@/components/difficultyDisplay";
import { selectDecisiveFactors, suitabilityHeadline } from "@/components/suitabilityDisplay";

type SuitabilitySectionProps = {
  selectedIntelligence: TrailIntelligenceResponse | null;
};

export default function SuitabilitySection({
  selectedIntelligence,
}: SuitabilitySectionProps) {
  return (
    <>
      <section id="suitability" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

        <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
          Intelligence
        </p>


        <h3 className="mt-3 text-[22px] font-semibold">
          Difficulty, conditions & suitability
        </h3>


        {selectedIntelligence ? (
          <div className="mt-6 space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              {/*
                Measured route demands are integrated into the
                Difficulty card below as supporting evidence, never
                as a separate competing score. There is exactly one
                difficulty presentation: official when recorded,
                estimated otherwise.
              */}
              {/*
                The official card and the estimated card answer two
                different questions and are never merged. When
                OpenStreetMap records a grade, that card is the answer
                and the estimate stays inside the technical disclosure
                below, so the interface never shows two competing
                difficulty labels.
              */}
              <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
                <p className="text-[10px] uppercase tracking-[0.15em] text-white/30">
                  {selectedIntelligence.difficulty.source.class
                    ? "Official difficulty"
                    : selectedIntelligence.difficulty.ml
                          .available
                      ? "Model-estimated difficulty"
                      : "Difficulty"}
                </p>

                {(() => {
                  const view = difficultyPresentation({
                    recordedGrade:
                      selectedIntelligence.difficulty.source
                        .sac_scale,
                    officialTier:
                      selectedIntelligence.difficulty.source
                        .tier,
                    estimateTier:
                      selectedIntelligence.difficulty.ml
                        .estimate,
                    estimateAvailable:
                      selectedIntelligence.difficulty.ml
                        .available,
                  });
                  return (
                    <>
                      {view.label ? (
                        <p className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-white">
                          {view.label}
                        </p>
                      ) : (
                        <p className="mt-3 text-xl font-semibold text-white/60">
                          Not enough evidence
                        </p>
                      )}

                      <p className="mt-1.5 text-[12px] leading-5 text-white/45">
                        {view.provenance}
                        {view.qualification
                          ? ` ${view.qualification}`
                          : ""}
                      </p>

                      {/*
                        Provenance detail: the recorded grade stays
                        verbatim, and an estimate keeps its native
                        tier plus the product range that tier spans,
                        so the conservative label is visible rather
                        than implied.
                      */}
                      {view.recordedGrade ? (
                        <p className="mt-1 text-[11px] leading-5 text-white/30">
                          Recorded grade {view.recordedGrade}.
                        </p>
                      ) : view.nativeTier ? (
                        <p className="mt-1 text-[11px] leading-5 text-white/30">
                          Model tier {view.nativeTier}
                          {view.estimatedRange
                            ? ` · covers ${view.estimatedRange} recorded grades`
                            : ""}
                          .
                        </p>
                      ) : null}
                    </>
                  );
                })()}
                {/*
                  Route answers are a vote across verified member ways,
                  never a prediction from route totals. The split is
                  shown so a divided route reads as divided, and the
                  majority is never presented as unanimous.
                */}
                {selectedIntelligence.difficulty.ml.aggregation ? (
                  <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-white/30">
                      Route sections scored
                    </p>
                    <p className="mt-1.5 text-[10px] leading-4 text-white/40">
                      {selectedIntelligence.difficulty.ml
                        .aggregation.scored_members ===
                      selectedIntelligence.difficulty.ml
                        .aggregation.total_members
                        ? `All ${selectedIntelligence.difficulty.ml.aggregation.scored_members} verified sections scored individually.`
                        : `${selectedIntelligence.difficulty.ml.aggregation.scored_members} of ${selectedIntelligence.difficulty.ml.aggregation.total_members} verified sections scored individually.`}{" "}
                      {Object.entries(
                        selectedIntelligence.difficulty.ml
                          .aggregation.tier_counts,
                      )
                        .filter(([, count]) => count > 0)
                        .map(
                          ([tier, count]) =>
                            `${count} ${tier}`,
                        )
                        .join(" · ")}
                      .
                    </p>
                    {selectedIntelligence.difficulty.ml
                      .aggregation.members_disagree ? (
                      <p className="mt-1.5 text-[10px] leading-4 text-amber-200/60">
                        Sections of this route disagree about its
                        difficulty, so treat the majority as a hint
                        and check the harder sections before
                        committing to it.
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {/*
                  States what the ESTIMATE did not use. It says
                  nothing about whether the route has elevation: the
                  measured profile can exist and simply not have been
                  fed to the model, so the two facts are kept apart.
                */}
                {!selectedIntelligence.difficulty.source
                  .class &&
                selectedIntelligence.difficulty.ml.available &&
                selectedIntelligence.difficulty.ml
                  .terrain_features_used === false ? (
                  <p className="mt-1.5 text-[11px] leading-5 text-white/35">
                    The estimate did not use this route&apos;s
                    measured elevation; it rests on recorded tags
                    and route shape. Measured terrain is shown above
                    regardless.
                  </p>
                ) : null}
                {/*
                  Measured demands live inside Difficulty as
                  supporting evidence. Each line is a measured
                  quantity from the verified route geometry — what
                  the route asks of a walker — never a second
                  difficulty grade and never combined with the
                  estimate into one score.
                */}
                {selectedIntelligence.route_complexity
                  ?.available &&
                (selectedIntelligence.route_complexity
                  .components ?? []).length > 0 ? (
                  <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-white/30">
                      Measured demands
                    </p>
                    <ul className="mt-2 space-y-1">
                      {selectedIntelligence.route_complexity.components.map(
                        (part) => (
                          <li
                            key={part.component}
                            className="text-[11px] leading-5 text-white/45"
                          >
                            • {part.measured}
                          </li>
                        ),
                      )}
                    </ul>
                    {selectedIntelligence.route_complexity
                      .missing_evidence.length > 0 ? (
                      <p className="mt-1.5 text-[10px] leading-4 text-amber-200/60">
                        Not measured:{" "}
                        {selectedIntelligence.route_complexity.missing_evidence.join(
                          ", ",
                        )}
                        .
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </div>
              <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
                <p className="text-[10px] uppercase tracking-[0.15em] text-white/30">
                  Trail conditions
                </p>
                {(() => {
                  const { headline, tone } =
                    conditionHeadline(
                      selectedIntelligence.condition
                        .status,
                      selectedIntelligence.weather,
                    );
                  const observed = observedEvidence(
                    selectedIntelligence.weather,
                  );
                  const toneClass =
                    tone === "good"
                      ? "text-emerald-200"
                      : tone === "warn"
                        ? "text-amber-200"
                        : tone === "bad"
                          ? "text-rose-200"
                          : "text-white/50";
                  return (
                    <>
                      <p
                        className={`mt-3 text-3xl font-semibold tracking-[-0.03em] ${toneClass}`}
                      >
                        {headline}
                      </p>

                      {/*
                        Observed evidence. Each row is a block-level
                        pair with a real gap and its own line, so a
                        label and its value can never run together
                        visually. Missing values are omitted, never
                        rendered as zero.
                      */}
                      {tone === "neutral" ? (
                        <p className="mt-2 text-[12px] leading-5 text-white/40">
                          Live weather evidence is unavailable
                          for this route right now.
                        </p>
                      ) : observed.length > 0 ? (
                        <ul className="mt-3 space-y-2">
                          {observed.map((row) => (
                            <li
                              key={row.label}
                              className="flex items-baseline justify-between gap-4 text-[12px] leading-5"
                            >
                              <span className="text-white/45">
                                {row.label}
                              </span>
                              <span className="shrink-0 font-semibold text-white/90">
                                {row.value}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-2 text-[12px] leading-5 text-white/40">
                          No observed weather values were
                          returned for this route.
                        </p>
                      )}

                      {/*
                        The inferred statement, explicitly marked as
                        inference. It is a likelihood drawn from the
                        observations above plus route evidence, never
                        a measurement of the ground.
                      */}
                      <p className="mt-3 text-[12px] leading-5 text-white/50">
                        <span className="text-white/35">
                          Inferred:{" "}
                        </span>
                        {
                          selectedIntelligence.condition
                            .summary
                        }
                      </p>
                    </>
                  );
                })()}
              </div>
            </div>

            {/*
              The per-factor evidence behind the inferred condition
              is genuine working, but it is not what a reader needs
              in order to act, so it sits collapsed under the card
              rather than filling the visible area. Missing evidence
              stays visible, because a skipped check is a fact about
              what the user is NOT being told.
            */}
            {(selectedIntelligence.condition.factors ?? [])
              .length > 0 ||
            selectedIntelligence.condition
              .missing_evidence.length > 0 ? (
              <details className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4">
                <summary className="cursor-pointer list-none text-[11px] text-white/40">
                  How this condition was worked out
                </summary>

                {(selectedIntelligence.condition.factors ?? [])
                  .length > 0 ? (
                  <ul className="mt-3 space-y-2">
                    {(
                      selectedIntelligence.condition
                        .factors ?? []
                    ).map((factor) => (
                      <li
                        key={`${factor.factor}-${factor.state}`}
                        className="space-y-1 text-[11px] leading-5 text-white/45"
                      >
                        <span className="mr-2 inline-block rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[9px] uppercase tracking-[0.1em] text-white/50">
                          {CONDITION_STATE_LABELS[
                            factor.state
                          ] ?? factor.state.replaceAll("_", " ")}
                        </span>
                        <span>
                          {factor.detail}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}

                {selectedIntelligence.condition
                  .missing_evidence.length > 0 ? (
                  <p className="mt-3 text-[11px] leading-5 text-amber-200/60">
                    Not available for this route:{" "}
                    {missingEvidenceText(
                      selectedIntelligence.condition
                        .missing_evidence
                    )}
                    . Those checks were skipped rather than guessed.
                  </p>
                ) : null}
              </details>
            ) : null}

            <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
              <p className="text-[10px] uppercase tracking-[0.15em] text-white/30">
                Suitability
              </p>
              {(() => {
                const { headline, tone } =
                  suitabilityHeadline(
                    selectedIntelligence.suitability
                      .level,
                    selectedIntelligence.suitability
                      .assessed_over,
                  );
                const decisive = selectDecisiveFactors(
                  selectedIntelligence.suitability
                    .factors,
                );
                const toneClass =
                  tone === "good"
                    ? "text-emerald-200"
                    : tone === "warn"
                      ? "text-amber-200"
                      : tone === "bad"
                        ? "text-rose-200"
                        : "text-white";
                return (
                  <>
                    <p
                      className={`mt-3 text-3xl font-semibold tracking-[-0.03em] ${toneClass}`}
                    >
                      {headline}
                    </p>
                    <p className="mt-2 text-[12px] leading-5 text-white/50">
                      {
                        selectedIntelligence
                          .suitability.headline
                      }
                    </p>
                    {decisive.length > 0 ? (
                      <ul className="mt-3 space-y-2">
                        {decisive.map((factor) => (
                          <li
                            key={factor.factor}
                            className="text-[12px] leading-5 text-white/60"
                          >
                            • {factor.evidence}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </>
                );
              })()}
              {/*
                Scope only. The full factor list is a duplicate of the
                reasons already shown above plus the sections they come
                from, so it is not repeated here.
              */}
              <p className="mt-3 text-[10px] leading-4 text-white/25">
                {selectedIntelligence.suitability.assessment_scope}
              </p>
            </div>

            {selectedIntelligence.difficulty.ml.available ||
            selectedIntelligence.difficulty.model_readiness.ready ? (
              <details className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4">
                <summary className="cursor-pointer list-none text-[10px] uppercase tracking-[0.15em] text-white/30">
                  How the difficulty estimate works
                </summary>
                <p className="mt-3 text-[11px] leading-5 text-white/35">
                  {selectedIntelligence.difficulty.reconciliation
                    ?.message ??
                    selectedIntelligence.difficulty.model_readiness
                      .message}
                </p>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-[10px] text-white/25">
                  <dt>Native model tier</dt>
                  <dd className="text-white/40">
                    {selectedIntelligence.difficulty.ml.estimate ??
                      "unavailable"}
                  </dd>
                  <dt>
                    {selectedIntelligence.difficulty.ml
                      .confidence_kind ===
                    "member_agreement_share"
                      ? "Member agreement"
                      : "Model score"}
                  </dt>
                  <dd className="text-white/40">
                    {selectedIntelligence.difficulty.ml.available
                      ? selectedIntelligence.difficulty.ml
                          .confidence_kind ===
                        "member_agreement_share"
                        ? `${Math.round(
                            (selectedIntelligence.difficulty.ml
                              .confidence ?? 0) * 100,
                          )}% of scored sections`
                        : `${(
                            selectedIntelligence.difficulty.ml
                              .confidence ?? 0
                          ).toFixed(2)} (uncalibrated — relative, not a probability)`
                      : "n/a"}
                  </dd>
                  <dt>Model</dt>
                  <dd className="text-white/40">
                    {selectedIntelligence.difficulty.model_readiness
                      .model_name ??
                      selectedIntelligence.difficulty.ml
                        .model_name ??
                      "n/a"}
                  </dd>
                  <dt>Feature coverage</dt>
                  <dd className="text-white/40">
                    {Math.round(
                      (selectedIntelligence.difficulty.ml
                        .feature_coverage ?? 0) * 100
                    )}
                    %
                  </dd>
                  {(() => {
                    const reliability =
                      selectedIntelligence.difficulty.ml
                        .reliability;
                    const perClass = reliability?.per_class;
                    if (!perClass) {
                      return null;
                    }
                    return Object.entries(perClass).map(
                      ([tier, row]) => (
                        <div
                          key={tier}
                          className="col-span-2 flex items-baseline justify-between gap-3"
                        >
                          <dt className="truncate">
                            {selectedIntelligence.difficulty
                              .model_readiness.tier_labels?.[
                              tier
                            ] ?? tier}
                          </dt>
                          <dd className="shrink-0 text-white/40">
                            precision {Math.round(row.precision * 100)}%
                            {" · "}
                            recall {Math.round(row.recall * 100)}%
                          </dd>
                        </div>
                      ),
                    );
                  })()}
                </dl>
                <p className="mt-3 text-[10px] leading-4 text-white/20">
                  The model predicts which difficulty tier a path&apos;s
                  recorded OpenStreetMap grade falls into: walkable,
                  mountain, or alpine. It reads only label-free
                  attributes of the path and its measured elevation
                  profile, and it was evaluated on regions held out of
                  training entirely. It is an estimate and never
                  overrides a recorded grade.
                </p>
                {/*
                  The measured comparison against the alternative a
                  reader would otherwise have used. Real OpenStreetMap is
                  dominated by easy graded ways, so "74% accurate" is
                  meaningless without the majority baseline beside it.
                */}
                {selectedIntelligence.difficulty.ml.reliability
                  ?.held_out_accuracy != null ? (
                  <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-white/30">
                      Measured on real trails
                    </p>
                    <p className="mt-1.5 text-[10px] leading-4 text-white/40">
                      Scored on geographically held-out OpenStreetMap
                      ways, under the real grade mix rather than a
                      balanced one. Accuracy{" "}
                      {Math.round(
                        (selectedIntelligence.difficulty.ml.reliability
                          ?.held_out_accuracy ?? 0) * 100,
                      )}
                      % against{" "}
                      {Math.round(
                        (selectedIntelligence.difficulty.ml.reliability
                          ?.majority_baseline_accuracy ?? 0) * 100,
                      )}
                      % for always answering with the most common tier;
                      macro-F1{" "}
                      {Math.round(
                        (selectedIntelligence.difficulty.ml.reliability
                          ?.held_out_macro_f1 ?? 0) * 100,
                      )}
                      % against{" "}
                      {Math.round(
                        (selectedIntelligence.difficulty.ml.reliability
                          ?.majority_baseline_macro_f1 ?? 0) * 100,
                      )}
                      %. The alpine tier is its weakest, so treat an
                      alpine estimate as a prompt to check the route.
                    </p>
                  </div>
                ) : null}
                {selectedIntelligence.difficulty.model_readiness
                  .limitations ? (
                  <p className="mt-2 text-[10px] leading-4 text-white/20">
                    {selectedIntelligence.difficulty.model_readiness
                      .limitations}
                  </p>
                ) : null}
              </details>
            ) : null}
          </div>
        ) : (
          <p className="mt-5 text-sm leading-7 text-white/45">
            Select a verified trail to load route difficulty, condition likelihood, and suitability context.
          </p>
        )}

      </section>
    </>
  );
}
