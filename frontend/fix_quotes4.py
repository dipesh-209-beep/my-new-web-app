with open('components/search/StopAutocomplete.tsx', 'rb') as f:
    content = f.read()

# Replace the double quotes with "
# The original has: No stops match "{debouncedValue.trim()}".
# We need: No stops match ""{debouncedValue.trim()}"".
content = content.replace(
    b'No stops match "{debouncedValue.trim()}".',
    b'No stops match ""{debouncedValue.trim()}"".'
)

with open('components/search/StopAutocomplete.tsx', 'wb') as f:
    f.write(content)
print('Done')