import sys

# Read the current file
with open("e:\\matlab os\\start.ps1", "r", encoding="utf8") as f:
    content = f.read()

# Remove duplicate content - keep only the first occurrence of each section
# Strategy: split into lines, keep first occurrence of each unique line
lines = content.split("\n")
seen = set()
unique_lines = []
for line in lines:
    if line not in seen:
        unique_lines.append(line)
        seen.add(line)

# Write cleaned file
with open("e:\\matlab os\\start.ps1", "w", encoding="utf8") as f:
    f.write("\n".join(unique_lines))

print("File cleaned - duplicates removed. Lines:", len(unique_lines))