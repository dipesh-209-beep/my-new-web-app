import os

# Write the file with exact bytes - using explicit bytes for HTML entities
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
    # Write the literal characters: & l t ;  (bytes: 26 6c 74 3b)
    f.write(b'    .replace(/</g, "') 
    f.write(bytes([0x26, 0x6c, 0x74, 0x3b]))  # <
    f.write(b'")\n')
    # Write the literal characters: & g t ;  (bytes: 26 67 74 3b)
    f.write(b'    .replace(/>/g, "')
    f.write(bytes([0x26, 0x67, 0x74, 0x3b]))  # >
    f.write(b'")\n')
    # Write the literal characters: & q u o t ; (bytes: 26 71 75 6f 74 3b)
    f.write(b'    .replace(/"/g, "')
    f.write(bytes([0x26, 0x71, 0x75, 0x6f, 0x74, 0x3b]))  # "
    f.write(b'")\n')
    # Write the literal characters: & a p o s ; (bytes: 26 61 70 6f 73 3b)
    f.write(b"    .replace(/'/g, \"")
    f.write(bytes([0x26, 0x61, 0x70, 0x6f, 0x73, 0x3b]))  # &apos;
    f.write(b'");\n')
    f.write(b'}\n')
print('Done')