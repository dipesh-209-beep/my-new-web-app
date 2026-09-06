with open('components/search/StopAutocomplete.tsx', 'rb') as f:
    content = f.read()

# Replace with " - using the literal bytes for "
content = content.replace(
    b'No stops match "{debouncedValue.trim()}".',
    b'No stops match "{debouncedValue.trim()}".'
)

with open('components/search/StopAutocomplete.tsx', 'wb') as f:
    f.write(content)
print('Done')