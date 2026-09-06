import { useRef, useState } from "react";
import { RouteSearchResult } from "@/types/route";
import { ApiError, findRoute } from "@/lib/api";

type LoadingStage = "idle" | "searching" | "calculating_alternatives" | "done";

interface UseRouteSearchResult {
  result: RouteSearchResult | null;
  loading: boolean;
  loadingStage: LoadingStage;
  error: string | null;
  /** viaIds: intermediate stop_ids the trip must pass through, in
   * order. include_alternatives is still requested, but the backend
   * ignores it (and returns no alternatives) whenever viaIds is
   * non-empty -- see findRoute's `via` param doc. */
  search: (originId: string, destinationId: string, viaIds?: string[]) => Promise<void>;
  retry: () => void;
  reset: () => void;
}

/**
 * Owns GET /route-finder request state. A 404 from the backend means
 * "no route between these stops" -- a normal outcome rendered as
 * `{ found: false }`, distinct from network/server errors which set
 * `error` instead.
 */
export function useRouteSearch(): UseRouteSearchResult {
  const [result, setResult] = useState<RouteSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState<LoadingStage>("idle");
  const [error, setError] = useState<string | null>(null);

  // Store last search params for retry
  const lastSearchRef = useRef<{ originId: string; destinationId: string; viaIds: string[] } | null>(null);

  // Guards against a slow earlier search overwriting a faster later one
  // if the user fires two searches back-to-back.
  const requestIdRef = useRef(0);

  async function search(originId: string, destinationId: string, viaIds: string[] = []) {
    const requestId = ++requestIdRef.current;
    lastSearchRef.current = { originId, destinationId, viaIds };
    setLoading(true);
    setLoadingStage("searching");
    setError(null);
    setResult(null);

    try {
      const outcome = await findRoute({
        origin: originId,
        destination: destinationId,
        include_alternatives: true,
        via: viaIds,
      });
      if (requestIdRef.current !== requestId) return;

      if (!outcome.found) {
        setResult({ found: false });
      } else {
        setLoadingStage("calculating_alternatives");
        setResult({ found: true, ...outcome.result });
      }
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      let message = "Couldn't reach the server. Check your connection and try again.";
      if (err instanceof ApiError) {
        if (err.kind === "timeout") {
          // route-finder gets the longer 20s budget specifically because it
          // may do OSRM-dependent work -- a timeout here most plausibly
          // means that's running slow, not that the request is malformed.
          message = "This is taking longer than usual. Try again in a moment.";
        } else if (err.kind !== "network") {
          message = "Something went wrong. Try again.";
        }
      }
      setError(message);
    } finally {
      if (requestIdRef.current === requestId) {
        setLoading(false);
        setLoadingStage("done");
      }
    }
  }

  function retry() {
    const params = lastSearchRef.current;
    if (params) {
      search(params.originId, params.destinationId, params.viaIds);
    }
  }

  function reset() {
    requestIdRef.current++;
    lastSearchRef.current = null;
    setResult(null);
    setError(null);
    setLoading(false);
    setLoadingStage("idle");
  }

  return { result, loading, loadingStage, error, search, retry, reset };
}