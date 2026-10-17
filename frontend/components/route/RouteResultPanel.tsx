"use client";

import { useRef, type ReactNode } from "react";
import { LoadingStage, RouteAlternative, RouteFinderResult, RouteSearchResult } from "@/types/route";
import RouteTimeline from "./RouteTimeline";
import { BusIcon, ClockIcon, TransferIcon, WalkIcon } from "@/components/icons/TransitIcons";
import InlineAlert from "@/components/ui/InlineAlert";
import SelectablePill from "@/components/ui/SelectablePill";
import { TRANSFER_ROUTE_ID } from "@/lib/constants";
import { formatFareRange } from "./RouteEstimates";

interface RouteResultPanelProps {
  result: RouteSearchResult | null;
  loading: boolean;
  loadingStage?: LoadingStage;
  error: string | null;
  /** -1 = the primary/recommended result; otherwise an index into
   * result.alternatives. Controlled by the parent so the same selection
   * also drives what the map draws (see app/page.tsx). */
  selectedIndex: number;
  onSelectedIndexChange: (index: number) => void;
  onRetry?: () => void;
  /** Index of the leg the itinerary marks as the current step (shared with
   * the map), or null for none. */
  selectedLegIndex?: number | null;
  /** Called when a timeline step is selected or deselected. */
  onLegSelect?: (index: number | null) => void;
}

const ALTERNATIVE_LABELS: Record<RouteAlternative["label"], string> = {
  alternate_direct_route: "Direct alternative",
  shortest_distance: "Least distance",
  fastest_estimated: "Fastest (est.)",
};

// Why the primary ("Recommended") option was chosen. Each string states
// only what the backend's ranking actually compared (straight-line
// stop-to-stop distance and number of transfers, plus congestion only when
// the user opted in) -- the UI must never upgrade these into
// "fastest"/"cheapest"/"most frequent" claims, because time and fare never
// drive the ranking, and never imply the distance is a measured road
// distance or a journey time. Unknown/missing reasons render no
// explanation at all rather than a guess.
const RANK_REASON_TEXT: Record<string, string> = {
  only_option: "Only one route was found between these stops.",
  fewest_transfers: "Fewer transfers than the other options.",
  shortest_distance: "Shortest stop-to-stop distance among the options.",
  avoids_congestion: "Scores better once current congestion is factored in.",
  best_overall: "Matches the other options on distance and transfers; shown first by a fixed tie-break.",
  via_stops: "Includes the stops you asked to travel through.",
};

// These three reasons compare the primary against another candidate, so the
// wording above says "the other options". That is only accurate when the
// response actually shows them: with include_alternatives=false the rider sees
// the primary alone, so the same reason is worded to say the comparison
// happened without implying the compared routes are visible. Reasons that do
// not reference other options (only_option, avoids_congestion, via_stops) have
// no variant.
const RANK_REASON_TEXT_NO_ALTERNATIVES: Record<string, string> = {
  fewest_transfers: "Fewer transfers than the other routes the search compared.",
  shortest_distance: "Shortest stop-to-stop distance of the routes the search compared.",
  best_overall:
    "Matches another route the search compared on distance and transfers; shown first by a fixed tie-break.",
};

// Pick the wording that matches what the rider can see. An unknown reason has
// no entry in either map and renders nothing, rather than a guessed claim.
function rankReasonText(reason: string, hasVisibleAlternatives: boolean): string | undefined {
  if (!hasVisibleAlternatives && reason in RANK_REASON_TEXT_NO_ALTERNATIVES) {
    return RANK_REASON_TEXT_NO_ALTERNATIVES[reason];
  }
  return RANK_REASON_TEXT[reason];
}

