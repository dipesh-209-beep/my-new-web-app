// Shared HTML escaping utility for Leaflet popups/tooltips
// Stop/route names come from admin data entry (or ultimately OSM/CSV
// imports) and get interpolated into raw HTML strings. Leaflet treats
// string content as HTML, not text, so anything containing < /> / &
// etc. would otherwise render (and execute, for something like an
// <img onerror>) as markup. Escape every such value at the point of
// interpolation.
//
// `&` must be escaped first: escaping it after `<`/`>` would turn the
// `&` in an already-produced `&lt;` into `&amp;lt;`. It is a no-op to
// skip it entirely (the bug this fixes): a bare `&` in the input would
// survive, and while every current sink is element content -- where the
// browser re-reads a decoded entity as text -- any sink that later moved
// to an attribute, URL or JS context would re-decode `&#60;...&#62;`
// back into live markup.
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}
