import { ImageResponse } from "next/og";

export const size = { width: 512, height: 512 };
export const contentType = "image/png";

// Simple bus glyph on the app's brand marigold, rendered at build/request
// time so the PWA manifest and browser tab both have a real icon without
// shipping a binary asset that has to be kept in sync by hand.
export default function Icon() {
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
          borderRadius: 96,
        }}
      >
        <svg width="320" height="320" viewBox="0 0 24 24" fill="none">
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
