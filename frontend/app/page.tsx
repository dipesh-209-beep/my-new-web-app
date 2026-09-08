"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import SearchForm, { ViaStopField } from "@/components/search/SearchForm";
import CongestionPanel from "@/components/CongestionPanel";
import RouteResultPanel from "@/components/route/RouteResultPanel";
import { Stop, StopPickTarget } from "@/types/route";
import { buildStopLabel } from "@/lib/stopLabel";
import { getStop } from "@/lib/api";
import { useStops } from "@/hooks/useStops";
import { useGeolocation } from "@/hooks/useGeolocation";
import { useRouteSearch } from "@/hooks/useRouteSearch";
import { useCongestion } from "@/hooks/useCongestion";
import { useRouteBrowser } from "@/hooks/useRouteBrowser";
import { ChevronIcon, LayersIcon } from "@/components/icons/TransitIcons";
import { useSheet } from "@/hooks/useSheet";
import { MapErrorBoundary } from "@/components/MapErrorBoundary";
import LayerToggle from "@/components/ui/LayerToggle";
import InlineAlert from "@/components/ui/InlineAlert";

// Leaflet touches `window`, so the map must load client-side only.
const BusMap = dynamic(() => import("@/components/BusMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center text-sm text-ink-secondary">
      Loading map…
    </div>
  ),
});

export default function Home() {
  return (
    // useSearchParams() below opts this page out of static prerendering
    // unless wrapped in Suspense -- the fallback never actually shows in
    // practice since this is a fully client-rendered page, but Next.js
    // requires the boundary to exist.
    <Suspense fallback={null}>
      <HomeInner />
    </Suspense>
  );
}

