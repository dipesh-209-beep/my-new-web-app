"use client";

import { LayersIcon } from "@/components/icons/TransitIcons";
import { CONGESTION_COLORS } from "@/lib/constants";
import LayerToggle from "@/components/ui/LayerToggle";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOUR_BUCKETS = [0, 3, 6, 9, 12, 15, 18, 21];

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
}: CongestionPanelProps) {
  const isCustomTime = dayOfWeek !== null || hourBucket !== null;

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
      <LayerToggle
        icon={<LayersIcon size={14} />}
        iconColorClass="text-accent-orange"
        iconBgClass="bg-accent-orange/10"
        activeBgClass="bg-accent-orange"
        hoverClass="hover:border-accent-orange hover:text-accent-orange"
        title="Map layers"
        subtitle="Traffic conditions"
        enabled={enabled}
        onToggle={onToggle}
      />

      {enabled && (
        <>
          <p className="text-xs text-ink-secondary">
            Historical traffic pattern by segment. Shown as an overlay only -- routes below aren&apos;t
            re-ranked by it.
          </p>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label htmlFor="congestion-day" className="sr-only">
              Day of week
            </label>
            <select
              id="congestion-day"
              value={dayOfWeek ?? "now"}
              onChange={(e) => onDayChange(e.target.value === "now" ? null : Number(e.target.value))}
              className="rounded-md border border-route-line bg-white px-2 py-1 text-ink"
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
              className="rounded-md border border-route-line bg-white px-2 py-1 text-ink"
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
                className="text-accent-orange hover:underline"
              >
                Reset to now
              </button>
            )}
          </div>

          <div className="flex items-center gap-4 text-xs text-ink-secondary">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: CONGESTION_COLORS.free_flow }} />
              Low
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: CONGESTION_COLORS.moderate }} />
              Moderate
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: CONGESTION_COLORS.heavy }} />
              Heavy
            </span>
          </div>

          <p className="font-mono text-xs text-ink-secondary">
            {loading
              ? "Loading…"
              : segmentCount === 0
              ? "No congestion data for this segment/time yet."
              : `${segmentCount} segment${segmentCount === 1 ? "" : "s"} with data.${
                  hasSeededOnly ? " (baseline estimate, not yet confirmed by real traffic)" : ""
                }`}
          </p>
        </>
      )}
    </div>
  );
}
