import type { ButtonHTMLAttributes, ReactNode } from "react";

interface SelectablePillProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  /** Whether this option is the current one; drives `aria-pressed` and the
   * selected vs unselected styling. */
  selected: boolean;
  children: ReactNode;
}

// The exact tokens that were previously duplicated verbatim on the two
// route-result option buttons (the Direct/Recommended pill and the
// alternatives). Kept as constants so the primitive and its tests pin the
// same strings, and so a consumer can only extend -- never silently change --
// the shared look.
const BASE =
  "inline-flex min-h-11 shrink-0 items-center rounded-full border px-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-purple";
const SELECTED = "border-accent-purple bg-accent-purple/10 font-medium text-accent-purple";
const UNSELECTED =
  "border-route-line bg-white text-ink-secondary hover:border-accent-purple hover:text-accent-purple";

/**
 * A single-select pill for option groups (e.g. route alternatives). Renders a
 * native `<button type="button">`, so keyboard activation (Enter/Space),
 * focus, and the 44px touch target (`min-h-11`) are the browser's, and the
 * `aria-pressed` state mirrors `selected`. Styling is fixed here; pass
 * `className` only to add layout around the pill, not to restyle it.
 */
export default function SelectablePill({
  selected,
  className = "",
  children,
  ...rest
}: SelectablePillProps) {
  const classes = `${BASE} ${selected ? SELECTED : UNSELECTED} ${className}`.trim();
  return (
    <button type="button" className={classes} {...rest} aria-pressed={selected}>
      {children}
    </button>
  );
}
