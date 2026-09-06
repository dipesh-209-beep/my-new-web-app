# Write escapeHtml.ts with proper HTML entity escaping
lines = [
    "// Shared HTML escaping utility for Leaflet popups/tooltips",
    "// Stop/route names come from admin data entry (or ultimately OSM/CSV",
    "// imports) and get interpolated into raw HTML strings. Leaflet treats",
    "// string content as HTML, not text, so anything containing < /> / &",
    "// etc. would otherwise render (and execute, for something like an",
    "// <img onerror>) as markup. Escape every such value at the point of",
    "// interpolation.",
    "",
    "export function escapeHtml(value: string): string {",
    "  return value",
    '    .replace(/&/g, "&")',
    '    .replace(/</g, "<")',
    '    .replace(/>/g, ">")',
    '    .replace(/"/g, """")',
    "    .replace(/'/g, \"'\");",
    "}",
    ""
]

with open('lib/escapeHtml.ts', 'w') as f:
    f.write('\n'.join(lines))
print('Done')