/**
 * lib/apiBase.ts
 *
 * Single base-URL resolver shared by all API clients (lib/api.ts,
 * lib/adminApi.ts, lib/userApi.ts) -- previously each had their own copy,
 * and all three hardcoded the `http://` scheme, which silently breaks
 * every API call when the site is served over HTTPS (mixed content).
 *
 * Resolution order:
 *   1. NEXT_PUBLIC_API_BASE_URL -- explicit override for production
 *      deployments / Docker where the backend is a different host than
 *      the page being served.
 *   2. Derived from the current page, protocol included -- so the scheme
 *      matches how the app was loaded (http for dev/LAN, https for a TLS
 *      deployment), and LAN access works (opening http://192.168.1.5:3000
 *      talks to http://192.168.1.5:8000 instead of a localhost that isn't
 *      the user's machine). The backend port is a fixed 8000 dev default.
 *   3. http://localhost:8000 -- only reachable when window is unavailable
 *      (SSR / build-time module evaluation), which never actually fetches.
 */
export function apiBase(): string {
  const envBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (envBase) return envBase;

  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}