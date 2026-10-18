import { RouteLeg } from "@/types/route";
import { LEG_COLORS, TRANSFER_ROUTE_ID } from "@/lib/constants";
import { BusIcon, WalkIcon } from "@/components/icons/TransitIcons";

interface RouteTimelineProps {
  legs: RouteLeg[];
  /** Index of the leg currently highlighted (as the rider's current step),
   * or null for none. Shared with the map so the itinerary and the drawn
   * route stay in sync. */
  selectedLegIndex?: number | null;
  /** Present only when the leg steps are selectable. Called with the leg
   * index on select, or null when the selected step is toggled off. */
  onLegSelect?: (index: number | null) => void;
}

function legDistanceKm(leg: RouteLeg): string | null {
  const meters = leg.road_geometry?.distance_m;
  if (meters === undefined || meters === null) return null;
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`;
}

function legDurationMin(leg: RouteLeg): string | null {
  // Backend's fixed-speed estimate, never the OSRM driving-profile
  // duration_s (a private car at free-flow speed, not a bus).
  const seconds = leg.estimated_duration_s;
  if (seconds === undefined || seconds === null) return null;
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

/**
 * Renders a route-finder result as explicit board/ride/alight instructions
 * (with transfer steps between them), instead of a bare polyline the rider
 * has to interpret. Only uses data the backend actually returns: route_name,
 * operator_name, direction_towards, board/alight stops, hop count,
 * road_geometry distance and the backend's estimated_duration_s -- never
 * invents fare, timing, a live position, or a walking distance.
 *
 * A ride leg reads as: board at this stop, ride this route (towards this
 * terminus when known), get off at this stop. A `TRANSFER` walking leg is a
 * "Transfer" step naming the stop(s) it connects, so the transfer point and
 * the next leg's "Board at …" together describe the change of buses.
 *
 * When `onLegSelect` is supplied each step becomes a real button, so a
 * keyboard/AT user can mark the step the map should highlight (and react to
 * a leg picked on the map). Without a handler the steps stay plain,
 * non-interactive content rather than dead buttons.
 */
export default function RouteTimeline({
  legs,
  selectedLegIndex = null,
  onLegSelect,
}: RouteTimelineProps) {
  const interactive = typeof onLegSelect === "function";

  return (
    <ol className="flex flex-col">
      {legs.map((leg, i) => {
        const isWalk = leg.route_id === TRANSFER_ROUTE_ID;
        const isLast = i === legs.length - 1;
        const isSelected = interactive && i === selectedLegIndex;
        const distance = legDistanceKm(leg);
        const duration = legDurationMin(leg);
        // Hop count = number of stop-to-stop segments ridden, NOT the
        // physical stop count (which includes the boarding stop and
        // overstates how much of the route the rider covers).
        const hops = leg.num_ride_segments;
        // A transfer between two different stops is a short walk; one
        // between the same stop (a same-stop interchange) has nothing to
        // walk. Either way the transfer point is the stop(s) named here.
        const sameStop = leg.board_stop.stop_id === leg.alight_stop.stop_id;

        const iconNode = (
          <span
            className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
              isWalk ? "bg-surface-sunken text-ink-secondary" : "text-white"
            }`}
            style={isWalk ? undefined : { backgroundColor: LEG_COLORS[i % LEG_COLORS.length] }}
            aria-hidden
          >
            {isWalk ? <WalkIcon size={13} /> : <BusIcon size={13} />}
          </span>
        );

        const bodyNode = (
          <div className="flex-1 pt-0.5">
            {isWalk ? (
              <>
                <p className="text-sm font-medium text-accent-purple">Transfer</p>
                <p className="text-xs text-ink-secondary">
                  {sameStop
                    ? `Transfer at ${leg.board_stop.stop_name}`
                    : `Walk from ${leg.board_stop.stop_name} to ${leg.alight_stop.stop_name}`}
                </p>
                {(distance || duration) && (
                  <p className="font-mono text-xs text-ink-secondary">
                    {[distance, duration].filter(Boolean).join(" · ")}
                  </p>
                )}
              </>
            ) : (
              <>
                <p className="text-sm font-medium text-ink">{leg.route_name}</p>
                <p className="text-xs text-ink-secondary">Board at {leg.board_stop.stop_name}</p>
                {leg.direction_towards && (
                  <p className="text-xs text-ink-secondary">towards {leg.direction_towards}</p>
                )}
                <p className="text-xs text-ink-secondary">Get off at {leg.alight_stop.stop_name}</p>
                <p className="font-mono text-xs text-ink-secondary">
                  {hops} stop{hops === 1 ? "" : "s"}
                  {leg.operator_name && <> · {leg.operator_name}</>}
                  {duration && <> · ~{duration}</>}
                </p>
              </>
            )}
          </div>
        );

        return (
          <li key={`${leg.route_id}-${i}`} className="relative flex gap-3 pb-4 last:pb-0">
            {!isLast && (
              <span
                aria-hidden
                className={`absolute left-[11px] top-6 h-[calc(100%-1.25rem)] w-px ${
                  isWalk ? "border-l border-dashed border-route-line bg-transparent" : "bg-route-line"
                }`}
              />
            )}
            {interactive ? (
              <button
                type="button"
                onClick={() => onLegSelect!(isSelected ? null : i)}
                aria-pressed={isSelected}
                className={`-mx-1 flex flex-1 gap-3 rounded-md px-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-purple ${
                  isSelected ? "bg-accent-purple/5 ring-1 ring-accent-purple/40" : ""
                }`}
              >
                <span className="sr-only">
                  Step {i + 1} of {legs.length}:{" "}
                </span>
                {iconNode}
                {bodyNode}
              </button>
            ) : (
              <>
                {iconNode}
                {bodyNode}
              </>
            )}
          </li>
        );
      })}
    </ol>
  );
}
