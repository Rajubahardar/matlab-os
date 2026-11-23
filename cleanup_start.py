import os

# Read current file
with open("e:\\matlab os\\start.ps1", "r") as f:
    content = f.read()

# Remove duplicate content - keep only the first occurrence sections
# Find the marker lines and strip duplicates
lines = content.split("\n")
seen = set()
unique_lines = []
for line in lines:
    if line not in seen:
        unique_lines.append(line)
        seen.add(line)

# Write cleaned file
with open("e:\\matlab os\\start.ps1", "w") as f:
    f.write("\n".join(unique_lines))

print("File cleaned up - duplicates removed")