// Format a compact summary for alternative tooltips
function formatAltSummary(alt: RouteAlternative): string {
  const altRideLegs = alt.legs.filter((leg) => leg.route_id !== TRANSFER_ROUTE_ID);
  const altWalkLegs = alt.legs.filter((leg) => leg.route_id === TRANSFER_ROUTE_ID);
  const parts = [`${alt.legs.length} leg${alt.legs.length > 1 ? "s" : ""}`];
  if (altRideLegs.length > 0) {
    parts.push(`${altRideLegs.length} bus${altRideLegs.length > 1 ? "es" : ""}`);
  }
  if (altWalkLegs.length > 0) {
    parts.push(`${altWalkLegs.length} walk${altWalkLegs.length > 1 ? "s" : ""}`);
  }
  if (alt.transfer_count > 0) {
    parts.push(`${alt.transfer_count} transfer${alt.transfer_count > 1 ? "s" : ""}`);
  }
  return parts.join(" · ");
}

function formatDuration(totalSeconds: number): { value: string; unit: string } {
  const minutes = Math.round(totalSeconds / 60);
  if (minutes < 60) return { value: String(minutes), unit: "min" };
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return remaining === 0
    ? { value: String(hours), unit: "hr" }
    : { value: `${hours}h ${remaining}`, unit: "min" };
}

