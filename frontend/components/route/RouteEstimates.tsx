import { FareOut } from "@/types/route";

interface RouteEstimatesProps {
  /** Whole-route estimated running time in minutes (RouteOut.estimated_duration_min).
   * Null when the source data has no estimate for this route. */
  durationMinutes: number | null;
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

/** "Rs. 30–35", or "Rs. 30" when the band's min and max are equal. Returns
 * null when either amount is missing, non-numeric, non-finite, or negative,
 * so the caller shows an unavailable state rather than "Rs. undefined" or
 * "Rs. NaN". */
export function formatFareRange(fare: FareOut): string | null {
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
  fare,
  fareLoading = false,
}: RouteEstimatesProps) {
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
    </section>
  );
}
