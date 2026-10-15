import { useCallback, useEffect, useState } from "react";
import { Stop } from "@/types/route";
import { getAllStops } from "@/lib/api";

interface UseStopsResult {
  stops: Stop[];
  loading: boolean;
  error: boolean;
  reload: () => void;
}

/**
 * Loads every stop once on mount (paged through internally by
 * getAllStops). The returned list is what stop resolution depends on:
 * SearchForm resolves typed/picked labels against it, the map draws its
 * pickable stops from it, and "use my location" disambiguates against it.
 * So when this fails, route search is genuinely unavailable until a retry
 * succeeds -- the failure is surfaced as `error` (with a working `reload`)
 * rather than swallowed, and an empty list from the server is treated as a
 * failure too, never as a valid "no stops".
 *
 * On a failed reload the previously loaded stops are kept, so a transient
 * refresh error doesn't force the user back to an unusable empty state.
 */
export function useStops(): UseStopsResult {
  const [stops, setStops] = useState<Stop[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(false);
      try {
        const all = await getAllStops();
        if (cancelled) return;
        if (all.length === 0) {
          // A route planner with no stops can't resolve anything, so an
          // empty response is a failed load, not a valid empty list.
          setError(true);
        } else {
          setStops(all);
        }
      } catch {
        if (!cancelled) setError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  return { stops, loading, error, reload };
}
