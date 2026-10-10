import { describe, expect, it } from "vitest";
import { buildStopLabel, buildStopLabelIndex } from "@/lib/stopLabel";
import { normalizeText } from "@/lib/stopSearch";
import { Stop } from "@/types/route";

function makeStop(overrides: Partial<Stop> = {}): Stop {
  return {
    stop_id: "S0001",
    stop_name: "Ratna Park",
    lat: 27.7,
    lng: 85.31,
    zone: null,
    district: null,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
    ...overrides,
  };
}

describe("buildStopLabel", () => {
  it("returns the bare stop_name when it's unique among allStops", () => {
    const stop = makeStop({ stop_name: "Ratna Park" });
    const all = [stop, makeStop({ stop_id: "S0002", stop_name: "Koteshwor" })];
    expect(buildStopLabel(stop, all)).toBe("Ratna Park");
  });

  it("disambiguates with district when stop_name repeats", () => {
    const a = makeStop({ stop_id: "S0001", stop_name: "Chowk", district: "Kathmandu" });
    const b = makeStop({ stop_id: "S0002", stop_name: "Chowk", district: "Lalitpur" });
    const all = [a, b];
    expect(buildStopLabel(a, all)).toBe("Chowk (Kathmandu)");
    expect(buildStopLabel(b, all)).toBe("Chowk (Lalitpur)");
  });

  it("falls back to stop_id for disambiguation when district is missing", () => {
    const a = makeStop({ stop_id: "S0001", stop_name: "Bus Park", district: null });
    const b = makeStop({ stop_id: "S0002", stop_name: "Bus Park", district: null });
    const all = [a, b];
    expect(buildStopLabel(a, all)).toBe("Bus Park (S0001)");
    expect(buildStopLabel(b, all)).toBe("Bus Park (S0002)");
  });

  it("does not disambiguate a stop that merely shares a name with itself once", () => {
    const stop = makeStop({ stop_name: "Solo Stop" });
    expect(buildStopLabel(stop, [stop])).toBe("Solo Stop");
  });

  it("treats case-only variants as duplicates and disambiguates them", () => {
    // Regression: source data carries both "Kalanki" (S0130) and
    // "KALANKI" (S_395) at different coordinates. The old check was
    // case-sensitive, so they shared a label and one silently overwrote
    // the other in the label index.
    const a = makeStop({ stop_id: "S0130", stop_name: "Kalanki", district: "KATHMANDU" });
    const b = makeStop({ stop_id: "S_395", stop_name: "KALANKI", district: null });
    const all = [a, b];
    expect(buildStopLabel(a, all)).not.toBe(buildStopLabel(b, all));
    // Neither has district populated on both, so district can't
    // discriminate; the fallback is stop_id.
    expect(buildStopLabel(a, all)).toBe("Kalanki (S0130)");
    expect(buildStopLabel(b, all)).toBe("KALANKI (S_395)");
  });

  it("uses landmark to disambiguate when district is missing on one collider", () => {
    const a = makeStop({
      stop_id: "S0130",
      stop_name: "Kalanki",
      district: "KATHMANDU",
      landmark: "Kalanki Chowk / Junction",
    });
    const b = makeStop({
      stop_id: "S_395",
      stop_name: "KALANKI",
      district: null,
      landmark: "Kalanki Gate",
    });
    const all = [a, b];
    // district fails (empty on b), zone fails (both null), landmark
    // succeeds (populated on both, distinct).
    expect(buildStopLabel(a, all)).toBe("Kalanki (Kalanki Chowk / Junction)");
    expect(buildStopLabel(b, all)).toBe("KALANKI (Kalanki Gate)");
  });

  it("uses ward when it's the only discriminator populated on all colliders", () => {
    const a = makeStop({ stop_id: "S0001", stop_name: "Chowk", ward: 15 });
    const b = makeStop({ stop_id: "S0002", stop_name: "Chowk", ward: 20 });
    const all = [a, b];
    expect(buildStopLabel(a, all)).toBe("Chowk (15)");
    expect(buildStopLabel(b, all)).toBe("Chowk (20)");
  });
});

describe("buildStopLabelIndex", () => {
  it("maps labels back to their Stop, matching the index's normalization", () => {
    const stop = makeStop({ stop_name: "Ratna Park" });
    const { labelToStop } = buildStopLabelIndex([stop]);
    // Keys are normalized (case/punctuation/whitespace-insensitive), so
    // any reasonable typed variant resolves -- callers look up with
    // normalizeText(typedLabel), same as SearchForm.
    expect(labelToStop.get("ratnapark")).toBe(stop);
    expect(labelToStop.get(normalizeText("Ratna  Park"))).toBe(stop);
  });

  it("produces one label entry per stop, matching buildStopLabel's disambiguation", () => {
    const a = makeStop({ stop_id: "S0001", stop_name: "Chowk", district: "Kathmandu" });
    const b = makeStop({ stop_id: "S0002", stop_name: "Chowk", district: "Lalitpur" });
    const { labels, labelToStop } = buildStopLabelIndex([a, b]);

    expect(labels).toEqual([
      { stop_id: "S0001", label: "Chowk (Kathmandu)" },
      { stop_id: "S0002", label: "Chowk (Lalitpur)" },
    ]);
    expect(labelToStop.get("chowkkathmandu")).toBe(a);
    expect(labelToStop.get("chowklalitpur")).toBe(b);
  });

  it("never lets a distinct stop's label be overwritten by a collision", () => {
    // Stop A is "Chowk" in Kathmandu -> disambiguated label
    // "Chowk (Kathmandu)". Stop B is *literally named* "Chowk (Kathmandu)".
    // Both produce the same label string; the index must keep both
    // entries rather than silently dropping one.
    const a = makeStop({ stop_id: "S0001", stop_name: "Chowk", district: "Kathmandu" });
    const b = makeStop({ stop_id: "S0002", stop_name: "Chowk (Kathmandu)", district: null });
    const { labels, labelToStop } = buildStopLabelIndex([a, b]);

    expect(labels).toHaveLength(2);
    expect(new Set(labels.map((l) => l.stop_id)).size).toBe(2);
    // One of them must have been forced to a distinct key; both stop_ids
    // must resolve back to their own stop.
    const resolvedIds = labels.map((l) => labelToStop.get(normalizeText(l.label))?.stop_id);
    expect(new Set(resolvedIds).size).toBe(2);
  });

  it("keeps case-variant duplicates (Kalanki / KALANKI) as two distinct entries", () => {
    const a = makeStop({ stop_id: "S0130", stop_name: "Kalanki", district: "KATHMANDU" });
    const b = makeStop({ stop_id: "S_395", stop_name: "KALANKI", district: null });
    const { labels, labelToStop } = buildStopLabelIndex([a, b]);

    expect(labels).toHaveLength(2);
    expect(labelToStop.get("kalankis0130")).toBe(a);
    expect(labelToStop.get("kalankis395")).toBe(b);
  });
});