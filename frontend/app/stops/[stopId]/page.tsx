"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ApiError, getStop, getStopRoutes } from "@/lib/api";
import SuggestionBox from "@/components/user/SuggestionBox";
import InlineAlert from "@/components/ui/InlineAlert";
import { RouteOut, Stop } from "@/types/route";

const BusMap = dynamic(() => import("@/components/BusMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center text-sm text-ink-secondary">
      Loading map…
    </div>
  ),
});

export default function StopDetailPage() {
  const params = useParams<{ stopId: string }>();
  const stopId = decodeURIComponent(params.stopId);

  const [stop, setStop] = useState<Stop | null>(null);
  const [routes, setRoutes] = useState<RouteOut[]>([]);
  const [routesLoading, setRoutesLoading] = useState(true);
  const [routesError, setRoutesError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped by the serving-routes Retry button so its own effect re-runs.
  // Kept separate from the stop-details effect so retrying never reloads
  // the stop itself.
  const [routesReloadToken, setRoutesReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setNotFound(false);
      setError(null);
      try {
        const data = await getStop(stopId);
        if (!cancelled) setStop(data);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else if (err instanceof ApiError && err.kind === "timeout") {
          setError("This is taking longer than usual. Try again in a moment.");
        } else {
          setError("Couldn't load this stop. Try again.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [stopId]);

  // The serving-routes request is supplementary to the stop's own details,
  // so it lives in its own effect with its own error state. A failure here
  // must never take the stop down, and a retry (bumping routesReloadToken)
  // re-runs only this request. The `cancelled` guard keeps a superseded
  // request from overwriting a newer one.
  useEffect(() => {
    let cancelled = false;

    async function loadRoutes() {
      setRoutesLoading(true);
      setRoutesError(null);
      try {
        const data = await getStopRoutes(stopId);
        if (!cancelled) setRoutes(data);
      } catch (err) {
        if (cancelled) return;
        setRoutesError(
          err instanceof ApiError && err.kind === "timeout"
            ? "Loading the routes for this stop is taking longer than usual. Try again."
            : "Couldn't load the routes serving this stop. Try again."
        );
      } finally {
        if (!cancelled) setRoutesLoading(false);
      }
    }

    loadRoutes();
    return () => {
      cancelled = true;
    };
  }, [stopId, routesReloadToken]);

  function retryRoutes() {
    // Clear immediately so the alert goes away, then re-run the effect.
    setRoutesError(null);
    setRoutesReloadToken((token) => token + 1);
  }

  // Every state gets a title that names the stop the URL asked for, so a
  // loading, missing or errored page can never keep a previously-viewed
  // stop's name in the tab. The `stop.stop_id === stopId` guard makes the
  // success title conditional on the loaded record actually matching the
  // current id, so a stale response from a previous stop cannot win.
  useEffect(() => {
    if (stop && stop.stop_id === stopId) {
      document.title = `${stop.stop_name} — Kathmandu Bus Route Finder`;
    } else if (notFound) {
      document.title = `Stop ${stopId} not found — Kathmandu Bus Route Finder`;
    } else if (error) {
      document.title = `Stop ${stopId} — failed to load — Kathmandu Bus Route Finder`;
    } else {
      document.title = `Stop ${stopId} — Kathmandu Bus Route Finder`;
    }
  }, [stop, stopId, notFound, error]);

  if (loading) {
    return (
      <div className="mx-auto flex h-full max-w-xl flex-col gap-3 p-4">
        <div className="h-6 w-1/2 animate-pulse rounded bg-surface" />
        <div className="h-4 w-1/3 animate-pulse rounded bg-surface" />
        <div className="mt-2 h-24 animate-pulse rounded-lg border border-route-line bg-surface" />
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="mx-auto flex h-full max-w-xl flex-col items-start gap-2 p-4">
        <p className="text-sm text-ink">
          Stop <span className="font-mono">{stopId}</span> doesn&apos;t exist.
        </p>
        <Link href="/stops" className="text-sm text-accent-blue hover:underline">
          ← Back to all stops
        </Link>
      </div>
    );
  }

  if (error || !stop) {
    return (
      <div className="mx-auto flex h-full max-w-xl flex-col items-start gap-2 p-4">
        <p role="alert" className="text-sm text-accent-red">
          {error ?? "Something went wrong."}
        </p>
        <Link href="/stops" className="text-sm text-accent-blue hover:underline">
          ← Back to all stops
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-full max-w-xl flex-col gap-4 overflow-y-auto p-4">
      <Link href="/stops" className="text-sm text-ink-secondary hover:text-accent-blue">
        ← All stops
      </Link>

      <div>
        <h1 className="text-lg font-semibold text-ink">{stop.stop_name}</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          {stop.district ?? "District unknown"}
          {stop.zone && <> · {stop.zone}</>}
        </p>
        <div className="mt-2 flex gap-1">
          {stop.is_major_stop && (
            <span className="rounded-full bg-accent-blue/10 px-2 py-0.5 text-xs text-accent-blue">
              Major stop
            </span>
          )}
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
      </div>

      <p className="font-mono text-xs text-ink-secondary">
        {stop.lat.toFixed(5)}, {stop.lng.toFixed(5)}
      </p>

      <div className="h-48 overflow-hidden rounded-xl border border-route-line shadow-card sm:h-56">
        <BusMap browseRouteStops={[{ sequence_no: 1, stop }]} />
      </div>

      <div className="flex flex-wrap gap-2">
        <Link
          href={`/?origin=${encodeURIComponent(stop.stop_id)}`}
          className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-ink hover:bg-brand-dark"
        >
          Set as origin
        </Link>
        <Link
          href={`/?destination=${encodeURIComponent(stop.stop_id)}`}
          className="rounded-md border border-route-line px-4 py-2 text-sm text-ink hover:border-accent-red hover:text-accent-red"
        >
          Set as destination
        </Link>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-medium text-ink">Routes serving this stop</h2>
        {routesError && (
          <InlineAlert
            variant="error"
            action={{
              label: "Retry",
              onClick: retryRoutes,
            }}
          >
            {routesError}
          </InlineAlert>
        )}
        {routesLoading && (
          <div className="flex flex-col gap-1.5">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="h-10 animate-pulse rounded-lg border border-route-line bg-surface" />
            ))}
          </div>
        )}
        {!routesLoading && !routesError && routes.length === 0 && (
          <p className="text-sm text-ink-secondary">
            No routes currently list this stop. Search a specific origin and destination on the{" "}
            <Link href="/" className="text-accent-blue hover:underline">
              search page
            </Link>{" "}
            -- the route finder can still connect through here via a nearby transfer.
          </p>
        )}
        <ul className="flex flex-col gap-1.5">
          {routes.map((route) => (
            <li key={route.route_id}>
              <Link
                href={`/routes/${encodeURIComponent(route.route_id)}`}
                className="flex items-center justify-between gap-3 rounded-lg border border-route-line bg-white px-3 py-2 hover:border-accent-blue"
              >
                <p className="text-sm font-medium text-ink">
                  {route.short_name ? `${route.short_name} — ` : ""}
                  {route.route_name}
                </p>
                <span className="shrink-0 font-mono text-xs text-ink-secondary">{route.vehicle_type}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <SuggestionBox targetType="stop" targetId={stopId} />
    </div>
  );
}
