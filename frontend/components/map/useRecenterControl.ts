"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { LatLng } from "@/types/route";

interface UseRecenterControlProps {
  map: L.Map | null;
  routeBoundsRef: React.RefObject<L.LatLngBounds | null>;
  userLocationRef: React.RefObject<LatLng | null>;
}

export function useRecenterControl({
  map,
  routeBoundsRef,
  userLocationRef,
}: UseRecenterControlProps) {
  const controlRef = useRef<L.Control | null>(null);

  useEffect(() => {
    if (!map) return;

    const RecenterControl = L.Control.extend({
      onAdd: () => {
        const button = L.DomUtil.create("button") as HTMLButtonElement;
        button.type = "button";
        button.setAttribute("aria-label", "Recenter map");
        button.title = "Recenter map";
        button.style.width = "34px";
        button.style.height = "34px";
        button.style.background = "#ffffff";
        button.style.border = "1px solid #DEDCF0";
        button.style.borderRadius = "4px";
        button.style.boxShadow = "0 0 0 1px rgba(27,26,46,0.08)";
        button.style.display = "flex";
        button.style.alignItems = "center";
        button.style.justifyContent = "center";
        button.style.cursor = "pointer";
        button.innerHTML =
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#1B1A2E" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>';
        button.addEventListener("click", () => {
          if (routeBoundsRef.current) {
            map.fitBounds(routeBoundsRef.current, { padding: [40, 40] });
          } else if (userLocationRef.current) {
            map.setView([userLocationRef.current.lat, userLocationRef.current.lng], 15);
          } else {
            map.setView([27.7041, 85.32], 12);
          }
        });
        L.DomEvent.disableClickPropagation(button);
        return button;
      },
    });
    controlRef.current = new RecenterControl({ position: "bottomright" });
    controlRef.current.addTo(map);

    return () => {
      controlRef.current?.remove();
      controlRef.current = null;
    };
  }, [map, routeBoundsRef, userLocationRef]);
}