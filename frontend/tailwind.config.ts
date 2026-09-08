import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Deep indigo-ink base with ONE brand accent (marigold) reserved
        // for "this is the interactive, brand-owned thing" -- primary CTAs,
        // the active/picked stop marker, the nav brand mark. Every other
        // color in `accent.*` stays purely functional (route legend, leg
        // colors, congestion), same mapping as before: bus/origin = blue,
        // walking = purple, transfer = orange, destination = red,
        // success/normal = green, congestion = green -> amber -> red.
        // Reserving marigold for brand-only meaning keeps it from
        // colliding with any of those functional hues on the map.
        route: {
          bg: "#FFFFFF",
          panel: "#FFFFFF",
          accent: "#2563EB",
          line: "#DEDCF0",
        },
        surface: {
          DEFAULT: "#F6F5F2",
          raised: "#FFFFFF",
          sunken: "#ECEAF3",
        },
        ink: {
          DEFAULT: "#1B1A2E",
          secondary: "#5B5876",
          tertiary: "#8D89A6",
        },
        brand: {
          DEFAULT: "#E0A614",
          dark: "#B9860B",
          soft: "#FBF0D6",
        },
        accent: {
          blue: "#2563EB",
          purple: "#7C3AED",
          pink: "#DB2777",
          orange: "#EA580C",
          green: "#16A34A",
          teal: "#0D9488",
          yellow: "#CA8A04",
          red: "#DC2626",
        },
      },
      fontFamily: {
        // Single grotesk family for everything (display and body alike);
        // the weight/tracking does the differentiating, not a second
        // typeface. Self-hosted via @fontsource-variable/archivo
        // (imported once in app/layout.tsx) rather than a live Google
        // Fonts fetch, so the build has no external network dependency
        // and this is the only place naming a font family in code --
        // swapping the family later is a one-line change here plus the
        // matching @fontsource package.
        sans: [
          "Archivo Variable",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        sheet: "0 -4px 24px rgba(27, 26, 46, 0.12)",
        // Flatter, signage-like: a crisp hairline reads more like a
        // printed route board than a soft SaaS drop shadow. Kept as a
        // named shadow (not a plain border utility) so existing
        // `shadow-card` usages pick this up with no per-file changes.
        card: "0 0 0 1px rgba(27, 26, 46, 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
