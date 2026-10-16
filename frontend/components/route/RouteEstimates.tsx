import { FareAmounts, FareOut } from "@/types/route";

interface RouteEstimatesProps {
  /** Whole-route estimated running time in minutes (RouteOut.estimated_duration_min).
   * Null when the source data has no estimate for this route. */
  durationMinutes: number | null;
  /** Scheduled headway in whole minutes (RouteOut.frequency_min). Null when
   * the source has no usable value. A scheduled/estimated interval, not a
   * live arrival countdown. */
  frequencyMinutes?: number | null;
  /** Scheduled service window as "HH:MM" (RouteOut.service_start_time /
   * service_end_time). Both come together or not at all -- the backend drops
   * a one-sided window to null on both ends. */
  serviceStartTime?: string | null;
  serviceEndTime?: string | null;
  /** Distance-banded fare covering the route's full length, or null when
   * there's no route distance or no band covers it. */
  fare: FareOut | null;
  /** True while the fare lookup is still in flight, so the panel can say
   * "Checking…" instead of flashing a misleading "unavailable". */
  fareLoading?: boolean;
}

/** True only for a real, finite, non-negative number. Guards the formatters
 * below against null/undefined/NaN/Infinity, strings, or negative values from
 * a malformed or unexpected server response. Fares are NUMERIC(7,2) CHECK
 * (>= 0) and durations are non-negative, so a legitimate zero must survive. */
function isValidAmount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

/** "45 min" / "2 hr" / "1h 6m" -- mirrors the route-results panel's unit
 * style so the same figure reads the same across the app. */
export function formatDurationMinutes(minutes: number): string {
  const rounded = Math.round(minutes);
  if (rounded < 60) return `${rounded} min`;
  const hours = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return rest === 0 ? `${hours} hr` : `${hours}h ${rest}m`;
}

/** NPR amount with up to two decimals (the fare columns are NUMERIC(7,2)),
 * trailing zeros dropped: 30 -> "30", 24.5 -> "24.5", 24.05 -> "24.05". */
function formatNpr(amount: number): string {
  return amount.toFixed(2).replace(/\.?0+$/, "");
}

/** Strict 24-hour "HH:MM". The backend normalizes service times to this
 * exact shape, so anything else is treated as absent. */
const HH_MM = /^([01]\d|2[0-3]):([0-5]\d)$/;

/** "Every ~15 min" / "Every ~1 hr" for a scheduled headway, or null when the
 * value isn't a usable positive number. The "~" flags it as an estimate: the
 * source data stores a single near-uniform value per route, so this is a
 * planning figure, never a live headway or countdown. */
export function formatFrequencyInterval(minutes: number | null | undefined): string | null {
  if (!isValidAmount(minutes) || minutes <= 0) return null;
  return `Every ~${formatDurationMinutes(minutes)}`;
}

/** "06:00 – 20:00" when both bounds are valid HH:MM and the start precedes
 * the end, else null. Re-checks what the backend already guarantees so a
 * malformed payload can never render a half or reversed window; the schema
 * has no overnight semantics, so a same-or-earlier end is treated as
 * unknown. */
export function formatServiceHours(
  start: string | null | undefined,
  end: string | null | undefined,
): string | null {
  if (typeof start !== "string" || typeof end !== "string") return null;
  if (!HH_MM.test(start) || !HH_MM.test(end) || start >= end) return null;
  return `${start} – ${end}`;
}

/** "Rs. 30–35", or "Rs. 30" when the band's min and max are equal. Returns
 * null when either amount is missing, non-numeric, non-finite, or negative,
 * so the caller shows an unavailable state rather than "Rs. undefined" or
 * "Rs. NaN". Accepts any min/max pair (a single FareOut band or a summed
 * JourneyFareOut total). */
