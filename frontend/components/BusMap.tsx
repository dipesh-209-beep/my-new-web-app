"use client";

import { useRef, useImperativeHandle, forwardRef } from "react";
import L from "leaflet";
import {
  CongestionSegment,
  LatLng,
  RouteGeometry,
  RouteSearchResult,
  RouteStopEntry,
  Stop,
  StopPickTarget,
  WalkingRoute,
} from "@/types/route";
import {
  useMap,
  useAllStopsLayer,
  useCongestionLayer,
  useRouteBrowserLayer,
  useUserLocationLayer,
  useRouteResultLayer,
  useRecenterControl,
  useCursorFeedback,
} from "@/components/map";

interface BusMapProps {
  result?: RouteSearchResult | null;
  /** All stops, drawn as small clickable dots so the user can pick a stop
   * directly on the map instead of typing its name. */
  allStops?: Stop[];
  /** Master on/off for the all-stops layer -- separate from the automatic
   * dimming that happens once a route is found (see `dimmed` below).
   * Defaults on so map-click stop-picking keeps working out of the box. */
  showAllStops?: boolean;
  /** Which field (origin/destination) a stop click should fill; null disables
   * map-click selection (dots still render, just aren't wired to onStopPick). */
  pickTarget?: StopPickTarget;
  onStopPick?: (stop: Stop) => void;
  userLocation?: LatLng | null;
  /** Walking directions from userLocation to the nearest stop. */
  walkingRoute?: WalkingRoute | null;
  nearestStop?: Stop | null;
  /** Historical congestion overlay -- straight lines between board/alight
   * stops (no road geometry available at this granularity), colored by
   * congestion_level. Independent of `result`; shows regardless of
   * whether a route is currently searched. */
  congestionSegments?: CongestionSegment[];
  /** Route browser overlay: whichever route is toggled visible via the eye
   * toggle on /routes (or a route/stop detail page embed), drawn as
   * numbered stops in ride order + a
   * connecting line. Independent of `result` and congestion. */
  browseRouteStops?: RouteStopEntry[];
  /** Road-following OSRM geometry for browseRouteStops, when available --
   * draws the connecting line along actual roads instead of straight
   * segments between consecutive stops. Null/undefined falls back to the
   * straight-line connector (OSRM unavailable, or still loading). */
  browseRouteGeometry?: RouteGeometry | null;
}

interface BusMapHandle {
  invalidateSize: () => void;
}

const BusMap = forwardRef<BusMapHandle, BusMapProps>(({
  result = null,
  allStops = [],
  showAllStops = true,
  pickTarget = null,
  onStopPick,
  userLocation = null,
  walkingRoute = null,
  nearestStop = null,
  congestionSegments = [],
  browseRouteStops = [],
  browseRouteGeometry = null,
}, ref) => {
  const routeBoundsRef = useRef<L.LatLngBounds | null>(null);
  const userLocationRef = useRef<LatLng | null>(userLocation);

  // Sync userLocation ref
  userLocationRef.current = userLocation;

  const {
    mapContainerRef,
    map,
    zoom,
    fitBounds,
    invalidateSize,
  } = useMap();

  // Expose invalidateSize to parent via ref
  useImperativeHandle(ref, () => ({
    invalidateSize,
  }), [invalidateSize]);

  // All-stops layer
  useAllStopsLayer({
    map,
    allStops,
    showAllStops,
    dimmed: Boolean(result?.found),
    zoom,
    pickTarget,
    onStopPick: onStopPick ?? null,
  });

  // Congestion layer
  useCongestionLayer({
    map,
    congestionSegments,
    allStops,
  });

  // Route browser layer
  useRouteBrowserLayer({
    map,
    browseRouteStops,
    browseRouteGeometry,
  });

  // User location layer
  useUserLocationLayer({
    map,
    userLocation,
    walkingRoute,
    nearestStop,
  });

  // Route result layer
  useRouteResultLayer({
    map,
    result,
    routeBoundsRef,
    onFitBounds: fitBounds,
  });

  // Recenter control
  useRecenterControl({
    map,
    routeBoundsRef,
    userLocationRef,
  });

  // Cursor feedback
  useCursorFeedback({
    mapContainerRef,
    pickTarget,
  });

  return (
    <div
      ref={mapContainerRef}
      role="region"
      aria-label="Route map"
      style={{ height: "100%", width: "100%", minHeight: "400px" }}
    />
  );
});

BusMap.displayName = "BusMap";

export default BusMap;