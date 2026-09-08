"use client";

import { useEffect } from "react";
import L from "leaflet";
import { RouteStopEntry, RouteGeometry } from "@/types/route";
import { escapeHtml } from "@/lib/escapeHtml";
import { sequencedStopIcon } from "./markerKit";

const BROWSE_ROUTE_COLOR = "#7C3AED";

interface UseRouteBrowserLayerProps {
  map: L.Map | null;
  browseRouteStops: RouteStopEntry[];
  browseRouteGeometry: RouteGeometry | null;
}

export function useRouteBrowserLayer({
  map,
  browseRouteStops,
  browseRouteGeometry,
}: UseRouteBrowserLayerProps) {
  useEffect(() => {
    if (!map || browseRouteStops.length === 0) return;

    // Coerce + validate coordinates. Prefer each stop's route/direction-
    // specific display position (display_lat/display_lng) when the
    // backend computed one -- see GET /routes/{route_id}/stops's
    // `direction` param and app/routing/stop_positioning.py -- falling
    // back to the stop's own canonical lat/lng otherwise (no adjustment
    // available, or none needed).
    const validEntries = browseRouteStops
      .map((entry) => ({
        entry,
        lat: Number(entry.display_lat ?? entry.stop?.lat),
        lng: Number(entry.display_lng ?? entry.stop?.lng),
      }))
      .filter(({ lat, lng }) => Number.isFinite(lat) && Number.isFinite(lng));

    if (validEntries.length < browseRouteStops.length) {
      console.warn(
        `[BusMap] Dropped ${browseRouteStops.length - validEntries.length} route stop(s) with invalid lat/lng -- check the /routes/{route_id}/stops response shape.`,
        browseRouteStops.filter((entry) => {
          const lat = Number(entry.stop?.lat);
          const lng = Number(entry.stop?.lng);
          return !(Number.isFinite(lat) && Number.isFinite(lng));
        })
      );
    }

    if (validEntries.length === 0) {
      console.error("[BusMap] No valid coordinates in browseRouteStops -- nothing to draw. Check field names (lat/lng vs latitude/longitude?).");
      return;
    }

    const layer = L.layerGroup().addTo(map);
    const latLngs: [number, number][] = validEntries.map(({ lat, lng }) => [lat, lng]);

    // Prefer the OSRM road-following polyline when available
    const roadPoints = browseRouteGeometry?.geometry.coordinates.map(
      ([lng, lat]) => [lat, lng] as [number, number]
    );
    const linePoints = roadPoints ?? latLngs;
    L.polyline(linePoints, { color: BROWSE_ROUTE_COLOR, weight: 4, opacity: 0.85 }).addTo(layer);

    validEntries.forEach(({ entry, lat, lng }) => {
      const marker = L.marker([lat, lng], {
        icon: sequencedStopIcon(entry.sequence_no, BROWSE_ROUTE_COLOR),
      });
      marker.bindTooltip(`${entry.sequence_no}. ${escapeHtml(entry.stop.stop_name)}`, {
        direction: "top",
        offset: [0, -8],
      });
      layer.addLayer(marker);
    });

    const bounds = L.latLngBounds(latLngs);
    map.fitBounds(bounds, { padding: [40, 40] });

    return () => {
      layer.remove();
    };
  }, [map, browseRouteStops, browseRouteGeometry]);
}
