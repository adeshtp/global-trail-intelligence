"use client";

import { type Dispatch, type SetStateAction } from "react";

import { suggestedQuestions } from "@/app/explore/helpers";
import type { AssistantResponse, TrailIntelligenceResponse } from "@/app/explore/types";
import type { FormEvent } from "react";

type AssistantDockProps = {
  assistantAnswer: AssistantResponse | null;
  assistantError: string | null;
  assistantLoading: boolean;
  assistantOpen: boolean;
  assistantQuestion: string;
  handleAssistantSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  selectedIntelligence: TrailIntelligenceResponse | null;
  setAssistantOpen: Dispatch<SetStateAction<boolean>>;
  setAssistantQuestion: Dispatch<SetStateAction<string>>;
};

export default function AssistantDock({
  assistantAnswer,
  assistantError,
  assistantLoading,
  assistantOpen,
  assistantQuestion,
  handleAssistantSubmit,
  selectedIntelligence,
  setAssistantOpen,
  setAssistantQuestion,
}: AssistantDockProps) {
  return (
    <>
      {selectedIntelligence ? (
        <div
          className="pointer-events-none fixed bottom-5 right-5 z-40 flex w-[min(24rem,calc(100vw-2.5rem))] flex-col items-end gap-3"
        >
          {assistantOpen ? (
            <div className="pointer-events-auto max-h-[min(32rem,70vh)] w-full overflow-y-auto rounded-2xl border border-white/12 bg-[#0b1724]/97 p-4 shadow-[0_24px_60px_rgba(0,0,0,0.55)] backdrop-blur">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-[12px] uppercase tracking-[0.15em] text-white/60">
                    Trail assistant
                  </p>
                  <p className="mt-0.5 text-[13px] font-semibold text-white/85">
                    {selectedIntelligence.trail.name ??
                      "Selected trail"}
                  </p>
                </div>
                <button
                  type="button"
                  aria-label="Close assistant"
                  onClick={() => setAssistantOpen(false)}
                  className="shrink-0 rounded-lg px-2 py-1 text-[13px] text-white/60 transition hover:bg-white/[0.07] hover:text-white"
                >
                  Close
                </button>
              </div>

              <p className="mt-2 text-[12px] leading-4 text-white/55">
                Answers come only from this trail&apos;s verified data.
              </p>

              <form
                onSubmit={handleAssistantSubmit}
                className="mt-3"
              >
                <div className="flex gap-2">
                  <input
                    value={assistantQuestion}
                    onChange={(event) =>
                      setAssistantQuestion(event.target.value)
                    }
                    placeholder="Ask about this trail…"
                    maxLength={600}
                    className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[13px] text-white outline-none placeholder:text-white/55 focus:border-sky-300/40"
                  />
                  <button
                    type="submit"
                    disabled={
                      assistantLoading ||
                      !assistantQuestion.trim()
                    }
                    className="shrink-0 rounded-xl border border-sky-300/20 bg-sky-300/[0.08] px-3 py-2 text-[13px] font-semibold text-sky-200 transition hover:bg-sky-300/[0.14] disabled:cursor-wait disabled:opacity-60"
                  >
                    {assistantLoading ? "…" : "Ask"}
                  </button>
                </div>
              </form>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {suggestedQuestions(
                  selectedIntelligence
                ).map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() =>
                      setAssistantQuestion(prompt)
                    }
                    className="rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[12px] text-white/70 transition hover:border-sky-300/25 hover:text-white/80"
                  >
                    {prompt}
                  </button>
                ))}
              </div>

              {assistantError ? (
                <p className="mt-3 text-[13px] text-amber-200/85">
                  {assistantError}
                </p>
              ) : null}

              {assistantAnswer ? (
                <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
                  <p className="whitespace-pre-line text-[13px] leading-5 text-white/75">
                    {assistantAnswer.answer}
                  </p>
                </div>
              ) : null}
            </div>
          ) : null}

          <button
            type="button"
            aria-label={
              assistantOpen
                ? "Hide trail assistant"
                : "Ask the trail assistant"
            }
            onClick={() => setAssistantOpen(!assistantOpen)}
            className="pointer-events-auto flex items-center gap-2 rounded-full border border-sky-300/25 bg-[#0d1825]/95 px-4 py-3 text-[13px] font-semibold text-sky-100 shadow-[0_12px_32px_rgba(0,0,0,0.45)] backdrop-blur transition hover:border-sky-300/50"
          >
            <span
              aria-hidden="true"
              className="grid h-5 w-5 place-items-center rounded-full bg-sky-300/15 text-[13px]"
            >
              ?
            </span>
            {assistantOpen
              ? "Hide assistant"
              : "Ask about this trail"}
          </button>
        </div>
      ) : null}
    </>
  );
}
