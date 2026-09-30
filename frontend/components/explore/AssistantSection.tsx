"use client";

import { type Dispatch, type SetStateAction } from "react";

import type { AssistantResponse, TrailIntelligenceResponse } from "@/app/explore/types";
import type { FormEvent } from "react";

type AssistantSectionProps = {
  assistantAnswer: AssistantResponse | null;
  assistantError: string | null;
  assistantLoading: boolean;
  assistantQuestion: string;
  handleAssistantSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  selectedIntelligence: TrailIntelligenceResponse | null;
  setAssistantQuestion: Dispatch<SetStateAction<string>>;
};

export default function AssistantSection({
  assistantAnswer,
  assistantError,
  assistantLoading,
  assistantQuestion,
  handleAssistantSubmit,
  selectedIntelligence,
  setAssistantQuestion,
}: AssistantSectionProps) {
  return (
    <>
      <section id="assistant" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

        <p className="text-[13px] uppercase tracking-[0.2em] text-white/60">
          Assistant
        </p>


        <h3 className="mt-3 text-[22px] font-semibold">
          Ask about the trail
        </h3>


        {selectedIntelligence ? (
          <form
            onSubmit={handleAssistantSubmit}
            className="mt-5"
          >
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                value={assistantQuestion}
                onChange={(event) =>
                  setAssistantQuestion(event.target.value)
                }
                placeholder="Ask about conditions, difficulty, or gear…"
                maxLength={600}
                className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none placeholder:text-white/55 focus:border-sky-300/40"
              />
              <button
                type="submit"
                disabled={
                  assistantLoading ||
                  !assistantQuestion.trim()
                }
                className="rounded-xl border border-sky-300/20 bg-sky-300/[0.08] px-4 py-2 text-[13px] font-semibold text-sky-200 transition hover:bg-sky-300/[0.14] disabled:cursor-wait disabled:opacity-60"
              >
                {assistantLoading ? "Thinking…" : "Ask"}
              </button>
            </div>
            {assistantError ? (
              <p className="mt-3 text-[13px] text-amber-200/85">
                {assistantError}
              </p>
            ) : null}
            {assistantAnswer ? (
              <div className="mt-4 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
                {assistantAnswer.status ===
                  "not_in_context" ? (
                  <p className="mb-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.05] px-3 py-2 text-[12px] leading-4 text-amber-100/75">
                    This question is not answered from the trail
                    data, so nothing was invented for it.
                  </p>
                ) : null}
                {/*
                  The answer is the conversation. Which engine
                  wrote the prose is a property of the system,
                  not something the person asking asked about, so
                  the generator label and the provider note are
                  not shown. The contract still carries
                  `grounded` and `generated_by` for anything that
                  needs them.
                */}
                <p className="whitespace-pre-line text-sm leading-6 text-white/75">
                  {assistantAnswer.answer}
                </p>
              </div>
            ) : null}
          </form>
        ) : (
          <p className="mt-5 text-sm leading-7 text-white/70">
            Select a verified trail to ask a grounded question about its intelligence.
          </p>
        )}

      </section>
    </>
  );
}