export default function RouteResultPanel({
  result,
  loading,
  loadingStage = "idle",
  error,
  selectedIndex,
  onSelectedIndexChange,
  onRetry,
  selectedLegIndex = null,
  onLegSelect,
}: RouteResultPanelProps) {
  // The panel root is the one element that is the same node in every state
  // (loading / error / no-route / idle / found), so it is where focus can
  // safely land when a control the user just activated disappears. Retry is
  // the case that needs it: it lives inside the error card, which is replaced
  // by the loading card the moment it is pressed, so without this the focused
  // button would unmount and drop focus to <body>. The wrapper is not a tab
  // stop (tabIndex={-1}) and keeps no visible outline, so it does not add a
  // keyboard stop or steal focus on its own.
  const panelRef = useRef<HTMLDivElement>(null);
  const withPanel = (node: ReactNode) => (
    <div
      ref={panelRef}
      tabIndex={-1}
      role="region"
      aria-label="Route results"
      className="outline-none"
    >
      {node}
    </div>
  );

  // The loading and found states below both render a polite, screen-reader-only
  // status as the *first child of their card*. Both cards are a root <div> with
  // the same first child, so React reuses that status node across the
  // loading -> found transition: the found summary is a text change in an
  // already-mounted live region. A live region created in the same commit as
  // its text -- which is what a per-state region did -- is not reliably
  // announced. It is the only polite region in either state, so nothing is
  // announced twice.
  if (loading) {
    const stageText = loadingStage === "calculating_alternatives"
      ? "Calculating alternative routes…"
      : "Finding the best routes…";
    return withPanel(
      <div className="flex flex-col gap-2 rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
        <p role="status" aria-live="polite" className="sr-only">
          {stageText}
        </p>
        <div className="h-4 w-2/3 animate-pulse rounded bg-surface-sunken" />
        <div className="h-16 animate-pulse rounded bg-surface-sunken" />
        <div className="h-16 animate-pulse rounded bg-surface-sunken" />
        <p className="text-xs text-ink-secondary">{stageText}</p>
      </div>
    );
  }

  if (error) {
    return withPanel(
      <InlineAlert
        variant="error"
        action={
          onRetry
            ? {
                label: "Retry",
                onClick: () => {
                  // This button is about to be replaced by the loading card;
                  // move focus to the stable panel root first so it is not
                  // dropped to <body>.
                  onRetry();
                  panelRef.current?.focus();
                },
              }
            : undefined
        }
      >
        {error}
      </InlineAlert>
    );
  }

  if (result && !result.found) {
    return withPanel(
      <div
        role="status"
        aria-live="polite"
        className="rounded-xl border border-route-line bg-surface-raised p-4 text-sm text-ink shadow-card"
      >
        <p className="font-medium">No bus route found between these stops.</p>
        <p className="mt-1 text-ink-secondary">Try nearby stops, or a different origin or destination.</p>
      </div>
    );
  }

  if (!result) {
    return withPanel(
      <p className="text-sm text-ink-secondary">
        Pick a starting stop and a destination, then hit Find route to see it on the map.
      </p>
    );
  }

  // result.found === true from here on.
  const isPrimary = selectedIndex === -1;
  const active: RouteFinderResult | RouteAlternative = isPrimary
    ? result
    : result.alternatives[selectedIndex];

  const legs = active.legs;
  const rideLegs = legs.filter((leg) => leg.route_id !== TRANSFER_ROUTE_ID);
  const walkLegs = legs.filter((leg) => leg.route_id === TRANSFER_ROUTE_ID);
  const activeAlt = isPrimary ? null : result.alternatives[selectedIndex];
  // Each option carries its own fare now, so the selected alternative's
  // card never borrows the primary result's fare (the old bug).
  const activeFare = active.fare ?? null;
  // Same defensive formatting as the route-detail panel: null when the fare
  // object is missing OR either amount is absent/non-finite/negative, so a
  // malformed response can never render as "Rs. undefined" or "Rs. NaN".
  const activeFareRange = activeFare ? formatFareRange(activeFare) : null;
  // A journey total is complete only when every ride leg matched a fare
  // band. When one didn't, the range covers just the priced legs, so the
  // panel must say so rather than present a partial sum as the total.
  const fareIncomplete = activeFare ? !activeFare.complete : false;
  const verificationNote =
    activeFare &&
    typeof activeFare.verification_note === "string" &&
    activeFare.verification_note.trim() !== ""
      ? activeFare.verification_note
      : null;

  // Trip time is ALWAYS the backend's fixed-speed estimate, never the
  // OSRM driving-profile duration_s -- that describes how long a private
  // car would take at free-flow speed, not a bus that stops, boards and
  // sits in traffic. Prefer the per-result total the backend computed;
  // for hand-built/external data fall back to summing each leg's own
  // estimate. Labeled "(est.)" unconditionally, because it always is one.
  const totalDurationS =
    active.total_estimated_duration_s ??
    legs.reduce((sum, leg) => sum + (leg.estimated_duration_s ?? 0), 0);
  const duration = formatDuration(totalDurationS);
  const walkDurationS = walkLegs.reduce(
    (sum, leg) => sum + (leg.estimated_duration_s ?? 0),
    0
  );
  const hasWalkDuration =
    walkLegs.length > 0 && walkLegs.every((leg) => leg.estimated_duration_s != null);

  const cardLabel = isPrimary
    ? active.transfer_count === 0
      ? "Direct"
      : "Recommended"
    : ALTERNATIVE_LABELS[activeAlt!.label];

  // Only the primary carries a rank_reason. Show it when a genuine choice
  // was made (there were alternatives) or when the primary is a transfer --
  // exactly the case the bare "Recommended" label failed to explain. A lone
  // direct route needs no explanation beyond "Direct".
  const primaryReason =
    isPrimary && result.rank_reason
      ? rankReasonText(result.rank_reason, result.alternatives.length > 0)
      : undefined;
  const showPrimaryReason =
    primaryReason !== undefined &&
    (result.alternatives.length > 0 || active.transfer_count > 0);

  // A concise, screen-reader-only status for the found state. The results
  // panel itself is deliberately NOT a live region: wrapping the whole card
  // (alternatives, timeline, fare notes) in aria-live means every re-render
  // -- including selecting a different alternative -- replays the entire
  // card. This one-line summary is the whole announcement; the visible
  // content is read on demand as the user moves through it.
  const transferSummary =
    active.transfer_count > 0
      ? `, ${active.transfer_count} transfer${active.transfer_count > 1 ? "s" : ""}`
      : ", direct";
  const statusSummary = `${cardLabel}. Estimated ${duration.value} ${duration.unit}${transferSummary}.`;

  return withPanel(
    <div className="flex flex-col gap-3 rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
      <p role="status" aria-live="polite" className="sr-only">
        {statusSummary}
      </p>
      {result.alternatives.length > 0 && (
        <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5" role="group" aria-label="Route alternatives">
          <SelectablePill
            onClick={() => onSelectedIndexChange(-1)}
            selected={isPrimary}
            aria-label={result.transfer_count === 0 ? "Direct route" : "Recommended route"}
          >
            {result.transfer_count === 0 ? "Direct" : "Recommended"}
          </SelectablePill>
          {result.alternatives.map((alt, i) => (
            <SelectablePill
              key={`${alt.label}-${i}`}
              onClick={() => onSelectedIndexChange(i)}
              selected={selectedIndex === i}
              aria-label={`${ALTERNATIVE_LABELS[alt.label]}: ${formatAltSummary(alt)}`}
            >
              {ALTERNATIVE_LABELS[alt.label]}
            </SelectablePill>
          ))}
        </div>
      )}

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-accent-purple">{cardLabel}</p>

        {showPrimaryReason && (
          <>
            <p className="mt-1 text-xs text-ink-secondary">{primaryReason}</p>
            <p className="mt-0.5 text-xs text-ink-tertiary">
              Ranked by straight-line stop-to-stop distance and number of transfers; frequency and
              fare are shown for information only and are not used to rank.
            </p>
          </>
        )}

        <div className="mt-1.5 flex items-baseline gap-2">
          <span className="text-3xl font-bold leading-none tracking-tight text-ink">{duration.value}</span>
          <span className="text-sm font-medium text-ink-secondary">{duration.unit}</span>
          <span className="text-xs text-accent-yellow-text font-medium ml-1">(est.)</span>
        </div>

        <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-ink-secondary">
          <span className="inline-flex items-center gap-1">
            <TransferIcon size={13} />
            {active.transfer_count === 0
              ? "Direct"
              : `${active.transfer_count} transfer${active.transfer_count > 1 ? "s" : ""}`}
          </span>
          <span className="inline-flex items-center gap-1">
            <BusIcon size={13} />
            {rideLegs.length} bus{rideLegs.length === 1 ? "" : "es"}
          </span>
          {hasWalkDuration && walkDurationS > 0 && (
            <span className="inline-flex items-center gap-1">
              <WalkIcon size={13} />
              {Math.max(1, Math.round(walkDurationS / 60))} min walking
            </span>
          )}
          <span className="inline-flex items-center gap-1 font-semibold text-ink">
            {activeFareRange ?? "Fare unavailable"}
          </span>
        </div>

        <p className="mt-2 inline-flex items-center gap-1 text-xs text-ink-tertiary">
          <ClockIcon size={12} />
          Time is an estimate from assumed travel speeds, not a live ETA.
        </p>
        {activeFareRange && (
          <>
            <p className="mt-1 text-xs text-ink-tertiary">
              {fareIncomplete
                ? "Estimated fare covering only the buses we could price — not a final ticket price."
                : "Estimated fare, not a final ticket price."}
            </p>
            {fareIncomplete && (
              <p className="mt-1 text-xs text-ink-tertiary">
                {activeFare!.priced_leg_count} of {activeFare!.ride_leg_count} bus{" "}
                {activeFare!.ride_leg_count === 1 ? "leg has" : "legs have"} no fare
                estimate, so this total may be higher.
              </p>
            )}
            {verificationNote && (
              <details className="mt-1 text-xs text-ink-tertiary">
                <summary className="cursor-pointer">Fare verification information</summary>
                <p className="mt-1">{verificationNote}</p>
              </details>
            )}
          </>
        )}
        {activeAlt?.label === "fastest_estimated" && (
          <p className="mt-1 inline-flex items-center gap-1 text-xs text-ink-tertiary">
            <ClockIcon size={12} />
            This option was ranked by estimated travel time.
          </p>
        )}
      </div>

      <div className="border-t border-route-line pt-3">
        <RouteTimeline
          legs={legs}
          selectedLegIndex={selectedLegIndex}
          onLegSelect={onLegSelect}
        />
      </div>
    </div>
  );
}