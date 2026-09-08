import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

// iOS applies its own rounded-square mask to apple-touch-icon, so this
// fills the full square with the brand color rather than pre-rounding it
// like app/icon.tsx does for the standalone PWA/favicon use case.
export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#E0A614",
        }}
      >
        <svg width="112" height="112" viewBox="0 0 24 24" fill="none">
          <rect x="3" y="4" width="18" height="12" rx="2" fill="#FFFFFF" />
          <rect x="5" y="6.5" width="4.5" height="4" rx="0.5" fill="#E0A614" />
          <rect x="10.5" y="6.5" width="4.5" height="4" rx="0.5" fill="#E0A614" />
          <rect x="16" y="6.5" width="3" height="4" rx="0.5" fill="#E0A614" />
          <circle cx="7.5" cy="18" r="1.75" fill="#FFFFFF" />
          <circle cx="16.5" cy="18" r="1.75" fill="#FFFFFF" />
        </svg>
      </div>
    ),
    { ...size }
  );
}
