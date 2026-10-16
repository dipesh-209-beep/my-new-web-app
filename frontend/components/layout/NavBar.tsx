"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronIcon } from "@/components/icons/TransitIcons";
import UserLogin from "@/components/user/UserLogin";
import { useUserAuth } from "@/components/user/UserAuthContext";

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
  const [authOpen, setAuthOpen] = useState(false);
  const { token, username, hydrated: authHydrated, logout } = useUserAuth();
  // The sign-in control is a disclosure (a button revealing a panel that
  // holds a login form or a sign-out action), not a menu or a dialog, so it
  // gets aria-expanded/aria-controls and Escape-to-close -- not
  // aria-haspopup, which would promise a menu it never renders.
  const authPanelId = useId();
  const authRef = useRef<HTMLDivElement>(null);
  const authTriggerRef = useRef<HTMLButtonElement>(null);
  // The panel is a sibling of the trigger's wrapper (it anchors to the
  // right-hand actions group so `right-0` resolves against the bar's edge),
  // so the outside-click check has to know about it separately.
  const authPanelRef = useRef<HTMLDivElement>(null);

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

  // Close the account popup on an outside click or Escape, the same pattern
  // the autocomplete uses. Both listeners are document-level and registered
  // only while the popup is open, so they are not left attached for the whole
  // session -- and Escape works from anywhere inside the popup, not just the
  // trigger.
  useEffect(() => {
    if (!authOpen) return;
    function handleClick(e: MouseEvent) {
      const target = e.target as Node;
      // "Inside" means the trigger's wrapper or the panel itself -- the panel
      // no longer nests inside the wrapper, because it anchors to the actions
      // group so its `right-0` lines up with the bar's right edge.
      if (authRef.current?.contains(target) || authPanelRef.current?.contains(target)) {
        return;
      }
      setAuthOpen(false);
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setAuthOpen(false);
        // Focus goes back to the trigger rather than being dropped to <body>
        // when the panel (and whatever inside it held focus) unmounts.
        authTriggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [authOpen]);

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
    <Link href="/" className="flex min-h-11 min-w-11 items-center gap-1.5 font-semibold tracking-tight">
      <span
        aria-hidden
        className="flex h-5 w-5 items-center justify-center rounded-[3px] bg-brand text-[10px] font-bold text-ink"
      >
        K
      </span>
      <span className="sr-only text-ink sm:not-sr-only sm:inline">KTM</span>{" "}
      <span className="sr-only text-brand-dark sm:not-sr-only sm:inline">Transit</span>
    </Link>
  );

  if (minimized && hydrated) {
    return (
      <header className="flex min-h-7 shrink-0 items-center justify-between border-b border-route-line bg-surface-raised px-3 py-1 text-xs sm:px-4">
        {brandMark}
        <button
          type="button"
          onClick={toggleMinimized}
          aria-label="Restore navigation bar"
          title="Restore navigation bar"
          className="flex min-h-11 min-w-11 items-center justify-center gap-1 rounded p-2 text-ink-secondary hover:text-ink"
        >
          <ChevronIcon direction="down" />
        </button>
      </header>
    );
  }

  return (
    <header className="flex min-h-12 shrink-0 flex-wrap items-center justify-between gap-x-2 gap-y-1 border-b border-route-line bg-surface-raised px-2 py-1.5 text-sm sm:gap-x-3 sm:px-4">
      {brandMark}
      <nav className="flex items-center gap-1 sm:gap-5">
        {LINKS.map((link) => {
          const isActive = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-current={isActive ? "page" : undefined}
              className={`inline-flex min-h-11 min-w-11 items-center justify-center border-b-2 transition-colors ${
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
      {/* The sign-in panel is anchored to this group rather than to the
          trigger's own wrapper, so `right-0` resolves against the bar's
          right-hand edge instead of an element 8 px + one button away from it.
          The group also carries `max-sm:ml-auto`: below `sm` the narrow header
          can wrap these actions onto a second row, and `justify-between` alone
          would park the lone group at that row's *left* edge -- dragging a
          right-anchored 320 px panel off the left of a 320 px viewport.
          `ml-auto` is scoped to `max-sm` because at `sm` and up the header
          never wraps and `justify-between` already places the group on the
          right, so desktop spacing is left untouched. */}
      <div className="relative flex items-center gap-2 max-sm:ml-auto sm:gap-3">
        <span className="hidden items-center gap-1.5 text-xs text-ink-secondary sm:flex">
          <span className="h-1.5 w-1.5 rounded-full bg-accent-green" aria-hidden />
          Kathmandu Valley
        </span>
        <div ref={authRef}>
          <button
            type="button"
            ref={authTriggerRef}
            onClick={() => setAuthOpen((v) => !v)}
            aria-expanded={authOpen}
            aria-controls={authOpen ? authPanelId : undefined}
            aria-label={authHydrated && token ? `Account: ${username ?? "signed in"}` : "Sign in"}
            className="flex min-h-11 max-w-[14rem] items-center gap-1.5 rounded-md border border-route-line px-2.5 py-1 text-sm text-ink transition-colors hover:border-accent-blue"
          >
            {authHydrated && token ? (
              <>
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent-green" aria-hidden />
                <span className="truncate">{username ?? "Signed in"}</span>
              </>
            ) : (
              <span className="text-ink-secondary">Sign in</span>
            )}
            <ChevronIcon direction={authOpen ? "up" : "down"} />
          </button>
        </div>
        {/* `w-80` (20rem) is the desktop width; the `max-w` cap stops a 320 px
            panel overhanging the left edge of a narrow viewport (a 320 px
            viewport minus the header's 8 px padding cannot hold a 320 px box).
            The 1rem leaves that same 8 px as a margin on both sides. It only
            binds below ~384 px, so on desktop `w-80` wins and the popup is
            unchanged. */}
        {authOpen && (
          <div
            id={authPanelId}
            ref={authPanelRef}
            className="absolute right-0 top-full z-50 mt-2 w-80 max-w-[calc(100vw-1rem)]"
          >
            {authHydrated && token ? (
              <div className="rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
                <p className="text-sm text-ink">
                  Signed in as <span className="font-medium">{username ?? "a user"}</span>
                </p>
                <button
                  type="button"
                  onClick={() => {
                    logout();
                    setAuthOpen(false);
                  }}
                  className="mt-3 rounded-md border border-route-line px-3 py-1.5 text-sm text-ink-secondary transition-colors hover:border-accent-red hover:text-accent-red"
                >
                  Sign out
                </button>
              </div>
            ) : (
              <UserLogin onDone={() => setAuthOpen(false)} />
            )}
          </div>
        )}
        <button
          type="button"
          onClick={toggleMinimized}
          aria-label="Minimize navigation bar"
          title="Minimize navigation bar"
          className="flex min-h-11 min-w-11 items-center justify-center gap-1 rounded p-2 text-ink-secondary hover:text-ink"
        >
          <ChevronIcon direction="up" />
        </button>
      </div>
    </header>
  );
}
