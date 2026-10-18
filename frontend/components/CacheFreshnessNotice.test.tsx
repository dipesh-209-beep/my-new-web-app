import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import CacheFreshnessNotice, { formatCachedAge } from "@/components/CacheFreshnessNotice";

describe("formatCachedAge", () => {
  const now = 1_000_000_000_000;

  it("returns null when the cached time is unknown", () => {
    expect(formatCachedAge(null, now)).toBeNull();
    expect(formatCachedAge(0, now)).toBeNull();
  });

  it("formats the age in minutes and hours", () => {
    expect(formatCachedAge(now, now)).toBe("just now");
    expect(formatCachedAge(now - 60_000, now)).toBe("1 minute ago");
    expect(formatCachedAge(now - 5 * 60_000, now)).toBe("5 minutes ago");
    expect(formatCachedAge(now - 60 * 60_000, now)).toBe("1 hour ago");
    expect(formatCachedAge(now - 150 * 60_000, now)).toBe("3 hours ago");
  });
});

let serviceWorker: EventTarget;

beforeEach(() => {
  serviceWorker = new EventTarget();
  Object.defineProperty(navigator, "serviceWorker", {
    value: serviceWorker,
    configurable: true,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CacheFreshnessNotice", () => {
  it("renders nothing when the data is fresh and online", () => {
    const { container } = render(<CacheFreshnessNotice />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a staleness note with the age when the worker reports stale data", () => {
    render(<CacheFreshnessNotice />);
    act(() => {
      serviceWorker.dispatchEvent(
        new MessageEvent("message", {
          data: {
            type: "ktm-bus-cache-status",
            stale: true,
            cachedAt: Date.now() - 5 * 60_000,
          },
        })
      );
    });
    const note = screen.getByRole("status");
    expect(note).toHaveTextContent("Showing saved data");
    expect(note).toHaveTextContent("5 minutes ago");
  });

  it("shows an offline note when the browser goes offline", () => {
    render(<CacheFreshnessNotice />);
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    act(() => window.dispatchEvent(new Event("offline")));
    expect(screen.getByRole("status")).toHaveTextContent(/offline/i);
  });
});
