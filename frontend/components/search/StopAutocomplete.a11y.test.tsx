import { describe, expect, it } from "vitest";
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "vitest-axe";
import StopAutocomplete from "./StopAutocomplete";
import { Stop } from "@/types/route";

const STOPS: Stop[] = [
  {
    stop_id: "S1",
    stop_name: "Ratna Park",
    lat: 0,
    lng: 0,
    is_major_stop: true,
    is_interchange: false,
    status: "active",
  },
  {
    stop_id: "S2",
    stop_name: "Baneshwor",
    lat: 0,
    lng: 0,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
  },
];

/** Controlled host so the real component can be typed into. This is a
 * stateful parent, not a stand-in for the autocomplete itself. */
function Harness({ stops = STOPS }: { stops?: Stop[] }) {
  const [value, setValue] = useState("");
  return (
    <StopAutocomplete
      id="origin"
      label="From"
      stops={stops}
      inputValue={value}
      onInputChange={setValue}
      onSelect={(stop) => setValue(stop.stop_name)}
      debounceMs={0}
    />
  );
}

describe("StopAutocomplete a11y", () => {
  it("has no axe violations in the collapsed state and exposes combobox semantics", async () => {
    const { container } = render(<Harness />);
    const combobox = screen.getByRole("combobox", { name: "From" });
    expect(combobox).toHaveAttribute("aria-expanded", "false");
    expect(combobox).toHaveAttribute("aria-autocomplete", "list");
    expect(combobox).toHaveAttribute("aria-controls");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });

  it("has no axe violations with the suggestion listbox expanded", async () => {
    const { container } = render(<Harness />);
    const user = userEvent.setup();
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "rat");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThan(0);
    expect(combobox).toHaveAttribute("aria-expanded", "true");
    expect(combobox).toHaveAttribute("aria-controls", screen.getByRole("listbox").id);
    expect(combobox).toHaveAttribute("aria-activedescendant", options[0].id);

    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });

  it("has no axe violations when no stop matches the query", async () => {
    const { container } = render(<Harness />);
    const user = userEvent.setup();
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "zzzzzz");
    await waitFor(() => expect(screen.getByText(/No stops match/)).toBeInTheDocument());

    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });
});
