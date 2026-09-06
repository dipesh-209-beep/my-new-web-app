"use client";

import { useEffect, useMemo, useRef, useState, memo } from "react";
import { Stop } from "@/types/route";
import { buildStopLabel } from "@/lib/stopLabel";
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
  placeholder,
  footerActions,
  debounceMs = 150,
}: StopAutocompleteProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
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
    const query = debouncedValue.trim().toLowerCase();
    if (!query) return [];

    const seen = new Set<string>();
    const matches: { stop: Stop; label: string; district?: string; stopId?: string }[] = [];
    for (const stop of stops) {
      if (matches.length >= MAX_RESULTS) break;
      if (seen.has(stop.stop_id)) continue;
      const nameMatch = stop.stop_name.toLowerCase().includes(query);
      const districtMatch = stop.district?.toLowerCase().includes(query) ?? false;
      if (nameMatch || districtMatch) {
        seen.add(stop.stop_id);
        matches.push({
          stop,
          label: buildStopLabel(stop, stops),
          district: stop.district ?? undefined,
          stopId: stop.stop_id,
        });
      }
    }
    return matches;
  }, [debouncedValue, stops]);

  // Reset highlight to the top match whenever the candidate list changes
  const [lastQueryForHighlight, setLastQueryForHighlight] = useState(debouncedValue);
  if (lastQueryForHighlight !== debouncedValue) {
    setLastQueryForHighlight(debouncedValue);
    if (highlightedIndex !== 0) setHighlightedIndex(0);
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
      const match = results[highlightedIndex];
      if (match) selectStop(match.stop);
    } else if (e.key === "Escape") {
      setIsOpen(false);
    }
  }

  const showEmptyState = isOpen && debouncedValue.trim().length > 0 && results.length === 0;

  // Check if current input matches a selected stop (for visual feedback)
  const isSelectedStop = selectedStop && inputValue.trim().toLowerCase() === selectedStop.stop_name.toLowerCase();

  return (
    <div ref={containerRef} className="relative flex flex-col gap-1">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>

      <div className="relative flex items-center">
        <input
          id={id}
          role="combobox"
          aria-expanded={isOpen}
          aria-controls={listboxId}
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
          className={`w-full rounded-md border bg-surface-raised py-2.5 pl-3 pr-8 text-sm text-ink outline-none transition-colors focus:border-accent-blue focus:bg-white ${
            invalid ? "border-accent-red" : isSelectedStop ? "border-accent-green" : "border-route-line"
          }`}
        />
        {inputValue && (
          <button
            type="button"
            onClick={() => {
              onInputChange("");
              setIsOpen(false);
            }}
            aria-label={`Clear ${label.toLowerCase()}`}
            className="absolute right-2 flex h-5 w-5 items-center justify-center rounded-full text-ink-tertiary hover:bg-surface-sunken hover:text-ink"
          >
            ×
          </button>
        )}

        {isOpen && (results.length > 0 || showEmptyState) && (
          <ul
            id={listboxId}
            role="listbox"
            className="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-y-auto rounded-md border border-route-line bg-white shadow-card"
          >
            {results.map((match, i) => (
              <li
                id={`${id}-option-${i}`}
                key={match.stop.stop_id}
                role="option"
                aria-selected={i === highlightedIndex}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectStop(match.stop);
                }}
                onMouseEnter={() => setHighlightedIndex(i)}
                className={`flex cursor-pointer items-start gap-2 px-3 py-2 text-sm ${
                  i === highlightedIndex ? "bg-accent-blue/10 text-accent-blue" : "text-ink"
                }`}
              >
                <PinDotIcon size={7} className="mt-1.5 shrink-0 text-ink-tertiary" />
                <div className="min-w-0">
                  <p className="truncate font-medium">{match.stop.stop_name}</p>
                  <div className="flex flex-wrap gap-1.5 mt-0.5 text-xs text-ink-secondary">
                    {match.district && <span>{match.district}</span>}
                    {match.stopId && <span className="font-mono opacity-60">{match.stopId}</span>}
                    {match.stop.zone && <span>{match.stop.zone}</span>}
                    {match.stop.is_major_stop && <span className="text-accent-blue">Major</span>}
                    {match.stop.is_interchange && <span className="text-accent-purple">Interchange</span>}
                  </div>
                </div>
              </li>
            ))}
            {showEmptyState && (
              <li className="px-3 py-3 text-sm text-ink-secondary">
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