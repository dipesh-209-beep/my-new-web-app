export const metadata = {
  title: "Offline — Kathmandu Bus Route Finder",
};

export default function OfflinePage() {
  return (
    <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center gap-3 p-4 text-center">
      <h1 className="text-lg font-semibold text-ink">You&apos;re offline</h1>
      <p className="text-sm text-ink-secondary">
        This page hasn&apos;t been loaded before, so it isn&apos;t available offline. Pages and
        stop/route data you&apos;ve already visited should still work once you reconnect briefly.
      </p>
    </div>
  );
}
