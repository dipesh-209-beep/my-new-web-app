import { describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StopAutocomplete from "./StopAutocomplete";
import { Stop } from "@/types/route";

function stop(stop_id: string, stop_name: string): Stop {
  return {
    stop_id,
    stop_name,
    lat: 0,
    lng: 0,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
  };
}

const STOPS: Stop[] = [stop("S1", "Ratna Park"), stop("S2", "Baneshwor")];

const MANY_MATCHES: Stop[] = [
  stop("S1", "Ratna Park"),
  stop("S2", "Ratna Chowk"),
  stop("S3", "Ratnapark Gate"),
];

/** Same count as MANY_MATCHES and all still match "rat", but no name is
 * shared -- a list that is replaced rather than merely shrunk. */
const REPLACEMENT_MATCHES: Stop[] = [
  stop("S4", "Ratna Mandir"),
  stop("S5", "Ratna Tole"),
  stop("S6", "Ratnapark Hub"),
];

/** Controlled host: the real component is stateful only through this parent,
 * exactly as SearchForm drives it. `stops` is passed through so a test can
 * shrink the candidate list while the query stays the same. */
function Harness({
  stops = STOPS,
  onSelect,
}: {
  stops?: Stop[];
  onSelect?: (s: Stop) => void;
}) {
  const [value, setValue] = useState("");
  return (
    <StopAutocomplete
      id="origin"
      label="From"
      stops={stops}
      inputValue={value}
      onInputChange={setValue}
      onSelect={(s) => {
        setValue(s.stop_name);
        onSelect?.(s);
      }}
      debounceMs={0}
    />
  );
}

describe("StopAutocomplete keyboard and focus behavior", () => {
  it("restores focus to the input when the clear button is activated", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "rat");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /clear from/i }));

    expect(combobox).toHaveFocus();
    expect(combobox).toHaveValue("");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("moves the active option down and back up with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "a");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());
    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThan(1);

    expect(combobox).toHaveAttribute("aria-activedescendant", options[0].id);

    await user.keyboard("{ArrowDown}");
    expect(combobox).toHaveAttribute("aria-activedescendant", options[1].id);

    await user.keyboard("{ArrowUp}");
    expect(combobox).toHaveAttribute("aria-activedescendant", options[0].id);
  });

  it("selects the active option on Enter and closes the list", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "rat");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    await user.keyboard("{Enter}");

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].stop_name).toBe("Ratna Park");
    expect(combobox).toHaveValue("Ratna Park");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes the suggestions on Escape without selecting", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "rat");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    await user.keyboard("{Escape}");

    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox).toHaveAttribute("aria-expanded", "false");
  });

  it("only sets aria-controls while the referenced listbox exists", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    expect(combobox).not.toHaveAttribute("aria-controls");

    await user.type(combobox, "rat");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());
    const controls = combobox.getAttribute("aria-controls");
    expect(controls).not.toBeNull();
    expect(document.getElementById(controls as string)).toBe(screen.getByRole("listbox"));

    await user.keyboard("{Escape}");
    expect(combobox).not.toHaveAttribute("aria-controls");
  });

  it("does not advertise expanded once the query is cleared to empty", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "rat");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());
    expect(combobox).toHaveAttribute("aria-expanded", "true");

    // Clearing fires onChange, which sets isOpen true again, but an empty
    // query yields no results -- so no listbox renders. aria-expanded must
    // describe that absent popup, not the leftover isOpen flag.
    await user.clear(combobox);

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox).toHaveAttribute("aria-expanded", "false");
    expect(combobox).not.toHaveAttribute("aria-controls");
  });

  it("keeps a valid active descendant when the result list shrinks", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const { rerender } = render(<Harness stops={MANY_MATCHES} onSelect={onSelect} />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "rat");
    await waitFor(() => expect(screen.getAllByRole("option").length).toBe(3));

    // Highlight the third option, then remove it from the candidate set
    // without touching the query.
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(combobox).toHaveAttribute(
      "aria-activedescendant",
      screen.getAllByRole("option")[2].id
    );

    rerender(<Harness stops={[MANY_MATCHES[0]]} onSelect={onSelect} />);

    await waitFor(() => expect(screen.getAllByRole("option").length).toBe(1));
    const active = combobox.getAttribute("aria-activedescendant");
    if (active !== null) {
      expect(document.getElementById(active)).not.toBeNull();
    }

    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].stop_name).toBe("Ratna Park");
  });

  it("keeps the active option valid when the result list is replaced at the same length", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const { rerender } = render(<Harness stops={MANY_MATCHES} onSelect={onSelect} />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "rat");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(3));

    // Highlight the second option and remember which stop that was.
    await user.keyboard("{ArrowDown}");
    const highlightedBefore = screen.getAllByRole("option")[1];
    expect(combobox).toHaveAttribute("aria-activedescendant", highlightedBefore.id);
    const removedName = highlightedBefore.textContent ?? "";

    // Replace the whole result set with a different one of the same length,
    // leaving the query untouched.
    rerender(<Harness stops={REPLACEMENT_MATCHES} onSelect={onSelect} />);

    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(3));
    const options = screen.getAllByRole("option");

    // aria-activedescendant still resolves to an option that is rendered.
    const active = combobox.getAttribute("aria-activedescendant");
    expect(active).not.toBeNull();
    const activeEl = document.getElementById(active as string);
    expect(activeEl).not.toBeNull();
    expect(options).toContain(activeEl);
    const activeName = activeEl?.textContent ?? "";

    // Enter selects the stop that is highlighted now -- one of the current
    // results, never the stop the list no longer contains.
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(1);
    const selectedName = onSelect.mock.calls[0][0].stop_name;
    expect(activeName).toContain(selectedName);
    expect(REPLACEMENT_MATCHES.map((s) => s.stop_name)).toContain(selectedName);
    expect(MANY_MATCHES.map((s) => s.stop_name)).not.toContain(selectedName);
    expect(selectedName).not.toBe(removedName);
  });

  it("shows the no-results message when nothing matches", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    await user.type(combobox, "zzzzzz");

    await waitFor(() =>
      expect(screen.getByText(/No stops match/)).toBeInTheDocument()
    );
    // The empty state is a listbox, so aria-controls must point at it.
    expect(combobox).toHaveAttribute(
      "aria-controls",
      screen.getByRole("listbox").id
    );
  });

  it("closes the no-results popup on Escape", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    const combobox = screen.getByRole("combobox", { name: "From" });

    // Type a query that yields no results
    await user.type(combobox, "zzzzzz");

    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());
    expect(screen.getByText(/No stops match/)).toBeInTheDocument();
    expect(combobox).toHaveAttribute("aria-expanded", "true");

    // Press Escape -- should close the popup
    await user.keyboard("{Escape}");

    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox).toHaveAttribute("aria-expanded", "false");
    expect(combobox).not.toHaveAttribute("aria-controls");
  });

  it("forwards describedById to the input's aria-describedby", () => {
    render(
      <StopAutocomplete
        id="test"
        label="Test"
        stops={STOPS}
        inputValue=""
        onInputChange={() => {}}
        onSelect={() => {}}
        describedById="test-error"
      />
    );
    expect(screen.getByRole("combobox", { name: "Test" })).toHaveAttribute(
      "aria-describedby",
      "test-error"
    );
  });

  it("does not set aria-describedby when describedById is omitted", () => {
    render(
      <StopAutocomplete
        id="test2"
        label="Test2"
        stops={STOPS}
        inputValue=""
        onInputChange={() => {}}
        onSelect={() => {}}
      />
    );
    expect(screen.getByRole("combobox", { name: "Test2" })).not.toHaveAttribute(
      "aria-describedby"
    );
  });
});
