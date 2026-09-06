"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { Stop, StopPickTarget } from "@/types/route";
import { clusterStops } from "@/lib/stopClustering";
import { escapeHtml } from "@/lib/escapeHtml";

function clusterIcon(count: number): L.DivIcon {
  const size = count >= 100 ? 34 : count >= 10 ? 30 : 26;
  return L.divIcon({
    className: "",
    html: `<span style="display:flex;align-items:center;justify-content:center;width:${size}px;height:${size}px;border-radius:9999px;background:#4B5563;color:#ffffff;font-size:11px;font-weight:600;border:2px solid #ffffff;box-shadow:0 0 0 1px rgba(0,0,0,0.25);">${count}</span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

interface UseAllStopsLayerProps {
  map: L.Map | null;
  allStops: Stop[];
  showAllStops: boolean;
  dimmed: boolean;
  zoom: number;
  pickTarget: StopPickTarget;
  onStopPick: ((stop: Stop) => void) | null;
}

export function useAllStopsLayer({
  map,
  allStops,
  showAllStops,
  dimmed,
  zoom,
  pickTarget,
  onStopPick,
}: UseAllStopsLayerProps) {
  const pickTargetRef = useRef(pickTarget);
  const onStopPickRef = useRef(onStopPick);

  useEffect(() => {
    pickTargetRef.current = pickTarget;
  }, [pickTarget]);

  useEffect(() => {
    onStopPickRef.current = onStopPick;
  }, [onStopPick]);

  useEffect(() => {
    if (!map || !showAllStops) return;

    const stopsLayer = L.layerGroup().addTo(map);
    const dotFillOpacity = dimmed ? 0.35 : 0.85;
    const dotColor = dimmed ? "#D1D5DB" : "#4b5563";
    const dotFill = dimmed ? "#E5E7EB" : "#9CA3AF";

    const clusters = clusterStops(allStops, zoom);

    clusters.forEach((cluster) => {
      if (cluster.stops.length === 1) {
        const stop = cluster.stops[0];

        // Validate coordinates
        if (!Number.isFinite(stop.lat) || !Number.isFinite(stop.lng)) {
          return;
        }

        const dot = L.circleMarker([stop.lat, stop.lng], {
          radius: 4,
          weight: 1,
          color: dotColor,
          fillColor: dotFill,
          fillOpacity: dotFillOpacity,
        });
        dot.bindTooltip(escapeHtml(stop.stop_name), {
          direction: "top",
          offset: [0, -4],
          className: "ktm-tooltip",
        });
        dot.on("click", () => {
          const target = pickTargetRef.current;
          if (target) onStopPickRef.current?.(stop);
        });
        dot.on("mouseover", () => {
          if (pickTargetRef.current) dot.setStyle({ radius: 6, fillColor: "#2563EB" });
        });
        dot.on("mouseout", () => {
          dot.setStyle({ radius: 4, fillColor: dotFill });
        });
        stopsLayer.addLayer(dot);
        return;
      }

      const marker = L.marker([cluster.lat, cluster.lng], {
        icon: clusterIcon(cluster.stops.length),
      });
      marker.bindTooltip(`${cluster.stops.length} stops -- click to zoom in`, {
        direction: "top",
        offset: [0, -12],
        className: "ktm-tooltip",
      });
      marker.on("click", () => {
        map.setView([cluster.lat, cluster.lng], Math.min(zoom + 3, 18));
      });
      stopsLayer.addLayer(marker);
    });

    return () => {
      stopsLayer.remove();
    };
  }, [map, allStops, showAllStops, dimmed, zoom]);
}
