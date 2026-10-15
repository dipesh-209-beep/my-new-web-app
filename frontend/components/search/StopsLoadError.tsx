import InlineAlert from "@/components/ui/InlineAlert";

interface StopsLoadErrorProps {
  /** True when a previous load succeeded, so the list on screen is stale
   * but still usable -- changes the message and keeps search possible. */
  hasStaleStops: boolean;
  onRetry: () => void;
}

/**
 * Shown when the stop list fails to load. Route search depends entirely on
 * that list (SearchForm resolves labels against it and the map draws its
 * pickable stops from it), so this blocks search rather than merely
 * degrading it -- unless an earlier load already succeeded, in which case
 * the stale list still works. The copy must say which case the user is in.
 */
export default function StopsLoadError({ hasStaleStops, onRetry }: StopsLoadErrorProps) {
  return (
    <InlineAlert variant="warning" action={{ label: "Retry", onClick: onRetry }}>
      {hasStaleStops
        ? "Couldn't refresh the bus stop list. You can keep searching with the stops already loaded, but they may be out of date."
        : "Couldn't load the bus stop list, so route search isn't available yet. Retry to load it."}
    </InlineAlert>
  );
}
