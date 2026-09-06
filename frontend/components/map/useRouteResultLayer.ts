"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { RouteSearchResult } from "@/types/route";
import { LEG_COLORS } from "@/lib/constants";
import { escapeHtml } from "@/lib/escapeHtml";

const originIcon = L.divIcon({
  className: "",
  html: `<span style="display:block;width:18px;height:18px;border-radius:9999px;background:#2563EB;border:2px solid #ffffff;box-shadow:0 0 0 1px rgba(0,0,0,0.25);"></span>`,
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

const destinationIcon = L.divIcon({
  className: "",
  html: `<span style="display:block;width:18px;height:18px;border-radius:9999px;background:#DC2626;border:2px solid #ffffff;box-shadow:0 0 0 1px rgba(0,0,0,0.25);"></span>`,
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

const transferIcon = L.divIcon({
  className: "",
  html: `<span style="display:block;width:14px;height:14px;border-radius:9999px;background:#7C3AED;border:2px solid #ffffff;box-shadow:0 0 0 1px rgba(0,0,0,0.25);"></span>`,
  iconSize: [14, 14],
  iconAnchor: [7, 7],
});

interface UseRouteResultLayerProps {
  map: L.Map | null;
  result: RouteSearchResult | null;
  routeBoundsRef: React.MutableRefObject<L.LatLngBounds | null>;
  onFitBounds: (bounds: L.LatLngBounds) => void;
}

export function useRouteResultLayer({
  map,
  result,
  routeBoundsRef,
  onFitBounds,
}: UseRouteResultLayerProps) {
  const legLayersRef = useRef<L.LayerGroup[]>([]);
  const legendRef = useRef<L.Control | null>(null);

  useEffect(() => {
    if (!map) return;

    // Clean up previous layers
    legLayersRef.current.forEach((layer) => layer.remove());
    legLayersRef.current = [];
    legendRef.current?.remove();
    legendRef.current = null;
    routeBoundsRef.current = null;

    if (!result?.found || result.legs.length === 0) return;

    const bounds: L.LatLngExpression[] = [];
    const legendRows: { label: string; color: string; dashed: boolean }[] = [];

    result.legs.forEach((leg, i) => {
      const legLayer = L.layerGroup().addTo(map);
      legLayersRef.current.push(legLayer);

      const isWalk = leg.route_id === "TRANSFER";
      const color = isWalk ? "#9CA3AF" : LEG_COLORS[i % LEG_COLORS.length];
      const isFirstLeg = i === 0;
      const isLastLeg = i === result.legs.length - 1;

      legendRows.push({
        label: isWalk ? "Walk transfer" : leg.route_name,
        color,
        dashed: isWalk,
      });

      const fromIcon = isFirstLeg ? originIcon : transferIcon;
      const toIcon = isLastLeg ? destinationIcon : transferIcon;

      // Validate coordinates
      const validateCoord = (coord: number) => Number.isFinite(coord);
      if (
        !validateCoord(leg.board_stop.lat) ||
        !validateCoord(leg.board_stop.lng) ||
        !validateCoord(leg.alight_stop.lat) ||
        !validateCoord(leg.alight_stop.lng)
      ) {
        return;
      }

      const fromMarker = L.marker([leg.board_stop.lat, leg.board_stop.lng], {
        icon: fromIcon,
      });
      fromMarker.bindPopup(
        `<strong>${escapeHtml(leg.board_stop.stop_name)}</strong><br/>${
          isFirstLeg ? "Origin" : "Transfer point"
        }`
      );
      legLayer.addLayer(fromMarker);

      const toMarker = L.marker([leg.alight_stop.lat, leg.alight_stop.lng], {
        icon: toIcon,
      });
      toMarker.bindPopup(
        `<strong>${escapeHtml(leg.alight_stop.stop_name)}</strong><br/>${
          isLastLeg ? "Destination" : "Transfer point"
        }`
      );
      legLayer.addLayer(toMarker);

      // Intermediate stops
      leg.stops.slice(1, -1).forEach((stop) => {
        if (!validateCoord(stop.lat) || !validateCoord(stop.lng)) return;
        const dot = L.circleMarker([stop.lat, stop.lng], {
          radius: 5,
          color,
          fillColor: "#ffffff",
          fillOpacity: 1,
          weight: 2,
        });
        dot.bindPopup(escapeHtml(stop.stop_name));
        legLayer.addLayer(dot);
      });

      // Road geometry or straight line fallback
      const roadPoints = leg.road_geometry?.geometry.coordinates.map(
        ([lng, lat]) => [lat, lng] as [number, number]
      );

      const points: [number, number][] =
        roadPoints ?? [
          [leg.board_stop.lat, leg.board_stop.lng],
          [leg.alight_stop.lat, leg.alight_stop.lng],
        ];

      const polyline = L.polyline(points, {
        color,
        weight: isWalk ? 4 : 5,
        dashArray: isWalk ? "6 8" : undefined,
      });

      const kmLabel = leg.road_geometry
        ? `${(leg.road_geometry.distance_m / 1000).toFixed(1)} km`
        : null;
      polyline.bindPopup(
        `<strong>${isWalk ? "Walk" : escapeHtml(leg.route_name)}</strong>${
          kmLabel ? `<br/>${kmLabel}` : ""
        }`
      );
      legLayer.addLayer(polyline);

      bounds.push(...points);
    });

    if (bounds.length > 0) {
      const latLngBounds = L.latLngBounds(bounds);
      routeBoundsRef.current = latLngBounds;
      onFitBounds(latLngBounds);
    }

    // Legend control
    const LegendControl = L.Control.extend({
      onAdd: () => {
        const div = L.DomUtil.create("div");
        div.style.background = "#ffffff";
        div.style.border = "1px solid #E4E4DF";
        div.style.borderRadius = "8px";
        div.style.padding = "8px 10px";
        div.style.fontSize = "12px";
        div.style.color = "#171717";
        div.style.lineHeight = "1.6";
        div.style.boxShadow = "0 1px 3px rgba(0,0,0,0.08)";
        div.style.maxWidth = "min(220px, 60vw)";

        legendRows.forEach((row, i) => {
          const rowButton = document.createElement("button");
          rowButton.type = "button";
          rowButton.style.display = "flex";
          rowButton.style.alignItems = "flex-start";
          rowButton.style.gap = "6px";
          rowButton.style.width = "100%";
          rowButton.style.background = "none";
          rowButton.style.border = "none";
          rowButton.style.padding = "2px 0";
          rowButton.style.cursor = "pointer";
          rowButton.style.textAlign = "left";
          rowButton.style.font = "inherit";
          rowButton.style.color = "inherit";
          rowButton.setAttribute("aria-label", `Toggle ${row.label} on the map`);
          rowButton.setAttribute("aria-pressed", "true");

          const swatch = document.createElement("span");
          swatch.style.display = "inline-block";
          swatch.style.width = "14px";
          swatch.style.height = "0px";
          swatch.style.marginTop = "7px";
          swatch.style.flexShrink = "0";
          swatch.style.borderTop = `3px ${row.dashed ? "dashed" : "solid"} ${row.color}`;

          const labelSpan = document.createElement("span");
          labelSpan.style.wordBreak = "break-word";
          labelSpan.textContent = row.label;

          rowButton.appendChild(swatch);
          rowButton.appendChild(labelSpan);

          let visible = true;
          rowButton.addEventListener("click", () => {
            visible = !visible;
            rowButton.setAttribute("aria-pressed", String(visible));
            if (visible) {
              legLayersRef.current[i].addTo(map);
              rowButton.style.opacity = "1";
              labelSpan.style.textDecoration = "none";
            } else {
              legLayersRef.current[i].remove();
              rowButton.style.opacity = "0.45";
              labelSpan.style.textDecoration = "line-through";
            }
          });

          div.appendChild(rowButton);
        });

        L.DomEvent.disableClickPropagation(div);
        return div;
      },
    });
    legendRef.current = new LegendControl({ position: "bottomleft" });
    legendRef.current.addTo(map);

    return () => {
      legLayersRef.current.forEach((layer) => layer.remove());
      legendRef.current?.remove();
    };
  }, [map, result, routeBoundsRef, onFitBounds]);
}
