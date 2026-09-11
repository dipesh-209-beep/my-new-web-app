"use client";

import { FareOut, LoadingStage, RouteAlternative, RouteLeg, RouteSearchResult } from "@/types/route";
import RouteTimeline from "./RouteTimeline";
import { BusIcon, ClockIcon, TransferIcon, WalkIcon } from "@/components/icons/TransitIcons";
import InlineAlert from "@/components/ui/InlineAlert";
import { TRANSFER_ROUTE_ID } from "@/lib/constants";

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
}

const ALTERNATIVE_LABELS: Record<RouteAlternative["label"], string> = {
  alternate_direct_route: "Direct alternative",
  shortest_distance: "Least distance",
  fastest_estimated: "Fastest (est.)",
};

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

function formatFare(fare: FareOut): string {
  const range =
    fare.fare_npr_min === fare.fare_npr_max
      ? `Rs. ${fare.fare_npr_min}`
      : `Rs. ${fare.fare_npr_min}–${fare.fare_npr_max}`;
  return fare.student_discount_pct ? `${range} (${fare.student_discount_pct}% off, students)` : range;
}

// Simple haversine distance for straight-line estimation
function calculateStraightLineDistance(
  from: { lat: number; lng: number },
  to: { lat: number; lng: number }
): number {
  const R = 6371; // Earth radius in km
  const toRad = (deg: number) => deg * Math.PI / 180;
  const dLat = toRad(to.lat - from.lat);
  const dLng = toRad(to.lng - from.lng);
  const lat1 = toRad(from.lat);
  const lat2 = toRad(to.lat);
  const sinDLat = Math.sin(dLat / 2);
  const sinDLng = Math.sin(dLng / 2);
  const haversineA = sinDLat * sinDLat + sinDLng * sinDLng * Math.cos(lat1) * Math.cos(lat2);
  const c = 2 * Math.atan2(Math.sqrt(haversineA), Math.sqrt(1 - haversineA));
  return R * c;
}

