import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Kathmandu Bus Route Finder",
    short_name: "KTM Bus",
    description: "Find direct and single-transfer bus routes across Kathmandu Valley",
    start_url: "/",
    display: "standalone",
    background_color: "#FFFFFF",
    theme_color: "#E0A614",
    icons: [
      { src: "/icon", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icon", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
