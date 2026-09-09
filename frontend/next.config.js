/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Allow HMR WebSocket connections from LAN IPs (e.g. http://192.168.x.x:3000)
  // when accessing the dev server from another device on the same network.
  // The `next dev -H 0.0.0.0` in package.json already binds to all interfaces;
  // this prevents Next.js from blocking the cross-origin WebSocket upgrade.
  allowedDevOrigins: [
    "http://localhost:*",
    "http://127.0.0.1:*",
  ],
};

module.exports = nextConfig;
