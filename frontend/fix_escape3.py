import os

# The exact bytes we want in the file
# The replacement strings should be HTML entities: < > " &apos;
content = bytearray()

content.extend(b'// Shared HTML escaping utility for Leaflet popups/tooltips\n')
content.extend(b'// Stop/route names come from admin data entry (or ultimately OSM/CSV\n')
content.extend(b'// imports) and get interpolated into raw HTML strings. Leaflet treats\n')
content.extend(b'// string content as HTML, not text, so anything containing < /> / &\n')
content.extend(b'// etc. would otherwise render (and execute, for something like an\n')
content.extend(b'// <img onerror>) as markup. Escape every such value at the point of\n')
content.extend(b'// interpolation.\n')
content.extend(b'\n')
content.extend(b'export function escapeHtml(value: string): string {\n')
content.extend(b'  return value\n')
content.extend(b'    .replace(/&/g, "&")\n')
content.extend(b'    .replace(/</g, "<")\n')
content.extend(b'    .replace(/>/g, ">")\n')
content.extend(b'    .replace(/"/g, """)\n')
content.extend(b"    .replace(/'/g, \"&apos;\");\n")
content.extend(b'}\n')

with open('lib/escapeHtml.ts', 'wb') as f:
    f.write(content)
print('Done')