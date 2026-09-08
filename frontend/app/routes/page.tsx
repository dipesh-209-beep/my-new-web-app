"use client";

import Link from "next/link";
import { useRouteBrowser } from "@/hooks/useRouteBrowser";
import { formatRouteDistance } from "@/lib/routeDistance";
import { BusIcon } from "@/components/icons/TransitIcons";

export default function RoutesPage() {
  const { routes, total, loading, loadingMore, searchQuery, setSearchQuery, hasMore, loadMore } =
    useRouteBrowser();

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-4 overflow-y-auto p-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Browse routes</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          {total > 0 ? `${total} routes across the Valley` : "Browse every route in the network"}
        </p>
      </div>

      <input
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        placeholder="Search by route name or number…"
        className="w-full rounded-md border border-route-line bg-surface-raised px-3 py-2 text-sm text-ink outline-none focus:border-accent-green"
      />

      {loading && routes.length === 0 && (
        <div className="flex flex-col gap-2">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-16 animate-pulse rounded-xl border border-route-line bg-surface-sunken" />
          ))}
        </div>
      )}

      {!loading && routes.length === 0 && (
        <p className="text-sm text-ink-secondary">
          {searchQuery.trim()
            ? `No routes match "${searchQuery.trim()}".`
            : "No routes available."}
        </p>
      )}

      <ul className="flex flex-col divide-y divide-route-line rounded-xl border border-route-line bg-surface-raised shadow-card">
        {routes.map((route) => (
          <li key={route.route_id}>
            <Link
              href={`/routes/${encodeURIComponent(route.route_id)}`}
              className="flex items-center gap-3 px-4 py-3 hover:bg-surface"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-green/10 text-accent-green">
                <BusIcon size={15} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">
                  {route.short_name ? `${route.short_name} — ` : ""}
                  {route.route_name}
                </p>
                <p className="truncate font-mono text-xs text-ink-secondary">
                  {route.vehicle_type} · {route.total_stops} stops
                  {formatRouteDistance(route) && <> · {formatRouteDistance(route)}</>}
                  {route.operator && <> · {route.operator.name}</>}
                </p>
              </div>
              {route.status !== "active" && (
                <span className="shrink-0 rounded-full bg-accent-yellow/10 px-2 py-0.5 text-xs text-accent-yellow">
                  {route.status}
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>

      {hasMore && (
        <button
          type="button"
          onClick={loadMore}
          disabled={loadingMore}
          className="self-center rounded-md border border-route-line px-4 py-2 text-sm text-ink-secondary hover:border-accent-green hover:text-accent-green disabled:opacity-50"
        >
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
