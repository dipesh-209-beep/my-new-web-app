// Small shared icon set. Kept as one file since each icon is a handful of
// paths with no per-icon state -- splitting into separate files would just
// add import noise for something this small. All icons inherit color via
// `currentColor` so callers control hue with a text-* class.

interface IconProps {
  className?: string;
  size?: number;
}

export function BusIcon({ className, size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <rect x="3" y="4" width="18" height="13" rx="2" />
      <path d="M3 11h18" />
      <path d="M7 17v2M17 17v2" />
      <circle cx="7.5" cy="14" r="0.5" fill="currentColor" />
      <circle cx="16.5" cy="14" r="0.5" fill="currentColor" />
    </svg>
  );
}

/** Same silhouette as BusIcon but filled solid rather than outlined --
 * used wherever the bus glyph sits on a colored chip (the map's stop
 * markers, route badges) rather than inline with text, since a filled
 * mark reads clearly at the small sizes those chips render at. Kept as a
 * separate component (not a `variant` prop on BusIcon) so each call site
 * states its intent -- outline for text-adjacent use, filled for
 * chip/badge use -- without a boolean to look up. */
export function BusIconFilled({ className, size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden
    >
      <path d="M5 4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v11a2 2 0 0 1-1 1.73V19a1 1 0 0 1-1 1h-1a1 1 0 0 1-1-1v-1H8v1a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-2.27A2 2 0 0 1 4 15V4Zm2 0v6h10V4H7Zm.5 9.25a1.25 1.25 0 1 0 0 2.5 1.25 1.25 0 0 0 0-2.5Zm9 0a1.25 1.25 0 1 0 0 2.5 1.25 1.25 0 0 0 0-2.5Z" />
    </svg>
  );
}

export function WalkIcon({ className, size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <circle cx="13" cy="4" r="1.6" fill="currentColor" stroke="none" />
      <path d="M10 8.5 7 11l1 5" />
      <path d="M13 8.5 15 12l-1.5 3.5 1 4.5" />
      <path d="M10 8.5 15 9.5" />
      <path d="M9 16l-2.5 4M13.5 15.5l3 4.5" />
    </svg>
  );
}

export function SwapIcon({ className, size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M7 4v16" />
      <path d="M3 8l4-4 4 4" />
      <path d="M17 20V4" />
      <path d="M21 16l-4 4-4-4" />
    </svg>
  );
}

export function LocationIcon({ className, size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21Z" />
      <circle cx="12" cy="9.5" r="2.5" />
    </svg>
  );
}

export function CrosshairIcon({ className, size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      className={className}
      aria-hidden
    >
      <circle cx="12" cy="12" r="6" />
      <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
    </svg>
  );
}

export function PinDotIcon({ className, size = 12 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

export function LayersIcon({ className, size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M12 2 2 7l10 5 10-5-10-5Z" />
      <path d="m2 17 10 5 10-5" />
      <path d="m2 12 10 5 10-5" />
    </svg>
  );
}

export function TransferIcon({ className, size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M7 7h11l-3-3M17 17H6l3 3" />
    </svg>
  );
}

export function ClockIcon({ className, size = 14 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 3" />
    </svg>
  );
}

export function ChevronIcon({
  direction,
  className,
  size = 14,
}: IconProps & { direction: "up" | "down" }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      className={className}
      style={{ transform: direction === "up" ? "rotate(180deg)" : undefined }}
      aria-hidden
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}
