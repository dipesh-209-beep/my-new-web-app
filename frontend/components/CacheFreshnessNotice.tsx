"use client";

import { useCacheFreshness } from "@/hooks/useCacheFreshness";

/** Human-readable age of a cached copy, or null when the time is unknown. */
export function formatCachedAge(cachedAt: number | null, now: number = Date.now()): string | null {
  if (!cachedAt || cachedAt <= 0) return null;
  const minutes = Math.max(0, Math.round((now - cachedAt) / 60000));
  if (minutes < 1) return "just now";
  if (minutes === 1) return "1 minute ago";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
}

/**
 * A low-key notice telling the user that the list data on screen came from
 * the service worker's cache rather than a live network response. Renders
 * nothing in the normal online/fresh case, so it is not visual noise.
 */
export default function CacheFreshnessNotice() {
  const { stale, cachedAt, offline } = useCacheFreshness();

  if (!offline && !stale) return null;

  const age = formatCachedAge(cachedAt);

  return (
    <p
      role="status"
      className="rounded-lg border border-route-line bg-surface-sunken px-3 py-2 text-xs text-ink-secondary"
    >
      {offline
        ? "You're offline — showing saved data."
        : "Showing saved data; it may be a little out of date."}
      {!offline && age ? ` Last updated ${age}.` : ""}
    </p>
  );
}
