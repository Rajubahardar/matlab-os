import os
import pathlib

# Project root
PROJECT_ROOT = pathlib.Path(r'E:\matlab os')
print(f'Project root: {PROJECT_ROOT}')

# Create main package structure
dirs = [
    'matlab_os',
    'matlab_os/agent',
    'matlab_os/integration',
    'matlab_os/memory',
    'matlab_os/intelligence',
    'matlab_os/validation',
    'projects',
    'data',
    'logs',
    'tests',
    'docs',
]

for d in dirs:
    path = PROJECT_ROOT / d
    path.mkdir(parents=True, exist_ok=True)
    print(f'Created: {path}')

# Create __init__.py files
init_files = {
    r'matlab_os\_\_init__.py': '"""MATLAB OS - MATLAB Expert Agent Package"""\n__version__ = "0.1.0"',
    r'matlab_os/agent\_\_init__.py': '"""MATLAB Agent Package"""',
    r'matlab_os/integration\_\_init__.py': '"""Integration Package"""',
    r'matlab_os/memory\_\_init__.py': '"""Memory Package"""',
    r'matlab_os/intelligence\_\_init__.py': '"""Intelligence Package"""',
    r'matlab_os/validation\_\_init__.py': '"""Validation Package"""',
}

for path_str, content in init_files.items():
    path = PROJECT_ROOT / path_str
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f'Created: {path}')

print('\nDirectory structure and init files created successfully')