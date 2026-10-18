#!/usr/bin/env node
/**
 * scripts/check-api-base.mjs
 *
 * Build-time smoke check for the resolved API base (AUD-015).
 *
 * lib/apiBase.ts resolves the API origin in this order:
 *   1. NEXT_PUBLIC_API_BASE_URL
 *   2. <page protocol>//<page hostname>:8000   (anything that served the page)
 *   3. http://localhost:8000                   (no window; SSR/build only)
 *
 * Next.js inlines NEXT_PUBLIC_* at build time, so whatever is (or is not)
 * set when `next build` runs is baked into the client bundle permanently.
 * The dangerous case is a production build with the variable unset: the
 * bundle then hardcodes ":8000", which only works if the browser can reach
 * the API directly on port 8000. Behind a host-only proxy that serves just
 * 80/443, every API call fails with no obvious cause.
 *
 * `npm run build:production` runs this first and refuses to build in that
 * case. Running plain `npm run build` (CI, local compile checks) is
 * unaffected -- this script only fails when it is actually invoked.
 *
 * Escapes, for deliberately non-production builds:
 *   ALLOW_DEFAULT_API_BASE=1  accept the :8000 fallback
 *   ALLOW_LOCAL_API_BASE=1    accept a localhost/127.0.0.1 override
 */

const raw = process.env.NEXT_PUBLIC_API_BASE_URL;

function fail(message) {
  console.error(`\n[check:api-base] ${message}\n`);
  process.exit(1);
}

let resolved;

if (raw) {
  if (raw !== raw.trim()) {
    fail(`NEXT_PUBLIC_API_BASE_URL has surrounding whitespace: ${JSON.stringify(raw)}`);
  }

  let url;
  try {
    url = new URL(raw);
  } catch {
    fail(`NEXT_PUBLIC_API_BASE_URL is not a valid absolute URL: ${JSON.stringify(raw)}`);
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    fail(`NEXT_PUBLIC_API_BASE_URL must use http or https, got ${url.protocol}`);
  }

  const host = url.hostname;
  if ((host === "localhost" || host === "127.0.0.1" || host === "::1") && process.env.ALLOW_LOCAL_API_BASE !== "1") {
    fail(
      `NEXT_PUBLIC_API_BASE_URL points at ${host} (${raw}). A bundle built with a ` +
        "loopback API base only works when the browser and the API run on the same " +
        "machine. Set ALLOW_LOCAL_API_BASE=1 if that is intentional.",
    );
  }

  resolved = raw;
} else if (process.env.ALLOW_DEFAULT_API_BASE === "1") {
  console.warn(
    "[check:api-base] NEXT_PUBLIC_API_BASE_URL is unset; accepting the :8000 " +
      "fallback because ALLOW_DEFAULT_API_BASE=1.",
  );
  resolved = "<page host>:8000 (fallback)";
} else {
  fail(
    "NEXT_PUBLIC_API_BASE_URL is not set. Next.js inlines it at build time, so " +
      "this bundle would fall back to <page host>:8000, which breaks whenever the " +
      "API is not reachable directly on port 8000 (e.g. behind a proxy serving " +
      "only 80/443).\n" +
      "  Set it, e.g.:  NEXT_PUBLIC_API_BASE_URL=https://api.example.com npm run build:production\n" +
      "  Or, to build a throwaway bundle on purpose:  ALLOW_DEFAULT_API_BASE=1 npm run build",
  );
}

console.log(`[check:api-base] ok: API base = ${resolved}`);
