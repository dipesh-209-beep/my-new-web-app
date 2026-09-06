with open('components/search/StopAutocomplete.tsx', 'rb') as f:
    content = f.read()

# Replace with " - using hex bytes for & q u o t ;
# & = 0x26, q = 0x71, u = 0x75, o = 0x6f, t = 0x74, ; = 0x3b
entity = bytes([0x26, 0x71, 0x75, 0x6f, 0x74, 0x3b])

content = content.replace(
    b'No stops match "{debouncedValue.trim()}".',
    b'No stops match ' + entity + b'{debouncedValue.trim()}' + entity + b'.'
)

with open('components/search/StopAutocomplete.tsx', 'wb') as f:
    f.write(content)
print('Done')