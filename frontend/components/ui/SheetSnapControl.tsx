"use client";

import { useId } from "react";
import { SheetSnap } from "@/lib/sheetSnap";

interface SheetSnapControlProps {
  value: SheetSnap;
  onChange: (next: SheetSnap) => void;
  className?: string;
}

// Native radios (not role="slider"): the sheet has three discrete, named
// positions, so a radio group exposes both the set of choices and the
// current one for free, and the browser gives arrow-key movement and a
// single tab stop without any JS. Visible text stays short to fit the
// sheet header; the full name per option lives in aria-label.
const SNAP_OPTIONS: { value: SheetSnap; label: string; name: string }[] = [
  { value: "minimized", label: "Min", name: "Minimized" },
  { value: "half", label: "Half", name: "Half height" },
  { value: "full", label: "Full", name: "Full height" },
];

export default function SheetSnapControl({ value, onChange, className = "" }: SheetSnapControlProps) {
  const groupName = useId();

  return (
    <div
      role="radiogroup"
      aria-label="Panel height"
      className={`flex items-center rounded-lg bg-surface-sunken p-1 ${className}`}
    >
      {SNAP_OPTIONS.map((option) => (
        <label key={option.value} className="flex-1">
          <input
            type="radio"
            name={groupName}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
            aria-label={option.name}
            className="peer sr-only"
          />
          <span
            className={`flex min-h-11 min-w-11 cursor-pointer items-center justify-center rounded-md px-3 py-1 text-xs font-medium transition-colors peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-accent-blue ${
              value === option.value
                ? "bg-white text-ink shadow-sm"
                : "text-ink-secondary hover:text-ink"
            }`}
          >
            {option.label}
          </span>
        </label>
      ))}
    </div>
  );
}
