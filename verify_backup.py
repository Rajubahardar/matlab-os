import os
import sys

dst = r'E:\matlab os\backup_V1_PY_20261107_175948'
print(f'Backup path: {dst}')
print(f'Exists: {os.path.exists(dst)}')
print(f'Is dir: {os.path.isdir(dst)}')

if os.path.exists(dst):
    items = os.listdir(dst)
    print(f'\nItems in backup ({len(items)}):')
    for item in sorted(items):
        full = os.path.join(dst, item)
        if os.path.isdir(full):
            print(f'  [DIR] {item}')
        else:
            print(f'  [FILE] {item}')
    
    # Check for key project files
    key_files = ['matlab_os', 'config.toml', 'docs', 'tests', '.env.example']
    print('\nKey project files check:')
    for kf in key_files:
        kfpath = os.path.join(dst, kf)
        print(f'  {kf}: {"FOUND" if os.path.exists(kfpath) else "MISSING"}')