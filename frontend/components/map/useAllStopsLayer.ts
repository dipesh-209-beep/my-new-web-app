"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { Stop, StopPickTarget } from "@/types/route";
import { clusterStops } from "@/lib/stopClustering";
import { escapeHtml } from "@/lib/escapeHtml";
import { busTagIcon, clusterIcon } from "./markerKit";

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

    const clusters = clusterStops(allStops, zoom);

    clusters.forEach((cluster) => {
      if (cluster.stops.length === 1) {
        const stop = cluster.stops[0];

        // Validate coordinates
        if (!Number.isFinite(stop.lat) || !Number.isFinite(stop.lng)) {
          return;
        }

        const marker = L.marker([stop.lat, stop.lng], {
          icon: busTagIcon(dimmed ? "dimmed" : "default"),
        });
        marker.bindTooltip(escapeHtml(stop.stop_name), {
          direction: "top",
          offset: [0, -10],
          className: "ktm-tooltip",
        });
        marker.on("click", () => {
          const target = pickTargetRef.current;
          if (target) onStopPickRef.current?.(stop);
        });
        marker.on("mouseover", () => {
          if (pickTargetRef.current) marker.setIcon(busTagIcon("active"));
        });
        marker.on("mouseout", () => {
          marker.setIcon(busTagIcon(dimmed ? "dimmed" : "default"));
        });
        stopsLayer.addLayer(marker);
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
