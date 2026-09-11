"use client";

import { useState } from "react";
import AdminLogin from "@/components/admin/AdminLogin";
import CreateStopForm from "@/components/admin/CreateStopForm";
import CreateRouteForm from "@/components/admin/CreateRouteForm";
import RouteStopEditor from "@/components/admin/RouteStopEditor";
import { clearAdminToken, getAdminToken, setAdminToken } from "@/lib/adminApi";
import { useStops } from "@/hooks/useStops";
import InlineAlert from "@/components/ui/InlineAlert";

type AdminTab = "stops" | "routes" | "route-stops";

const TABS: { id: AdminTab; label: string }[] = [
  { id: "stops", label: "Create stop" },
  { id: "routes", label: "Create route" },
  { id: "route-stops", label: "Route stops" },
];

interface AdminWorkspaceProps {
  token: string;
  onTokenInvalid: () => void;
}

/**
 * Authenticated half of /admin. Lives in its own component so useStops()
 * only mounts once a token exists -- no point fetching the whole stop list
 * (and hitting the API) for the login screen. Tab state is local because it
 * only matters once inside the workspace.
 */
function AdminWorkspace({ token, onTokenInvalid }: AdminWorkspaceProps) {
  const [tab, setTab] = useState<AdminTab>("stops");
  const { stops, loading: stopsLoading, error: stopsError } = useStops();

  return (
    <div className="mt-6 flex flex-col gap-4">
      {stopsError && (
        <InlineAlert variant="error">
          Couldn&apos;t load the stop list from the server. Stop/routes forms need it for search.
        </InlineAlert>
      )}

      <div role="tablist" aria-label="Admin sections" className="flex rounded-lg bg-surface-sunken p-1 text-sm">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`flex-1 rounded-md px-3 py-1.5 transition-colors ${
              tab === id ? "bg-white text-ink shadow-sm" : "text-ink-secondary hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "stops" && <CreateStopForm token={token} onForbidden={onTokenInvalid} />}

      {tab === "routes" && (
        <CreateRouteForm
          token={token}
          stops={stops}
          stopsLoading={stopsLoading}
          onForbidden={onTokenInvalid}
          onCreated={() => setTab("route-stops")}
        />
      )}

      {tab === "route-stops" && (
        <RouteStopEditor
          token={token}
          stops={stops}
          stopsLoading={stopsLoading}
          onForbidden={onTokenInvalid}
        />
      )}
    </div>
  );
}

/**
 * /admin -- data-entry UI for the backend's admin API. No token is ever
 * sent at link/preload time (nothing here runs on the server); the page
 * reads the JWT from localStorage on the client and shows the login form
 * when it's missing or a mutation came back 401.
 */
export default function AdminPage() {
  const [token, setTokenState] = useState<string | null>(() => {
    const stored = getAdminToken();
    return stored === null ? null : stored; // distinguishes "no token" (login) from 401 logout
  });

  function handleLogin(newToken: string) {
    setAdminToken(newToken);
    setTokenState(newToken);
  }

  function handleLogout() {
    clearAdminToken();
    setTokenState(null);
  }

  function handleTokenInvalid() {
    clearAdminToken();
    setTokenState(null);
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Admin</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Create stops and routes, then link and order their stops.
          </p>
        </div>
        {token && (
          <button
            type="button"
            onClick={handleLogout}
            className="rounded-md border border-route-line px-3 py-1.5 text-sm text-ink-secondary hover:border-accent-red hover:text-accent-red"
          >
            Sign out
          </button>
        )}
      </div>

      {!token ? (
        <div className="mt-6 flex flex-col items-start gap-2">
          <AdminLogin onLogin={handleLogin} />
          <p className="text-xs text-ink-tertiary">
            Create your first admin account with <code>make seed-admin</code>, then sign in here.
          </p>
        </div>
      ) : (
        <AdminWorkspace token={token} onTokenInvalid={handleTokenInvalid} />
      )}
    </main>
  );
}