export default function RouteResultPanel({
  result,
  loading,
  loadingStage = "idle",
  error,
  selectedIndex,
  onSelectedIndexChange,
  onRetry,
}: RouteResultPanelProps) {
  if (loading) {
    const stageText = loadingStage === "calculating_alternatives"
      ? "Calculating alternative routes…"
      : "Finding the best routes…";
    return (
      <div
        aria-live="polite"
        className="flex flex-col gap-2 rounded-xl border border-route-line bg-surface-raised p-4 shadow-card"
      >
        <div className="h-4 w-2/3 animate-pulse rounded bg-surface-sunken" />
        <div className="h-16 animate-pulse rounded bg-surface-sunken" />
        <div className="h-16 animate-pulse rounded bg-surface-sunken" />
        <p className="text-xs text-ink-secondary">{stageText}</p>
      </div>
    );
  }

  if (error) {
    return (
      <InlineAlert variant="error" action={onRetry ? { label: "Retry", onClick: onRetry } : undefined}>
        {error}
      </InlineAlert>
    );
  }

  if (result && !result.found) {
    return (
      <div className="rounded-xl border border-route-line bg-surface-raised p-4 text-sm text-ink shadow-card">
        <p className="font-medium">No bus route found between these stops.</p>
        <p className="mt-1 text-ink-secondary">Try nearby stops, or a different origin or destination.</p>
      </div>
    );
  }

  if (!result) {
    return (
      <p className="text-sm text-ink-secondary">
        Pick a starting stop and a destination, then hit Find route to see it on the map.
      </p>
    );
  }

  // result.found === true from here on.
  const isPrimary = selectedIndex === -1;
  const active: { legs: RouteLeg[]; total_cost: number; transfer_count: number } = isPrimary
    ? result
    : result.alternatives[selectedIndex];

  const legs = active.legs;
  const rideLegs = legs.filter((leg) => leg.route_id !== TRANSFER_ROUTE_ID);
  const walkLegs = legs.filter((leg) => leg.route_id === TRANSFER_ROUTE_ID);

  // Backend now attaches real OSRM road_geometry to alternatives too
  // (deduplicated to genuinely distinct paths, capped at 2, so it's no
  // longer the tripled-OSRM-calls cost it used to be) -- so this checks
  // the data itself rather than assuming only the primary result has
  // it. Kept as a per-leg check (not just "isPrimary") because OSRM can
  // still fail for an individual leg at request time (see
  // _attach_road_geometry's `except OSRMError: pass`), leaving that
  // leg's road_geometry null regardless of which option it's on.
  const allLegsHaveGeometry = legs.every((leg) => leg.road_geometry);

  const activeAlt = isPrimary ? null : result.alternatives[selectedIndex];

  // Calculate estimated duration when road geometry is missing
  // Use average speed of ~20 km/h for bus, ~4.5 km/h for walking
  function estimateDuration(legsToEstimate: RouteLeg[]): number {
    const BUS_SPEED_KMH = 20;
    const WALK_SPEED_KMH = 4.5;
    let totalSeconds = 0;
    for (const leg of legsToEstimate) {
      const isWalk = leg.route_id === TRANSFER_ROUTE_ID;
      const speedKmh = isWalk ? WALK_SPEED_KMH : BUS_SPEED_KMH;
      // Use road_geometry distance if available, otherwise straight-line distance
      const distanceKm = leg.road_geometry
        ? leg.road_geometry.distance_m / 1000
        : calculateStraightLineDistance(leg.board_stop, leg.alight_stop);
      totalSeconds += (distanceKm / speedKmh) * 3600;
    }
    return totalSeconds;
  }

  const totalDurationS = allLegsHaveGeometry
    ? legs.reduce((sum, leg) => sum + (leg.road_geometry?.duration_s ?? 0), 0)
    : estimateDuration(legs);
  const duration = formatDuration(totalDurationS);
  const isEstimated = !allLegsHaveGeometry;
  const walkDurationS = walkLegs.reduce((sum, leg) => sum + (leg.road_geometry?.duration_s ?? 0), 0);
  const hasWalkDuration = walkLegs.length > 0 && walkLegs.every((leg) => leg.road_geometry);

  const cardLabel = isPrimary
    ? active.transfer_count === 0
      ? "Direct"
      : "Recommended"
    : ALTERNATIVE_LABELS[activeAlt!.label];

  return (
    <div
      aria-live="polite"
      className="flex flex-col gap-3 rounded-xl border border-route-line bg-surface-raised p-4 shadow-card"
    >
      {result.alternatives.length > 0 && (
        <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5" role="group" aria-label="Route alternatives">
          <button
            type="button"
            onClick={() => onSelectedIndexChange(-1)}
            aria-pressed={isPrimary}
            aria-label={result.transfer_count === 0 ? "Direct route" : "Recommended route"}
            className={`shrink-0 rounded-full border px-2.5 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-purple ${
              isPrimary
                ? "border-accent-purple bg-accent-purple/10 font-medium text-accent-purple"
                : "border-route-line bg-white text-ink-secondary hover:border-accent-purple hover:text-accent-purple"
            }`}
          >
            {result.transfer_count === 0 ? "Direct" : "Recommended"}
          </button>
          {result.alternatives.map((alt, i) => (
            <button
              key={`${alt.label}-${i}`}
              type="button"
              onClick={() => onSelectedIndexChange(i)}
              aria-pressed={selectedIndex === i}
              aria-label={`${ALTERNATIVE_LABELS[alt.label]}: ${formatAltSummary(alt)}`}
              className={`shrink-0 rounded-full border px-2.5 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-purple ${
                selectedIndex === i
                  ? "border-accent-purple bg-accent-purple/10 font-medium text-accent-purple"
                  : "border-route-line bg-white text-ink-secondary hover:border-accent-purple hover:text-accent-purple"
              }`}
            >
              {ALTERNATIVE_LABELS[alt.label]}
            </button>
          ))}
        </div>
      )}

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-accent-purple">{cardLabel}</p>

        <div className="mt-1.5 flex items-baseline gap-2">
          {isEstimated ? (
            <>
              <span className="text-3xl font-bold leading-none tracking-tight text-ink">{duration.value}</span>
              <span className="text-sm font-medium text-ink-secondary">{duration.unit}</span>
              <span className="text-xs text-accent-yellow font-medium ml-1">(est.)</span>
            </>
          ) : (
            <>
              <span className="text-3xl font-bold leading-none tracking-tight text-ink">{duration.value}</span>
              <span className="text-sm font-medium text-ink-secondary">{duration.unit}</span>
            </>
          )}
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
            {result.fare ? formatFare(result.fare) : "Fare unavailable"}
          </span>
        </div>

        {activeAlt?.label === "fastest_estimated" && (
          <p className="mt-2 inline-flex items-center gap-1 text-xs text-ink-tertiary">
            <ClockIcon size={12} />
            Estimated from assumed travel speeds, not a live ETA.
          </p>
        )}
      </div>

      <div className="border-t border-route-line pt-3">
        <RouteTimeline legs={legs} />
      </div>
    </div>
  );
}