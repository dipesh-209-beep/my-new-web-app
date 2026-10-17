"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { useRouteBrowser } from "@/hooks/useRouteBrowser";
import { formatRouteDistance } from "@/lib/routeDistance";
import { BusIcon } from "@/components/icons/TransitIcons";
import InlineAlert from "@/components/ui/InlineAlert";

export default function RoutesPage() {
  const { routes, total, loading, loadingMore, error, retry, searchQuery, setSearchQuery, hasMore, loadMore } =
    useRouteBrowser();

  useEffect(() => {
    document.title = "Browse routes — Kathmandu Bus Route Finder";
  }, []);

  // When the last page arrives the Load more button unmounts with focus on it,
  // which would drop focus to <body>. These two effects move focus to the list
  // instead, but only when it was a Load more activation that ended the list --
  // not when typing a query narrows the results, which must not steal focus.
  const listRef = useRef<HTMLDivElement>(null);
  const loadMoreFocusRef = useRef(false);
  useEffect(() => {
    if (!hasMore && loadMoreFocusRef.current) {
      loadMoreFocusRef.current = false;
      // Restore focus only when pagination completing is what dropped it: the
      // button unmounts while focused, so focus falls to <body>. If the user
      // has since moved focus onto a live control -- e.g. into the search field
      // to start a new search while the page request was still in flight --
      // that focus is deliberate and must not be pulled to the list.
      const active = document.activeElement;
      const focusWasLost = active === document.body || !document.body.contains(active);
      if (focusWasLost) listRef.current?.focus();
    }
  }, [hasMore]);
  useEffect(() => {
    // A page finished but more remain: the button is still mounted, so disarm
    // the flag rather than leave it armed for a later, unrelated hasMore change.
    if (!loadingMore) loadMoreFocusRef.current = false;
  }, [loadingMore]);
  function handleLoadMore() {
    loadMoreFocusRef.current = true;
    loadMore();
  }

  // One concise, screen-reader-only status for every way the list changes:
  // the settled result count, the no-match message, and "load more" progress.
  // The visible count paragraph is not a live region, and a live region that
  // appears at the same time as its text is often not announced -- so this
  // always-present region carries the announcement instead. It stays empty
  // (and therefore silent) while a first page is still loading and there are
  // no rows yet, so the initial "Browse every route…" flash is not announced.
  const trimmedQuery = searchQuery.trim();
  const routeNoun = total === 1 ? "route" : "routes";
  // A failed refresh keeps the previously loaded rows on screen (useful, so not
  // discarded), but they are not results of the failed request. Track that so
  // the count, the live-region status and the error copy can say so rather than
  // presenting them as fresh.
  const hasStaleResults = error !== null && routes.length > 0;
  const shownNoun = routes.length === 1 ? "route" : "routes";
  const listStatus = loadingMore
    ? "Loading more routes…"
    : hasStaleResults
      ? `Couldn't refresh the route list. Showing ${routes.length} previously loaded ${shownNoun}.`
      : routes.length > 0
        ? `${total} ${routeNoun} found${hasMore ? `, ${routes.length} shown` : ""}.`
        : !error && trimmedQuery && !loading
          ? `No routes match "${trimmedQuery}".`
          : "";

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-4 overflow-y-auto p-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Browse routes</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          {hasStaleResults
            ? `Showing ${routes.length} previously loaded ${shownNoun}`
            : total > 0
              ? `${total} routes across the Valley`
              : "Browse every route in the network"}
        </p>
      </div>

      <p role="status" aria-live="polite" className="sr-only">
        {listStatus}
      </p>

      <label htmlFor="routes-search" className="sr-only">
        Search by route name or number…
      </label>
      <input
        id="routes-search"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        placeholder="Search by route name or number…"
        className="w-full rounded-md border border-route-line bg-surface-raised px-3 py-2.5 text-base text-ink outline-none focus:border-accent-green"
      />

      {error && (
        <InlineAlert variant="error" action={{ label: "Retry", onClick: retry }}>
          {hasStaleResults ? `${error} Showing the previous results.` : error}
        </InlineAlert>
      )}

      {loading && routes.length === 0 && (
        <div className="flex flex-col gap-2">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-16 animate-pulse rounded-xl border border-route-line bg-surface-sunken" />
          ))}
        </div>
      )}

      {!loading && !error && routes.length === 0 && (
        <p className="text-sm text-ink-secondary">
          {searchQuery.trim()
            ? `No routes match "${searchQuery.trim()}".`
            : "No routes available."}
        </p>
      )}

      <div
        ref={listRef}
        role="region"
        aria-label="Routes"
        tabIndex={-1}
        className="outline-none"
      >
        <ul className="flex flex-col divide-y divide-route-line rounded-xl border border-route-line bg-surface-raised shadow-card">
          {routes.map((route) => {
            const distance = formatRouteDistance(route);
            return (
              <li key={route.route_id}>
                <Link
                  href={`/routes/${encodeURIComponent(route.route_id)}`}
                  className="flex items-center gap-3 px-4 py-3 hover:bg-surface"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-green/10 text-accent-green-text">
                    <BusIcon size={15} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-ink">
                      {route.short_name ? `${route.short_name} — ` : ""}
                      {route.route_name}
                    </p>
                    <p className="truncate font-mono text-xs text-ink-secondary">
                      {route.vehicle_type} · {route.total_stops} stops
                      {distance && <> · {distance}</>}
                    </p>
                  </div>
                  {route.status !== "active" && (
                    <span className="shrink-0 rounded-full bg-accent-yellow/10 px-2 py-0.5 text-xs text-accent-yellow-text">
                      {route.status}
                    </span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>

      {hasMore && (
        <button
          type="button"
          onClick={handleLoadMore}
          aria-disabled={loadingMore}
          className="self-center rounded-md border border-route-line px-4 py-2 text-sm text-ink-secondary hover:border-accent-green hover:text-accent-green aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
        >
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
