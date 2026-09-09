"use client";

import { FormEvent, useState } from "react";
import { adminCreateRoute } from "@/lib/adminApi";
import { ApiError } from "@/lib/api";
import StopAutocomplete from "@/components/search/StopAutocomplete";
import InlineAlert from "@/components/ui/InlineAlert";
import { Stop } from "@/types/route";

interface CreateRouteFormProps {
  token: string;
  stops: Stop[];
  stopsLoading: boolean;
  onForbidden: () => void;
  /** Called after a route is created so the parent can refresh route lists. */
  onCreated: () => void;
}

/**
 * Create a route via POST /routes. start/end stops are picked with the same
 * autocomplete used by the public search UI. After creation, use the
 * "Route stops" tab to attach stops in order via add/reorder/remove.
 */
export default function CreateRouteForm({
  token,
  stops,
  stopsLoading,
  onForbidden,
  onCreated,
}: CreateRouteFormProps) {
  const [routeName, setRouteName] = useState("");
  const [shortName, setShortName] = useState("");
  const [vehicleType, setVehicleType] = useState("bus");
  const [routeType, setRouteType] = useState("");
  const [operator, setOperator] = useState("");
  const [startStop, setStartStop] = useState<Stop | null>(null);
  const [startInput, setStartInput] = useState("");
  const [endStop, setEndStop] = useState<Stop | null>(null);
  const [endInput, setEndInput] = useState("");
  const [isBidirectional, setIsBidirectional] = useState(false);
  const [isExpress, setIsExpress] = useState(false);
  const [hasAc, setHasAc] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdRouteId, setCreatedRouteId] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!routeName.trim() || !startStop || !endStop) {
      setError("Route name plus a start and end stop are required.");
      return;
    }
    setCreating(true);
    setError(null);
    setCreatedRouteId(null);
    try {
      const route = await adminCreateRoute(
        {
          route_name: routeName.trim(),
          short_name: shortName.trim() || null,
          vehicle_type: vehicleType.trim(),
          route_type: routeType.trim() || null,
          operator: operator.trim() || null,
          start_stop_id: startStop.stop_id,
          end_stop_id: endStop.stop_id,
          // Initial value only; add-route-stop syncs routes.total_stops.
          total_stops: 2,
          is_bidirectional: isBidirectional,
          is_express: isExpress,
          has_ac: hasAc,
        },
        token
      );
      setCreatedRouteId(route.route_id);
      setRouteName("");
      setShortName("");
      setOperator("");
      setStartInput("");
      setEndInput("");
      setStartStop(null);
      setEndStop(null);
      onCreated();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onForbidden();
        return;
      }
      setError(err instanceof ApiError ? err.message : "Couldn't reach the server.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          <span className="text-xs font-medium text-ink-secondary">Route name *</span>
          <input
            value={routeName}
            onChange={(e) => setRouteName(e.target.value)}
            required
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Short name (e.g. route number)</span>
          <input
            value={shortName}
            onChange={(e) => setShortName(e.target.value)}
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Vehicle type *</span>
          <input
            value={vehicleType}
            onChange={(e) => setVehicleType(e.target.value)}
            required
            list="admin-vehicle-types"
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Route type</span>
          <input
            value={routeType}
            onChange={(e) => setRouteType(e.target.value)}
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Operator (free text)</span>
          <input
            value={operator}
            onChange={(e) => setOperator(e.target.value)}
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>

        <div className="sm:col-span-2">
          <StopAutocomplete
            id="admin-route-start"
            label="Start stop"
            stops={stops}
            stopsLoading={stopsLoading}
            inputValue={startInput}
            onInputChange={setStartInput}
            onSelect={(stop) => setStartStop(stop)}
            selectedStop={startStop}
            placeholder="Start stop — type to search"
          />
        </div>
        <div className="sm:col-span-2">
          <StopAutocomplete
            id="admin-route-end"
            label="End stop"
            stops={stops}
            stopsLoading={stopsLoading}
            inputValue={endInput}
            onInputChange={setEndInput}
            onSelect={(stop) => setEndStop(stop)}
            selectedStop={endStop}
            placeholder="End stop — type to search"
          />
        </div>
      </div>

      <datalist id="admin-vehicle-types">
        <option value="bus" />
        <option value="microbus" />
        <option value="tempo" />
      </datalist>

      <fieldset className="flex flex-wrap gap-x-5 gap-y-2">
        <legend className="text-xs font-medium text-ink-secondary">Attributes</legend>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={isBidirectional}
            onChange={(e) => setIsBidirectional(e.target.checked)}
            className="h-4 w-4 rounded border-route-line accent-brand-dark"
          />
          Bidirectional (return leg exists)
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={isExpress}
            onChange={(e) => setIsExpress(e.target.checked)}
            className="h-4 w-4 rounded border-route-line accent-brand-dark"
          />
          Express
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={hasAc}
            onChange={(e) => setHasAc(e.target.checked)}
            className="h-4 w-4 rounded border-route-line accent-brand-dark"
          />
          Has AC
        </label>
      </fieldset>

      {error && <InlineAlert variant="error">{error}</InlineAlert>}
      {createdRouteId && (
        <p className="rounded-md border border-accent-green/30 bg-accent-green/5 px-3 py-2 text-sm text-accent-green">
          Created route <span className="font-mono">{createdRouteId}</span> — attach its stops in
          the &quot;Route stops&quot; tab.
        </p>
      )}

      <button
        type="submit"
        disabled={creating}
        className="w-fit rounded-md bg-brand px-4 py-2 text-sm font-semibold text-ink transition-colors hover:bg-brand-dark disabled:opacity-50"
      >
        {creating ? "Creating…" : "Create route"}
      </button>
    </form>
  );
}