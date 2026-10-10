import { describe, expect, it } from "vitest";
import { normalizeText, searchStops } from "@/lib/stopSearch";
import { Stop } from "@/types/route";

function makeStop(overrides: Partial<Stop> = {}): Stop {
  return {
    stop_id: "S0001",
    stop_name: "Ratna Park",
    lat: 27.7,
    lng: 85.31,
    zone: null,
    district: null,
    aliases: null,
    ward: null,
    landmark: null,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
    ...overrides,
  };
}

describe("normalizeText", () => {
  it("strips spaces, punctuation and capitalization so variants compare equal", () => {
    expect(normalizeText("Ratna Park")).toBe("ratnapark");
    expect(normalizeText("ratna-park")).toBe("ratnapark");
    expect(normalizeText("RATNA_PARK!")).toBe("ratnapark");
    expect(normalizeText("  Kalanki-Chowk  ")).toBe("kalankichowk");
  });

  it("preserves non-Latin letters (Devanagari) and strips diacritics", () => {
    expect(normalizeText("पिङ्गलस्थान")).toBe("पिङ्गलस्थान");
    expect(normalizeText("Zürich")).toBe("zurich");
  });
});

describe("searchStops", () => {
  it("finds Ratna Park from the compact query 'ratnapark'", () => {
    const ratna = makeStop({ stop_id: "S0013", stop_name: "Ratna Park" });
    const other = makeStop({ stop_id: "S0014", stop_name: "Koteshwor" });
    const results = searchStops("ratnapark", [other, ratna]);
    expect(results[0]?.stop.stop_id).toBe("S0013");
    expect(results[0]?.score).toBe(100);
  });

  it("finds S0130 Kalanki from 'kalanki chowk' via its landmark", () => {
    const kalanki = makeStop({
      stop_id: "S0130",
      stop_name: "Kalanki",
      district: "KATHMANDU",
      ward: 28,
      landmark: "Kalanki Chowk / Junction",
    });
    const kalankiDuplicate = makeStop({ stop_id: "S_395", stop_name: "KALANKI" });
    const results = searchStops("kalanki chowk", [kalankiDuplicate, kalanki]);
    // "kalanki chowk" normalizes to "kalankichowk", which is a prefix of
    // S0130's landmark "Kalanki Chowk / Junction"; S_395 has no
    // name/alias/landmark match at all.
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0130"]);
    expect(results[0]?.matchedField).toBe("landmark");
  });

  it("finds S0130 Kalanki from the hyphenated query 'kalanki-chowk'", () => {
    const kalanki = makeStop({
      stop_id: "S0130",
      stop_name: "Kalanki",
      landmark: "Kalanki Chowk / Junction",
    });
    const results = searchStops("kalanki-chowk", [kalanki]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0130"]);
  });

  it("matches a stop by its alias (Devanagari name, Latin alias)", () => {
    const pingalasthan = makeStop({
      stop_id: "S0071",
      stop_name: "पिङ्गलस्थान",
      aliases: "Pingalasthan",
    });
    const results = searchStops("pingalasthan", [pingalasthan]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0071"]);
    expect(results[0]?.matchedField).toBe("alias");
  });

  it("does not match on district (district is not a search field)", () => {
    // Regression: the old autocomplete matched stop.district, so a query
    // like "kathmandu" surfaced every stop in Kathmandu district ahead of
    // a real name match. district is a location attribute, not a name.
    const stop = makeStop({ stop_name: "Tripureshwor", district: "KATHMANDU" });
    expect(searchStops("kathmandu", [stop])).toEqual([]);
  });

  it("ranks an exact name above a fuzzy near-miss (fuzzy never outranks exact)", () => {
    const exact = makeStop({ stop_id: "S0005", stop_name: "Baneshwore" });
    const fuzzyNearMiss = makeStop({ stop_id: "S0019", stop_name: "Baneshwor" });
    // "Baneshwor" is within one edit of the query "baneshwore" (fuzzy
    // tier 40); "Baneshwore" is an exact name match (tier 100).
    const results = searchStops("baneshwore", [fuzzyNearMiss, exact]);
    expect(results[0]?.stop.stop_id).toBe("S0005");
    expect(results[1]?.stop.stop_id).toBe("S0019");
  });

  it("does not depend on dataset order (ranking, not first-in-dataset)", () => {
    // Put the exact match LAST in the array; ranking must still surface
    // it first regardless of input order.
    const filler = [
      makeStop({ stop_id: "S0002", stop_name: "Kapan Stop" }),
      makeStop({ stop_id: "S0003", stop_name: "Shobhabhagwati" }),
      makeStop({ stop_id: "S0004", stop_name: "Kaushaltar" }),
    ];
    const ratna = makeStop({ stop_id: "S0013", stop_name: "Ratna Park" });
    const results = searchStops("ratnapark", [...filler, ratna]);
    expect(results[0]?.stop.stop_id).toBe("S0013");
  });

  it("breaks ties by shorter name then alphabetically, deterministically", () => {
    const a = makeStop({ stop_id: "S0001", stop_name: "Baneshwor Chowk" });
    const b = makeStop({ stop_id: "S0002", stop_name: "Baneshwor" });
    const results = searchStops("baneshwor", [a, b]);
    // Both match the query as a prefix; the shorter name should win.
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0002", "S0001"]);
  });

  it("returns an empty list for a blank or whitespace-only query", () => {
    const stop = makeStop({ stop_name: "Ratna Park" });
    expect(searchStops("", [stop])).toEqual([]);
    expect(searchStops("   ", [stop])).toEqual([]);
  });
});