import re
with open('E:\\matlab os\\STATUS.md', 'r', encoding='utf-8') as f:
    content = f.read()
# Fix the duplicated text on line 17
lines = content.split('\n')
# Fix line 17 (index 16)
old_line17 = lines[16]
# Remove the duplicated part after "verified."
# The pattern is: "verified. 3 duplicated blocks..." - we want to keep only the first part
if 'verified. 3 duplicated blocks removed' in old_line17:
    # Keep everything up to and including "verified."
    # Then add the new clean text
    new_line17 = old_line17.split('verified.')[0] + 'verified. Engine-path regex corrected to use double-quoted syntax. Ctrl+C handler added to only stop MATLAB processes started by this launcher. PowerShell syntax structure verified.'
    lines[16] = new_line17
with open('E:\\matlab os\\STATUS.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('STATUS.md fixed')