"use client";

import { SlidersHorizontal } from "lucide-react";

import {
  DEFAULT_RESULT_VIEW,
  LENGTH_BUCKETS,
  activeFilterCount,
} from "@/app/explore/helpers";
import type {
  DifficultyBucket,
  LengthBucket,
  ResultView,
  SortKey,
} from "@/app/explore/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

const SORTS: Array<{ value: SortKey; label: string }> = [
  { value: "relevance", label: "Best match" },
  { value: "nearest", label: "Nearest first" },
  { value: "longest", label: "Longest first" },
  { value: "shortest", label: "Shortest first" },
  { value: "easiest", label: "Easiest first" },
];

const DIFFICULTIES: DifficultyBucket[] = [
  "Easy",
  "Moderate",
  "Hard",
  "Very Hard",
];

// A selected option is white on the dark surface, so it reads at a glance.
const CHOICE =
  "h-9 rounded-lg border-white/15 px-3 text-[13px] text-white/80 hover:bg-white/10 hover:text-white data-[state=on]:border-white data-[state=on]:bg-white data-[state=on]:text-[#07111f] data-[state=on]:hover:bg-white data-[state=on]:hover:text-[#07111f]";

type ResultControlsProps = {
  view: ResultView;
  onChange: (view: ResultView) => void;
  // How many trails the current filters kept, when the server narrowed the list.
  match: { matched: number; before: number } | null;
};

export default function ResultControls({
  view,
  onChange,
  match,
}: ResultControlsProps) {
  const active = activeFilterCount(view);

  return (
    <div className="shrink-0 border-b border-white/10 px-4 py-3">
      <div className="flex items-center gap-2">
        <Select
          value={view.sort}
          onValueChange={(value) =>
            onChange({ ...view, sort: value as SortKey })
          }
        >
          <SelectTrigger
            aria-label="Sort trails"
            className="h-10 min-w-0 flex-1 rounded-xl border-white/15 bg-white/[0.04] text-[13px] text-white/90"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent
            position="popper"
            className="z-[80] rounded-xl border-white/15 bg-[#0d1825] text-white"
          >
            {SORTS.map((sort) => (
              <SelectItem
                key={sort.value}
                value={sort.value}
                className="text-[13px] focus:bg-white/10 focus:text-white"
              >
                {sort.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Popover>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              aria-label={
                active ? `Filters, ${active} active` : "Filters"
              }
              className="h-10 shrink-0 gap-2 rounded-xl border-white/15 bg-white/[0.04] px-3.5 text-[13px] font-medium text-white/90 hover:bg-white/10 hover:text-white"
            >
              <SlidersHorizontal className="size-4" aria-hidden="true" />
              Filters
              {active > 0 && (
                <Badge className="h-5 min-w-5 rounded-full bg-white px-1.5 text-[12px] text-[#07111f]">
                  {active}
                </Badge>
              )}
            </Button>
          </PopoverTrigger>

          <PopoverContent
            align="end"
            className="z-[80] w-[320px] rounded-2xl border-white/15 bg-[#0d1825] p-4 text-white shadow-[0_18px_50px_rgba(0,0,0,0.5)]"
          >
            <fieldset>
              <legend className="text-[12px] font-medium uppercase tracking-[0.15em] text-white/60">
                Difficulty
              </legend>
              <ToggleGroup
                type="multiple"
                variant="outline"
                spacing={2}
                value={view.difficulty}
                onValueChange={(value) =>
                  onChange({
                    ...view,
                    difficulty: value as DifficultyBucket[],
                  })
                }
                className="mt-2 flex-wrap"
              >
                {DIFFICULTIES.map((label) => (
                  <ToggleGroupItem
                    key={label}
                    value={label}
                    className={CHOICE}
                  >
                    {label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
              <p className="mt-2 text-[12px] leading-4 text-white/55">
                From the grade recorded in OpenStreetMap. Trails with no
                recorded grade are hidden while this is on.
              </p>
            </fieldset>

            <fieldset className="mt-4">
              <legend className="text-[12px] font-medium uppercase tracking-[0.15em] text-white/60">
                Length
              </legend>
              <ToggleGroup
                type="single"
                variant="outline"
                spacing={2}
                value={view.length ?? ""}
                onValueChange={(value) =>
                  onChange({
                    ...view,
                    length: (value || null) as LengthBucket | null,
                  })
                }
                className="mt-2 flex-wrap"
              >
                {(Object.keys(LENGTH_BUCKETS) as LengthBucket[]).map(
                  (bucket) => (
                    <ToggleGroupItem
                      key={bucket}
                      value={bucket}
                      className={CHOICE}
                    >
                      {LENGTH_BUCKETS[bucket].label}
                    </ToggleGroupItem>
                  )
                )}
              </ToggleGroup>
            </fieldset>

            <div className="mt-4 flex items-center justify-between gap-3 border-t border-white/10 pt-4">
              <label
                htmlFor="mapped-only"
                className="text-[13px] leading-5 text-white/85"
              >
                On the map only
                <span className="block text-[12px] text-white/55">
                  Hide trails with no verified shape
                </span>
              </label>
              <Switch
                id="mapped-only"
                checked={view.mappedOnly}
                onCheckedChange={(checked) =>
                  onChange({ ...view, mappedOnly: checked })
                }
                className="data-[state=checked]:bg-white data-[state=unchecked]:bg-white/25"
              />
            </div>

            <Button
              type="button"
              variant="ghost"
              disabled={active === 0}
              onClick={() =>
                onChange({ ...DEFAULT_RESULT_VIEW, sort: view.sort })
              }
              className="mt-3 h-9 w-full rounded-lg text-[13px] text-white/80 hover:bg-white/10 hover:text-white"
            >
              Clear filters
            </Button>
          </PopoverContent>
        </Popover>
      </div>

      {match && (
        <p
          role="status"
          className="mt-2 text-[13px] leading-5 text-white/65"
        >
          {match.matched.toLocaleString()} of {match.before.toLocaleString()}{" "}
          {match.before === 1 ? "trail matches" : "trails match"}
        </p>
      )}
    </div>
  );
}