export function formatFareRange(fare: FareAmounts): string | null {
  const { fare_npr_min: min, fare_npr_max: max } = fare;
  if (!isValidAmount(min) || !isValidAmount(max)) return null;
  return min === max ? `Rs. ${formatNpr(min)}` : `Rs. ${formatNpr(min)}–${formatNpr(max)}`;
}

/**
 * Whole-route duration and fare estimates for the route-detail page. Both
 * figures are estimates and are labelled as such: the running time is a
 * planning estimate for the full route (not a live ETA or a door-to-door
 * journey time), and the fare is a distance-banded desk estimate that is
 * never presented as an official or final price. Missing or malformed data
 * shows an honest unavailable state rather than a fabricated value.
 */
export default function RouteEstimates({
  durationMinutes,
  frequencyMinutes = null,
  serviceStartTime = null,
  serviceEndTime = null,
  fare,
  fareLoading = false,
}: RouteEstimatesProps) {
  const frequencyLabel = formatFrequencyInterval(frequencyMinutes);
  const hoursLabel = formatServiceHours(serviceStartTime, serviceEndTime);
  const fareRange = fare ? formatFareRange(fare) : null;
  const verificationNote =
    fare && typeof fare.verification_note === "string" && fare.verification_note.trim() !== ""
      ? fare.verification_note
      : null;

  return (
    <section
      aria-label="Journey estimates"
      className="rounded-xl border border-route-line bg-surface-raised p-3 shadow-card"
    >
      <h2 className="text-xs font-semibold uppercase tracking-wide text-accent-purple">
        Journey estimates
      </h2>

      <div className="mt-2">
        <p className="text-sm text-ink-secondary">Estimated duration</p>
        {isValidAmount(durationMinutes) ? (
          <p className="text-lg font-semibold text-ink">{formatDurationMinutes(durationMinutes)}</p>
        ) : (
          <p className="text-sm text-ink-secondary">Unavailable for this route.</p>
        )}
        <p className="mt-1 text-xs text-ink-tertiary">
          Estimated running time for the whole route — not a live ETA, and not a door-to-door
          journey time.
        </p>
      </div>

      <div className="mt-3 border-t border-route-line pt-3">
        <p className="text-sm text-ink-secondary">Estimated fare (full route)</p>
        {fareLoading ? (
          <p className="text-sm text-ink-secondary">Checking fare…</p>
        ) : fareRange ? (
          <>
            <p className="text-lg font-semibold text-ink">{fareRange}</p>
            <p className="mt-1 text-xs text-ink-tertiary">
              Estimated fare to ride the full route — not a final ticket price.
            </p>
            {verificationNote && (
              <details className="mt-1 text-xs text-ink-tertiary">
                <summary className="cursor-pointer">Fare verification information</summary>
                <p className="mt-1">{verificationNote}</p>
              </details>
            )}
          </>
        ) : (
          <p className="text-sm text-ink-secondary">Fare estimate unavailable for this route.</p>
        )}
      </div>

      <div className="mt-3 border-t border-route-line pt-3">
        <p className="text-sm text-ink-secondary">Typical frequency</p>
        {frequencyLabel ? (
          <>
            <p className="text-lg font-semibold text-ink">{frequencyLabel}</p>
            <p className="mt-1 text-xs text-ink-tertiary">
              Estimated interval between departures from route data — not a live arrival time.
            </p>
          </>
        ) : (
          <p className="text-sm text-ink-secondary">Frequency unavailable for this route.</p>
        )}
      </div>

      <div className="mt-3 border-t border-route-line pt-3">
        <p className="text-sm text-ink-secondary">Typical service hours</p>
        {hoursLabel ? (
          <>
            <p className="text-lg font-semibold text-ink">{hoursLabel}</p>
            <p className="mt-1 text-xs text-ink-tertiary">
              Scheduled operating hours recorded for this route — not real-time, and a bus
              isn&apos;t necessarily running right now.
            </p>
          </>
        ) : (
          <p className="text-sm text-ink-secondary">Service hours unavailable for this route.</p>
        )}
      </div>
    </section>
  );
}
