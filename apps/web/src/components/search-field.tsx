"use client";

import { forwardRef } from "react";

import { cn } from "@/lib/utils";

const EXAMPLES = ["dogs", "moon", "people at the beach", "sunset", "mountains"];

interface SearchFieldProps {
  value: string;
  compact: boolean;
  searching: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onPickExample: (example: string) => void;
  onLeaveDown: () => void;
}

export const SearchField = forwardRef<HTMLInputElement, SearchFieldProps>(function SearchField(
  { value, compact, searching, onChange, onSubmit, onPickExample, onLeaveDown },
  ref,
) {
  return (
    <div role="search" className="flex flex-col gap-4">
      <div
        className={cn(
          "relative flex items-end gap-4 border-b pb-2 transition-colors duration-200",
          "border-border focus-within:border-star",
          searching && "border-star/60",
        )}
      >
        <input
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onSubmit();
            } else if (e.key === "Escape" && value) {
              e.preventDefault();
              onChange("");
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              onLeaveDown();
            }
          }}
          placeholder="Describe a photo you remember"
          aria-label="Search your photos by describing them"
          autoComplete="off"
          spellCheck={false}
          className={cn(
            "w-full min-w-0 bg-transparent font-serif text-foreground outline-none",
            "placeholder:text-muted-foreground/60 transition-[font-size] duration-200",
            compact ? "text-[28px] leading-[1.2]" : "text-[44px] leading-[1.15] tracking-[-0.01em]",
          )}
        />
        <kbd
          aria-hidden
          className="mb-2 hidden shrink-0 rounded-md border border-border px-1.5 text-[11px] leading-5 text-muted-foreground sm:block"
        >
          /
        </kbd>
        {searching && (
          <span aria-hidden className="absolute inset-x-0 -bottom-px h-px origin-left animate-pulse bg-star" />
        )}
      </div>

      {!compact && (
        <div className="flex flex-wrap items-center gap-1 text-sm">
          <span className="mr-1 text-muted-foreground">Try</span>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => onPickExample(example)}
              className="rounded-md px-2 py-1 text-foreground/80 transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-star"
            >
              {example}
            </button>
          ))}
        </div>
      )}
    </div>
  );
});
