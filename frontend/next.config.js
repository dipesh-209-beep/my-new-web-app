/**
 * Content-Security-Policy for the frontend document.
 *
 * Why this exists here and nowhere else: the API already sends a strict
 * policy (backend/app/main.py :: security_headers_middleware) but that only
 * ever lands on JSON responses -- a browser does not apply it to the HTML
 * document it renders. There is no `frontend` service in either compose
 * file and nginx proxies everything to `backend:8000`, so the HTML is served
 * by Next.js itself, on its own origin. These headers are therefore the only
 * ones protecting the document the browser actually parses.
 *
 * This matters more than a routine hardening item because both the admin and
 * the public-user JWTs live in localStorage (lib/adminApi.ts, lib/userApi.ts)
 * rather than an HttpOnly cookie, so any script that runs on this origin can
 * read them. A CSP that blocks untrusted script injection is the control that
 * stands between a stored-XSS vector and a stolen admin token.
 *
 * Deliberately NOT done here: nonce-based script-src, and a middleware.ts to
 * issue nonces. That is the only way to drop 'unsafe-inline' from script-src
 * (Next.js inlines its RSC flight payload), and it is out of scope for this
 * pass. Until then, 'unsafe-inline' stays -- but 'self' still blocks the
 * realistic vector, an injected <script src> pointing at an attacker's host.
 */

const isProduction = process.env.NODE_ENV === "production";

/**
 * Build connect-src.
 *
 * The API is NOT same-origin: lib/apiBase.ts resolves it to
 * `<page protocol>//<page hostname>:8000`, so the port differs from the
 * page's own origin and 'self' does not cover it. Hardcoding a hostname
 * would break the moment the app is reached by any other name (a LAN IP in
 * development, the real domain in production), which is exactly the failure
 * apiBase.ts was written to avoid.
 *
 * NOTE ON THE MECHANISM, because it is not what one would assume: the
 * NextConfig `headers()` hook is typed `() => Promise<Header[]> | Header[]` --
 * it takes NO request argument -- and is evaluated once, at build time. So
 * "derive the origin from the request host" is not available here; it would
 * require a middleware.ts, which is out of scope for this pass. What is
 * available is a source expression that is host-agnostic by construction,
 * which is why the fallback is port-scoped rather than host-scoped.
 *
 * This mirrors apiBase()'s own resolution order: an explicit
 * NEXT_PUBLIC_API_BASE_URL wins and is used alone, otherwise the API is
 * whatever host served the page on port 8000 -- for which the only
 * build-time-safe expression is a wildcard on that port.
 */
function buildConnectSrc() {
  const envBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (envBase) {
    try {
      // Explicit override: this is the only origin the app will call.
      return `'self' ${new URL(envBase).origin}`;
    } catch {
      // Malformed override is lib/apiBase.ts' problem to surface, not
      // something that should stop the server booting here. Fall through
      // to the wildcard rather than emitting an unparseable directive.
    }
  }
  // No override: allow the API on port 8000 over either scheme, for any
  // host. Scoped to a single port and a single purpose -- this is not a
  // general "connect anywhere" grant, and it is what lets the same build
  // work on localhost, a LAN address, and the production domain.
  return "'self' http://*:8000 https://*:8000";
}

function buildCsp() {
  const directives = [
    "default-src 'self'",
    // 'unsafe-inline' is required, not sloppy: Next.js inlines its RSC
    // flight payload as <script>self.__next_f.push(...)</script>.
    // 'unsafe-eval' is only needed by the dev server's React Refresh.
    `script-src 'self' 'unsafe-inline'${isProduction ? "" : " 'unsafe-eval'"}`,
    // 'unsafe-inline' is also required here, and for a different reason
    // than script-src: Leaflet builds markers as HTML with style=""
    // attributes (components/map/markerKit.ts), and CSP cannot nonce an
    // attribute style. Dropping it breaks every map marker.
    "style-src 'self' 'unsafe-inline'",
    // The map is the only thing that loads a third-party subresource, from
    // the OSM tile server (components/map/useMap.ts). Leaflet substitutes
    // {s} with a/b/c, hence the wildcard rather than three literals.
    "img-src 'self' data: https://*.tile.openstreetmap.org",
    // Fonts are self-hosted via @fontsource-variable/archivo (app/layout.tsx),
    // so there is deliberately no font CDN origin to allow.
    "font-src 'self'",
    `connect-src ${buildConnectSrc()}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];

  // Production only. In development this would upgrade the http:// LAN
  // origin that lib/apiBase.ts goes out of its way to support
  // (http://192.168.x.x:3000 talking to :8000), breaking local dev.
  if (isProduction) {
    directives.push("upgrade-insecure-requests");
  }

  return directives.join("; ");
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Stops Next.js advertising its version in `X-Powered-By: Next.js`, which
  // is free version fingerprinting.
  poweredByHeader: false,

  // Allow HMR WebSocket connections from LAN IPs (e.g. http://192.168.x.x:3000)
  // when accessing the dev server from another device on the same network.
  // The `next dev -H 0.0.0.0` in package.json already binds to all interfaces;
  // this prevents Next.js from blocking the cross-origin WebSocket upgrade.
  allowedDevOrigins: [
    "http://localhost:*",
    "http://127.0.0.1:*",
  ],

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: buildCsp() },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // Redundant with CSP frame-ancestors 'none' above, but kept for
          // the browsers that predate frame-ancestors support. The API sets
          // this same pair for its own responses.
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
