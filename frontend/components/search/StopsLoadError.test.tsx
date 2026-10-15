import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import StopsLoadError from "./StopsLoadError";

describe("StopsLoadError", () => {
  it("says search is unavailable and offers a working retry when there is no fallback list", () => {
    const onRetry = vi.fn();
    render(<StopsLoadError hasStaleStops={false} onRetry={onRetry} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/route search isn't available/i);
    expect(alert).not.toHaveTextContent(/keep searching/i);

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("says search still works with stale stops when a previous load succeeded", () => {
    render(<StopsLoadError hasStaleStops onRetry={() => {}} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/keep searching with the stops already loaded/i);
    expect(alert).not.toHaveTextContent(/isn't available/i);
  });
});
