"use client";

import { FormEvent, useState } from "react";
import { adminCreateStop } from "@/lib/adminApi";
import { ApiError } from "@/lib/api";
import InlineAlert from "@/components/ui/InlineAlert";

interface CreateStopFormProps {
  token: string;
  onForbidden: () => void;
}

const BOOLEAN_FIELDS: { key: keyof StopFormState; label: string }[] = [
  { key: "is_major_stop", label: "Major stop" },
  { key: "is_interchange", label: "Interchange" },
  { key: "has_shelter", label: "Has shelter" },
  { key: "has_ticket_counter", label: "Has ticket counter" },
  { key: "wheelchair_access", label: "Wheelchair access" },
  { key: "audio_support", label: "Audio support" },
];

interface StopFormState {
  is_major_stop: boolean;
  is_interchange: boolean;
  has_shelter: boolean;
  has_ticket_counter: boolean;
  wheelchair_access: boolean;
  audio_support: boolean;
}

const TARGET_ZONE = "Kathmandu Valley";

/**
 * Create a stop via POST /stops. stop_id is server-generated (S####).
 * The map/render layer reads lat/lng (and the DB stores geom derived from
 * them by a trigger), so keep those Kathmandu Valley coordinates.
 */
export default function CreateStopForm({ token, onForbidden }: CreateStopFormProps) {
  const [name, setName] = useState("");
  const [lat, setLat] = useState("27.7");
  const [lng, setLng] = useState("85.32");
  const [district, setDistrict] = useState("");
  const [zone, setZone] = useState(TARGET_ZONE);
  const [ward, setWard] = useState("");
  const [aliases, setAliases] = useState("");
  const [landmark, setLandmark] = useState("");
  const [flags, setFlags] = useState<StopFormState>({
    is_major_stop: false,
    is_interchange: false,
    has_shelter: false,
    has_ticket_counter: false,
    wheelchair_access: false,
    audio_support: false,
  });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdStopId, setCreatedStopId] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const latNum = Number(lat);
    const lngNum = Number(lng);
    if (!name.trim() || !Number.isFinite(latNum) || !Number.isFinite(lngNum)) {
      setError("Name, latitude, and longitude are required.");
      return;
    }
    setCreating(true);
    setError(null);
    setCreatedStopId(null);
    try {
      const stop = await adminCreateStop(
        {
          stop_name: name.trim(),
          lat: latNum,
          lng: lngNum,
          aliases: aliases.trim() || null,
          zone: zone.trim() || null,
          district: district.trim() || null,
          ward: ward.trim() ? Number(ward) : undefined,
          landmark: landmark.trim() || null,
          ...flags,
        },
        token
      );
      setCreatedStopId(stop.stop_id);
      setName("");
      setAliases("");
      setLandmark("");
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
          <span className="text-xs font-medium text-ink-secondary">Stop name *</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Latitude *</span>
          <input
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            required
            step="any"
            className="rounded-md border border-route-line bg-white px-3 py-2 font-mono text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Longitude *</span>
          <input
            value={lng}
            onChange={(e) => setLng(e.target.value)}
            required
            step="any"
            className="rounded-md border border-route-line bg-white px-3 py-2 font-mono text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">District</span>
          <input
            value={district}
            onChange={(e) => setDistrict(e.target.value)}
            list="admin-districts"
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Zone</span>
          <input
            value={zone}
            onChange={(e) => setZone(e.target.value)}
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Ward</span>
          <input
            value={ward}
            onChange={(e) => setWard(e.target.value)}
            inputMode="numeric"
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Aliases (comma-separated)</span>
          <input
            value={aliases}
            onChange={(e) => setAliases(e.target.value)}
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Landmark</span>
          <input
            value={landmark}
            onChange={(e) => setLandmark(e.target.value)}
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
      </div>

      <datalist id="admin-districts">
        <option value="Kathmandu" />
        <option value="Lalitpur" />
        <option value="Bhaktapur" />
        <option value="Kirtipur" />
      </datalist>

      <fieldset className="flex flex-wrap gap-x-5 gap-y-2">
        <legend className="text-xs font-medium text-ink-secondary">Attributes</legend>
        {BOOLEAN_FIELDS.map(({ key, label }) => (
          <label key={key} className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={flags[key]}
              onChange={(e) => setFlags((prev) => ({ ...prev, [key]: e.target.checked }))}
              className="h-4 w-4 rounded border-route-line accent-brand-dark"
            />
            {label}
          </label>
        ))}
      </fieldset>

      {error && <InlineAlert variant="error">{error}</InlineAlert>}
      {createdStopId && (
        <p className="rounded-md border border-accent-green/30 bg-accent-green/5 px-3 py-2 text-sm text-accent-green">
          Created stop <span className="font-mono">{createdStopId}</span>
        </p>
      )}

      <button
        type="submit"
        disabled={creating}
        className="w-fit rounded-md bg-brand px-4 py-2 text-sm font-semibold text-ink transition-colors hover:bg-brand-dark disabled:opacity-50"
      >
        {creating ? "Creating…" : "Create stop"}
      </button>
    </form>
  );
}