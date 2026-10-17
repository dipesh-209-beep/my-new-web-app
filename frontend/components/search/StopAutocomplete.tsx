"use client";

import { useEffect, useMemo, useRef, useState, memo } from "react";
import { Stop } from "@/types/route";
import { buildStopLabel } from "@/lib/stopLabel";
import { searchStops } from "@/lib/stopSearch";
import { PinDotIcon } from "@/components/icons/TransitIcons";

interface StopAutocompleteProps {
  id: string;
  label: string;
  stops: Stop[];
  stopsLoading?: boolean;
  /** The raw input text (may not resolve to a stop). */
  inputValue: string;
  /** Called when user types - updates inputValue only. */
  onInputChange: (value: string) => void;
  /** Called when user selects a stop from the list - parent updates both inputValue and selectedStop. */
  onSelect: (stop: Stop) => void;
  /** The currently selected stop (if any). Used to detect if input matches a real stop. */
  selectedStop?: Stop | null;
  invalid?: boolean;
  /** id of an element that describes this field -- e.g. a validation
   *  message -- forwarded to the input's aria-describedby so the field is
   *  programmatically tied to it. Omit when there is nothing to describe. */
  describedById?: string;
  placeholder?: string;
  /** Extra buttons rendered under the field, e.g. "Use my location". */
  footerActions?: React.ReactNode;
  /** Debounce delay in ms for filtering. Default 150ms. */
  debounceMs?: number;
}

const MAX_RESULTS = 8;

