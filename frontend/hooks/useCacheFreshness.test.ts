import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useCacheFreshness } from "@/hooks/useCacheFreshness";

let serviceWorker: EventTarget;

beforeEach(() => {
  serviceWorker = new EventTarget();
  Object.defineProperty(navigator, "serviceWorker", {
    value: serviceWorker,
    configurable: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function postStatus(data: unknown) {
  act(() => {
    serviceWorker.dispatchEvent(new MessageEvent("message", { data }));
  });
}

describe("useCacheFreshness", () => {
  it("starts fresh and online", () => {
    const { result } = renderHook(() => useCacheFreshness());
    expect(result.current.stale).toBe(false);
    expect(result.current.cachedAt).toBeNull();
    expect(result.current.offline).toBe(false);
  });

  it("reports staleness and cached-at from a worker status message", () => {
    const { result } = renderHook(() => useCacheFreshness());
    postStatus({ type: "ktm-bus-cache-status", stale: true, cachedAt: 12345 });
    expect(result.current.stale).toBe(true);
    expect(result.current.cachedAt).toBe(12345);
  });

  it("clears staleness when a fresh status arrives", () => {
    const { result } = renderHook(() => useCacheFreshness());
    postStatus({ type: "ktm-bus-cache-status", stale: true, cachedAt: 12345 });
    postStatus({ type: "ktm-bus-cache-status", stale: false, cachedAt: 99999 });
    expect(result.current.stale).toBe(false);
    expect(result.current.cachedAt).toBe(99999);
  });

  it("ignores unrelated messages", () => {
    const { result } = renderHook(() => useCacheFreshness());
    postStatus({ type: "some-other-message", stale: true });
    postStatus(null);
    expect(result.current.stale).toBe(false);
  });

  it("tracks offline transitions from window events", () => {
    const { result } = renderHook(() => useCacheFreshness());
    const onLine = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    act(() => window.dispatchEvent(new Event("offline")));
    expect(result.current.offline).toBe(true);
    onLine.mockReturnValue(true);
    act(() => window.dispatchEvent(new Event("online")));
    expect(result.current.offline).toBe(false);
  });
});
