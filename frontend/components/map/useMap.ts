"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import L from "leaflet";

const VALLEY_CENTER: [number, number] = [27.7041, 85.32];

export interface UseMapReturn {
  mapContainerRef: React.RefObject<HTMLDivElement>;
  map: L.Map | null;
  zoom: number;
  setZoom: React.Dispatch<React.SetStateAction<number>>;
  fitBounds: (bounds: L.LatLngBounds, options?: L.FitBoundsOptions) => void;
  invalidateSize: () => void;
}

export function useMap(): UseMapReturn {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const [map, setMap] = useState<L.Map | null>(null);
  const [zoom, setZoom] = useState(12);

  const fitBounds = useCallback(
    (bounds: L.LatLngBounds, options?: L.FitBoundsOptions) => {
      mapRef.current?.fitBounds(bounds, options);
    },
    []
  );

  const invalidateSize = useCallback(() => {
    mapRef.current?.invalidateSize();
  }, []);

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) {
      return;
    }

    const mapInstance = L.map(mapContainerRef.current).setView(VALLEY_CENTER, 12);
    mapRef.current = mapInstance;
    setMap(mapInstance);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(mapInstance);

    L.control.scale({ position: "bottomright", imperial: false }).addTo(mapInstance);

    mapInstance.on("zoomend", () => setZoom(mapInstance.getZoom()));

    return () => {
      mapInstance.remove();
      mapRef.current = null;
      setMap(null);
    };
  }, []);

  return {
    mapContainerRef,
    map,
    zoom,
    setZoom,
    fitBounds,
    invalidateSize,
  };
}