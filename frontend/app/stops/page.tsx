"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useStops } from "@/hooks/useStops";
import { buildStopLabel } from "@/lib/stopLabel";
import { PinDotIcon } from "@/components/icons/TransitIcons";
import InlineAlert from "@/components/ui/InlineAlert";

export default function StopsPage() {
  const { stops, loading, error } = useStops();
  const [query, setQuery] = useState("");

  useEffect(() => {
    document.title = "All stops — Kathmandu Bus Route Finder";
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return stops;
    return stops.filter(
      (stop) =>
        stop.stop_name.toLowerCase().includes(q) ||
        (stop.district?.toLowerCase().includes(q) ?? false)
    );
  }, [stops, query]);

  // Filtering here is client-side and instant, so a live region bound directly
  // to `filtered.length` would announce a new count on every keystroke. The
  // query the announcement is built from lags the typed query by 300 ms (the
  // interval the server-side route search already uses), so the status only
  // settles once the user pauses. An empty (or not-yet-settled) query
  // announces nothing, which also makes clearing the field silent.
  const [debouncedQuery, setDebouncedQuery] = useState(query);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(timer);
  }, [query]);

  const trimmedQuery = query.trim();
  const settled = trimmedQuery !== "" && debouncedQuery === query;
  const filterStatus = !settled
    ? ""
    : filtered.length === 0
      ? `No stops match "${trimmedQuery}".`
      : `${filtered.length} ${filtered.length === 1 ? "stop matches" : "stops match"} "${trimmedQuery}".`;

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-4 overflow-y-auto p-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">All stops</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          {stops.length > 0 ? `${stops.length} stops across the Valley` : "Browse every stop"}
        </p>
      </div>

      <p role="status" aria-live="polite" className="sr-only">
        {filterStatus}
      </p>

      <label htmlFor="stops-search" className="sr-only">
        Search by stop name or district…
      </label>
      <input
        id="stops-search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search by stop name or district…"
        className="w-full rounded-md border border-route-line bg-surface-raised px-3 py-2.5 text-base text-ink outline-none focus:border-accent-blue"
      />

      {error && (
        <InlineAlert variant="error">Couldn&apos;t load the stop list from the server.</InlineAlert>
      )}

      {loading && (
        <div className="flex flex-col gap-2">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded-xl border border-route-line bg-surface-sunken" />
          ))}
        </div>
      )}

      {!loading && filtered.length === 0 && stops.length > 0 && (
        <p className="text-sm text-ink-secondary">No stops match &quot;{query.trim()}&quot;.</p>
      )}

      <ul className="flex flex-col divide-y divide-route-line rounded-xl border border-route-line bg-surface-raised shadow-card">
        {filtered.map((stop) => (
          <li key={stop.stop_id}>
            <Link
              href={`/stops/${encodeURIComponent(stop.stop_id)}`}
              className="flex items-center gap-3 px-4 py-3 hover:bg-surface"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-blue/10 text-accent-blue">
                <PinDotIcon size={12} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">{buildStopLabel(stop, stops)}</p>
                {stop.district && <p className="truncate font-mono text-xs text-ink-secondary">{stop.district}</p>}
              </div>
              <div className="flex shrink-0 gap-1">
                {stop.is_interchange && (
                  <span className="rounded-full bg-accent-purple/10 px-2 py-0.5 text-xs text-accent-purple">
                    Interchange
                  </span>
                )}
                {stop.status !== "active" && (
                  <span className="rounded-full bg-accent-yellow/10 px-2 py-0.5 text-xs text-accent-yellow-text">
                    {stop.status}
                  </span>
                )}
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
