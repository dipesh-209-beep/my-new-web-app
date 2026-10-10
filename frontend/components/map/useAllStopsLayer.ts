"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { Stop, StopPickTarget } from "@/types/route";
import { clusterStops } from "@/lib/stopClustering";
import { escapeHtml } from "@/lib/escapeHtml";
import { busTagIcon, clusterIcon } from "./markerKit";
import { bindMarkerActivation, setMarkerFocusable } from "./markerA11y";

interface UseAllStopsLayerProps {
  map: L.Map | null;
  allStops: Stop[];
  showAllStops: boolean;
  dimmed: boolean;
  zoom: number;
  pickTarget: StopPickTarget;
  onStopPick: ((stop: Stop) => void) | null;
}

/** Accessible name for a stop marker. The "select as ..." hint only applies
 * while map-picking is armed, which is also the only time the marker is in
 * the tab order at all. */
function stopAriaLabel(stop: Stop, pickTarget: StopPickTarget): string {
  return pickTarget
    ? `${stop.stop_name} — select as ${pickTarget}`
    : stop.stop_name;
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

  // Markers currently drawn, so the pick-mode effect below can flip just their
  // focusability instead of rebuilding the layer (which would drop focus).
  const stopMarkersRef = useRef<{ marker: L.Marker; stop: Stop }[]>([]);

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
    stopMarkersRef.current = [];

    clusters.forEach((cluster) => {
      if (cluster.stops.length === 1) {
        const stop = cluster.stops[0];

        // Validate coordinates
        if (!Number.isFinite(stop.lat) || !Number.isFinite(stop.lng)) {
          return;
        }

        const marker = L.marker([stop.lat, stop.lng], {
          icon: busTagIcon(dimmed ? "dimmed" : "default"),
          // Leaflet's `keyboard: true` default would make every stop a
          // focusable button even when it does nothing (map-picking off).
          // We opt each marker in explicitly via markerA11y instead.
          keyboard: false,
        });

        // A single-stop marker only does something while map-picking is
        // armed, so its keyboard activation mirrors the click handler.
        const activateStop = () => {
          if (pickTargetRef.current) onStopPickRef.current?.(stop);
        };

        // (Re)apply accessibility to the current icon element. Called after
        // creation and again after any `setIcon`, which replaces the element.
        const decorateStop = () => {
          const el = marker.getElement();
          if (!el) return;
          bindMarkerActivation(marker, {
            label: stopAriaLabel(stop, pickTargetRef.current),
            onActivate: activateStop,
          });
          setMarkerFocusable(marker, pickTargetRef.current !== null);
        };

        marker.bindTooltip(escapeHtml(stop.stop_name), {
          direction: "top",
          offset: [0, -10],
          className: "ktm-tooltip",
        });
        marker.on("click", activateStop);
        marker.on("mouseover", () => {
          if (!pickTargetRef.current) return;
          marker.setIcon(busTagIcon("active"));
          decorateStop();
        });
        marker.on("mouseout", () => {
          marker.setIcon(busTagIcon(dimmed ? "dimmed" : "default"));
          decorateStop();
        });
        stopsLayer.addLayer(marker);
        decorateStop();
        stopMarkersRef.current.push({ marker, stop });
        return;
      }

      const marker = L.marker([cluster.lat, cluster.lng], {
        icon: clusterIcon(cluster.stops.length),
        keyboard: false,
      });

      const activateCluster = () => {
        map.setView([cluster.lat, cluster.lng], Math.min(zoom + 3, 18));
      };

      marker.bindTooltip(`${cluster.stops.length} stops -- click to zoom in`, {
        direction: "top",
        offset: [0, -12],
        className: "ktm-tooltip",
      });
      marker.on("click", activateCluster);
      stopsLayer.addLayer(marker);

      // Clusters are always actionable, so they stay in the tab order.
      bindMarkerActivation(marker, {
        label: `${cluster.stops.length} bus stops. Activate to zoom in.`,
        onActivate: activateCluster,
      });
      setMarkerFocusable(marker, true);
    });

    return () => {
      stopsLayer.remove();
      stopMarkersRef.current = [];
    };
  }, [map, allStops, showAllStops, dimmed, zoom]);

  // Arming/disarming map-picking changes what a stop marker does, so update
  // the tab order and label in place rather than rebuilding the layer.
  useEffect(() => {
    stopMarkersRef.current.forEach(({ marker, stop }) => {
      setMarkerFocusable(marker, pickTarget !== null);
      const el = marker.getElement();
      if (el) el.setAttribute("aria-label", stopAriaLabel(stop, pickTarget));
    });
  }, [pickTarget]);
}
