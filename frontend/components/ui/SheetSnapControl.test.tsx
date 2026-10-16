import { describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SheetSnapControl from "./SheetSnapControl";
import { SheetSnap } from "@/lib/sheetSnap";

// Mirrors how app/page.tsx drives it: the chosen snap is owned by the
// parent (useSheet), so the arrow-key tests need the value to actually
// update or React would snap the checked radio straight back.
function Harness({ initial = "full" }: { initial?: SheetSnap }) {
  const [value, setValue] = useState<SheetSnap>(initial);
  return <SheetSnapControl value={value} onChange={setValue} />;
}

describe("SheetSnapControl", () => {
  it("exposes the three snap positions as a named radio group", () => {
    render(<SheetSnapControl value="full" onChange={() => {}} />);
    expect(screen.getByRole("radiogroup", { name: "Panel height" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Minimized" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Half height" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Full height" })).toBeInTheDocument();
  });

  it("exposes the current position as the checked radio", () => {
    render(<SheetSnapControl value="half" onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "Half height" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Minimized" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Full height" })).not.toBeChecked();
  });

  it("reports the chosen position", async () => {
    const onChange = vi.fn();
    render(<SheetSnapControl value="full" onChange={onChange} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("radio", { name: "Half height" }));
    expect(onChange).toHaveBeenCalledWith("half");
  });

  // The whole point of C8: a keyboard user, with no pointer drag, must be
  // able to land on every one of the three positions.
  it("reaches every position with the arrow keys", async () => {
    render(<Harness initial="full" />);
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByRole("radio", { name: "Full height" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("radio", { name: "Half height" })).toBeChecked();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("radio", { name: "Minimized" })).toBeChecked();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Half height" })).toBeChecked();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Full height" })).toBeChecked();
  });

  it("keeps the focused option selected when Space is pressed", async () => {
    render(<Harness initial="full" />);
    const user = userEvent.setup();
    await user.tab();
    await user.keyboard("{ArrowLeft}");
    const half = screen.getByRole("radio", { name: "Half height" });
    expect(half).toHaveFocus();
    await user.keyboard(" ");
    expect(half).toBeChecked();
    expect(half).toHaveFocus();
  });

  // A native radio group is one tab stop: only the checked radio is in the
  // tab order, so tabbing again leaves the group rather than stepping
  // through the other two options.
  it("is a single tab stop", async () => {
    render(<Harness initial="half" />);
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByRole("radio", { name: "Half height" })).toHaveFocus();
    await user.tab();
    expect(document.activeElement?.getAttribute("role")).not.toBe("radio");
  });

  // Selecting a different position must not drop focus, or a keyboard user
  // would be returned to the top of the page on every change.
  it("keeps focus on the chosen option after selecting it", async () => {
    render(<Harness initial="full" />);
    const user = userEvent.setup();
    await user.tab();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("radio", { name: "Half height" })).toHaveFocus();
    expect(screen.getByRole("radio", { name: "Half height" })).toBeChecked();
  });

  // WCAG 2.5.3: the visible short label ("Min") must appear in the spoken
  // accessible name ("Minimized"), so voice control can still target it.
  it("keeps each visible label inside its accessible name", () => {
    render(<SheetSnapControl value="full" onChange={() => {}} />);
    const pairs: [string, string][] = [
      ["Min", "Minimized"],
      ["Half", "Half height"],
      ["Full", "Full height"],
    ];
    for (const [visible, name] of pairs) {
      expect(screen.getByText(visible)).toBeInTheDocument();
      expect(name).toContain(visible);
      expect(screen.getByRole("radio", { name })).toBeInTheDocument();
    }
  });
});
