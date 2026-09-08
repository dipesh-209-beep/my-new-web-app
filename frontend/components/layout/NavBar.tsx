"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronIcon } from "@/components/icons/TransitIcons";

const LINKS = [
  { href: "/", label: "Plan" },
  { href: "/routes", label: "Routes" },
  { href: "/stops", label: "Stops" },
];

// Persisted so a minimized bar stays minimized across page loads/navigation
// instead of popping back open every time -- the whole point is reclaiming
// vertical space (especially on the map-first home page on mobile), which
// resetting on every navigation would undermine.
const STORAGE_KEY = "ktm-transit:navbar-minimized";

/**
 * Slim top bar, shared via app/layout.tsx. Kept intentionally minimal --
 * no dashboard chrome -- since the map-first home page is still the
 * primary surface; this just gives /routes and /stops a way back and a
 * consistent brand mark.
 *
 * Minimize/restore collapses this down to a sliver with just the brand
 * mark and a restore control, for anyone who wants the full viewport back
 * (most useful on the home page's map, especially on small screens).
 */
export default function NavBar() {
  const pathname = usePathname();

  // Starts expanded on every render (server and first client render must
  // match, and localStorage isn't available during SSR); synced from
  // storage right after mount, so there's a one-frame flash of "expanded"
  // for anyone who'd previously minimized it, rather than a hydration
  // mismatch.
  const [minimized, setMinimized] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    // Deferred a tick (rather than calling setState synchronously in the
    // effect body) so this reads as "sync from an external system on a
    // later tick," matching the pattern used elsewhere in this codebase
    // (see useGeolocation's mount-time effect) instead of a same-commit
    // cascading update.
    const timer = setTimeout(() => {
      setMinimized(window.localStorage.getItem(STORAGE_KEY) === "1");
      setHydrated(true);
    }, 0);
    return () => clearTimeout(timer);
  }, []);

  function toggleMinimized() {
    const next = !minimized;
    setMinimized(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
    } catch {
      // Storage unavailable (private browsing, quota) -- the toggle still
      // works for this session, it just won't persist across reloads.
    }
  }

  const brandMark = (
    <Link href="/" className="flex items-center gap-1.5 font-semibold tracking-tight">
      <span
        aria-hidden
        className="flex h-5 w-5 items-center justify-center rounded-[3px] bg-brand text-[10px] font-bold text-ink"
      >
        K
      </span>
      <span className="text-ink">KTM</span>
      <span className="text-brand-dark">Transit</span>
    </Link>
  );

  if (minimized && hydrated) {
    return (
      <header className="flex h-7 shrink-0 items-center justify-between border-b border-route-line bg-surface-raised px-4 text-xs">
        {brandMark}
        <button
          type="button"
          onClick={toggleMinimized}
          aria-label="Restore navigation bar"
          title="Restore navigation bar"
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-ink-secondary hover:text-ink"
        >
          <ChevronIcon direction="down" />
        </button>
      </header>
    );
  }

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-route-line bg-surface-raised px-4 text-sm">
      {brandMark}
      <nav className="flex items-center gap-5">
        {LINKS.map((link) => {
          const isActive = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-current={isActive ? "page" : undefined}
              className={`border-b-2 pb-[3px] transition-colors ${
                isActive
                  ? "border-brand font-medium text-ink"
                  : "border-transparent text-ink-secondary hover:text-ink"
              }`}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
      <div className="flex items-center gap-3">
        <span className="hidden items-center gap-1.5 text-xs text-ink-secondary sm:flex">
          <span className="h-1.5 w-1.5 rounded-full bg-accent-green" aria-hidden />
          Kathmandu Valley
        </span>
        <button
          type="button"
          onClick={toggleMinimized}
          aria-label="Minimize navigation bar"
          title="Minimize navigation bar"
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-ink-secondary hover:text-ink"
        >
          <ChevronIcon direction="up" />
        </button>
      </div>
    </header>
  );
}
