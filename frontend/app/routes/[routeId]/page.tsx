"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ApiError, getRoute, getRouteGeometry, getRouteStops } from "@/lib/api";
import { formatRouteDistance } from "@/lib/routeDistance";
import SuggestionBox from "@/components/user/SuggestionBox";
import { RouteDirection, RouteGeometry, RouteOut, RouteStopEntry } from "@/types/route";

const BusMap = dynamic(() => import("@/components/BusMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center text-sm text-ink-secondary">
      Loading map…
    </div>
  ),
});

export default function RouteDetailPage() {
  const params = useParams<{ routeId: string }>();
  const routeId = decodeURIComponent(params.routeId);

  const [route, setRoute] = useState<RouteOut | null>(null);
  const [stops, setStops] = useState<RouteStopEntry[]>([]);
  const [geometry, setGeometry] = useState<RouteGeometry | null>(null);
  const [loading, setLoading] = useState(true);
  const [stopsLoading, setStopsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Only meaningful once `route` has loaded and route.is_bidirectional is
  // true -- see the toggle rendered below the map.
  const [direction, setDirection] = useState<RouteDirection>("forward");

  // Route metadata is direction-independent, so it's fetched once per
  // routeId rather than every time the direction toggle is flipped.
  useEffect(() => {
    let cancelled = false;

    async function loadRoute() {
      setLoading(true);
      setNotFound(false);
      setError(null);
      try {
        const routeData = await getRoute(routeId);
        if (!cancelled) setRoute(routeData);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else if (err instanceof ApiError && err.kind === "timeout") {
          setError("This is taking longer than usual. Try again in a moment.");
        } else {
          setError("Couldn't load this route. Try again.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadRoute();
    return () => {
      cancelled = true;
    };
  }, [routeId]);

  // Stops and the road-following geometry ARE direction-dependent, so
  // flipping forward/return refetches exactly these two and nothing else --
  // no re-fetch of route metadata, and no full-page skeleton flash.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      setStopsLoading(true);
      // Clear the previous direction's sequencing while the refetch is in
      // flight, so the list/map never show a stale opposite-direction set.
      setStops([]);
      setGeometry(null);
      try {
        const stopsData = await getRouteStops(routeId, direction);
        if (!cancelled) setStops(stopsData);
      } catch {
        // The route header stays fully usable even if this direction's stop
        // list fails -- the list just stays empty until the next attempt.
      } finally {
        if (!cancelled) setStopsLoading(false);
      }
    }

    // Road-following geometry is a nice-to-have for the map preview -- if
    // OSRM is unavailable or the request fails, BusMap falls back to
    // straight lines between stops, so this failure is swallowed rather
    // than surfaced as a page-level error.
    async function loadGeometry() {
      try {
        const data = await getRouteGeometry(routeId, direction);
        if (!cancelled) setGeometry(data);
      } catch {
        // fall back to straight-line connectors
      }
    }

    load();
    loadGeometry();
    return () => {
      cancelled = true;
    };
  }, [routeId, direction]);

  // Full-page skeleton while the route is first loading. Direction toggles
  // only refetch stops/geometry (see the second effect), so they don't flip
  // the page back to this placeholder.
  if (loading || (!route && stopsLoading)) {
    return (
      <div className="mx-auto flex h-full max-w-2xl flex-col gap-3 overflow-y-auto p-4">
        <div className="h-6 w-1/2 animate-pulse rounded bg-surface-sunken" />
        <div className="h-4 w-1/3 animate-pulse rounded bg-surface-sunken" />
        <div className="mt-4 flex flex-col gap-2">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="h-10 animate-pulse rounded bg-surface-sunken" />
          ))}
        </div>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="mx-auto flex h-full max-w-2xl flex-col items-start gap-2 p-4">
        <p className="text-sm text-ink">
          Route <span className="font-mono">{routeId}</span> doesn&apos;t exist.
        </p>
        <Link href="/routes" className="text-sm text-accent-green hover:underline">
          ← Back to all routes
        </Link>
      </div>
    );
  }

  if (error || !route) {
    return (
      <div className="mx-auto flex h-full max-w-2xl flex-col items-start gap-2 p-4">
        <p role="alert" className="text-sm text-accent-red">
          {error ?? "Something went wrong."}
        </p>
        <Link href="/routes" className="text-sm text-accent-green hover:underline">
          ← Back to all routes
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-full max-w-2xl flex-col gap-4 overflow-y-auto p-4">
      <Link href="/routes" className="text-sm text-ink-secondary hover:text-accent-green">
        ← All routes
      </Link>

      <div>
        <h1 className="text-lg font-semibold text-ink">
          {route.short_name ? `${route.short_name} — ` : ""}
          {route.route_name}
        </h1>
        <p className="mt-1 font-mono text-sm text-ink-secondary">
          {route.vehicle_type} · {route.total_stops} stops
          {formatRouteDistance(route) && <> · {formatRouteDistance(route)}</>}
        </p>
        {route.operator && (
          <p className="text-sm text-ink-secondary">Operated by {route.operator.name}</p>
        )}
        {route.status !== "active" && (
          <span className="mt-2 inline-block rounded-full bg-accent-yellow/10 px-2 py-0.5 text-xs text-accent-yellow">
            {route.status}
          </span>
        )}
      </div>

      <div className="h-56 overflow-hidden rounded-xl border border-route-line shadow-card sm:h-72">
        <BusMap browseRouteStops={stops} browseRouteGeometry={geometry} />
      </div>

      {route.is_bidirectional && (
        <div className="flex items-center gap-2 self-start rounded-lg bg-surface-sunken p-1 text-sm">
          <button
            type="button"
            onClick={() => setDirection("forward")}
            aria-pressed={direction === "forward"}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              direction === "forward"
                ? "bg-white text-ink shadow-sm"
                : "text-ink-secondary hover:text-ink"
            }`}
          >
            Forward
          </button>
          <button
            type="button"
            onClick={() => setDirection("reverse")}
            aria-pressed={direction === "reverse"}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              direction === "reverse"
                ? "bg-white text-ink shadow-sm"
                : "text-ink-secondary hover:text-ink"
            }`}
          >
            Return
          </button>
        </div>
      )}

      <Link
        href={`/?route=${encodeURIComponent(route.route_id)}`}
        className="self-start rounded-md bg-brand px-4 py-2 text-sm font-medium text-ink hover:bg-brand-dark"
      >
        View on full map
      </Link>

      <div>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-accent-purple">
          Stops in order
        </h2>
        <ol className="flex flex-col">
          {stops.map((entry, i) => (
            <li key={`${entry.stop.stop_id}-${entry.sequence_no}`} className="relative flex gap-3 pb-3 last:pb-0">
              {i < stops.length - 1 && (
                <span
                  aria-hidden
                  className="absolute left-[9px] top-5 h-[calc(100%-1rem)] w-px bg-route-line"
                />
              )}
              <span
                aria-hidden
                className="mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border border-route-line bg-white text-[10px] text-ink-secondary"
              >
                {entry.sequence_no}
              </span>
              <Link
                href={`/stops/${encodeURIComponent(entry.stop.stop_id)}`}
                className="text-sm text-ink hover:text-accent-blue"
              >
                {entry.stop.stop_name}
                {entry.stop.district && (
                  <span className="text-ink-secondary"> — {entry.stop.district}</span>
                )}
              </Link>
            </li>
          ))}
        </ol>
      </div>

      <SuggestionBox targetType="route" targetId={routeId} currentStops={stops} />
    </div>
  );
}
