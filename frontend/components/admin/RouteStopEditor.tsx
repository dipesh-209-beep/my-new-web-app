"use client";

import { useEffect, useMemo, useState } from "react";
import { getRoutes, getRouteStops } from "@/lib/api";
import {
  adminAddRouteStop,
  adminReloadGraph,
  adminRemoveRouteStop,
  adminReorderRouteStops,
  adminUpdateRouteStatus,
} from "@/lib/adminApi";
import { ApiError } from "@/lib/api";
import StopAutocomplete from "@/components/search/StopAutocomplete";
import InlineAlert from "@/components/ui/InlineAlert";
import { RouteStatus, RouteStopEntry, RouteSummary, Stop } from "@/types/route";
import { buildStopLabel } from "@/lib/stopLabel";

interface RouteStopEditorProps {
  token: string;
  stops: Stop[];
  stopsLoading: boolean;
  onForbidden: () => void;
}

/**
 * Full lifecycle editing of a route's stop sequence:
 *   - attach a stop (auto-inserting at any position via append + reorder)
 *   - reorder stops (up/down)
 *   - remove a stop (resequences automatically server-side)
 *   - flip a route's status (active <-> pending_release)
 *   - force a routing-graph rebuild
 */
export default function RouteStopEditor({
  token,
  stops,
  stopsLoading,
  onForbidden,
}: RouteStopEditorProps) {
  const [routes, setRoutes] = useState<RouteSummary[]>([]);
  const [routesLoading, setRoutesLoading] = useState(true);
  const [routeFilter, setRouteFilter] = useState("");
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);
  const [selectedRoute, setSelectedRoute] = useState<RouteSummary | null>(null);

  const [entries, setEntries] = useState<RouteStopEntry[]>([]);
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Add-stop form state
  const [newStop, setNewStop] = useState<Stop | null>(null);
  const [newStopInput, setNewStopInput] = useState("");
  const [insertPosition, setInsertPosition] = useState(1);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setRoutesLoading(true);
      try {
        // The shipped dataset has ~113 routes, well under the per-page cap.
        const data = await getRoutes({ limit: 200, offset: 0 });
        if (!cancelled) setRoutes(data.items);
      } catch {
        if (!cancelled) setError("Couldn't load the route list.");
      } finally {
        if (!cancelled) setRoutesLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredRoutes = useMemo(() => {
    const q = routeFilter.trim().toLowerCase();
    if (!q) return routes;
    return routes.filter(
      (r) =>
        r.route_id.toLowerCase().includes(q) ||
        r.route_name.toLowerCase().includes(q) ||
        (r.short_name?.toLowerCase().includes(q) ?? false)
    );
  }, [routes, routeFilter]);

  async function handleRouteChange(routeId: string) {
    setSelectedRouteId(routeId);
    setEntries([]);
    setError(null);
    setNotice(null);
    setNewStop(null);
    setNewStopInput("");
    if (!routeId) {
      setSelectedRoute(null);
      return;
    }
    const route = routes.find((r) => r.route_id === routeId) ?? null;
    setSelectedRoute(route);
    await loadEntries(routeId);
  }

  async function loadEntries(routeId: string) {
    setEntriesLoading(true);
    try {
      const data = await getRouteStops(routeId, "forward");
      setEntries(data);
      setInsertPosition(data.length + 1);
    } catch {
      setError("Couldn't load this route's stops.");
    } finally {
      setEntriesLoading(false);
    }
  }

  function handleUnauthorized(err: unknown): boolean {
    if (err instanceof ApiError && err.status === 401) {
      onForbidden();
      return true;
    }
    setError(err instanceof ApiError ? err.message : "Couldn't reach the server.");
    return false;
  }

  async function handleReorderByIndex(fromIndex: number, toIndex: number) {
    if (!selectedRouteId || busy) return;
    const seqs = entries.map((e) => e.sequence_no);
    const [moved] = seqs.splice(fromIndex, 1);
    seqs.splice(toIndex, 0, moved);
    setBusy(true);
    setError(null);
    try {
      await adminReorderRouteStops(selectedRouteId, seqs, token);
      await loadEntries(selectedRouteId);
    } catch (err) {
      handleUnauthorized(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(entry: RouteStopEntry) {
    if (!selectedRouteId || busy) return;
    const label = buildStopLabel(entry.stop, stops);
    if (!window.confirm(`Remove "${label}" (sequence ${entry.sequence_no}) from this route?`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await adminRemoveRouteStop(selectedRouteId, entry.sequence_no, token);
      await loadEntries(selectedRouteId);
    } catch (err) {
      handleUnauthorized(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleAddStop() {
    if (!selectedRouteId || !newStop || busy) return;
    const position = insertPosition;
    const currentSeqs = entries.map((e) => e.sequence_no);
    const appendSeq = currentSeqs.length + 1;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await adminAddRouteStop(selectedRouteId, newStop.stop_id, appendSeq, token);
      if (position <= currentSeqs.length) {
        // Insert in the middle: append, then reorder the new row into place.
        const seqs = currentSeqs.slice();
        seqs.splice(position - 1, 0, appendSeq);
        await adminReorderRouteStops(selectedRouteId, seqs, token);
      }
      setNewStop(null);
      setNewStopInput("");
      setNotice(`Added "${buildStopLabel(newStop, stops)}".`);
      await loadEntries(selectedRouteId);
    } catch (err) {
      handleUnauthorized(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleStatusChange(status: RouteStatus) {
    if (!selectedRouteId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const route = await adminUpdateRouteStatus(selectedRouteId, status, token);
      setSelectedRoute(route);
      setRoutes((prev) => prev.map((r) => (r.route_id === route.route_id ? route : r)));
      setNotice(`Route is now ${status}.`);
    } catch (err) {
      handleUnauthorized(err);
      if (err instanceof ApiError && err.status === 403) {
        setError("This account doesn't have permission to change route status (admin role required).");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleReloadGraph() {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const { nodes, edges } = await adminReloadGraph(token);
      setNotice(`Routing graph rebuilt: ${nodes} nodes, ${edges} edges.`);
    } catch (err) {
      handleUnauthorized(err);
      if (err instanceof ApiError && err.status === 403) {
        setError("This account doesn't have permission to reload the graph (admin role required).");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Route</span>
          <input
            value={routeFilter}
            onChange={(e) => setRouteFilter(e.target.value)}
            placeholder="Search route id / name…"
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Select route</span>
          <select
            value={selectedRouteId ?? ""}
            onChange={(e) => handleRouteChange(e.target.value)}
            disabled={routesLoading}
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          >
            <option value="">{routesLoading ? "Loading routes…" : "Pick a route…"}</option>
            {filteredRoutes.map((r) => (
              <option key={r.route_id} value={r.route_id}>
                {r.route_id} — {r.route_name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {selectedRoute && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="rounded-full bg-surface-sunken px-2.5 py-1 font-mono text-xs text-ink-secondary">
            {selectedRoute.route_id}
          </span>
          <span
            className={`rounded-full px-2.5 py-1 text-xs ${
              selectedRoute.status === "active"
                ? "bg-accent-green/10 text-accent-green"
                : "bg-accent-yellow/10 text-accent-yellow"
            }`}
          >
            {selectedRoute.status}
          </span>
          <span className="text-ink-secondary">
            {entries.length > 0 ? `${entries.length} stops` : "no stops linked yet"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => handleStatusChange(selectedRoute.status === "active" ? "pending_release" : "active")}
              disabled={busy}
              className="rounded-md border border-route-line bg-white px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface"
            >
              Set {selectedRoute.status === "active" ? "pending release" : "active"}
            </button>
            <button
              type="button"
              onClick={handleReloadGraph}
              disabled={busy}
              className="rounded-md border border-route-line bg-white px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface"
            >
              Rebuild graph
            </button>
          </div>
        </div>
      )}

      {selectedRoute && (
        <div className="rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
          <h3 className="text-sm font-semibold text-ink">Stop order</h3>
          <p className="mt-0.5 text-xs text-ink-secondary">
            Move stops with the up/down arrows; removing a stop leaves the sequence contiguous.
          </p>

          {entriesLoading && (
            <div className="mt-3 flex flex-col gap-2">
              {[...Array(5)].map((_, i) => (
                <div key={i} className="h-10 animate-pulse rounded-md border border-route-line bg-surface-sunken" />
              ))}
            </div>
          )}

          {!entriesLoading && entries.length === 0 && (
            <p className="mt-3 text-sm text-ink-secondary">
              No stops linked. Use &quot;Add a stop&quot; below.
            </p>
          )}

          <ol className="mt-3 flex flex-col divide-y divide-route-line">
            {entries.map((entry, idx) => {
              const label = buildStopLabel(entry.stop, stops);
              const isFirst = idx === 0;
              const isLast = idx === entries.length - 1;
              return (
                <li key={entry.sequence_no} className="flex items-center gap-2 py-1.5">
                  <span className="w-7 shrink-0 text-center font-mono text-xs text-ink-secondary">
                    {entry.sequence_no}
                  </span>
                  <PinDot />
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{label}</span>
                  <span className="hidden shrink-0 font-mono text-xs text-ink-tertiary sm:inline">
                    {entry.stop.stop_id}
                  </span>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      aria-label={`Move "${label}" up`}
                      disabled={isFirst || busy}
                      onClick={() => handleReorderByIndex(idx, idx - 1)}
                      className="rounded border border-route-line px-2 py-1 text-xs text-ink-secondary hover:bg-surface disabled:opacity-30"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      aria-label={`Move "${label}" down`}
                      disabled={isLast || busy}
                      onClick={() => handleReorderByIndex(idx, idx + 1)}
                      className="rounded border border-route-line px-2 py-1 text-xs text-ink-secondary hover:bg-surface disabled:opacity-30"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove "${label}"`}
                      disabled={busy}
                      onClick={() => handleRemove(entry)}
                      className="rounded border border-accent-red/30 px-2 py-1 text-xs text-accent-red hover:bg-accent-red/5 disabled:opacity-30"
                    >
                      Remove
                    </button>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {selectedRoute && (
        <div className="rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
          <h3 className="text-sm font-semibold text-ink">Add a stop</h3>
          <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <StopAutocomplete
                id="admin-editor-new-stop"
                label="Stop to add"
                stops={stops}
                stopsLoading={stopsLoading}
                inputValue={newStopInput}
                onInputChange={setNewStopInput}
                onSelect={(stop) => setNewStop(stop)}
                selectedStop={newStop}
                placeholder="Type to search for a stop…"
              />
            </div>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-xs font-medium text-ink-secondary">Insert at position</span>
              <select
                value={insertPosition}
                onChange={(e) => setInsertPosition(Number(e.target.value))}
                className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
              >
                {Array.from({ length: entries.length + 1 }, (_, i) => i + 1).map((p) => (
                  <option key={p} value={p}>
                    {p <= entries.length ? `Before stop ${p}` : "At the end"}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={handleAddStop}
              disabled={!newStop || busy}
              className="rounded-md bg-brand px-4 py-2 text-sm font-semibold text-ink transition-colors hover:bg-brand-dark disabled:opacity-50"
            >
              Add stop
            </button>
          </div>
        </div>
      )}

      {error && <InlineAlert variant="error">{error}</InlineAlert>}
      {notice && (
        <p className="rounded-md border border-accent-green/30 bg-accent-green/5 px-3 py-2 text-sm text-accent-green">
          {notice}
        </p>
      )}
    </div>
  );
}

function PinDot() {
  return (
    <span aria-hidden className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent-blue/10">
      <span className="h-1.5 w-1.5 rounded-full bg-accent-blue" />
    </span>
  );
}