function HomeInner() {
  const { stops, loading: stopsLoading, error: stopsError } = useStops();

  // Origin/destination text lives here (not inside SearchForm) so both the
  // form and a map click can write to it.
  const [originText, setOriginText] = useState("");
  const [destinationText, setDestinationText] = useState("");
  const [viaStops, setViaStops] = useState<ViaStopField[]>([]);
  const [pickTarget, setPickTarget] = useState<StopPickTarget>(null);
  const [showAllStops, setShowAllStops] = useState(true);

  const { userLocation, nearestStop, walkingRoute, locating, locateError, useMyLocation } =
    useGeolocation({
      stops,
      // Offer the detected stop as the origin, but only if the user hasn't
      // already typed/picked something themselves.
      onStopFound: (label) => setOriginText((prev) => prev || label),
    });

  const { result, loading, loadingStage, error, search, retry } = useRouteSearch();

  // Which result is currently shown: -1 = primary/recommended, otherwise an
  // index into result.alternatives. Lives here (not inside
  // RouteResultPanel) so the selection also drives what BusMap draws --
  // previously the alternative buttons only changed the sidebar timeline,
  // leaving the map stuck on the primary route. Reset to -1 whenever a new
  // search result comes in, via React's "adjust state during render"
  // pattern (https://react.dev/learn/you-might-not-need-an-effect) rather
  // than an effect -- each search produces a fresh `result` reference, so
  // comparing against a tracked previous value lets this bail out cleanly.
  const [selectedAltIndex, setSelectedAltIndex] = useState(-1);
  const [lastResultForSelection, setLastResultForSelection] = useState(result);
  if (lastResultForSelection !== result) {
    setLastResultForSelection(result);
    if (selectedAltIndex !== -1) setSelectedAltIndex(-1);
  }

  // The result BusMap actually draws: the primary result as-is, or the
  // primary result with its legs swapped for the selected alternative's
  // (alternatives don't carry fare/origin/destination of their own -- only
  // legs/total_cost/transfer_count differ -- so everything else about the
  // result stays the same).
  const mapResult =
    result && result.found && selectedAltIndex !== -1
      ? { ...result, ...result.alternatives[selectedAltIndex] }
      : result;

const congestion = useCongestion();
  const routeBrowser = useRouteBrowser();
  const { visibleRouteId, direction, setDirection } = routeBrowser;

  // BusMap ref for invalidating size on sheet changes
  const busMapRef = useRef<{ invalidateSize: () => void } | null>(null);
  
  // Invalidate map size - can be called from anywhere
  const invalidateMap = useCallback(() => {
    if (busMapRef.current) {
      busMapRef.current.invalidateSize();
    }
  }, [busMapRef]);

  // Bottom sheet state and handlers
  const {
    sidebarHydrated,
    dragHeightPx,
    showMinimized,
    liveHeightPx,
    toggleSidebarMinimized,
    handleHandlePointerDown,
    handleHandlePointerMove,
    handleHandlePointerUp,
  } = useSheet(invalidateMap);

  // Call invalidateSize when the sheet height changes
  useEffect(() => {
    invalidateMap();
  }, [liveHeightPx, showMinimized, sidebarHydrated, invalidateMap]);

  // One-time deep-link handling: /stops/[id] links here with
  // ?origin=<stop_id> or ?destination=<stop_id> (its "Set as From/To"
  // actions), and /routes/[id] links here with ?route=<route_id> (its
  // "View on map" action). Applied once on mount via a ref guard --
  // afterwards the URL params are stale and shouldn't fight with the
  // user's own edits.
  const searchParams = useSearchParams();
  const appliedDeepLinkRef = useRef(false);
  useEffect(() => {
    if (appliedDeepLinkRef.current) return;
    const originId = searchParams.get("origin");
    const destinationId = searchParams.get("destination");
    const routeId = searchParams.get("route");
    if (!originId && !destinationId && !routeId) return;
    appliedDeepLinkRef.current = true;

    if (originId) {
      getStop(originId)
        .then((stop) => setOriginText(buildStopLabel(stop, stops)))
        .catch(() => {
          /* bad/stale id in the URL -- leave the field blank rather than erroring */
        });
    }
    if (destinationId) {
      getStop(destinationId)
        .then((stop) => setDestinationText(buildStopLabel(stop, stops)))
        .catch(() => {
          /* bad/stale id in the URL -- leave the field blank rather than erroring */
        });
    }
    if (routeId) {
      routeBrowser.showRouteById(routeId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  function handleStopPick(stop: Stop) {
    // Same disambiguation as the geolocation flow -- a bare stop_name
    // would break SearchForm's resolveStop() for any name that isn't
    // unique.
    const label = buildStopLabel(stop, stops);
    if (pickTarget === "origin") {
      setOriginText(label);
    } else if (pickTarget === "destination") {
      setDestinationText(label);
    }
    setPickTarget(null); // one pick and done, same as most map apps
  }

  return (
    <main className="relative flex h-full w-full flex-col md:flex-row">
      {/* Mobile: full-screen map under the navbar, with the planner floating
          as a bottom sheet on top. Desktop: the classic two-column layout
          (planner column left, map fills the rest) -- see the suggested
          structure in the redesign brief. */}
      <aside
        className={`absolute inset-x-0 bottom-0 z-[500] flex flex-col overflow-y-auto rounded-t-2xl border border-route-line bg-surface-raised shadow-sheet md:static md:inset-auto md:z-auto md:h-full md:w-full md:max-w-sm md:rounded-none md:border-b-0 md:border-l-0 md:border-r md:shadow-none md:max-h-none max-h-[var(--sheet-height)] ${
          dragHeightPx == null ? "transition-[max-height,padding]" : ""
        } ${showMinimized ? "gap-0 p-2 md:max-w-[52px]" : "gap-5 p-4"}`}
        style={{ "--sheet-height": `${liveHeightPx}px` } as React.CSSProperties}
      >
        <span
          className="sheet-handle mx-auto touch-none md:hidden"
          aria-hidden
          onPointerDown={handleHandlePointerDown}
          onPointerMove={handleHandlePointerMove}
          onPointerUp={handleHandlePointerUp}
          onPointerCancel={handleHandlePointerUp}
        />

        <div className="flex items-center justify-between gap-2">
          {!showMinimized && (
            <div>
              <h1 className="text-xl font-semibold leading-tight tracking-tight text-ink">
                Plan your journey
              </h1>
              <p className="mt-1 text-sm text-ink-secondary">
                Kathmandu Valley public transit — direct or single-transfer routes.
              </p>
            </div>
          )}
          <button
            type="button"
            onClick={toggleSidebarMinimized}
            aria-label={showMinimized ? "Restore search panel" : "Minimize search panel"}
            title={showMinimized ? "Restore search panel" : "Minimize search panel"}
            className="flex flex-shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-ink-secondary hover:text-ink"
          >
            <ChevronIcon direction={showMinimized ? "down" : "up"} />
          </button>
        </div>

        {!showMinimized && (
          <>
            <SearchForm
              stops={stops}
              stopsLoading={stopsLoading}
              onSearch={search}
              loading={loading}
              originText={originText}
              destinationText={destinationText}
              onOriginTextChange={setOriginText}
              onDestinationTextChange={setDestinationText}
              viaStops={viaStops}
              onViaStopsChange={setViaStops}
              pickTarget={pickTarget}
              onPickTargetChange={setPickTarget}
              locating={locating}
              locateError={locateError}
              onUseMyLocation={useMyLocation}
            />

            <div className="rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
              <LayerToggle
                icon={<LayersIcon size={14} />}
                iconColorClass="text-ink-secondary"
                iconBgClass="bg-ink/5"
                activeBgClass="bg-ink"
                hoverClass="hover:border-ink hover:text-ink"
                title="Map layers"
                subtitle="All stops"
                enabled={showAllStops}
                onToggle={() => setShowAllStops((prev) => !prev)}
              />
            </div>

            <CongestionPanel
              enabled={congestion.enabled}
              onToggle={congestion.toggle}
              dayOfWeek={congestion.dayOfWeek}
              hourBucket={congestion.hourBucket}
              onDayChange={congestion.setDayOfWeek}
              onHourChange={congestion.setHourBucket}
              loading={congestion.loading}
              segmentCount={congestion.segments.length}
              hasSeededOnly={congestion.hasSeededOnly}
            />

            {visibleRouteId && (
              <div className="rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
                <p className="text-xs font-semibold uppercase tracking-wide text-accent-purple mb-2">
                  Browsing route
                </p>
                <p className="text-sm font-medium text-ink mb-3">
                  {routeBrowser.visibleRouteStops.length > 0
                    ? routeBrowser.visibleRouteStops[0].stop.stop_name
                    : "Loading…"}
                </p>
                {routeBrowser.routes.find((r) => r.route_id === visibleRouteId)?.is_bidirectional && (
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
              </div>
            )}

            {stopsError && (
              <InlineAlert
                variant="warning"
                action={{ label: "Retry", onClick: () => window.location.reload() }}
              >
                Couldn&apos;t load the stop list from the server. You can still search if you know
                exact stop names, but suggestions won&apos;t be available.
              </InlineAlert>
            )}

            <RouteResultPanel
              result={result}
              loading={loading}
              loadingStage={loadingStage}
              error={error}
              selectedIndex={selectedAltIndex}
              onSelectedIndexChange={setSelectedAltIndex}
              onRetry={retry}
            />
          </>
        )}
      </aside>

      <div className="h-full w-full flex-1 md:min-h-0">
        <MapErrorBoundary onError={() => busMapRef.current?.invalidateSize()}>
          <BusMap
            ref={busMapRef}
            key="bus-map"
            result={mapResult}
            allStops={stops}
            showAllStops={showAllStops}
            pickTarget={pickTarget}
            onStopPick={handleStopPick}
            userLocation={userLocation}
            walkingRoute={walkingRoute}
            nearestStop={nearestStop}
            congestionSegments={congestion.enabled ? congestion.segments : []}
            browseRouteStops={routeBrowser.visibleRouteStops}
            browseRouteGeometry={routeBrowser.visibleRouteGeometry}
          />
        </MapErrorBoundary>
      </div>
    </main>
  );
}
