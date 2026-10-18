import { useCallback, useRef, useState } from "react";
import { LatLng, Stop, WalkingRoute } from "@/types/route";
import { buildStopLabel } from "@/lib/stopLabel";
import { getNearbyStops, getWalkingRoute } from "@/lib/api";

interface UseGeolocationOptions {
  stops: Stop[];
  onStopFound?: (label: string) => void;
}

interface UseGeolocationResult {
  userLocation: LatLng | null;
  nearestStop: Stop | null;
  walkingRoute: WalkingRoute | null;
  locating: boolean;
  locateError: string | null;
  // Polite success message ("Nearest stop found: X.") for a screen reader to
  // announce -- the nearest stop is otherwise only shown on the map and
  // written into the "From" field, neither of which is a live region. Null
  // while a request is in flight, on failure, or before the first success.
  locateStatus: string | null;
  useMyLocation: () => void;
}

/**
 * Detects the browser's geolocation, finds the nearest stop via
 * /stops/nearby, and fetches a walking path to it via /walking-route.
 * Only runs when `useMyLocation()` is called explicitly by the user (a
 * button press) -- browsers increasingly refuse or auto-dismiss a
 * permission prompt that wasn't triggered by a user gesture, so firing
 * this automatically on mount could silently fail (or just look broken)
 * depending on the browser, with no user action to retry from.
 */
export function useGeolocation({ stops, onStopFound }: UseGeolocationOptions): UseGeolocationResult {
  const [userLocation, setUserLocation] = useState<LatLng | null>(null);
  const [nearestStop, setNearestStop] = useState<Stop | null>(null);
  const [walkingRoute, setWalkingRoute] = useState<WalkingRoute | null>(null);
  const [locating, setLocating] = useState(false);
  const [locateError, setLocateError] = useState<string | null>(null);
  const [locateStatus, setLocateStatus] = useState<string | null>(null);

  // Guard against a slow earlier geolocation request overwriting a
  // faster later one if the user taps "Use my location" twice quickly.
  const requestIdRef = useRef(0);

  const locateNearestStop = useCallback(
    async (lat: number, lng: number) => {
      const requestId = ++requestIdRef.current;
      setUserLocation({ lat, lng });
      try {
        const nearby = await getNearbyStops({ lat, lng, limit: 1 });
        if (requestIdRef.current !== requestId) return;
        if (nearby.length === 0) {
          setLocateError("No bus stops found near your location. Try searching manually.");
          return;
        }
        const stop = nearby[0];
        setNearestStop(stop);
        // Announce the outcome in terms the app actually knows: the nearest
        // stop's name. No distance -- /stops/nearby returns no distance and
        // the walking distance (walkingRoute.distance_m) is optional, so it
        // would be a misleading "nearest" figure when the foot route is down.
        // This is also why it does not claim it filled the "From" field: the
        // caller only offers the stop as the origin when that field is empty.
        setLocateStatus(`Nearest stop found: ${stop.stop_name}.`);
        onStopFound?.(buildStopLabel(stop, stops));

        try {
          const walk = await getWalkingRoute({
            from_lat: lat,
            from_lng: lng,
            to_lat: stop.lat,
            to_lng: stop.lng,
          });
          if (requestIdRef.current === requestId) setWalkingRoute(walk);
        } catch {
          // Walking directions are a nice-to-have (needs a foot-profile
          // OSRM instance running); the map falls back to a straight line
          // if this fails, so just leave walkingRoute null.
          if (requestIdRef.current === requestId) setWalkingRoute(null);
        }
      } catch (err) {
        if (requestIdRef.current !== requestId) return;
        if (err instanceof Error && err.name === "ApiError") {
          // Check for specific API error types
          const apiErr = err as { kind?: string };
          if (apiErr.kind === "network") {
            setLocateError("Connection failed. Check your internet and try again.");
          } else if (apiErr.kind === "timeout") {
            setLocateError("Request timed out. Try again in a moment.");
          } else {
            setLocateError("Couldn't find a nearby stop. Try again.");
          }
        } else {
          setLocateError("Couldn't find a nearby stop. Try again.");
        }
      }
    },
    [stops, onStopFound]
  );

  // useCallback so a caller that passes this down through a memoized
  // component keeps a stable prop identity; it only changes when
  // locateNearestStop does (i.e. when `stops` or `onStopFound` change).
  const useMyLocation = useCallback(() => {
    if (!navigator.geolocation) {
      setLocateError("Geolocation isn't available in this browser. Please search for a stop manually.");
      return;
    }
    setLocating(true);
    setLocateError(null);
    // Clear any previous success so a repeat request announces again even if
    // it resolves to the same nearest stop (a live region only speaks a
    // change, and the text would otherwise be identical).
    setLocateStatus(null);
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        await locateNearestStop(position.coords.latitude, position.coords.longitude);
        if (requestIdRef.current !== 0) setLocating(false);
      },
      (err) => {
        let message: string;
        switch (err.code) {
          case err.PERMISSION_DENIED:
            message = "Location permission denied. Enable it in browser settings or search for a stop manually.";
            break;
          case err.TIMEOUT:
            message = "Location request timed out. Try again or search for a stop manually.";
            break;
          case err.POSITION_UNAVAILABLE:
            message = "Location unavailable. Make sure GPS/location services are enabled, or search for a stop manually.";
            break;
          default:
            message = "Could not get your location. Please search for a stop manually.";
        }
        setLocateError(message);
        setLocating(false);
      },
      { timeout: 8000, enableHighAccuracy: true }
    );
  }, [locateNearestStop]);

  return { userLocation, nearestStop, walkingRoute, locating, locateError, locateStatus, useMyLocation };
}