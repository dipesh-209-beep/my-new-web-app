"use client";

import { useMemo, useRef, useState } from "react";
import { Stop, StopPickTarget } from "@/types/route";
import { buildStopLabel, buildStopLabelIndex } from "@/lib/stopLabel";
import StopAutocomplete from "./StopAutocomplete";
import { CrosshairIcon, LocationIcon, SwapIcon } from "@/components/icons/TransitIcons";

/** One intermediate "via" stop row. `id` is a stable React key (not the
 * stop_id -- the text may not resolve to a stop yet while the user is
 * typing), independent of insertion order so rows don't jump around as
 * the list is edited. */
export interface ViaStopField {
  id: string;
  text: string;
}

interface SearchFormProps {
  stops: Stop[];
  stopsLoading?: boolean;
  onSearch: (originId: string, destinationId: string, viaIds: string[]) => void;
  loading?: boolean;
  // Map-click stop picking is owned by the parent (it also drives the map),
  // so origin/destination text are controlled from there rather than kept
  // as local state here.
  originText: string;
  destinationText: string;
  onOriginTextChange: (value: string) => void;
  onDestinationTextChange: (value: string) => void;
  // Same reasoning as origin/destination -- kept in the parent so a
  // future map-click-to-add-a-stop flow can write to it too.
  viaStops: ViaStopField[];
  onViaStopsChange: (viaStops: ViaStopField[]) => void;
  pickTarget: StopPickTarget;
  onPickTargetChange: (target: StopPickTarget) => void;
  locating?: boolean;
  locateError?: string | null;
  onUseMyLocation: () => void;
}

type FieldError = "origin" | "destination" | "both" | "via" | null;

