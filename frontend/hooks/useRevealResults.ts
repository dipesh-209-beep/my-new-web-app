"use client";

import { useEffect, useRef } from "react";
import { RouteSearchResult } from "@/types/route";

/**
 * Calls `revealResults` exactly once per successful route search result.
 *
 * Only a found result triggers it: while a search is running (or before one
 * has run) `result` is null, and a 404 arrives as `{ found: false }`, so both
 * the loading and the empty/no-route states leave the sheet where the user
 * put it rather than forcing it into the success layout. The result-object
 * identity guards against re-firing on unrelated re-renders and against a
 * loop between the sheet position and this effect.
 */
export function useRevealResults(
  result: RouteSearchResult | null,
  revealResults: () => void
): void {
  const revealedResultRef = useRef<RouteSearchResult | null>(null);

  useEffect(() => {
    if (!result || !result.found) return;
    if (revealedResultRef.current === result) return;
    revealedResultRef.current = result;
    revealResults();
  }, [result, revealResults]);
}
