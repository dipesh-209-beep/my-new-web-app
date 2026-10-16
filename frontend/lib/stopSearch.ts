import { Stop } from "@/types/route";

/**
 * Normalize text for stop search comparison: lowercase, strip combining
 * diacritics and any character that isn't a letter or digit. Keeps
 * non-Latin letters (e.g. Devanagari) intact. Lets "Ratna Park",
 * "ratna-park" and "ratnapark" all compare equal so a query written
 * without separators still finds the stop.
 */
export function normalizeText(input: string): string {
  return input
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}]/gu, "")
    .normalize("NFC");
}

/** Individual letter/number runs of a searchable field, for word-prefix matching. */
function wordsOf(input: string): string[] {
  return input.toLowerCase().split(/[^\p{L}\p{M}\p{N}]+/u).filter(Boolean);
}

const MIN_QUERY_FOR_FUZZY = 5;

/**
 * Edit distance (Levenshtein) capped at 1: returns true when converting
 * `b` into `a` needs at most one insertion/deletion/substitution. Used
 * only as a last-resort tier so a deliberate typo like "baneshworr" still
 * finds Baneshwor.
 */
function withinOneEdit(a: string, b: string): boolean {
  if (Math.abs(a.length - b.length) > 1) return false;
  if (a === b) return true;
  const prev = new Array<number>(b.length + 1);
  const curr = new Array<number>(b.length + 1);
  for (let j = 0; j <= b.length; j += 1) prev[j] = j;
  for (let i = 1; i <= a.length; i += 1) {
    curr[0] = i;
    let rowMin = curr[0];
    for (let j = 1; j <= b.length; j += 1) {
      const subCost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + subCost);
      rowMin = Math.min(rowMin, curr[j]);
    }
    if (rowMin > 1) return false;
    prev.splice(0, prev.length, ...curr);
  }
  return prev[b.length] <= 1;
}

/**
 * True when `query` is within one edit of `text` either as a whole, or of
 * any individual token in it. The whole-field comparison alone misses the
 * dataset's suffixed names: a query like "koteshwar" is one edit from the
 * token "koteshwor" in "Koteshwor Stop", but several edits from the whole
 * normalized "koteshworstop", so the token check is what lets a real
 * spelling variant reach the stop. Tokens are normalized the same way as
 * fields (see normalizeText), so diacritics/case still don't matter.
 */
function fuzzyMatchesText(query: string, text: string): boolean {
  if (!text) return false;
  if (withinOneEdit(query, normalizeText(text))) return true;
  return wordsOf(text).some((word) => withinOneEdit(query, normalizeText(word)));
}

type SearchField = "name" | "alias" | "landmark";

/** What a stop matched on, plus its rank score. */
export interface StopMatch {
  stop: Stop;
  score: number;
  matchedField: SearchField;
}

/**
 * Rank stops against a query, best first. Searches name, aliases and
 * landmark (NOT district -- district is a location attribute shared by
 * hundreds of stops, and matching on it made searches like "kathmandu"
 * surface a flood of unrelated stops ahead of a real name match).
 *
 * Tiers, best to worst:
 *   100 exact name
 *    90 exact alias or landmark
 *    80 name starts with the query
 *    70 any searchable word starts with the query, or the alias/landmark
 *       field as a whole starts with it
 *    60 name contains the query
 *    50 alias/landmark contains the query
 *    40 name is within one edit of the query (query ≥ 5 chars)
 *    30 alias/landmark is within one edit of the query
 *
 * Exact and prefix tiers always beat substring tiers, and fuzzy matches can
 * never outrank a real name/alias match. Ties break by shortest name, then
 * case-insensitively alphabetically, then stop_id, so results are stable no
 * matter what order the dataset arrives in (no first-in-dataset cut-off).
 */
export function searchStops(query: string, stops: Stop[]): StopMatch[] {
  const q = normalizeText(query);
  if (!q) return [];

  const matches: StopMatch[] = [];

  for (const stop of stops) {
    const name = normalizeText(stop.stop_name ?? "");
    const aliasesRaw = stop.aliases ?? "";
    const landmarkRaw = stop.landmark ?? "";
    const aliases = normalizeText(aliasesRaw);
    const landmark = normalizeText(landmarkRaw);

    let score = 0;
    let matchedField: SearchField = "name";

    const searchables: { field: SearchField; text: string }[] = [
      { field: "alias", text: aliasesRaw },
      { field: "landmark", text: landmarkRaw },
    ];

    if (name === q) {
      score = 100;
    } else if (aliases === q || landmark === q) {
      score = 90;
      matchedField = aliases === q ? "alias" : "landmark";
    } else if (name.startsWith(q)) {
      score = 80;
    } else if (
      searchables.some((s) => normalizeText(s.text).startsWith(q)) ||
      wordsOf(name).some((w) => w.startsWith(q)) ||
      searchables.some((s) => wordsOf(s.text).some((w) => w.startsWith(q)))
    ) {
      score = 70;
      const aliasWordMatch = searchables.some((s) => wordsOf(s.text).some((w) => w.startsWith(q)));
      const nameWordMatch = wordsOf(name).some((w) => w.startsWith(q));
      if (nameWordMatch) {
        matchedField = "name";
      } else if (aliasWordMatch) {
        matchedField = "alias";
      } else {
        matchedField = "landmark";
      }
    } else if (name.includes(q)) {
      score = 60;
    } else if (aliases.includes(q) || landmark.includes(q)) {
      score = 50;
      matchedField = aliases.includes(q) ? "alias" : "landmark";
    } else if (
      q.length >= MIN_QUERY_FOR_FUZZY &&
      (fuzzyMatchesText(q, stop.stop_name ?? "") ||
        fuzzyMatchesText(q, aliasesRaw) ||
        fuzzyMatchesText(q, landmarkRaw))
    ) {
      if (fuzzyMatchesText(q, stop.stop_name ?? "")) {
        score = 40;
        matchedField = "name";
      } else if (fuzzyMatchesText(q, aliasesRaw)) {
        score = 30;
        matchedField = "alias";
      } else {
        score = 30;
        matchedField = "landmark";
      }
    }

    if (score > 0) {
      matches.push({ stop, score, matchedField });
    }
  }

  matches.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    const aName = a.stop.stop_name ?? "";
    const bName = b.stop.stop_name ?? "";
    if (aName.length !== bName.length) return aName.length - bName.length;
    // Case-insensitive name comparison. Comparing raw code units would sort
    // an all-caps name ("KALANKI") ahead of a mixed-case one ("Kalanki")
    // even though they are equally relevant -- that is an arbitrary artifact
    // of capitalization, not a signal of which stop is canonical. Both
    // records stay distinct; this only makes their order predictable.
    const aFolded = aName.toLowerCase();
    const bFolded = bName.toLowerCase();
    if (aFolded !== bFolded) return aFolded < bFolded ? -1 : 1;
    // Stable, deterministic final key: stop_id is unique, so equal-score
    // names that differ only by case still order consistently.
    return a.stop.stop_id < b.stop.stop_id ? -1 : 1;
  });

  return matches;
}