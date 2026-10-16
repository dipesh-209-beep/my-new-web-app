import { describe, expect, it, vi } from "vitest";
import { useState, type ComponentProps } from "react";
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

describe("SearchForm geolocation status", () => {
  function renderWithLocate(props: {
    locating?: boolean;
    locateError?: string | null;
    locateStatus?: string | null;
  }) {
    return render(
      <SearchForm
        stops={[makeStop("S0001", "Ratnapark"), makeStop("S0002", "Sundhara")]}
        originText="Ratnapark"
        destinationText="Sundhara"
        onOriginTextChange={() => {}}
        onDestinationTextChange={() => {}}
        viaStops={[]}
        onViaStopsChange={() => {}}
        pickTarget={null}
        onPickTargetChange={() => {}}
        onUseMyLocation={() => {}}
        onSearch={vi.fn()}
        {...props}
      />
    );
  }

  it("shows the success message in a polite status region", () => {
    renderWithLocate({ locateStatus: "Nearest stop found: Ratnapark." });
    expect(screen.getByRole("status")).toHaveTextContent("Nearest stop found: Ratnapark.");
  });

  it("keeps the status region mounted and empty before any success", () => {
    renderWithLocate({});
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("announces a failure as an alert without turning it into a success", () => {
    renderWithLocate({ locateError: "Location permission denied." });
    expect(screen.getByRole("alert")).toHaveTextContent("Location permission denied.");
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("reports the in-progress state on the button, not the status region", () => {
    renderWithLocate({ locating: true });
    const button = screen.getByRole("button", { name: /locating/i });
    // aria-disabled, not the disabled attribute: the button keeps focus while
    // geolocation is in flight instead of dropping it when disabled.
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).not.toBeDisabled();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });
});

describe("SearchForm focus management", () => {
  const stops = [makeStop("S0001", "Ratnapark"), makeStop("S0002", "Sundhara")];

  function renderForm(overrides: Partial<ComponentProps<typeof SearchForm>> = {}) {
    return render(
      <SearchForm
        stops={stops}
        originText="Ratnapark"
        destinationText="Sundhara"
        onOriginTextChange={() => {}}
        onDestinationTextChange={() => {}}
        viaStops={[]}
        onViaStopsChange={() => {}}
        pickTarget={null}
        onPickTargetChange={() => {}}
        onUseMyLocation={() => {}}
        onSearch={vi.fn()}
        {...overrides}
      />
    );
  }

  it("keeps Find route focusable while a search is in flight and ignores a repeat submit", () => {
    const onSearch = vi.fn();
    renderForm({ loading: true, onSearch });

    const button = screen.getByRole("button", { name: /searching/i });
    // aria-disabled (focus preserved), not the disabled attribute.
    expect(button).not.toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");

    fireEvent.click(button);
    expect(onSearch).not.toHaveBeenCalled();
  });

  it("keeps Use my location focusable while locating and does not re-request", () => {
    const onUseMyLocation = vi.fn();
    renderForm({ locating: true, onUseMyLocation });

    const button = screen.getByRole("button", { name: /locating/i });
    expect(button).not.toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");

    fireEvent.click(button);
    expect(onUseMyLocation).not.toHaveBeenCalled();
  });

  it("moves focus to Add a stop when a via row's remove button unmounts", () => {
    // viaStops is parent-controlled, so the row only disappears if the parent
    // re-renders with the filtered list -- model that with a stateful harness.
    function Harness() {
      const [vias, setVias] = useState<ViaStopField[]>([{ id: "via-1", text: "Koteshwor" }]);
      return (
        <SearchForm
          stops={stops}
          originText="Ratnapark"
          destinationText="Sundhara"
          onOriginTextChange={() => {}}
          onDestinationTextChange={() => {}}
          viaStops={vias}
          onViaStopsChange={setVias}
          pickTarget={null}
          onPickTargetChange={() => {}}
          onUseMyLocation={() => {}}
          onSearch={vi.fn()}
        />
      );
    }
    render(<Harness />);

    const remove = screen.getByRole("button", { name: /remove via stop 1/i });
    remove.focus();
    expect(remove).toHaveFocus();

    fireEvent.click(remove);

    // The remove button is gone; focus lands on the always-present add control
    // rather than being dropped to <body>.
    expect(screen.queryByRole("button", { name: /remove via stop 1/i })).toBeNull();
    expect(screen.getByRole("button", { name: /add a stop/i })).toHaveFocus();
  });

  it("marks Add a stop aria-disabled at the limit and refuses a fourth via", () => {
    const onViaStopsChange = vi.fn();
    renderForm({
      viaStops: [
        { id: "via-1", text: "A" },
        { id: "via-2", text: "B" },
        { id: "via-3", text: "C" },
      ],
      onViaStopsChange,
    });

    const add = screen.getByRole("button", { name: /add a stop/i });
    expect(add).toHaveAttribute("aria-disabled", "true");
    expect(add).not.toBeDisabled();

    fireEvent.click(add);
    expect(onViaStopsChange).not.toHaveBeenCalled();
  });
});