function StopAutocompleteImpl({
  id,
  label,
  stops,
  stopsLoading,
  inputValue,
  onInputChange,
  onSelect,
  selectedStop,
  invalid,
  describedById,
  placeholder,
  footerActions,
  debounceMs = 150,
}: StopAutocompleteProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = `${id}-listbox`;
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const [debouncedValue, setDebouncedValue] = useState(inputValue);

  // Debounce input for filtering
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedValue(inputValue);
    }, debounceMs);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [inputValue, debounceMs]);

  const results = useMemo(() => {
    if (!debouncedValue.trim()) return [];
    return searchStops(debouncedValue, stops)
      .slice(0, MAX_RESULTS)
      .map((match) => ({
        match,
        label: buildStopLabel(match.stop, stops),
        district: match.stop.district ?? undefined,
      }));
  }, [debouncedValue, stops]);

  // Reset highlight to the top match whenever the query changes
  const [lastQueryForHighlight, setLastQueryForHighlight] = useState(debouncedValue);
  if (lastQueryForHighlight !== debouncedValue) {
    setLastQueryForHighlight(debouncedValue);
    if (highlightedIndex !== 0) setHighlightedIndex(0);
  }

  // The candidate list can also change without the query changing -- the
  // stops prop loads, refreshes, or drops a stop -- so clamp the active
  // index to the current results. Without this, aria-activedescendant could
  // reference an option that is no longer rendered and Enter could select a
  // stale entry.
  if (highlightedIndex >= results.length && highlightedIndex !== 0) {
    setHighlightedIndex(0);
  }

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function selectStop(stop: Stop) {
    onSelect(stop);
    setIsOpen(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    // Escape always closes the popup if it's open, regardless of results.
    // This must run before the results.length === 0 check below so the
    // no-matches empty state is also dismissable.
    if (isOpen && e.key === "Escape") {
      setIsOpen(false);
      return;
    }

    if (!isOpen && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      setIsOpen(true);
      return;
    }
    if (!isOpen || results.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightedIndex((i) => (i + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightedIndex((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const result = results[highlightedIndex];
      if (result) selectStop(result.match.stop);
    }
  }

  const showEmptyState = isOpen && debouncedValue.trim().length > 0 && results.length === 0;
  // The listbox element exists only in this state, so both aria-controls and
  // aria-expanded are driven from it rather than from `isOpen` alone. `isOpen`
  // can be true with an empty query (clearing the field fires onChange →
  // setIsOpen(true) but yields no results), where no listbox renders -- saying
  // "expanded" then would advertise a popup that isn't there, and an
  // aria-controls pointing at an absent id is worse than none at all.
  const listboxVisible = isOpen && (results.length > 0 || showEmptyState);

  // Check if current input matches a selected stop (for visual feedback).
  // Compare against the disambiguated label (which is what the user sees)
  // rather than raw stop_name, so disambiguated stops like "Chowk (Kathmandu)"
  // still get the green selected border.
  const isSelectedStop = selectedStop && inputValue.trim().toLowerCase() === buildStopLabel(selectedStop, stops).toLowerCase();

  return (
    <div ref={containerRef} className="relative flex flex-col gap-1">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>

      <div className="relative flex items-center">
        <input
          ref={inputRef}
          id={id}
          role="combobox"
          aria-expanded={listboxVisible}
          aria-controls={listboxVisible ? listboxId : undefined}
          aria-autocomplete="list"
          aria-activedescendant={
            isOpen && results[highlightedIndex] ? `${id}-option-${highlightedIndex}` : undefined
          }
          value={inputValue}
          onChange={(e) => {
            onInputChange(e.target.value);
            setIsOpen(true);
          }}
          onFocus={() => debouncedValue.trim() && setIsOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={stopsLoading ? "Loading stops…" : placeholder}
          autoComplete="off"
          aria-invalid={invalid}
          aria-describedby={describedById}
          className={`w-full rounded-md border bg-surface-raised py-2.5 pl-3 pr-12 text-base text-ink outline-none transition-colors focus:border-accent-blue focus:bg-white ${
            invalid ? "border-accent-red" : isSelectedStop ? "border-accent-green" : "border-route-line"
          }`}
        />
        {inputValue && (
          <button
            type="button"
            onClick={() => {
              onInputChange("");
              setIsOpen(false);
              // This button unmounts as soon as inputValue is empty; move
              // focus back to the field first so it isn't dropped to <body>.
              inputRef.current?.focus();
            }}
            aria-label={`Clear ${label.toLowerCase()}`}
            className="absolute right-1 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full text-ink-tertiary hover:bg-surface-sunken hover:text-ink"
          >
            ×
          </button>
        )}

        {listboxVisible && (
          <ul
            id={listboxId}
            role="listbox"
            className="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-y-auto rounded-md border border-route-line bg-white shadow-card"
          >
            {results.map((result, i) => (
              <li
                id={`${id}-option-${i}`}
                key={result.match.stop.stop_id}
                role="option"
                aria-selected={i === highlightedIndex}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectStop(result.match.stop);
                }}
                onMouseEnter={() => setHighlightedIndex(i)}
                className={`flex cursor-pointer items-start gap-2 px-3 py-2 text-sm ${
                  i === highlightedIndex ? "bg-accent-blue/10 text-accent-blue" : "text-ink"
                }`}
              >
                <PinDotIcon size={7} className="mt-1.5 shrink-0 text-ink-tertiary" />
                <div className="min-w-0">
                  <p className="truncate font-medium">{result.label}</p>
                  <div className="flex flex-wrap gap-1.5 mt-0.5 text-xs text-ink-secondary">
                    {result.district && <span>{result.district}</span>}
                    {result.match.stop.zone && <span>{result.match.stop.zone}</span>}
                    {result.match.stop.is_major_stop && (
                      <span className="rounded-full bg-accent-blue/10 px-1.5 py-0.5 text-accent-blue">Major</span>
                    )}
                    {result.match.stop.is_interchange && (
                      <span className="rounded-full bg-accent-purple/10 px-1.5 py-0.5 text-accent-purple">
                        Interchange
                      </span>
                    )}
                  </div>
                </div>
              </li>
            ))}
            {showEmptyState && (
              <li role="option" aria-disabled="true" aria-selected="false" className="px-3 py-3 text-sm text-ink-secondary">
                No stops match &quot;{debouncedValue.trim()}&quot;.
              </li>
            )}
          </ul>
        )}
      </div>

      {footerActions && <div className="flex items-center gap-3 pl-0.5">{footerActions}</div>}
    </div>
  );
}

export default memo(StopAutocompleteImpl);