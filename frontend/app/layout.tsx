import type { Metadata, Viewport } from "next";
import "@fontsource-variable/archivo";
import "./globals.css";
import NavBar from "@/components/layout/NavBar";
import { UserAuthProvider } from "@/components/user/UserAuthContext";
import ServiceWorkerRegistration from "@/components/ServiceWorkerRegistration";

export const metadata: Metadata = {
  title: "Kathmandu Bus Route Finder",
  description: "Find direct and single-transfer bus routes across Kathmandu Valley",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "KTM Bus",
  },
};

export const viewport: Viewport = {
  themeColor: "#E0A614",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="flex h-full flex-col font-sans">
        <ServiceWorkerRegistration />
        <UserAuthProvider>
          {/* First tab stop, visually hidden until focused. Without it a
              keyboard user has to traverse the nav bar's every link on
              every page load before reaching the map, and the nav is
              duplicated by the sheet's own controls on mobile. Targets
              the single <main> landmark rendered here for every page. */}
          <a
            href="#main-content"
            className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-white focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:shadow-lg"
          >
            Skip to map and results
          </a>
          <NavBar />
          {/* The one <main> landmark for the whole app. Pages render
              their content inside it (app/page.tsx used to carry its own
              <main id="main-content">, which left /stops and /routes with
              no matching landmark and a skip link that went nowhere). */}
          {/* tabIndex={-1} makes the landmark programmatically focusable so
              the skip link actually moves focus here; without it the browser
              changes the hash but leaves focus on <body>, and a keyboard user
              still has to tab through the nav. */}
          <main id="main-content" tabIndex={-1} className="min-h-0 flex-1 outline-none">
            {children}
          </main>
        </UserAuthProvider>
      </body>
    </html>
  );
}
