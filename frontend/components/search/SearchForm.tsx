"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { Stop, StopPickTarget } from "@/types/route";
import { buildStopLabel, buildStopLabelIndex } from "@/lib/stopLabel";
import { normalizeText } from "@/lib/stopSearch";
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
  onSearch: (
    originId: string,
    destinationId: string,
    viaIds: string[],
    avoidCongestion: boolean
  ) => void;
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
  locateStatus?: string | null;
  onUseMyLocation: () => void;
}

type FieldError = "origin" | "destination" | "both" | "via" | "stops" | null;

// Stable id for the shared validation summary. An invalid field points its
// aria-describedby here so the message is programmatically tied to it, while
// the summary stays the single visible role="alert" announcement -- no
// duplicated message text and no second live region.
const FIELD_ERROR_ID = "search-form-error";

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
  locateStatus = null,
  onUseMyLocation,
}: SearchFormProps) {
  const [fieldError, setFieldError] = useState<FieldError>(null);
  const [originSelectedStop, setOriginSelectedStop] = useState<Stop | null>(null);
  const [destinationSelectedStop, setDestinationSelectedStop] = useState<Stop | null>(null);
  // Default off: the backend only changes route choice when this is true,
  // so an untouched form keeps the direct-route-first behavior.
  const [avoidCongestion, setAvoidCongestion] = useState(false);

  // Via stop IDs must be stable per instance but not shared across
  // unmounts/remounts (SSR hydration, React 18 strict mode double-mount).
  // A per-instance ref avoids collisions without relying on a module-level
  // counter that would keep incrementing across the app's lifetime.
  const viaIdCounterRef = useRef(0);
  function nextViaId(): string {
    viaIdCounterRef.current += 1;
    return `via-${viaIdCounterRef.current}`;
  }
  // Focus fallback for a via row's remove button, which unmounts on click.
  const addStopRef = useRef<HTMLButtonElement>(null);

  // See lib/stopLabel.ts -- must stay identical to how the parent labels a
  // picked Stop (map click, geolocation, "use my location"), or typed
  // text and programmatically-set text would resolve differently.
  const { labelToStop } = useMemo(() => buildStopLabelIndex(stops), [stops]);

  function resolveStop(typedLabel: string): Stop | null {
    // Same normalization as lib/stopLabel's index keys (lowercase, ignore
    // punctuation and capitalization variants), so a label typed freehand
    // resolves exactly like one pasted from the autocomplete.
    return labelToStop.get(normalizeText(typedLabel)) ?? null;
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
    // The remove button unmounts with its own row; move focus to the
    // always-present "Add a stop" control so it is not dropped to <body>.
    addStopRef.current?.focus();
  }

  function updateViaStop(id: string, text: string) {
    onViaStopsChange(viaStops.map((v) => (v.id === id ? { ...v, text } : v)));
    if (fieldError === "via") setFieldError(null);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    // The submit button stays focusable while a search is in flight (it uses
    // aria-disabled, not disabled -- see the button) so focus is not dropped
    // when it is pressed. That means an implicit submission (Enter in a
    // field) or a second press can still reach here; ignore it rather than
    // firing a duplicate search.
    if (loading) return;

    // Stop resolution needs the loaded stop list (resolveStop matches
    // against labelToStop built from `stops`), so with none loaded no
    // typed/picked label can ever resolve -- fail with an accurate reason
    // instead of the misleading "pick from the suggestions" below. The
    // submit button is disabled in this state too, but an implicit form
    // submission (e.g. Enter in a text field) can still reach here.
    if (stops.length === 0) {
      setFieldError("stops");
      return;
    }

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
    onSearch(origin.stop_id, destination.stop_id, viaIds, avoidCongestion);
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

  // StopAutocomplete is wrapped in React.memo, but that only helps if the
  // props it receives keep a stable identity. Typing in the destination
  // field re-renders SearchForm (the text is controlled from the parent),
  // and inline arrow handlers / freshly-built footerActions JSX would give
  // the untouched origin field new props every time, so it re-rendered too.
  // These useCallback/useMemo wrappers keep both fields' props stable so the
  // memo actually skips the field the user is not editing.
  const togglePick = useCallback(
    (target: "origin" | "destination") => {
      onPickTargetChange(pickTarget === target ? null : target);
    },
    [onPickTargetChange, pickTarget]
  );

  const handleOriginInputChange = useCallback(
    (value: string) => {
      onOriginTextChange(value);
      if (fieldError) setFieldError(null);
    },
    [onOriginTextChange, fieldError]
  );

  const handleOriginSelect = useCallback(
    (stop: Stop) => {
      onOriginTextChange(buildStopLabel(stop, stops));
      setOriginSelectedStop(stop);
      setFieldError(null);
    },
    [onOriginTextChange, stops]
  );

  const handleDestinationInputChange = useCallback(
    (value: string) => {
      onDestinationTextChange(value);
      if (fieldError) setFieldError(null);
    },
    [onDestinationTextChange, fieldError]
  );

  const handleDestinationSelect = useCallback(
    (stop: Stop) => {
      onDestinationTextChange(buildStopLabel(stop, stops));
      setDestinationSelectedStop(stop);
      setFieldError(null);
    },
    [onDestinationTextChange, stops]
  );

  const originFooterActions = useMemo(
    () => (
      <>
        <button
          type="button"
          onClick={() => {
            if (!locating) onUseMyLocation();
          }}
          aria-disabled={locating}
          className="inline-flex min-h-11 items-center gap-1 text-xs text-accent-blue hover:underline aria-disabled:opacity-50"
        >
          <LocationIcon size={12} />
          {locating ? "Locating…" : "Use my location"}
        </button>
        <button
          type="button"
          onClick={() => togglePick("origin")}
          aria-pressed={pickTarget === "origin"}
          className={`inline-flex min-h-11 items-center gap-1 text-xs hover:underline ${
            pickTarget === "origin" ? "font-semibold text-accent-blue" : "text-ink-secondary"
          }`}
        >
          <CrosshairIcon size={12} />
          {pickTarget === "origin" ? "Picking…" : "Pick on map"}
        </button>
      </>
    ),
    [locating, onUseMyLocation, pickTarget, togglePick]
  );

  const destinationFooterActions = useMemo(
    () => (
      <button
        type="button"
        onClick={() => togglePick("destination")}
        aria-pressed={pickTarget === "destination"}
        className={`inline-flex min-h-11 items-center gap-1 text-xs hover:underline ${
          pickTarget === "destination" ? "font-semibold text-accent-red" : "text-ink-secondary"
        }`}
      >
        <CrosshairIcon size={12} />
        {pickTarget === "destination" ? "Picking…" : "Pick on map"}
      </button>
    ),
    [pickTarget, togglePick]
  );

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

        <div className="flex min-w-0 flex-1 flex-col gap-2 pr-12">
          <StopAutocomplete
            id="origin"
            label="From"
            stops={stops}
            stopsLoading={stopsLoading}
            inputValue={originText}
            onInputChange={handleOriginInputChange}
            onSelect={handleOriginSelect}
            selectedStop={originSelectedStop}
            invalid={originInvalid}
            describedById={originInvalid ? FIELD_ERROR_ID : undefined}
            placeholder="From: search starting stop…"
            footerActions={originFooterActions}
          />

          <StopAutocomplete
            id="destination"
            label="To"
            stops={stops}
            stopsLoading={stopsLoading}
            inputValue={destinationText}
            onInputChange={handleDestinationInputChange}
            onSelect={handleDestinationSelect}
            selectedStop={destinationSelectedStop}
            invalid={destinationInvalid}
            describedById={destinationInvalid ? FIELD_ERROR_ID : undefined}
            placeholder="To: search destination…"
            footerActions={destinationFooterActions}
          />
        </div>

        <button
          type="button"
          onClick={handleSwap}
          aria-label="Swap origin and destination"
          title="Swap origin and destination"
          className="absolute right-0 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full border border-route-line bg-white text-ink-secondary shadow-card hover:border-accent-blue hover:text-accent-blue"
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
                   describedById={fieldError === "via" ? FIELD_ERROR_ID : undefined}
                   placeholder={`Via stop ${i + 1}…`}
                 />
              </div>
              <button
                type="button"
                onClick={() => removeViaStop(via.id)}
                aria-label={`Remove via stop ${i + 1}`}
                title="Remove this stop"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-ink-secondary hover:bg-surface-sunken hover:text-accent-red"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      <button
        ref={addStopRef}
        type="button"
        onClick={addViaStop}
        aria-disabled={viaStops.length >= 3}
        className="inline-flex min-h-11 items-center self-start text-xs font-medium text-accent-blue hover:underline aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-disabled:no-underline"
      >
        + Add a stop along the way
      </button>

      {fieldError && (
        <p id={FIELD_ERROR_ID} className="text-xs text-accent-red" role="alert">
          {fieldError === "stops"
            ? "Stops haven't loaded yet, so route search isn't available. Retry loading the stop list to continue."
            : fieldError === "via"
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
      {/* A successful "Use my location" otherwise only moves the map marker
          and (only if "From" is empty) fills that field -- neither is a live
          region, so a screen-reader user gets no confirmation. Kept mounted
          whether empty or not, so the message is a change to an existing
          region rather than a region rendered together with its text. The
          failure path stays the separate role="alert" above. */}
      <p role="status" aria-live="polite" className="sr-only">
        {locateStatus}
      </p>

      <div className="flex flex-col gap-1 border-t border-route-line pt-3">
        <label
          htmlFor="avoid-congestion"
          className="flex items-start gap-2 text-sm font-medium text-ink"
        >
          <input
            id="avoid-congestion"
            type="checkbox"
            checked={avoidCongestion}
            onChange={(e) => setAvoidCongestion(e.target.checked)}
            aria-describedby="avoid-congestion-help"
            className="mt-0.5 h-4 w-4 shrink-0 rounded border-route-line text-brand focus:ring-accent-blue"
          />
          Avoid congested routes
        </label>
        <p id="avoid-congestion-help" className="pl-6 text-xs text-ink-secondary">
          Prefer a less-congested connection, which may mean taking a transfer instead of a
          direct bus. Congestion levels are estimated and may be incomplete.
        </p>
      </div>

      <button
        type="submit"
        disabled={stops.length === 0}
        aria-disabled={loading || stops.length === 0}
        className="mt-1 min-h-11 rounded-md bg-brand py-2.5 text-sm font-semibold tracking-wide text-ink transition-colors hover:bg-brand-dark aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
      >
        {loading ? "Searching…" : "Find route"}
      </button>
    </form>
  );
}