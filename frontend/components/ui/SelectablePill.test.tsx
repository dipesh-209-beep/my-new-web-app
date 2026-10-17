import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SelectablePill from "./SelectablePill";

// The shared tokens the primitive must keep emitting. Pinning them proves the
// extraction from RouteResultPanel's inline buttons kept the same look.
const BASE_TOKENS = [
  "inline-flex",
  "min-h-11",
  "shrink-0",
  "items-center",
  "rounded-full",
  "border",
  "px-3",
  "text-xs",
  "focus-visible:ring-2",
  "focus-visible:ring-accent-purple",
];
const SELECTED_TOKENS = [
  "border-accent-purple",
  "bg-accent-purple/10",
  "font-medium",
  "text-accent-purple",
];
const UNSELECTED_TOKENS = [
  "border-route-line",
  "bg-white",
  "text-ink-secondary",
  "hover:border-accent-purple",
  "hover:text-accent-purple",
];

/** Split into whole class tokens so `hover:border-accent-purple` is not
 * mistaken for the selected `border-accent-purple`. */
function classTokens(element: HTMLElement): Set<string> {
  return new Set(element.className.split(/\s+/).filter(Boolean));
}

describe("SelectablePill", () => {
  it("is a native button whose pressed state mirrors `selected`", () => {
    const { rerender } = render(<SelectablePill selected={false}>Direct</SelectablePill>);
    const button = screen.getByRole("button", { name: "Direct" });
    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveAttribute("aria-pressed", "false");

    rerender(<SelectablePill selected>Direct</SelectablePill>);
    expect(screen.getByRole("button", { name: "Direct" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps the exact base + selected class tokens", () => {
    render(<SelectablePill selected>Direct</SelectablePill>);
    const tokens = classTokens(screen.getByRole("button"));
    [...BASE_TOKENS, ...SELECTED_TOKENS].forEach((token) => expect(tokens.has(token)).toBe(true));
    UNSELECTED_TOKENS.forEach((token) => expect(tokens.has(token)).toBe(false));
  });

  it("keeps the exact base + unselected class tokens", () => {
    render(<SelectablePill selected={false}>Direct</SelectablePill>);
    const tokens = classTokens(screen.getByRole("button"));
    [...BASE_TOKENS, ...UNSELECTED_TOKENS].forEach((token) => expect(tokens.has(token)).toBe(true));
    SELECTED_TOKENS.forEach((token) => expect(tokens.has(token)).toBe(false));
  });

  it("appends a caller className after the shared classes", () => {
    render(
      <SelectablePill selected className="self-start">
        Direct
      </SelectablePill>
    );
    const cls = screen.getByRole("button").className;
    expect(cls).toContain("rounded-full");
    expect(cls.endsWith("self-start")).toBe(true);
  });

  it("activates on click and on Enter/Space", async () => {
    const onClick = vi.fn();
    render(
      <SelectablePill selected={false} onClick={onClick}>
        Direct
      </SelectablePill>
    );
    const user = userEvent.setup();
    const button = screen.getByRole("button");

    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);

    button.focus();
    expect(button).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.keyboard(" ");
    expect(onClick).toHaveBeenCalledTimes(3);
  });

  it("forwards an accessible name and stays labelled when selected", () => {
    render(
      <SelectablePill selected aria-label="Least distance: 2 legs">
        Least distance
      </SelectablePill>
    );
    const button = screen.getByRole("button", { name: "Least distance: 2 legs" });
    expect(button).toHaveAttribute("aria-pressed", "true");
  });

  // `selected` owns the pressed state; a stray aria-pressed in the spread
  // cannot desync it.
  it("owns aria-pressed so it always mirrors `selected`", () => {
    render(
      <SelectablePill selected aria-pressed={false}>
        Direct
      </SelectablePill>
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
  });
});
