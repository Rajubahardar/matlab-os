import re

# Read the file
with open("e:\\matlab os\\start.ps1", "r", encoding="utf8") as f:
    content = f.read()

# Fix the -match patterns - replace double-quoted regex with single-quoted
# This fixes the PowerShell syntax error at lines 14 and 98
content = content.replace(
    r'''$engineMatch = $configContent -match "^\s*engine_path\s*=\s*"(.+)""''',
    r'''$engineMatch = $configContent -match '^\s*engine_path\s*=\s*"(.+)"' '''
)

# Write back
with open("e:\\matlab os\\start.ps1", "w", encoding="utf8") as f:
    f.write(content)

print("Fixed -match patterns in start.ps1")