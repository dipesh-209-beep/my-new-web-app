import { Stop } from "@/types/route";
import { normalizeText } from "@/lib/stopSearch";

/**
 * stop_name isn't guaranteed unique across the Kathmandu Valley (generic
 * names like "Chowk" or "Bus Park" repeat at different physical
 * locations, and the source data carries both "Kalanki" and "KALANKI"
 * at different coordinates). Every place that shows or matches a stop by
 * typed text must use this same disambiguation so the label a user
 * sees/types always maps back to exactly one Stop -- see SearchForm.tsx's
 * datalist and page.tsx's map-click / geolocation / "use my location"
 * handlers.
 *
 * A name is "duplicated" even when only its capitalization differs
 * ("KALANKI" vs "Kalanki" -- same normalized key), so those pairs are
 * forced apart too instead of silently sharing a label.
 */
export function buildStopLabel(stop: Stop, allStops: Stop[]): string {
  const key = normalizeText(stop.stop_name ?? "");
  const colliders = allStops.filter((s) => normalizeText(s.stop_name ?? "") === key);
  if (colliders.length <= 1) return stop.stop_name;

  // Pick the first attribute that is populated for every collider AND
  // unique across all of them (so each collider gets a distinct suffix).
  // Order matters: geographic context first, then the stop_id as a
  // last resort.
  const distinct = (values: string[]): boolean =>
    values.every((v) => v.trim() !== "") && new Set(values).size === values.length;

  const candidates: { label: string; value: (s: Stop) => string }[] = [
    { label: "district", value: (s) => s.district ?? "" },
    { label: "zone", value: (s) => s.zone ?? "" },
    { label: "landmark", value: (s) => s.landmark ?? "" },
    { label: "ward", value: (s) => (s.ward != null ? String(s.ward) : "") },
  ];

  for (const candidate of candidates) {
    const values = colliders.map(candidate.value);
    if (distinct(values)) {
      return `${stop.stop_name} (${candidate.value(stop)})`;
    }
  }

  return `${stop.stop_name} (${stop.stop_id})`;
}

/** Map of normalized label -> Stop, for O(1) lookup from typed text.
 * Labels are forced unique: if two distinct stops would produce the same
 * label string (possible when a literal stop_name happens to equal another
 * stop's disambiguated label), the later one gets a stop_id suffix rather
 * than silently overwriting the first entry. */
export function buildStopLabelIndex(
  stops: Stop[]
): { labelToStop: Map<string, Stop>; labels: { stop_id: string; label: string }[] } {
  const labelToStop = new Map<string, Stop>();
  const labels: { stop_id: string; label: string }[] = [];

  for (const s of stops) {
    let label = buildStopLabel(s, stops);
    let key = normalizeText(label);
    while (labelToStop.has(key) && labelToStop.get(key)!.stop_id !== s.stop_id) {
      label = `${label} (${s.stop_id})`;
      key = normalizeText(label);
    }
    if (!labelToStop.has(key)) {
      labelToStop.set(key, s);
      labels.push({ stop_id: s.stop_id, label });
    }
  }

  return { labelToStop, labels };
}