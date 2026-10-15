"use client";

import { useEffect } from "react";
import L from "leaflet";
import { CongestionSegment, Stop } from "@/types/route";
import { escapeHtml } from "@/lib/escapeHtml";
import { getCongestionColor } from "@/lib/congestionColor";

interface UseCongestionLayerProps {
  map: L.Map | null;
  congestionSegments: CongestionSegment[];
  allStops: Stop[];
}

export function useCongestionLayer({
  map,
  congestionSegments,
  allStops,
}: UseCongestionLayerProps) {
  useEffect(() => {
    if (!map || congestionSegments.length === 0) return;

    const stopById = new Map(allStops.map((s) => [s.stop_id, s]));
    const layer = L.layerGroup().addTo(map);

    congestionSegments.forEach((seg) => {
      const from = stopById.get(seg.from_stop_id);
      const to = stopById.get(seg.to_stop_id);
      if (!from || !to) return;

      // Validate coordinates
      if (
        !Number.isFinite(from.lat) ||
        !Number.isFinite(from.lng) ||
        !Number.isFinite(to.lat) ||
        !Number.isFinite(to.lng)
      ) {
        return;
      }

      const color = getCongestionColor(seg.congestion_ratio);
      const line = L.polyline(
        [
          [from.lat, from.lng],
          [to.lat, to.lng],
        ],
        {
          color,
          weight: 5,
          opacity: seg.is_seeded ? 0.5 : 0.85,
          dashArray: seg.is_seeded ? "3 5" : undefined,
        }
      );

      const minutes = Math.round(seg.avg_duration_s / 60);
      const freeFlowMinutes = Math.round(seg.free_flow_duration_s / 60);
      const label =
        seg.congestion_level === "free_flow"
          ? "Free-flow"
          : seg.congestion_level === "moderate"
          ? "Moderate congestion"
          : "Heavy congestion";
      // Every value here is derived, never observed (see the panel's
      // provenance note), so the popup must not say "typical" or "samples" --
      // both read as measured traffic. The straight-line caveat is included
      // because the drawn line is NOT the road geometry.
      const basis = seg.is_seeded
        ? "Free-flow baseline (OSRM); no reported congestion recorded."
        : `Estimated from route data — not a measured traffic count (${seg.sample_count} recorded estimate${
            seg.sample_count === 1 ? "" : "s"
          }).`;
      line.bindPopup(
        `<strong>${escapeHtml(from.stop_name)} \u2192 ${escapeHtml(to.stop_name)}</strong> (straight line)` +
          (seg.route_id ? `<br/>${escapeHtml(seg.route_id)}` : "") +
          `<br/>${label} (${seg.congestion_ratio.toFixed(1)}x free-flow)` +
          `<br/>~${minutes} min estimated, ${freeFlowMinutes} min free-flow` +
          `<br/>${basis}`
      );
      layer.addLayer(line);
    });

    return () => {
      layer.remove();
    };
  }, [map, congestionSegments, allStops]);
}
