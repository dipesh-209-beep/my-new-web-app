import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import SearchForm, { ViaStopField } from "./SearchForm";
import { Stop } from "@/types/route";

vi.mock("@/components/icons/TransitIcons", () => ({
  CrosshairIcon: () => <svg />,
  LocationIcon: () => <svg />,
  SwapIcon: () => <svg />,
  PinDotIcon: () => <svg />,
}));

function makeStop(id: string, name: string): Stop {
  return {
    stop_id: id,
    stop_name: name,
    lat: 27.7,
    lng: 85.3,
    zone: null,
    district: null,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
  };
}

function renderForm(onSearch: (o: string, d: string, v: string[], a: boolean) => void, viaStops: ViaStopField[] = []) {
  const stops = [
    makeStop("S0001", "Ratnapark"),
    makeStop("S0002", "Sundhara"),
    makeStop("S0003", "Koteshwor"),
  ];
  render(
    <SearchForm
      stops={stops}
      originText="Ratnapark"
      destinationText="Sundhara"
      onOriginTextChange={() => {}}
      onDestinationTextChange={() => {}}
      viaStops={viaStops}
      onViaStopsChange={() => {}}
      pickTarget={null}
      onPickTargetChange={() => {}}
      onUseMyLocation={() => {}}
      onSearch={onSearch}
    />
  );
}

function avoidCheckbox() {
  return screen.getByRole("checkbox", { name: /avoid congested routes/i });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: /find route/i }));
}

describe("SearchForm avoid-congestion control", () => {
  it("starts off by default", () => {
    renderForm(vi.fn());
    expect(avoidCheckbox()).not.toBeChecked();
  });

  it("submits avoidCongestion=false when left untouched", () => {
    const onSearch = vi.fn();
    renderForm(onSearch);
    submit();
    expect(onSearch).toHaveBeenCalledWith("S0001", "S0002", [], false);
  });

  it("affects the next search once enabled", () => {
    const onSearch = vi.fn();
    renderForm(onSearch);
    fireEvent.click(avoidCheckbox());
    submit();
    expect(onSearch).toHaveBeenCalledWith("S0001", "S0002", [], true);
  });

  it("still forwards via stops when congestion avoidance is enabled", () => {
    const onSearch = vi.fn();
    renderForm(onSearch, [{ id: "via-1", text: "Koteshwor" }]);
    fireEvent.click(avoidCheckbox());
    submit();
    expect(onSearch).toHaveBeenCalledWith("S0001", "S0002", ["S0003"], true);
  });
});

describe("SearchForm stop-list availability", () => {
  function renderWithStops(
    stops: Stop[],
    onSearch: (o: string, d: string, v: string[], a: boolean) => void,
    stopsLoading = false
  ) {
    return render(
      <SearchForm
        stops={stops}
        stopsLoading={stopsLoading}
        originText="Ratnapark"
        destinationText="Sundhara"
        onOriginTextChange={() => {}}
        onDestinationTextChange={() => {}}
        viaStops={[]}
        onViaStopsChange={() => {}}
        pickTarget={null}
        onPickTargetChange={() => {}}
        onUseMyLocation={() => {}}
        onSearch={onSearch}
      />
    );
  }

  it("disables search while the stop list is unavailable", () => {
    renderWithStops([], vi.fn(), true);
    expect(screen.getByRole("button", { name: /find route/i })).toBeDisabled();
  });

  it("blocks submission with an accurate message when stops are unavailable", () => {
    const onSearch = vi.fn();
    const { container } = renderWithStops([], onSearch);

    fireEvent.submit(container.querySelector("form") as HTMLFormElement);

    expect(onSearch).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/stop list/i);
  });

  it("enables search once the stop list is available", () => {
    const onSearch = vi.fn();
    renderWithStops([makeStop("S0001", "Ratnapark"), makeStop("S0002", "Sundhara")], onSearch);

    const button = screen.getByRole("button", { name: /find route/i });
    expect(button).toBeEnabled();
    submit();
    expect(onSearch).toHaveBeenCalledWith("S0001", "S0002", [], false);
  });
});
