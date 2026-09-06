with open('components/search/StopAutocomplete.tsx', 'r') as f:
    content = f.read()

# Replace the double quotes with "
content = content.replace(
    'No stops match "{debouncedValue.trim()}".',
    'No stops match "{debouncedValue.trim()}".'
)

with open('components/search/StopAutocomplete.tsx', 'w') as f:
    f.write(content)
print('Done')