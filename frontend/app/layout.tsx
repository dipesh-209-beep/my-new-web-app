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
          <NavBar />
          <div className="min-h-0 flex-1">{children}</div>
        </UserAuthProvider>
      </body>
    </html>
  );
}
