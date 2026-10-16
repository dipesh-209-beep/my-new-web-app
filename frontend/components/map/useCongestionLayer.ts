"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { CongestionSegment, Stop } from "@/types/route";
import { escapeHtml } from "@/lib/escapeHtml";
import { getCongestionColor } from "@/lib/congestionColor";
import { CONGESTION_COLORS } from "@/lib/constants";

interface UseCongestionLayerProps {
  map: L.Map | null;
  congestionSegments: CongestionSegment[];
  allStops: Stop[];
}

// The three named points of the overlay key, in order (low to high). The
// gradient itself is built from the same CONGESTION_COLORS map the Traffic
// panel legend uses, so the two keys can't drift; these labels are the
// non-colour path to the same meaning.
const LEGEND_LABELS = ["Low", "Moderate", "Heavy"] as const;

export function useCongestionLayer({
  map,
  congestionSegments,
  allStops,
}: UseCongestionLayerProps) {
  const legendRef = useRef<L.Control | null>(null);

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

    // Compact key so the overlay colours still mean something when the
    // mobile bottom sheet is minimized: the fuller key in CongestionPanel is
    // unmounted with the sheet body, but this Leaflet control lives on the
    // map. It is created only while segments exist (the caller passes [] when
    // the layer is off, see app/page.tsx). Top-right is the free corner:
    // Leaflet's zoom control is top-left, the route-leg legend bottom-left,
    // and the recenter/scale controls bottom-right.
    const CongestionLegend = L.Control.extend({
      onAdd: () => {
        const div = L.DomUtil.create("div");
        div.setAttribute("role", "group");
        div.setAttribute("aria-label", "Congestion key");
        div.style.background = "#ffffff";
        div.style.border = "1px solid #DEDCF0";
        div.style.borderRadius = "4px";
        div.style.padding = "8px 10px";
        div.style.fontSize = "12px";
        div.style.color = "#1B1A2E";
        div.style.lineHeight = "1.6";
        div.style.boxShadow = "0 0 0 1px rgba(27,26,46,0.08)";
        div.style.maxWidth = "min(180px, 55vw)";

        const caption = document.createElement("div");
        caption.textContent = "Congestion";
        caption.style.fontWeight = "600";
        caption.style.marginBottom = "4px";
        div.appendChild(caption);

        const bar = document.createElement("div");
        bar.setAttribute("aria-hidden", "true");
        bar.style.height = "8px";
        bar.style.borderRadius = "9999px";
        bar.style.background = `linear-gradient(to right, ${CONGESTION_COLORS.free_flow}, ${CONGESTION_COLORS.moderate}, ${CONGESTION_COLORS.heavy})`;
        div.appendChild(bar);

        const labels = document.createElement("div");
        labels.style.display = "flex";
        labels.style.justifyContent = "space-between";
        LEGEND_LABELS.forEach((label) => {
          const span = document.createElement("span");
          span.textContent = label;
          labels.appendChild(span);
        });
        div.appendChild(labels);

        L.DomEvent.disableClickPropagation(div);
        return div;
      },
    });
    legendRef.current = new CongestionLegend({ position: "topright" });
    legendRef.current.addTo(map);

    return () => {
      layer.remove();
      legendRef.current?.remove();
      legendRef.current = null;
    };
  }, [map, congestionSegments, allStops]);
}
