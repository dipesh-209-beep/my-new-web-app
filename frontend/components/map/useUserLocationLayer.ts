"use client";

import { useEffect } from "react";
import L from "leaflet";
import { LatLng, WalkingRoute, Stop } from "@/types/route";
import { escapeHtml } from "@/lib/escapeHtml";

const USER_WALK_COLOR = "#0D9488";

const userLocationIcon = L.divIcon({
  className: "",
  html: `<span style="position:relative;display:block;width:16px;height:16px;">
      <span style="position:absolute;inset:-8px;border-radius:9999px;background:rgba(37,99,235,0.25);"></span>
      <span style="position:absolute;inset:0;border-radius:9999px;background:#2563EB;border:2px solid #ffffff;box-shadow:0 0 0 1px rgba(0,0,0,0.25);"></span>
    </span>`,
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

interface UseUserLocationLayerProps {
  map: L.Map | null;
  userLocation: LatLng | null;
  walkingRoute: WalkingRoute | null;
  nearestStop: Stop | null;
}

export function useUserLocationLayer({
  map,
  userLocation,
  walkingRoute,
  nearestStop,
}: UseUserLocationLayerProps) {
  useEffect(() => {
    if (!map || !userLocation) return;

    const layer = L.layerGroup().addTo(map);

    const marker = L.marker([userLocation.lat, userLocation.lng], {
      icon: userLocationIcon,
      zIndexOffset: 1000,
    });
    marker.bindPopup("Your location");
    layer.addLayer(marker);

    if (walkingRoute) {
      const points = walkingRoute.geometry.coordinates.map(
        ([lng, lat]) => [lat, lng] as [number, number]
      );
      const line = L.polyline(points, {
        color: USER_WALK_COLOR,
        weight: 4,
        dashArray: "4 8",
      });
      const distanceKm = (walkingRoute.distance_m / 1000).toFixed(1);
      const minutes = Math.round(walkingRoute.duration_s / 60);
      line.bindPopup(
        `<strong>Walk to ${escapeHtml(nearestStop?.stop_name ?? "nearest stop")}</strong><br/>${distanceKm} km \u00b7 ~${minutes} min`
      );
      layer.addLayer(line);
    } else if (nearestStop) {
      // Validate coordinates
      if (
        Number.isFinite(nearestStop.lat) &&
        Number.isFinite(nearestStop.lng) &&
        Number.isFinite(userLocation.lat) &&
        Number.isFinite(userLocation.lng)
      ) {
        // OSRM foot-routing unavailable -- fall back to a straight line
        const line = L.polyline(
          [
            [userLocation.lat, userLocation.lng],
            [nearestStop.lat, nearestStop.lng],
          ],
          { color: USER_WALK_COLOR, weight: 3, dashArray: "2 6", opacity: 0.7 }
        );
        line.bindPopup(`Approximate walking path to ${escapeHtml(nearestStop.stop_name)} (straight line)`);
        layer.addLayer(line);
      }
    }

    return () => {
      layer.remove();
    };
  }, [map, userLocation, walkingRoute, nearestStop]);
}