export default function SearchForm({
  stops,
  stopsLoading,
  onSearch,
  loading,
  originText,
  destinationText,
  onOriginTextChange,
  onDestinationTextChange,
  viaStops,
  onViaStopsChange,
  pickTarget,
  onPickTargetChange,
  locating = false,
  locateError = null,
  onUseMyLocation,
}: SearchFormProps) {
  const [fieldError, setFieldError] = useState<FieldError>(null);
  const [originSelectedStop, setOriginSelectedStop] = useState<Stop | null>(null);
  const [destinationSelectedStop, setDestinationSelectedStop] = useState<Stop | null>(null);

  // Via stop IDs must be stable per instance but not shared across
  // unmounts/remounts (SSR hydration, React 18 strict mode double-mount).
  // A per-instance ref avoids collisions without relying on a module-level
  // counter that would keep incrementing across the app's lifetime.
  const viaIdCounterRef = useRef(0);
  function nextViaId(): string {
    viaIdCounterRef.current += 1;
    return `via-${viaIdCounterRef.current}`;
  }

  // See lib/stopLabel.ts -- must stay identical to how the parent labels a
  // picked Stop (map click, geolocation, "use my location"), or typed
  // text and programmatically-set text would resolve differently.
  const { labelToStop } = useMemo(() => buildStopLabelIndex(stops), [stops]);

  function resolveStop(typedLabel: string): Stop | null {
    return labelToStop.get(typedLabel.trim().toLowerCase()) ?? null;
  }

  function addViaStop() {
    // Cap at 3 -- each extra via stop is a full extra leg the backend
    // has to solve independently (see find_route_via_stops), and a
    // rider planning more than a handful of forced stops is really
    // planning a multi-leg trip by hand rather than using this field.
    if (viaStops.length >= 3) return;
    onViaStopsChange([...viaStops, { id: nextViaId(), text: "" }]);
  }

  function removeViaStop(id: string) {
    onViaStopsChange(viaStops.filter((v) => v.id !== id));
    if (fieldError === "via") setFieldError(null);
  }

  function updateViaStop(id: string, text: string) {
    onViaStopsChange(viaStops.map((v) => (v.id === id ? { ...v, text } : v)));
    if (fieldError === "via") setFieldError(null);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const origin = resolveStop(originText);
    const destination = resolveStop(destinationText);

    if (!origin && !destination) {
      setFieldError("both");
      return;
    }
    if (!origin) {
      setFieldError("origin");
      return;
    }
    if (!destination) {
      setFieldError("destination");
      return;
    }
    if (origin.stop_id === destination.stop_id) {
      setFieldError("both");
      return;
    }

    // Blank via rows are just an unfinished "+ Add stop" click -- ignore
    // them rather than erroring, so someone doesn't have to remove an
    // empty row before they can search.
    const filledVias = viaStops.filter((v) => v.text.trim() !== "");
    const viaResolved: Stop[] = [];
    for (const via of filledVias) {
      const stop = resolveStop(via.text);
      if (!stop) {
        setFieldError("via");
        return;
      }
      viaResolved.push(stop);
    }
    const viaIds = viaResolved.map((s) => s.stop_id);
    // A via stop identical to the origin, the destination, or another
    // via stop isn't a real waypoint -- it wouldn't add anything to the
    // trip and would just make find_route_via_stops solve a pointless
    // zero-length leg.
    const allIds = [origin.stop_id, ...viaIds, destination.stop_id];
    if (new Set(allIds).size !== allIds.length) {
      setFieldError("via");
      return;
    }

    setFieldError(null);
    onSearch(origin.stop_id, destination.stop_id, viaIds);
  }

  function handleSwap() {
    onOriginTextChange(destinationText);
    onDestinationTextChange(originText);
    // Swap selected stops too
    const temp = originSelectedStop;
    setOriginSelectedStop(destinationSelectedStop);
    setDestinationSelectedStop(temp);
    setFieldError(null);
  }

  function togglePick(target: "origin" | "destination") {
    onPickTargetChange(pickTarget === target ? null : target);
  }

  const originInvalid = fieldError === "origin" || fieldError === "both";
  const destinationInvalid = fieldError === "destination" || fieldError === "both";

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-3 rounded-xl border border-route-line bg-surface-raised p-3 shadow-card"
    >
      {pickTarget && (
        <p className="rounded-md border border-accent-blue/30 bg-accent-blue/5 px-3 py-2 text-xs text-accent-blue">
          Tap a stop on the map to set your {pickTarget === "origin" ? "origin" : "destination"}.
        </p>
      )}

      <div className="relative flex gap-2.5">
        {/* Shared origin -> destination connector: blue dot, dashed line,
            red dot -- read at a glance without a per-field label. */}
        <div className="flex w-3 shrink-0 flex-col items-center pt-3.5" aria-hidden>
          <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-accent-blue" />
          <span className="my-1 w-px flex-1 border-l border-dashed border-route-line" />
          <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-accent-red" />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-2 pr-9">
          <StopAutocomplete
            id="origin"
            label="From"
            stops={stops}
            stopsLoading={stopsLoading}
            inputValue={originText}
            onInputChange={(v) => {
              onOriginTextChange(v);
              if (fieldError) setFieldError(null);
            }}
            onSelect={(stop) => {
              onOriginTextChange(buildStopLabel(stop, stops));
              setOriginSelectedStop(stop);
              setFieldError(null);
            }}
            selectedStop={originSelectedStop}
            invalid={originInvalid}
            placeholder="From: search starting stop…"
            footerActions={
              <>
                <button
                  type="button"
                  onClick={onUseMyLocation}
                  disabled={locating}
                  className="inline-flex items-center gap-1 text-xs text-accent-blue hover:underline disabled:opacity-50"
                >
                  <LocationIcon size={12} />
                  {locating ? "Locating…" : "Use my location"}
                </button>
                <button
                  type="button"
                  onClick={() => togglePick("origin")}
                  aria-pressed={pickTarget === "origin"}
                  className={`inline-flex items-center gap-1 text-xs hover:underline ${
                    pickTarget === "origin" ? "font-semibold text-accent-blue" : "text-ink-secondary"
                  }`}
                >
                  <CrosshairIcon size={12} />
                  {pickTarget === "origin" ? "Picking…" : "Pick on map"}
                </button>
              </>
            }
          />

          <StopAutocomplete
            id="destination"
            label="To"
            stops={stops}
            stopsLoading={stopsLoading}
            inputValue={destinationText}
            onInputChange={(v) => {
              onDestinationTextChange(v);
              if (fieldError) setFieldError(null);
            }}
            onSelect={(stop) => {
              onDestinationTextChange(buildStopLabel(stop, stops));
              setDestinationSelectedStop(stop);
              setFieldError(null);
            }}
            selectedStop={destinationSelectedStop}
            invalid={destinationInvalid}
            placeholder="To: search destination…"
            footerActions={
              <button
                type="button"
                onClick={() => togglePick("destination")}
                aria-pressed={pickTarget === "destination"}
                className={`inline-flex items-center gap-1 text-xs hover:underline ${
                  pickTarget === "destination" ? "font-semibold text-accent-red" : "text-ink-secondary"
                }`}
              >
                <CrosshairIcon size={12} />
                {pickTarget === "destination" ? "Picking…" : "Pick on map"}
              </button>
            }
          />
        </div>

        <button
          type="button"
          onClick={handleSwap}
          aria-label="Swap origin and destination"
          title="Swap origin and destination"
          className="absolute right-0 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full border border-route-line bg-white text-ink-secondary shadow-card hover:border-accent-blue hover:text-accent-blue"
        >
          <SwapIcon size={15} />
        </button>
      </div>

      {viaStops.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-route-line pt-3">
          {viaStops.map((via, i) => (
            <div key={via.id} className="flex items-center gap-2">
              <span
                className="flex h-2 w-2 shrink-0 items-center justify-center rounded-full border-2 border-accent-purple"
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <StopAutocomplete
                  id={`via-${i}`}
                  label={`Via stop ${i + 1}`}
                  stops={stops}
                  stopsLoading={stopsLoading}
                  inputValue={via.text}
                  onInputChange={(v) => updateViaStop(via.id, v)}
                  onSelect={(stop) => updateViaStop(via.id, buildStopLabel(stop, stops))}
                  invalid={fieldError === "via"}
                  placeholder={`Via stop ${i + 1}…`}
                />
              </div>
              <button
                type="button"
                onClick={() => removeViaStop(via.id)}
                aria-label={`Remove via stop ${i + 1}`}
                title="Remove this stop"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-secondary hover:bg-surface-sunken hover:text-accent-red"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={addViaStop}
        disabled={viaStops.length >= 3}
        className="self-start text-xs font-medium text-accent-blue hover:underline disabled:cursor-not-allowed disabled:opacity-50 disabled:no-underline"
      >
        + Add a stop along the way
      </button>

      {fieldError && (
        <p className="text-xs text-accent-red" role="alert">
          {fieldError === "via"
            ? "Pick a valid, unique stop from the suggestions for each via stop."
            : fieldError === "both" && originText.trim() && originText === destinationText
            ? "Origin and destination can't be the same stop."
            : "Pick a valid stop from the suggestions for both fields."}
        </p>
      )}
      {locateError && (
        <p className="text-xs text-accent-red" role="alert">
          {locateError}
        </p>
      )}

      <button
        type="submit"
        disabled={loading}
        className="mt-1 rounded-md bg-accent-blue py-2.5 text-sm font-semibold tracking-wide text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "Searching…" : "Find route"}
      </button>
    </form>
  );
}