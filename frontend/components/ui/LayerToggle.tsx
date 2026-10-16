"use client";

import { useId } from "react";

interface LayerToggleProps {
  icon: React.ReactNode;
  /** Tailwind text-color class for the icon chip, e.g. "text-accent-orange". */
  iconColorClass: string;
  /** Tailwind bg-color class for the icon chip background, e.g. "bg-accent-orange/10". */
  iconBgClass: string;
  /** Tailwind bg-color class for the pill when it's on, e.g. "bg-accent-orange". */
  activeBgClass: string;
  /** Tailwind hover text/border color classes for the pill when it's off. */
  hoverClass: string;
  title: string;
  subtitle: string;
  enabled: boolean;
  onToggle: () => void;
}

/**
 * "Map layers" header row: an icon chip, a two-line label, and an on/off
 * pill. Used for both the all-stops toggle (app/page.tsx) and the traffic
 * toggle (CongestionPanel.tsx) -- previously each rebuilt this by hand
 * with slightly different colors for the same visual role.
 *
 * The pill itself is `rounded-md`, not `rounded-full` -- a fully-rounded
 * pill is the generic default toggle-switch shape used everywhere; this
 * app's map/tooltip chrome already moved to flatter, smaller radii (see
 * boxShadow.card in tailwind.config.ts), so the panel controls now match.
 */
export default function LayerToggle({
  icon,
  iconColorClass,
  iconBgClass,
  activeBgClass,
  hoverClass,
  title,
  subtitle,
  enabled,
  onToggle,
}: LayerToggleProps) {
  // The pill's only visible text is "On"/"Off", which names the state, not
  // the layer. Name the control after the layer it toggles via the visible
  // title instead, so a screen reader announces "Traffic, toggle button,
  // pressed" rather than an unlabelled "On". aria-pressed still carries the
  // state, so the visible "On"/"Off" text is left exactly as-is.
  const titleId = useId();

  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className={`flex h-7 w-7 items-center justify-center rounded-md ${iconBgClass} ${iconColorClass}`}>
          {icon}
        </span>
        <div className="min-w-0">
          <p id={titleId} className="text-xs font-semibold uppercase tracking-wide text-ink-secondary">
            {title}
          </p>
          <p className="text-sm text-ink">{subtitle}</p>
        </div>
      </div>
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={enabled}
        aria-labelledby={titleId}
        className={`inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-md px-3 text-xs font-medium transition-colors ${
          enabled ? `${activeBgClass} text-white` : `border border-route-line text-ink-secondary ${hoverClass}`
        }`}
      >
        {enabled ? "On" : "Off"}
      </button>
    </div>
  );
}
