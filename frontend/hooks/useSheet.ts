"use client";

import { useEffect, useRef, useState } from "react";
import { clampDragHeightPx, isNarrowViewport, nearestSnap, parseStoredSnap, SheetSnap, snapHeightPx } from "@/lib/sheetSnap";

const SIDEBAR_STORAGE_KEY = "ktm-transit:search-panel-minimized";

interface UseSheetReturn {
  sheetSnap: SheetSnap;
  setSheetSnap: (next: SheetSnap) => void;
  sidebarHydrated: boolean;
  dragHeightPx: number | null;
  setDragHeightPx: (height: number | null) => void;
  dragStartRef: React.RefObject<{ startY: number; startHeightPx: number } | null>;
  showMinimized: boolean;
  liveHeightPx: number;
  viewportHeight: number;
  toggleSidebarMinimized: () => void;
  /** Called when a route search succeeds: on a narrow viewport, drops the
   * sheet from its default "full" snap to "half" so the map (and the drawn
   * route) stays visible instead of the sheet covering most of the screen.
   * A no-op on desktop, and never overrides a deliberate minimize/half. */
  revealResults: () => void;
  handleHandlePointerDown: (e: React.PointerEvent<HTMLSpanElement>) => void;
  handleHandlePointerMove: (e: React.PointerEvent<HTMLSpanElement>) => void;
  handleHandlePointerUp: () => void;
  invalidateMap: () => void;
}

export function useSheet(invalidateMap?: () => void): UseSheetReturn {
  const [sheetSnap, setSheetSnapState] = useState<SheetSnap>("full");
  const [sidebarHydrated, setSidebarHydrated] = useState(false);
  const [dragHeightPx, setDragHeightPx] = useState<number | null>(null);
  const [viewportHeight, setViewportHeight] = useState(800);
  const dragStartRef = useRef<{ startY: number; startHeightPx: number } | null>(null);

  // Update viewport height on resize
  useEffect(() => {
    const updateViewportHeight = () => setViewportHeight(window.innerHeight);
    updateViewportHeight();
    window.addEventListener("resize", updateViewportHeight);
    return () => window.removeEventListener("resize", updateViewportHeight);
  }, []);

  // Hydrate from localStorage
  useEffect(() => {
    const timer = setTimeout(() => {
      setSheetSnapState(parseStoredSnap(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)));
      setSidebarHydrated(true);
    }, 0);
    return () => clearTimeout(timer);
  }, []);

  function setSheetSnap(next: SheetSnap) {
    setSheetSnapState(next);
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, next);
    } catch {
      // Storage unavailable (private browsing, quota) -- the toggle still
      // works for this session, it just won't persist across reloads.
    }
    // Invalidate map size after sheet snap changes
    if (invalidateMap) {
      // Use setTimeout to allow layout to settle
      setTimeout(() => invalidateMap(), 0);
    }
  }

  function toggleSidebarMinimized() {
    setSheetSnap(sheetSnap === "minimized" ? "full" : "minimized");
  }

  // Drop to "half" only when there is something worth making room for and
  // the user hasn't already chosen a smaller position. Deliberately does NOT
  // write localStorage: this is a per-session reaction to a search result,
  // not a change to the user's saved default. It also never runs on desktop,
  // where the sheet is a static column and the snap height is ignored.
  function revealResults() {
    if (typeof window === "undefined") return;
    if (!isNarrowViewport(window.innerWidth)) return;
    if (sheetSnap !== "full") return;
    setSheetSnapState("half");
    if (invalidateMap) {
      setTimeout(() => invalidateMap(), 0);
    }
  }

  function handleHandlePointerDown(e: React.PointerEvent<HTMLSpanElement>) {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragStartRef.current = { startY: e.clientY, startHeightPx: snapHeightPx(sheetSnap, viewportHeight) };
  }

  function handleHandlePointerMove(e: React.PointerEvent<HTMLSpanElement>) {
    if (!dragStartRef.current) return;
    const draggedUpBy = dragStartRef.current.startY - e.clientY;
    setDragHeightPx(
      clampDragHeightPx(dragStartRef.current.startHeightPx + draggedUpBy, viewportHeight)
    );
  }

  function handleHandlePointerUp() {
    if (dragHeightPx != null) {
      setSheetSnap(nearestSnap(dragHeightPx, viewportHeight));
    }
    setDragHeightPx(null);
    dragStartRef.current = null;
  }

  // Only collapse visually once hydrated -- otherwise a previously-
  // minimized panel would flash open on every load before the effect
  // above catches up. Content visibility follows the committed snap, not
  // the live drag height, so the form doesn't flicker in/out mid-drag.
  const showMinimized = sheetSnap === "minimized" && sidebarHydrated;
  const liveHeightPx = dragHeightPx ?? snapHeightPx(sidebarHydrated ? sheetSnap : "full", viewportHeight);

  return {
    sheetSnap,
    setSheetSnap,
    sidebarHydrated,
    dragHeightPx,
    setDragHeightPx,
    dragStartRef,
    showMinimized,
    liveHeightPx,
    viewportHeight,
    toggleSidebarMinimized,
    revealResults,
    handleHandlePointerDown,
    handleHandlePointerMove,
    handleHandlePointerUp,
    invalidateMap: invalidateMap ?? (() => {}),
  };
}