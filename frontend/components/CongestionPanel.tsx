"use client";

import { LayersIcon } from "@/components/icons/TransitIcons";
import { CONGESTION_COLORS } from "@/lib/constants";
import LayerToggle from "@/components/ui/LayerToggle";
import { CongestionProvenance } from "@/types/route";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOUR_BUCKETS = [0, 3, 6, 9, 12, 15, 18, 21];

// Shown when the API didn't send a provenance note (e.g. an older cached
// response). Kept here so the disclosure can never be silently absent.
const FALLBACK_PROVENANCE_NOTE =
  "Estimated, not measured. Congestion values are derived from OSRM free-flow " +
  "durations and a static severity table whose origin is not recorded — there " +
  "is no live or real-time traffic feed, and a green segment does not guarantee " +
  "a clear road.";

function formatHour(hour: number): string {
  const period = hour < 12 ? "AM" : "PM";
  const display = hour % 12 === 0 ? 12 : hour % 12;
  return `${display}${period}`;
}

interface CongestionPanelProps {
  enabled: boolean;
  onToggle: () => void;
  // null = "now" (server picks the current Nepal-time bucket); a picked
  // value overrides it so the user can browse other times of day.
  dayOfWeek: number | null;
  hourBucket: number | null;
  onDayChange: (day: number | null) => void;
  onHourChange: (hour: number | null) => void;
  loading?: boolean;
  segmentCount: number;
  hasSeededOnly: boolean;
  /** True when the last load failed; distinct from "no data", so an empty
   * overlay never reads as "checked, roads clear". */
  error?: boolean;
  /** API-reported provenance (derived/not-live). Falls back to a constant
   * honest note when absent. */
  provenance?: CongestionProvenance | null;
}

export default function CongestionPanel({
  enabled,
  onToggle,
  dayOfWeek,
  hourBucket,
  onDayChange,
  onHourChange,
  loading,
  segmentCount,
  hasSeededOnly,
  error = false,
  provenance = null,
}: CongestionPanelProps) {
  const isCustomTime = dayOfWeek !== null || hourBucket !== null;
  const provenanceNote = provenance?.note?.trim() || FALLBACK_PROVENANCE_NOTE;

  let summary: string;
  if (loading) {
    summary = "Loading…";
  } else if (segmentCount === 0) {
    summary = error
      ? "Couldn't load congestion estimates. The overlay may be incomplete."
      : "No congestion estimate for this time.";
  } else {
    const noun = segmentCount === 1 ? "segment estimate" : "segment estimates";
    const baseline = hasSeededOnly ? " All are free-flow baseline (OSRM), not reported congestion." : "";
    const refresh = error ? " Couldn't refresh; showing an earlier estimate." : "";
    summary = `${segmentCount} ${noun}.${baseline}${refresh}`;
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
      <LayerToggle
        icon={<LayersIcon size={14} />}
        iconColorClass="text-accent-orange"
        iconBgClass="bg-accent-orange/10"
        activeBgClass="bg-accent-orange"
        hoverClass="hover:border-accent-orange hover:text-accent-orange"
        title="Traffic"
        subtitle="Estimated congestion overlay"
        enabled={enabled}
        onToggle={onToggle}
      />

      {enabled && (
        <>
          <p className="text-xs text-ink-secondary">{provenanceNote}</p>

          <p className="text-xs text-ink-tertiary">
            Shown as an overlay only — it doesn&apos;t re-rank the routes below. Lines are drawn
            straight between each segment&apos;s two stops, not along roads; route search
            (&ldquo;Avoid congested routes&rdquo;) also uses area zones that aren&apos;t drawn here.
          </p>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label htmlFor="congestion-day" className="sr-only">
              Day of week
            </label>
            <select
              id="congestion-day"
              value={dayOfWeek ?? "now"}
              onChange={(e) => onDayChange(e.target.value === "now" ? null : Number(e.target.value))}
              className="min-h-11 rounded-md border border-route-line bg-white px-2 py-1 text-ink"
            >
              <option value="now">Today</option>
              {DAY_LABELS.map((label, i) => (
                <option key={label} value={i}>
                  {label}
                </option>
              ))}
            </select>
            <label htmlFor="congestion-hour" className="sr-only">
              Time of day
            </label>
            <select
              id="congestion-hour"
              value={hourBucket ?? "now"}
              onChange={(e) => onHourChange(e.target.value === "now" ? null : Number(e.target.value))}
              className="min-h-11 rounded-md border border-route-line bg-white px-2 py-1 text-ink"
            >
              <option value="now">Now</option>
              {HOUR_BUCKETS.map((h) => (
                <option key={h} value={h}>
                  {formatHour(h)}–{formatHour((h + 3) % 24)}
                </option>
              ))}
            </select>
            {isCustomTime && (
              <button
                type="button"
                onClick={() => {
                  onDayChange(null);
                  onHourChange(null);
                }}
                className="inline-flex min-h-11 items-center text-accent-orange hover:underline"
              >
                Reset to now
              </button>
            )}
          </div>

          <div className="flex flex-col gap-1">
            <div
              aria-hidden
              className="h-2 w-full rounded-full"
              style={{
                background: `linear-gradient(to right, ${CONGESTION_COLORS.free_flow}, ${CONGESTION_COLORS.moderate}, ${CONGESTION_COLORS.heavy})`,
              }}
            />
            <div className="flex justify-between text-xs text-ink-secondary">
              <span>Low</span>
              <span>Moderate</span>
              <span>Heavy</span>
            </div>
          </div>

          <p className="font-mono text-xs text-ink-secondary">{summary}</p>
        </>
      )}
    </div>
  );
}
