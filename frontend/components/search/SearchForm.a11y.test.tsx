import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import SearchForm, { ViaStopField } from "./SearchForm";
import { Stop } from "@/types/route";

vi.mock("@/components/icons/TransitIcons", () => ({
  CrosshairIcon: () => <svg />,
  LocationIcon: () => <svg />,
  SwapIcon: () => <svg />,
  PinDotIcon: () => <svg />,
}));

describe("SearchForm a11y", () => {
  it("has no axe violations", async () => {
    const stops: Stop[] = [];
    const viaStops: ViaStopField[] = [];
    const { container } = render(
      <SearchForm
        stops={stops}
        originText=""
        destinationText=""
        onOriginTextChange={() => {}}
        onDestinationTextChange={() => {}}
        viaStops={viaStops}
        onViaStopsChange={() => {}}
        pickTarget={null}
        onPickTargetChange={() => {}}
        onUseMyLocation={() => {}}
        onSearch={() => {}}
      />,
    );
    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });
});
