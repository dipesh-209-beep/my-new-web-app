import os

# Write the file with exact bytes - using explicit hex for HTML entities
with open('lib/escapeHtml.ts', 'wb') as f:
    f.write(b'// Shared HTML escaping utility for Leaflet popups/tooltips\n')
    f.write(b'// Stop/route names come from admin data entry (or ultimately OSM/CSV\n')
    f.write(b'// imports) and get interpolated into raw HTML strings. Leaflet treats\n')
    f.write(b'// string content as HTML, not text, so anything containing < /> / &\n')
    f.write(b'// etc. would otherwise render (and execute, for something like an\n')
    f.write(b'// <img onerror>) as markup. Escape every such value at the point of\n')
    f.write(b'// interpolation.\n')
    f.write(b'\n')
    f.write(b'export function escapeHtml(value: string): string {\n')
    f.write(b'  return value\n')
    f.write(b'    .replace(/&/g, "&")\n')
    # Write < as literal bytes: 26 6c 74 3b
    f.write(b'    .replace(/</g, "<")\n')
    f.write(b'    .replace(/>/g, ">")\n')
    f.write(b'    .replace(/"/g, """)\n')
    f.write(b"    .replace(/'/g, \"&apos;\");\n")
    f.write(b'}\n')
print('Done')