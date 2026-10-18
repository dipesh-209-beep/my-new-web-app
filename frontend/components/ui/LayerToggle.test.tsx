import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LayerToggle from "./LayerToggle";

function renderToggle(enabled = false) {
  const onToggle = vi.fn();
  render(
    <LayerToggle
      icon={<svg data-testid="layer-icon" />}
      iconColorClass="text-accent-orange"
      iconBgClass="bg-accent-orange/10"
      activeBgClass="bg-accent-orange"
      hoverClass="hover:border-accent-orange hover:text-accent-orange"
      title="Traffic"
      subtitle="Show live traffic overlay"
      enabled={enabled}
      onToggle={onToggle}
    />,
  );
  return { onToggle };
}

describe("LayerToggle", () => {
  it("renders the title and subtitle and mirrors the off state", () => {
    renderToggle();
    expect(screen.getByText("Traffic")).toBeInTheDocument();
    expect(screen.getByText("Show live traffic overlay")).toBeInTheDocument();
    // The layer title is the section heading, so it joins the page's heading
    // outline rather than being an unlabelled paragraph.
    expect(screen.getByRole("heading", { name: "Traffic" })).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Traffic" });
    expect(button).toHaveTextContent("Off");
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it("mirrors the on state and reports presses", async () => {
    const { onToggle } = renderToggle(true);
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "Traffic" });
    expect(button).toHaveTextContent("On");
    expect(button).toHaveAttribute("aria-pressed", "true");
    await user.click(button);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  // The pill's visible text is only "On"/"Off"; the accessible name must be
  // the layer it controls (the visible title), or a screen-reader user hears
  // an unlabelled "On" with no idea what it turns on.
  it("names the toggle after the layer it controls, not the On/Off state", () => {
    renderToggle();
    expect(screen.getByRole("button", { name: "Traffic" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Off" })).toBeNull();
  });

  it("keeps the layer name as the enabled state changes", () => {
    renderToggle(true);
    expect(screen.getByRole("button", { name: "Traffic" })).toHaveAttribute("aria-pressed", "true");
  });

  it("activates with the keyboard", async () => {
    const { onToggle } = renderToggle();
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByRole("button")).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.keyboard(" ");
    expect(onToggle).toHaveBeenCalledTimes(2);
  });

  // Regression guard for the 320 px + 200% text-scaling overflow. A headless
  // Chromium probe showed the Traffic pill's box extending ~15 px past the
  // sidebar edge (the sidebar gained 16 px of horizontal scroll) because the
  // label column could not shrink and the pill could. jsdom cannot measure
  // layout, so this pins the two classes the probe proved remove the overflow:
  // the label column shrinks (`min-w-0`) instead of the pill (`shrink-0`).
  it("keeps the layout guards that stop the pill overflowing at 320px + 200% text", () => {
    renderToggle();
    const button = screen.getByRole("button");
    // Structure: <title p> -> inner text wrapper (min-w-0) -> outer label
    // column (min-w-0), reached by traversal rather than a class selector.
    const textWrapper = screen.getByText("Traffic").parentElement;
    const labelColumn = textWrapper?.parentElement;
    expect(button.className).toContain("shrink-0");
    expect(textWrapper?.className).toContain("min-w-0");
    expect(labelColumn?.className).toContain("min-w-0");
  });
});
