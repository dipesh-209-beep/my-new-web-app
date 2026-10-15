import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import StopsLoadError from "./StopsLoadError";

describe("StopsLoadError a11y", () => {
  it("has no axe violations with no fallback list", async () => {
    const { container } = render(<StopsLoadError hasStaleStops={false} onRetry={() => {}} />);
    expect((await axe(container)).violations).toHaveLength(0);
  });

  it("has no axe violations with a stale list", async () => {
    const { container } = render(<StopsLoadError hasStaleStops onRetry={() => {}} />);
    expect((await axe(container)).violations).toHaveLength(0);
  });
});
