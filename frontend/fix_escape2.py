# Write escapeHtml.ts with proper HTML entity escaping - using explicit bytes
content = (
    "// Shared HTML escaping utility for Leaflet popups/tooltips\n"
    "// Stop/route names come from admin data entry (or ultimately OSM/CSV\n"
    "// imports) and get interpolated into raw HTML strings. Leaflet treats\n"
    "// string content as HTML, not text, so anything containing < /> / &\n"
    "// etc. would otherwise render (and execute, for something like an\n"
    "// <img onerror>) as markup. Escape every such value at the point of\n"
    "// interpolation.\n"
    "\n"
    "export function escapeHtml(value: string): string {\n"
    "  return value\n"
    "    .replace(/&/g, \"&\")\n"
    "    .replace(/</g, \"<\")\n"
    "    .replace(/>/g, \">\")\n"
    "    .replace(/\"/g, \"\")\n"  # This should be " in the output
    "    .replace(/'/g, \"'\");\n"
    "}\n"
)

# Now we need to replace the string literal in the source code
# The issue is the source code has """ but we need the actual characters & q u o t ;
# Let's write the actual bytes
with open('lib/escapeHtml.ts', 'wb') as f:
    f.write(b"""// Shared HTML escaping utility for Leaflet popups/tooltips
// Stop/route names come from admin data entry (or ultimately OSM/CSV
// imports) and get interpolated into raw HTML strings. Leaflet treats
// string content as HTML, not text, so anything containing < /> / &
// etc. would otherwise render (and execute, for something like an
// <img onerror>) as markup. Escape every such value at the point of
// interpolation.

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&")
    .replace(/</g, "<")
    .replace(/>/g, ">")
    .replace(/"/g, """)
    .replace(/'/g, "'");
}
""")
print('Done')