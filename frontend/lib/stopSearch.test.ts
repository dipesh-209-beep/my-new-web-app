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

  it("ranks a name fuzzy match above a landmark-only fuzzy match (score, not length)", () => {
    // Both stops reach "koteshwar" only through the lowest tier: the token
    // "koteshwor" is one edit from that query, as the name of one stop and
    // as the landmark of the other. The landmark-only stop has the shorter
    // name ("Ghat"), so if both shared a single fuzzy score the length
    // tie-break would float it above the name match. The name tier must be
    // 40 and the alias/landmark tier 30 for the name match to win on score.
    const nameMatch = makeStop({ stop_id: "S0107", stop_name: "Koteshwor Stop" });
    const landmarkMatch = makeStop({
      stop_id: "S0200",
      stop_name: "Ghat",
      landmark: "Koteshwor Road",
    });
    const results = searchStops("koteshwar", [landmarkMatch, nameMatch]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0107", "S0200"]);
    expect(results.map((r) => r.score)).toEqual([40, 30]);
    expect(results.map((r) => r.matchedField)).toEqual(["name", "landmark"]);
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

describe("case-colliding stop names", () => {
  // The real dataset carries near-duplicate records that share a normalized
  // name but differ in capitalization -- e.g. S0130 "Kalanki" and S_395
  // "KALANKI". They are separate, distinct stops. The curated record is
  // normally cased; the auto-generated duplicate is single-cased. The
  // canonical record must win on relevance, not by luck of stop_id ordering.
  function kalankiPair(): Stop[] {
    return [
      makeStop({ stop_id: "S_395", stop_name: "KALANKI" }),
      makeStop({ stop_id: "S0130", stop_name: "Kalanki", district: "KATHMANDU", ward: 28 }),
    ];
  }

  it("orders 'Kalanki' before 'KALANKI' for the query 'kalanki'", () => {
    const results = searchStops("kalanki", kalankiPair());
    expect(results.map((r) => r.stop.stop_name)).toEqual(["Kalanki", "KALANKI"]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0130", "S_395"]);
    // The canonically-cased record is an exact name match; the single-cased
    // duplicate is a case-only variant and is demoted to the prefix tier.
    expect(results[0]?.score).toBe(100);
    expect(results[0]?.matchedField).toBe("name");
    expect(results[1]?.score).toBe(80);
    expect(results[1]?.matchedField).toBe("name");
  });

  it("ranks a canonically-cased name ahead of a case variant even when its stop_id sorts later", () => {
    // Regression: the old tie-break fell through to raw stop_id once the
    // folded names matched, so a variant with the smaller id could win. The
    // ranking must not depend on which id the duplicate happens to carry.
    const variant = makeStop({ stop_id: "S0001", stop_name: "KALANKI" });
    const canonical = makeStop({ stop_id: "S0002", stop_name: "Kalanki" });
    const results = searchStops("kalanki", [variant, canonical]);
    expect(results.map((r) => r.stop.stop_name)).toEqual(["Kalanki", "KALANKI"]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0002", "S0001"]);
  });

  it("orders 'Tripureshwor' before 'TRIPURESHWOR' for the query 'tripureshwor'", () => {
    const stops = [
      makeStop({ stop_id: "S_396", stop_name: "TRIPURESHWOR" }),
      makeStop({ stop_id: "S0056", stop_name: "Tripureshwor", district: "KATHMANDU", ward: 15 }),
    ];
    const results = searchStops("tripureshwor", stops);
    expect(results.map((r) => r.stop.stop_name)).toEqual(["Tripureshwor", "TRIPURESHWOR"]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0056", "S_396"]);
  });

  it("orders 'Bhadrakali' before 'bhadrakali' (an all-lower variant)", () => {
    const stops = [
      makeStop({ stop_id: "S_394", stop_name: "bhadrakali" }),
      makeStop({ stop_id: "S0088", stop_name: "Bhadrakali", district: "KATHMANDU" }),
    ];
    const results = searchStops("bhadrakali", stops);
    expect(results.map((r) => r.stop.stop_name)).toEqual(["Bhadrakali", "bhadrakali"]);
    expect(results.map((r) => r.score)).toEqual([100, 80]);
  });

  it("produces the same order on repeated searches", () => {
    const stops = kalankiPair();
    const first = searchStops("kalanki", stops).map((r) => r.stop.stop_id);
    for (let i = 0; i < 5; i += 1) {
      expect(searchStops("kalanki", stops).map((r) => r.stop.stop_id)).toEqual(first);
    }
  });

  it("keeps the query's case irrelevant to the result order", () => {
    const stops = kalankiPair();
    const expected = searchStops("kalanki", stops).map((r) => r.stop.stop_id);
    expect(searchStops("Kalanki", stops).map((r) => r.stop.stop_id)).toEqual(expected);
    expect(searchStops("KALANKI", stops).map((r) => r.stop.stop_id)).toEqual(expected);
  });

  it("still ranks the shorter name first among equal-score matches", () => {
    // Regression guard: the case-insensitive comparison must not disturb
    // the pre-existing length-then-name tie-break.
    const a = makeStop({ stop_id: "S0001", stop_name: "Baneshwor Chowk" });
    const b = makeStop({ stop_id: "S0002", stop_name: "Baneshwor" });
    expect(searchStops("baneshwor", [a, b]).map((r) => r.stop.stop_id)).toEqual(["S0002", "S0001"]);
  });
});

describe("case-variant duplicates among the real dataset", () => {
  // S_398 "sallaghari" is an auto-generated, all-lowercase duplicate. The
  // curated stops nearby are "Sallaghari Stop" (S0095) and "Sallaghari
  // North" (S0014). A query that normalizes exactly to the duplicate's name
  // must not let the case-only variant outrank the canonically-cased stops
  // that merely start with the query.
  it("ranks canonically-cased 'Sallaghari ...' stops above the lowercase duplicate", () => {
    const duplicate = makeStop({ stop_id: "S_398", stop_name: "sallaghari" });
    const stop = makeStop({ stop_id: "S0095", stop_name: "Sallaghari Stop" });
    const north = makeStop({ stop_id: "S0014", stop_name: "Sallaghari North", is_major_stop: true });
    const results = searchStops("sallaghari", [duplicate, north, stop]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0095", "S0014", "S_398"]);
    expect(results.every((r) => r.score === 80)).toBe(true);
    // The duplicate is flagged degraded and sorted last among the tie.
    expect(results[2]?.degraded).toBe(true);
    expect(results.slice(0, 2).every((r) => !r.degraded)).toBe(true);
  });

  it("does not treat a caseless (Devanagari) exact name as a degraded variant", () => {
    const stop = makeStop({ stop_id: "S0071", stop_name: "पिङ्गलस्थान" });
    const results = searchStops("पिङ्गलस्थान", [stop]);
    expect(results[0]?.score).toBe(100);
    expect(results[0]?.degraded).toBe(false);
  });
});

describe("real-world spelling variants (dataset stop names)", () => {
  // The dataset spells these "Koteshwor Stop" (S0107) and "Gaushala Chowk
  // Stop" (S0204). Riders commonly type the alternate vowel ("koteshwar")
  // or drop one ("gausala"). The lowest fuzzy tier must still reach those
  // stops by comparing the query against each *token* of the name, not only
  // the whole normalized string -- which carries the "...stop"/"...chowk"
  // suffix and so exceeds one edit. Names below are the real dataset values.

  it("finds 'Koteshwor Stop' from the common 'koteshwar' spelling", () => {
    const koteshwor = makeStop({ stop_id: "S0107", stop_name: "Koteshwor Stop" });
    const results = searchStops("koteshwar", [koteshwor]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0107"]);
    expect(results[0]?.matchedField).toBe("name");
    expect(results[0]?.score).toBe(40);
  });

  it("finds 'Gaushala Chowk Stop' from the 'gausala' spelling", () => {
    const gaushala = makeStop({ stop_id: "S0204", stop_name: "Gaushala Chowk Stop" });
    const results = searchStops("gausala", [gaushala]);
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0204"]);
    expect(results[0]?.matchedField).toBe("name");
    expect(results[0]?.score).toBe(40);
  });

  it("still finds the compact exact spelling 'ratnapark' at the top tier", () => {
    const ratna = makeStop({ stop_id: "S0013", stop_name: "Ratna Park" });
    const results = searchStops("ratnapark", [ratna]);
    expect(results[0]?.stop.stop_id).toBe("S0013");
    expect(results[0]?.score).toBe(100);
  });

  it("keeps every prefix/real match above the fuzzy near-miss", () => {
    const exact = makeStop({ stop_id: "S0107", stop_name: "Koteshwor Stop" });
    const nearMiss = makeStop({ stop_id: "S_404", stop_name: "koteshwor west stop" });
    // "koteshwor" is an 80 prefix for both names.
    const byPrefix = searchStops("koteshwor", [nearMiss, exact]);
    expect(byPrefix.every((r) => r.score === 80)).toBe(true);
    // Shorter name wins the 80-tier tie, deterministically.
    expect(byPrefix.map((r) => r.stop.stop_id)).toEqual(["S0107", "S_404"]);
    // "koteshwar" drops to the fuzzy tier for both, still shorter-first.
    const byFuzzy = searchStops("koteshwar", [nearMiss, exact]);
    expect(byFuzzy.every((r) => r.score === 40)).toBe(true);
    expect(byFuzzy.map((r) => r.stop.stop_id)).toEqual(["S0107", "S_404"]);
  });

  it("does not fuzzy-match a query shorter than the fuzzy minimum", () => {
    // "chok" is one edit from the token "chowk" but is only 4 chars, below
    // MIN_QUERY_FOR_FUZZY. No chok->chowk normalization is added here, so it
    // must not surface a Chowk stop by fuzzy alone.
    const chowk = makeStop({ stop_id: "S0001", stop_name: "Gwarko Chowk Stop" });
    expect(searchStops("chok", [chowk])).toEqual([]);
  });

  it("matches a dataset alias via the token-aware fuzzy tier", () => {
    const stop = makeStop({ stop_id: "S0071", stop_name: "पिङ्गलस्थान", aliases: "Pingalasthan" });
    const results = searchStops("pingalasthann", [stop]); // one extra 'n'
    expect(results.map((r) => r.stop.stop_id)).toEqual(["S0071"]);
    expect(results[0]?.matchedField).toBe("alias");
  });
});